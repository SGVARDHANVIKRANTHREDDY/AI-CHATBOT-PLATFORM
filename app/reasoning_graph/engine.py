from __future__ import annotations

import time
from typing import Any

from app.agents.agent_router import AgentRouter
from app.agents.agent_state import AgentState
from app.llm.base import LLMProvider
from app.orchestrator.tool_runner import StreamingToolRunner
from app.orchestrator.watchdog import AgentExecutionContext
from app.reasoning_graph.models import NodeStatus, NodeType, ReasoningGraph, ReasoningNode
from app.shared.monitoring import TOOL_CALL_COUNT
from app.shared.tracing import start_span
from app.shared.utils import emit_observability_event, get_logger
from app.swarm.execution import ExecutionStrategy, SwarmExecution
from app.vector_memory.memory_retriever import MemoryRetriever

_LOG = get_logger(__name__)


class ReasoningGraphEngine:
    """
    Executes a ReasoningGraph by resolving dependencies and routing nodes
    to appropriate handlers (LLM, Tools, Memory, or Agents).

    Supports pluggable execution strategies:
      - SequentialExecution (default)
      - SwarmExecution (parallel with agent limits)
    """

    def __init__(
        self,
        llm: LLMProvider,
        tool_runner: StreamingToolRunner,
        memory_retriever: MemoryRetriever,
        agent_router: AgentRouter,
        strategy: ExecutionStrategy | None = None,
    ):
        self.llm = llm
        self.tool_runner = tool_runner
        self.memory_retriever = memory_retriever
        self.agent_router = agent_router
        self.strategy = strategy or SwarmExecution()

    async def execute(
        self,
        graph: ReasoningGraph,
        state: AgentState,
        exec_ctx: AgentExecutionContext | None = None,
    ) -> list[Any]:
        """
        Main execution loop for the reasoning graph.
        Delegates to the configured ExecutionStrategy.
        Returns a list of (agent_key, version_id) tuples for feedback.
        """
        _LOG.info(f"Starting ReasoningGraph execution ({len(graph.nodes)} nodes) with {type(self.strategy).__name__}")
        self._exec_ctx = exec_ctx

        raw_results = await self.strategy.execute(graph, state, self._execute_node)

        # Filter for valid version entries
        used_versions = [(agent_key, v_id) for v_id, agent_key in raw_results if v_id and agent_key]
        return used_versions

    async def _execute_node(
        self, node: ReasoningNode, graph: ReasoningGraph, state: AgentState
    ) -> tuple[str | None, str | None]:
        """Execute a single node and return (version_id, agent_key) if applicable."""
        node.status = NodeStatus.RUNNING
        t0 = time.perf_counter()
        _LOG.info(f"Executing {node.type} node: {node.id}")

        version_id = None
        agent_key = None

        try:
            # ── Watchdog budget check before each node ──
            if self._exec_ctx:
                self._exec_ctx.record_iteration()
                self._exec_ctx.check()

            dep_results = {d: graph.nodes[d].result for d in node.dependencies}
            context = f"Dependency Results: {dep_results}\nTask: {node.task}"

            if node.metadata.get("agent"):
                # Route to registered agent
                from app.agents.task_graph import TaskNode as LegacyTaskNode

                legacy_node = LegacyTaskNode(id=node.id, agent=node.metadata["agent"], task=node.task)
                node.result, version_id = await self.agent_router.route_and_execute(legacy_node, state)
                agent_key = node.metadata["agent"]

            elif node.type == NodeType.REASONING:
                node.result = await self.llm.ask(
                    f"Perform the following reasoning task:\n{context}",
                    system_prompt="You are a logical reasoning module.",
                )

            elif node.type == NodeType.TOOL_CALL:
                # format expected: tool_name(args)
                import re

                match = re.search(r"(\w+)\((.*)\)", node.task)
                if match:
                    name, args = match.groups()
                    if self._exec_ctx:
                        self._exec_ctx.record_tool_call(name)
                        self._exec_ctx.check()
                    with start_span("tool.execute", {"tool.name": name}):
                        tool_t0 = time.perf_counter()
                        node.result = await self.tool_runner.run_tool(name, args)
                        tool_elapsed = (time.perf_counter() - tool_t0) * 1000
                        TOOL_CALL_COUNT.labels(tool_name=name, status="ok").inc()
                        emit_observability_event(
                            _LOG,
                            event="tool.execute",
                            category="tool",
                            duration_ms=tool_elapsed,
                            tool_name=name,
                        )
                    state.increment_tool_count()
                else:
                    node.result = f"Error: Failed to parse tool call from {node.task}"

            elif node.type == NodeType.MEMORY_LOOKUP:
                node.result = await self.memory_retriever.retrieve_context(node.task)

            node.status = NodeStatus.COMPLETED
            elapsed = (time.perf_counter() - t0) * 1000
            emit_observability_event(
                _LOG,
                event="agent.node.complete",
                category="agent",
                duration_ms=elapsed,
                node_id=node.id,
                node_type=str(node.type),
                agent_key=agent_key,
            )

            # Save partial result for watchdog recovery
            if self._exec_ctx:
                self._exec_ctx.save_partial({"node": node.id, "result": node.result})

        except Exception as e:
            # Re-raise budget exceptions so the orchestrator handles them
            from app.orchestrator.watchdog import AgentBudgetExceededError

            if isinstance(e, AgentBudgetExceededError):
                raise
            elapsed = (time.perf_counter() - t0) * 1000
            emit_observability_event(
                _LOG,
                event="agent.node.error",
                category="agent",
                duration_ms=elapsed,
                node_id=node.id,
                error=str(e),
            )
            _LOG.error(f"Node {node.id} failed: {e}")
            node.status = NodeStatus.FAILED
            node.result = f"Error: {e}"

        return version_id, agent_key

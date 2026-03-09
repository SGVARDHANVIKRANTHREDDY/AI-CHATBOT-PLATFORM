from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.agents.agent_state import AgentState
from app.agents.task_graph import TaskNode
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class AgentRouter:
    """
    Routes task nodes to their respective agent implementations.
    """

    def __init__(self):
        self.agents: dict[str, Callable[[TaskNode, AgentState], Awaitable[Any]]] = {}

    def register_agent(self, name: str, agent_fn: Callable[[TaskNode, AgentState], Awaitable[Any]]):
        self.agents[name] = agent_fn
        _LOG.info(f"Registered agent: {name}")

    async def route_and_execute(self, node: TaskNode, state: AgentState) -> tuple[Any, str]:
        """Dispatches a task to the registered agent and returns (result, version_id)."""
        if node.agent not in self.agents:
            msg = f"No agent registered for type: {node.agent}"
            _LOG.error(msg)
            return f"Error: {msg}", "static"

        _LOG.info(f"Routing task {node.id} to {node.agent}")
        state.add_trace(f"Starting task: {node.id} via {node.agent}")

        node.status = "running"
        try:
            result, version_id = await self.agents[node.agent](node, state)

            node.status = "completed"
            node.result = result
            state.add_result(node.id, result)
            state.add_trace(f"Completed task: {node.id} (v: {version_id})")
            return result, version_id
        except Exception as e:
            _LOG.error(f"Agent {node.agent} failed task {node.id}: {e}")
            node.status = "failed"
            node.result = f"Error: {e}"
            state.add_trace(f"Failed task: {node.id} - {e}")
            return node.result, "error"

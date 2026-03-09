from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from app.agents.agent_router import AgentRouter
from app.agents.agent_state import AgentState
from app.agents.coding_agent import CodingAgent
from app.agents.critic_agent import CriticAgent
from app.agents.planner_agent import PlannerAgent
from app.agents.reasoning_agent import ReasoningAgent
from app.agents.research_agent import ResearchAgent
from app.cache.semantic_cache import SemanticCache
from app.config.settings import settings
from app.evaluation.dataset_builder import DatasetBuilder
from app.evaluation.response_grader import ResponseGrader
from app.knowledge_graph.entity_extractor import EntityExtractor
from app.llm.model_router import ModelRouter
from app.memory.memory_controller import UnifiedMemoryController
from app.orchestrator.pipeline import ChatPipeline
from app.orchestrator.tool_runner import StreamingToolRunner
from app.orchestrator.watchdog import (
    AgentBudgetExceededError,
    AgentExecutionContext,
    AgentWatchdog,
)
from app.plugins.registry import PluginRegistry
from app.prompts.evolution.manager import PromptEvolutionManager
from app.reasoning_graph.engine import ReasoningGraphEngine
from app.reasoning_graph.models import NodeStatus

# ── Reliability & Production Hardening ────────────────────────────
from app.reliability.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.reliability.load_guard import (
    AgentExecutionLimiter,
    LoadGuardRejectionError,
)
from app.reliability.response_guard import ResponseValidator
from app.reliability.retry_policy import RetryPolicy
from app.reliability.timeout_controller import TimeoutController
from app.security.prompt_guard import PromptGuard
from app.security.refusal_guard import decide_refusal
from app.shared.monitoring import (
    AGENT_CRASHES,
    AGENT_EXECUTION_TIME,
    AI_AGENT_ITERATIONS,
    HALLUCINATION_RATE,
    LLM_REQUEST_FAILURES,
    REASONING_GRAPH_DEPTH,
    REASONING_GRAPH_NODES,
    RESPONSE_VALIDATION_ISSUES,
)
from app.shared.utils import emit_observability_event, get_logger
from app.tool_router.neural_router import NeuralToolRouter
from app.vector_memory.memory_retriever import MemoryRetriever

_LOG = get_logger(__name__)

# ── Default reliability settings ──────────────────────────────────
_DEFAULT_LLM_TIMEOUT = float(settings.LLM_TIMEOUT)
_DEFAULT_LLM_RETRIES = settings.LLM_RETRY_ATTEMPTS


class ChatOrchestrator:
    """Main orchestration layer with production-hardening.

    Integrates: CircuitBreaker, RetryPolicy, TimeoutController,
    ResponseValidator, AgentExecutionLimiter, and UnifiedMemoryController.
    """

    def __init__(self, pipeline: ChatPipeline, llm_provider: Any):
        self.pipeline = pipeline
        self.llm = llm_provider
        self.guard = PromptGuard()
        self.sem_cache = SemanticCache()

        # Core Infrastructure
        self.router = ModelRouter()
        self.tool_runner = StreamingToolRunner()
        self.memory_retriever = MemoryRetriever()

        # Frontier Layer: Prompt Evolution
        prompt_storage = settings.DATA_DIR / "prompts" / "evolution.json"
        self.prompt_manager = PromptEvolutionManager(str(prompt_storage), self.llm)

        # Frontier Layer: Neural Tool Selection
        self.neural_tool_router = NeuralToolRouter()

        # Multi-Agent Layer
        self.planner = PlannerAgent(self.llm, self.prompt_manager)
        self.agent_router = AgentRouter()
        self.critic = CriticAgent(self.llm, self.prompt_manager)

        # Knowledge & Plugins
        self.kg_extractor = EntityExtractor(self.llm)
        self.plugins = PluginRegistry()
        self.plugins.discover()

        # Register Specialized Agents
        res_agent = ResearchAgent(self.llm, self.pipeline, self.tool_runner, self.prompt_manager)
        re_agent = ReasoningAgent(self.llm, self.prompt_manager)
        code_agent = CodingAgent(self.llm, self.tool_runner, self.prompt_manager)

        self.agent_router.register_agent("research_agent", res_agent.execute)
        self.agent_router.register_agent("reasoning_agent", re_agent.execute)
        self.agent_router.register_agent("coding_agent", code_agent.execute)

        # Register Plugin Tools into Tool Runner
        self.tool_runner.registry.update(self.plugins.get_tools())

        # Frontier Layer: Reasoning Graph Engine (needs agent_router)
        self.graph_engine = ReasoningGraphEngine(self.llm, self.tool_runner, self.memory_retriever, self.agent_router)

        # Evaluation
        self.grader = ResponseGrader(self.llm)
        self.dataset = DatasetBuilder()

        # ── Production Hardening ──────────────────────────────────
        # Circuit breaker for LLM provider calls
        self._llm_circuit = CircuitBreaker(
            "llm_provider",
            failure_threshold=5,
            recovery_timeout=30.0,
            fallback=self._llm_fallback,
        )

        # Retry policy for LLM calls (wraps inside circuit breaker)
        self._llm_retry = RetryPolicy(
            "llm_provider",
            max_retries=_DEFAULT_LLM_RETRIES,
            base_delay=1.0,
            max_delay=15.0,
        )

        # Timeout controller for LLM calls
        self._llm_timeout = TimeoutController("llm_ask", timeout_seconds=_DEFAULT_LLM_TIMEOUT)

        # Response validator with known tool names
        known_tools = set(self.tool_runner.registry.keys())
        self._response_validator = ResponseValidator(
            known_tools=known_tools,
            max_response_length=settings.UX_TOKEN_LIMIT * 4,  # ~chars
        )

        # Agent execution limiter
        self._agent_limiter = AgentExecutionLimiter(max_agents=20, queue_timeout=15.0)

        # Agent safety watchdog
        self._watchdog = AgentWatchdog(poll_interval=1.0)

        # Unified memory controller
        self._memory_controller = UnifiedMemoryController(
            memory_retriever=self.memory_retriever,
            llm_provider=self.llm,
        )

    # ── Reliability-wrapped LLM call ──────────────────────────────

    async def _safe_llm_ask(self, prompt: str, system_prompt: str = "", model: str | None = None) -> str:
        """LLM call protected by timeout → retry → circuit breaker.

        Call chain: CircuitBreaker → RetryPolicy → TimeoutController → LLM
        """

        async def _timed_ask() -> str:
            return await self._llm_timeout.execute(self.llm.ask, prompt, system_prompt=system_prompt, model=model)

        async def _retried_ask() -> str:
            return await self._llm_retry.execute(_timed_ask)

        try:
            return await self._llm_circuit.call(_retried_ask)
        except CircuitOpenError:
            LLM_REQUEST_FAILURES.labels(provider="primary", error_type="circuit_open").inc()
            return "I'm temporarily unable to process this request. Please try again shortly."
        except Exception as exc:
            LLM_REQUEST_FAILURES.labels(provider="primary", error_type=type(exc).__name__).inc()
            raise

    async def _llm_fallback(self, *args: Any, **kwargs: Any) -> str:
        """Fallback when circuit is open — return a safe degraded response."""
        _LOG.warning("LLM circuit open — using fallback response")
        return "I'm experiencing temporary difficulties connecting to my language model. Please try again in a moment."

    # ── Agent loop ────────────────────────────────────────────────

    async def _execute_agent_loop(
        self, question: str, session_id: str
    ) -> tuple[str, dict[str, Any], AgentState, list[tuple[str, str]]]:
        """Orchestrates the granular reasoning graph execution.

        Protected by AgentExecutionLimiter to bound concurrent agent loops
        and by AgentWatchdog for hard wall-clock enforcement.
        """
        import uuid as _uuid

        exec_id = f"agent-{_uuid.uuid4().hex[:12]}"

        async def _guarded_loop(
            ctx: AgentExecutionContext,
        ) -> tuple[str, dict[str, Any], AgentState, list[tuple[str, str]]]:
            return await self._run_agent_graph(question, session_id, ctx)

        result, ctx = await self._watchdog.guarded_execute(
            exec_id,
            _guarded_loop,
            session_id=session_id,
        )

        if result is not None:
            return result

        # Budget exceeded — build degraded response from partial results
        _LOG.warning(
            "Agent loop %s terminated: %s — returning partial results",
            exec_id,
            ctx.termination_reason.value,
        )
        state = AgentState(session_id=session_id)
        state.add_trace(
            f"Watchdog terminated execution: {ctx.termination_reason.value} "
            f"(iters={ctx.iteration_count}, tools={ctx.tool_call_count}, "
            f"elapsed={ctx.elapsed_seconds:.1f}s)"
        )
        partial_text = (
            "\n".join(str(r.get("result", "")) for r in ctx.partial_results)
            or "I was unable to complete the full analysis within the time budget."
        )

        return partial_text, {}, state, []

    async def _run_agent_graph(
        self,
        question: str,
        session_id: str,
        ctx: AgentExecutionContext,
    ) -> tuple[str, dict[str, Any], AgentState, list[tuple[str, str]]]:
        """Inner agent loop — runs under watchdog protection."""
        _LOG.info("[%s] Starting reasoning graph for: %s", ctx.execution_id, question)
        agent_t0 = time.perf_counter()

        # 1. Planning
        graph, planner_version = await self.planner.plan(question)
        ctx.record_iteration()
        ctx.check()

        state = AgentState(session_id=session_id)
        state.add_trace(f"Planner version: {planner_version}")

        used_versions = [("planner_agent", planner_version)]

        emit_observability_event(
            _LOG,
            event="prompt.version",
            category="prompt",
            prompt_key="planner_agent",
            version=planner_version,
        )

        # 2. Execution (Graph-based) — pass context for per-node budget checks
        try:
            graph_versions = await self.graph_engine.execute(graph, state, ctx)
            used_versions.extend(graph_versions)
        except AgentBudgetExceededError:
            _LOG.warning("[%s] budget exceeded during graph execution", ctx.execution_id)
            state.add_trace(f"Budget exceeded: {ctx.termination_reason.value}")
        except Exception as exc:
            AGENT_CRASHES.labels(agent_type="graph_engine").inc()
            _LOG.error("Graph engine execution failed: %s", exc)
            state.add_trace(f"Graph execution error: {exc}")

        # ── Telemetry: Graph complexity ──
        REASONING_GRAPH_NODES.observe(len(graph.nodes))
        max_depth = max((n.depth for n in graph.nodes.values()), default=0)
        REASONING_GRAPH_DEPTH.observe(max_depth)
        AI_AGENT_ITERATIONS.observe(len(state.completed_steps))

        # 3. Aggregation
        results_summary = "\n".join(
            [f"{n.id} ({n.type}): {n.result}" for n in graph.nodes.values() if n.status == NodeStatus.COMPLETED]
        )
        final_aggregation_prompt = (
            f"Based on the following execution results, provide a comprehensive "
            f"final answer to: '{question}'\n\nResults:\n{results_summary}"
        )

        # Use reliability-wrapped LLM call for synthesis
        version_id = "static"
        if self.prompt_manager:
            template, version_id = self.prompt_manager.get_prompt_with_id("synthesizer")
            if not template:
                self.prompt_manager.initialize_prompt(
                    "synthesizer",
                    "You are a professional synthesizer. Provide a final answer based on the context.",
                )
                template, version_id = self.prompt_manager.get_prompt_with_id("synthesizer")
            used_versions.append(("synthesizer", version_id))
            final_answer = await self._safe_llm_ask(final_aggregation_prompt, system_prompt=template)
        else:
            final_answer = await self._safe_llm_ask(
                final_aggregation_prompt,
                system_prompt="You are a professional synthesizer.",
            )

        # 4. Self-Correction (Critic)
        evaluation, critic_version = await self.critic.evaluate(final_answer, state)
        used_versions.append(("critic_agent", critic_version))

        if evaluation.get("needs_revision") and evaluation.get("corrected_response"):
            _LOG.info("Critic requested revision. Using corrected response.")
            final_answer = evaluation["corrected_response"]
            state.add_trace("Response corrected by CriticAgent.")

        # ── Telemetry: Hallucination tracking ──
        if evaluation.get("hallucinations_detected"):
            HALLUCINATION_RATE.labels(severity="high").inc()

        # ── Observability: Agent execution timing + prompt versions ──
        agent_elapsed = time.perf_counter() - agent_t0
        AGENT_EXECUTION_TIME.labels(agent_type="full_loop").observe(agent_elapsed)
        for p_key, v_id in used_versions:
            emit_observability_event(
                _LOG,
                event="prompt.version",
                category="prompt",
                prompt_key=p_key,
                version=v_id,
            )
        emit_observability_event(
            _LOG,
            event="agent.loop.complete",
            category="agent",
            duration_ms=agent_elapsed * 1000,
            node_count=len(graph.nodes),
            steps_completed=len(state.completed_steps),
        )

        return final_answer, evaluation, state, used_versions

    # ── Main entry point ──────────────────────────────────────────

    async def generate_answer(
        self,
        question: str,
        *,
        session_id: str = "default",
        use_rag: bool = True,
        use_web: bool = False,
        rag_top_k: int = 3,
        system_prompt: str | None = None,
        stream: bool = False,
    ) -> dict[str, Any] | AsyncIterator[str]:
        # 0. Prompt Security Check
        if self.guard.scan(question):
            return {
                "answer": "Security Alert: Suspicious input pattern detected. Request blocked.",
                "confidence": "low",
                "used_rag": False,
                "used_web": False,
                "citations": [],
            }

        # 0b. Semantic Cache Check
        cached_response = await self.sem_cache.get(question)
        if cached_response:
            return {
                "answer": cached_response,
                "confidence": "high",
                "used_rag": False,
                "used_web": False,
                "citations": [],
                "cached": True,
            }

        # 1. Intelligence & Context Gathering
        routing_info = await self.router.route(question)
        model = routing_info["selected_model"]

        long_term_context = await self.memory_retriever.retrieve_context(question)

        final_prompt, sys_prompt, context_data = await self.pipeline.gather_context(
            question,
            use_rag=use_rag,
            use_web=use_web,
            rag_top_k=rag_top_k,
            system_prompt=system_prompt,
            memory_vector_context=long_term_context,
        )

        # 2. Safety Refusal (Pre-LLM)
        refusal = decide_refusal(use_rag=use_rag, top_rag_score=context_data.get("rag_score"))
        if refusal and refusal.refuse:
            return {
                "answer": refusal.message,
                "confidence": "low",
                "used_rag": use_rag,
                "rag_score": context_data.get("rag_score"),
                "used_web": use_web,
                "citations": context_data.get("rag_citations", []),
            }

        # 3. Agentic Intelligence Execution
        if stream:
            llm_stream = self.llm.ask_stream(final_prompt, system_prompt=sys_prompt, model=model)
            return self.tool_runner.wrap_stream(llm_stream)

        # Non-streaming: Execute Full Agentic Brain (bounded by agent limiter)
        try:
            raw_answer, critic_eval, agent_state, used_versions = await self._agent_limiter.execute(
                self._execute_agent_loop, question, session_id
            )
        except LoadGuardRejectionError:
            _LOG.warning("Agent limiter rejected — too many concurrent agents")
            # Fallback to direct LLM call without agentic loop
            raw_answer = await self._safe_llm_ask(final_prompt, system_prompt=sys_prompt, model=model)
            critic_eval = {}
            agent_state = AgentState(session_id=session_id)
            used_versions = []

        # 4. Response Validation (Production Hardening)
        validation = self._response_validator.validate(raw_answer)
        if validation.sanitized_response:
            raw_answer = validation.sanitized_response

        # Emit validation metrics
        for issue in validation.issues:
            RESPONSE_VALIDATION_ISSUES.labels(category=issue.category, severity=issue.severity).inc()

        if not validation.is_valid:
            _LOG.warning("Response validation failed — %d issues", len(validation.issues))

        # 5. Post-Process: Knowledge Graph & Evaluation
        kg_data = await self.kg_extractor.extract(f"User: {question}\nAssistant: {raw_answer}")
        self.memory_retriever.kg.add_data(kg_data["entities"], kg_data["relationships"])

        overall_grade = await self.grader.grade(question, raw_answer)
        score = overall_grade.get("score", 0.0)

        # Record Feedback for Self-Evolving Prompts
        for p_key, v_id in used_versions:
            await self.prompt_manager.record_feedback(p_key, v_id, score)

        # Log to evaluation dataset
        self.dataset.log_interaction(question, raw_answer, overall_grade, plan=agent_state.task_graph)

        # 6. Memory Persistence (via unified controller)
        await self._memory_controller.store_interaction(session_id, question, raw_answer, kg_data=kg_data)

        if self.pipeline.memory_service:
            self.pipeline.memory_service.add_message("user", question)
            self.pipeline.memory_service.add_message("assistant", raw_answer)

        # Populate Semantic Cache
        await self.sem_cache.set(question, raw_answer)

        return {
            "answer": raw_answer,
            "confidence": "high" if overall_grade.get("score", 0) > 0.8 else "medium",
            "used_rag": use_rag,
            "used_web": use_web,
            "citations": context_data.get("rag_citations", []),
            "model": model,
            "intent": routing_info["intent"],
            "agent_stats": {
                "steps": len(agent_state.completed_steps),
                "is_hallucination_free": not critic_eval.get("hallucinations_detected", False),
                "grade_score": overall_grade.get("score"),
            },
            "validation": {
                "is_valid": validation.is_valid,
                "issues_count": len(validation.issues),
            },
        }

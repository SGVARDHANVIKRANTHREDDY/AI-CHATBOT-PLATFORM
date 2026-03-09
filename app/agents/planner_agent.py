from __future__ import annotations

import json
import re
import time
from typing import Any

from app.llm.base import LLMProvider
from app.reasoning_graph.models import NodeType, ReasoningGraph, ReasoningNode
from app.shared.monitoring import AGENT_EXECUTION_TIME
from app.shared.tracing import traced
from app.shared.utils import emit_observability_event, get_logger

_LOG = get_logger(__name__)


class PlannerAgent:
    """
    Decomposes user queries into a granular ReasoningGraph.
    """

    PLANNER_PROMPT_KEY = "planner_agent"

    DEFAULT_PROMPT = """You are an AI Task Planner. Your goal is to break a user's request into a granular Directed Acyclic Graph (DAG) of reasoning steps, tool calls, and memory lookups.

Node Types:
- reasoning: Analytical steps or logic.
- tool_call: External actions (e.g., search, code). Format task as 'tool_name(args)'.
- memory_lookup: Retrieving specific context from memory.

You must output a JSON object with a list of 'nodes'.
Each node must have: 'id', 'type' (reasoning, tool_call, memory_lookup), 'task', and 'dependencies' (list of IDs).

Example for 'Find AI news and summarize':
{
  "nodes": [
    {"id": "mem", "type": "memory_lookup", "task": "latest AI news query history", "dependencies": []},
    {"id": "search", "type": "tool_call", "task": "web_search(query='latest AI news today')", "dependencies": ["mem"]},
    {"id": "summary", "type": "reasoning", "task": "Summarize the key trends from search results", "dependencies": ["search"]}
  ]
}

User Request: {query}
JSON Output:"""

    def __init__(self, llm: LLMProvider, prompt_manager: Any = None):
        self.llm = llm
        self.prompt_manager = prompt_manager
        if self.prompt_manager:
            self.prompt_manager.initialize_prompt(self.PLANNER_PROMPT_KEY, self.DEFAULT_PROMPT)

    @traced("agent.plan")
    async def plan(self, query: str) -> tuple[ReasoningGraph, str]:
        """Generates a ReasoningGraph and returns the prompt version ID used."""
        t0 = time.perf_counter()
        version_id = "static"
        if self.prompt_manager:
            template, version_id = self.prompt_manager.get_prompt_with_id(self.PLANNER_PROMPT_KEY)
            prompt = template.replace("{query}", query)
        else:
            prompt = self.DEFAULT_PROMPT.replace("{query}", query)

        emit_observability_event(
            _LOG,
            event="agent.plan.start",
            category="agent",
            prompt_version=version_id,
            query_length=len(query),
        )

        result_str = await self.llm.ask(prompt, system_prompt="You are a precise task decomposition assistant.")

        try:
            match = re.search(r"\{.*\}", result_str, re.DOTALL)
            data = json.loads(match.group(0)) if match else json.loads(result_str)

            graph = ReasoningGraph()
            for node_data in data.get("nodes", []):
                nd = dict(node_data)
                # Normalise legacy "agent" field into metadata
                if "agent" in nd and "type" not in nd:
                    nd.setdefault("metadata", {})["agent"] = nd.pop("agent")
                    nd["type"] = "reasoning"
                elif "agent" in nd:
                    nd.setdefault("metadata", {})["agent"] = nd.pop("agent")
                node = ReasoningNode(**nd)
                graph.add_node(node)

            elapsed = (time.perf_counter() - t0) * 1000
            AGENT_EXECUTION_TIME.labels(agent_type="planner").observe(elapsed / 1000)
            emit_observability_event(
                _LOG,
                event="agent.plan.complete",
                category="agent",
                duration_ms=elapsed,
                node_count=len(graph.nodes),
                prompt_version=version_id,
            )
            _LOG.info(f"Generated reasoning graph with {len(graph.nodes)} nodes.")
            return graph, version_id
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            emit_observability_event(
                _LOG,
                event="agent.plan.error",
                category="agent",
                duration_ms=elapsed,
                error=str(e),
            )
            _LOG.error(f"Failed to parse reasoning plan: {e}. Raw: {result_str}")
            graph = ReasoningGraph()
            graph.add_node(ReasoningNode(id="fallback", type=NodeType.REASONING, task=query, dependencies=[]))
            return graph, version_id

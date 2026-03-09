from __future__ import annotations

from typing import Any

from app.agents.agent_state import AgentState
from app.agents.task_graph import TaskNode
from app.llm.base import LLMProvider
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class ReasoningAgent:
    """
    Focused on analysis, summarization, and logical reasoning.
    """

    PROMPT_KEY = "reasoning_agent"

    DEFAULT_PROMPT = """You are a Reasoning Agent. Your task is to analyze/summarize: {task}
Information available: {context}
Provide a clear, logical, and concise result based strictly on the context."""

    def __init__(self, llm: LLMProvider, prompt_manager: Any = None):
        self.llm = llm
        self.prompt_manager = prompt_manager
        if self.prompt_manager:
            self.prompt_manager.initialize_prompt(self.PROMPT_KEY, self.DEFAULT_PROMPT)

    async def execute(self, node: TaskNode, state: AgentState) -> tuple[str, str]:
        _LOG.info(f"ReasoningAgent executing task: {node.task}")
        context = state.get_context_for_agent()

        version_id = "static"
        if self.prompt_manager:
            template, version_id = self.prompt_manager.get_prompt_with_id(self.PROMPT_KEY)
            full_prompt = template.format(task=node.task, context=context)
        else:
            full_prompt = self.DEFAULT_PROMPT.format(task=node.task, context=context)

        result = await self.llm.ask(full_prompt, system_prompt="You are an expert analytical assistant.")
        return result, version_id

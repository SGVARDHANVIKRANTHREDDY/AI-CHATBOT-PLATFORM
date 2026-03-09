from __future__ import annotations

import json
from typing import Any

from app.agents.agent_state import AgentState
from app.llm.base import LLMProvider
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class CriticAgent:
    """
    Evaluates final responses for logical consistency, completeness, and hallucinations.
    """

    CRITIC_PROMPT_KEY = "critic_agent"

    DEFAULT_PROMPT = """You are a Critic Agent. Evaluate the following draft response based on the execution context.
Draft Response: {response}
Execution Trace: {trace}

Check for:
1. Logical correctness.
2. Hallucinations (facts not in context).
3. Completeness.

Output a JSON with:
- score: 0.0 to 1.0
- feedback: str
- needs_revision: bool
- corrected_response: str (optional)
"""

    def __init__(self, llm: LLMProvider, prompt_manager: Any = None):
        self.llm = llm
        self.prompt_manager = prompt_manager
        if self.prompt_manager:
            self.prompt_manager.initialize_prompt(self.CRITIC_PROMPT_KEY, self.DEFAULT_PROMPT)

    async def evaluate(self, response: str, state: AgentState) -> tuple[dict[str, Any], str]:
        """Performs a self-critical evaluation of the final result."""
        _LOG.info("CriticAgent evaluating final response.")

        trace = "\n".join(state.reasoning_trace)

        version_id = "static"
        if self.prompt_manager:
            template, version_id = self.prompt_manager.get_prompt_with_id(self.CRITIC_PROMPT_KEY)
            prompt = template.format(response=response, trace=trace)
        else:
            prompt = self.DEFAULT_PROMPT.format(response=response, trace=trace)

        result_str = await self.llm.ask(prompt, system_prompt="You are a strict quality control auditor.")

        try:
            # Simple extraction
            import re

            match = re.search(r"\{.*\}", result_str, re.DOTALL)
            parsed = json.loads(match.group(0)) if match else {"score": 0.5, "feedback": "Parsing failed"}
            return parsed, version_id
        except Exception:
            return {"score": 0.5, "feedback": "Evaluation failed to parse"}, version_id

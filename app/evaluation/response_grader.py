from __future__ import annotations
import json
import re
from typing import Dict, Any
from app.llm.base import LLMProvider
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

class ResponseGrader:
    """
    Evaluates the final agent response for correctness, completeness, and quality.
    Uses LLM-as-a-judge for automated scoring.
    """
    
    GRADER_PROMPT = """You are an independent AI Response Evaluator. 
Evaluate the following interaction based on:
1. Correctness: Does it solve the user's request accurately?
2. Completeness: Are all parts of the request addressed?
3. Reasoning: Is the logic sound and explainable?

User Query: {query}
Agent Response: {response}

Output a JSON with:
- score: 0.0 to 1.0
- logical_consistency: bool
- hallucinations_detected: bool
- feedback: str
"""

    def __init__(self, llm: LLMProvider):
        self.llm = llm

    async def grade(self, query: str, response: str) -> Dict[str, Any]:
        """Automated grading of an interaction."""
        _LOG.info("Grading interaction quality.")
        
        prompt = self.GRADER_PROMPT.format(query=query, response=response)
        result_str = await self.llm.ask(prompt, system_prompt="You are a meticulous judge of AI quality.")
        
        try:
            match = re.search(r"\{.*\}", result_str, re.DOTALL)
            return json.loads(match.group(0)) if match else {"score": 0.0, "feedback": "Grader parsing failed"}
        except Exception:
            return {"score": 0.0, "feedback": "Grader execution failed"}

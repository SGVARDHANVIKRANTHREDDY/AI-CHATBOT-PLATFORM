from __future__ import annotations
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime

class AgentState(BaseModel):
    """
    Maintains the state of a multi-agent execution session.
    Tracks task progress, results, and reasoning history.
    """
    session_id: str
    task_graph: Dict[str, Any] = Field(default_factory=dict)
    completed_steps: List[str] = Field(default_factory=list)
    intermediate_results: Dict[str, Any] = Field(default_factory=dict)
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    reasoning_trace: List[str] = Field(default_factory=list)
    start_time: datetime = Field(default_factory=datetime.now)
    max_steps: int = 8
    current_step_count: int = 0
    max_tool_calls: int = 10
    tool_call_count: int = 0

    def add_result(self, step_id: str, result: Any):
        self.intermediate_results[step_id] = result
        self.completed_steps.append(step_id)
        self.current_step_count += 1

    def increment_tool_count(self):
        self.tool_call_count += 1

    def is_complete(self) -> bool:
        """
        Check if execution should stop.
        Stop if:
        1. All terminal nodes in task graph are reached (not implemented here)
        2. current_step_count >= max_steps
        3. tool_call_count >= max_tool_calls
        """
        return (
            self.current_step_count >= self.max_steps or 
            self.tool_call_count >= self.max_tool_calls
        )

    def add_trace(self, message: str):
        self.reasoning_trace.append(f"[{datetime.now().isoformat()}] {message}")

    def get_context_for_agent(self) -> str:
        """Returns a summarized view of the state for agent consumption."""
        summary = ["Current Execution State:"]
        for step_id, res in self.intermediate_results.items():
            summary.append(f"- Step {step_id} Result: {str(res)[:200]}...")
        return "\n".join(summary)

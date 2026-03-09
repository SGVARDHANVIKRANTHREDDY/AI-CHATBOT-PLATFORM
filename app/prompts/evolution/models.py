from __future__ import annotations
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class PromptPerformance(BaseModel):
    """Metrics for a specific prompt version."""
    avg_score: float = 0.0
    hit_count: int = 0
    failure_rate: float = 0.0
    last_evaluated: datetime = Field(default_factory=datetime.now)

class PromptVersion(BaseModel):
    """A specific iteration of a prompt template."""
    version_id: str
    template: str
    created_at: datetime = Field(default_factory=datetime.now)
    performance: PromptPerformance = Field(default_factory=PromptPerformance)
    is_active: bool = False
    parent_version_id: Optional[str] = None
    mutation_feedback: Optional[str] = None  # LLM feedback that led to this mutation

class PromptEvolution(BaseModel):
    """The evolution history of a specific system component's prompt."""
    prompt_key: str  # e.g., "planner", "research", "critic"
    versions: Dict[str, PromptVersion] = {}
    active_version_id: str
    baseline_version_id: str
    candidate_version_id: Optional[str] = None
    
    def get_active(self) -> PromptVersion:
        return self.versions[self.active_version_id]
        
    def get_baseline(self) -> PromptVersion:
        return self.versions[self.baseline_version_id]

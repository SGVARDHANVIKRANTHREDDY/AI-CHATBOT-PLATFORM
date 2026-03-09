from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from app.reasoning_graph.models import ReasoningGraph, ReasoningNode, NodeStatus
from app.agents.agent_state import AgentState
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

# ─── Swarm Configuration Limits ───────────────────────────────────
MAX_SWARM_AGENTS = 5
MAX_PARALLEL_TASKS = 10

@dataclass
class SwarmResult:
    """Aggregated result from a swarm execution."""
    agent_results: Dict[str, Any] = field(default_factory=dict)
    merged_answer: str = ""
    total_agents_used: int = 0
    execution_time_ms: float = 0.0


class ExecutionStrategy(ABC):
    """
    Abstract base for execution strategies.
    Sits between TaskGraph and execution, making the system extensible.
    
    TaskGraph
       ↓
    ExecutionStrategy
       ├── SequentialExecution
       └── SwarmExecution
    """
    
    @abstractmethod
    async def execute(
        self, 
        graph: ReasoningGraph, 
        state: AgentState,
        executor_fn: Any
    ) -> List[Any]:
        """Execute graph nodes using a specific strategy."""
        raise NotImplementedError


class SequentialExecution(ExecutionStrategy):
    """Executes graph nodes one at a time, in dependency order."""
    
    async def execute(self, graph: ReasoningGraph, state: AgentState, executor_fn: Any) -> List[Any]:
        _LOG.info("Using SequentialExecution strategy")
        all_versions = []
        
        while not graph.is_complete():
            ready = graph.get_ready_nodes()
            if not ready:
                break
            
            for node in ready:
                result = await executor_fn(node, graph, state)
                all_versions.append(result)
                
        return all_versions


class SwarmExecution(ExecutionStrategy):
    """
    Spawns multiple parallel agents using asyncio.gather.
    Respects MAX_SWARM_AGENTS and MAX_PARALLEL_TASKS limits.
    """
    
    def __init__(self, max_agents: int = MAX_SWARM_AGENTS, max_parallel: int = MAX_PARALLEL_TASKS):
        self.max_agents = max_agents
        self.max_parallel = max_parallel
        self._active_agents = 0

    async def execute(self, graph: ReasoningGraph, state: AgentState, executor_fn: Any) -> List[Any]:
        _LOG.info(f"Using SwarmExecution strategy (max_agents={self.max_agents}, max_parallel={self.max_parallel})")
        all_versions = []
        
        while not graph.is_complete():
            ready = graph.get_ready_nodes()
            if not ready:
                break
            
            # Enforce swarm limits
            batch_size = min(len(ready), self.max_parallel, self.max_agents - self._active_agents)
            if batch_size <= 0:
                _LOG.warning("Swarm agent limit reached. Falling back to sequential.")
                batch_size = 1
                
            batch = ready[:batch_size]
            self._active_agents += len(batch)
            
            _LOG.info(f"Swarm dispatching {len(batch)} agents in parallel (active: {self._active_agents})")
            
            # Execute batch concurrently
            tasks = [executor_fn(node, graph, state) for node in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, Exception):
                    _LOG.error(f"Swarm agent failed: {result}")
                else:
                    all_versions.append(result)
            
            self._active_agents -= len(batch)
                
        return all_versions


class SwarmMerger:
    """Merges results from multiple swarm agents into a coherent answer."""
    
    def __init__(self, llm: Any):
        self.llm = llm

    async def merge(self, results: Dict[str, str], original_query: str) -> str:
        """Uses an LLM to synthesize multiple agent results into a single answer."""
        if not results:
            return "No results available."
        
        if len(results) == 1:
            return list(results.values())[0]
        
        results_text = "\n".join([f"Agent '{k}': {v}" for k, v in results.items()])
        merge_prompt = f"""Multiple agents worked on the following query: '{original_query}'

Their individual results:
{results_text}

Provide a single, coherent, comprehensive answer that synthesizes all the agent outputs. 
Remove redundancy and resolve any conflicts."""
        
        merged = await self.llm.ask(merge_prompt, system_prompt="You are a synthesis agent that merges multiple perspectives.")
        return merged

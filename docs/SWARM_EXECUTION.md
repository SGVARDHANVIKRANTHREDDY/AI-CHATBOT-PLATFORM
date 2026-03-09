# Swarm Execution

> Complete documentation of the swarm execution system: parallel agent orchestration, execution strategies, result merging, and concurrency controls.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Execution Strategies](#execution-strategies)
  - [Sequential Execution](#sequential-execution)
  - [Swarm Execution](#swarm-execution)
- [Swarm Merger](#swarm-merger)
- [Concurrency Limits](#concurrency-limits)
- [Integration with Reasoning Graph](#integration-with-reasoning-graph)
- [Configuration](#configuration)
- [Failure Modes](#failure-modes)

---

## Overview

The swarm execution system enables parallel agent execution for independent reasoning graph nodes. When the `ReasoningGraphEngine` identifies nodes with no unresolved dependencies, it can dispatch them simultaneously using the swarm strategy.

**Key design decisions:**
- Strategy pattern for pluggable execution modes
- `asyncio.gather()` for concurrent agent execution
- Hard limits on parallel agents to prevent resource exhaustion
- LLM-based result merging for multi-agent outputs
- `return_exceptions=True` for partial failure tolerance

---

## Architecture

```
ReasoningGraphEngine
     │
     ▼
┌─────────────────────────────────┐
│ ExecutionStrategy (abstract)     │
├─────────────────────────────────┤
│ • SequentialExecution           │
│   └── One node at a time        │
│                                 │
│ • SwarmExecution                │
│   └── asyncio.gather(nodes)     │
│   └── Up to MAX_SWARM_AGENTS    │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│ SwarmMerger                     │
│ LLM-based synthesis of          │
│ multi-agent results              │
└─────────────────────────────────┘
```

---

## Execution Strategies

### Sequential Execution

**Location:** `app/swarm/execution.py`

```python
class SequentialExecution(ExecutionStrategy):
    """Execute nodes one at a time in dependency order."""

    async def execute(self, graph: ReasoningGraph, state: AgentState, executor_fn) -> dict:
        results = {}

        while not graph.is_complete():
            ready_nodes = graph.get_ready_nodes()

            for node in ready_nodes:
                node.status = NodeStatus.RUNNING
                try:
                    result = await executor_fn(node, state)
                    node.status = NodeStatus.COMPLETED
                    node.result = result
                    results[node.id] = result
                    state.add_result(node.id, result)
                except Exception as e:
                    node.status = NodeStatus.FAILED
                    node.result = str(e)
                    results[node.id] = f"Error: {e}"

        return results
```

**When to use:**
- Simple queries (single reasoning step)
- Resource-constrained environments
- When ordering matters beyond DAG dependencies
- Debugging and testing

### Swarm Execution

```python
MAX_SWARM_AGENTS = 5
MAX_PARALLEL_TASKS = 10

class SwarmExecution(ExecutionStrategy):
    """Execute independent nodes in parallel using asyncio.gather."""

    async def execute(self, graph: ReasoningGraph, state: AgentState, executor_fn) -> dict:
        results = {}

        while not graph.is_complete():
            ready_nodes = graph.get_ready_nodes()

            # Cap parallel execution
            batch = ready_nodes[:MAX_SWARM_AGENTS]

            # Mark all as running
            for node in batch:
                node.status = NodeStatus.RUNNING

            # Execute in parallel
            tasks = [executor_fn(node, state) for node in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for node, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    node.status = NodeStatus.FAILED
                    node.result = str(result)
                    results[node.id] = f"Error: {result}"
                else:
                    node.status = NodeStatus.COMPLETED
                    node.result = result
                    results[node.id] = result
                    state.add_result(node.id, result)

        return results
```

**Concurrency model:**
- Each "wave" processes up to `MAX_SWARM_AGENTS` nodes
- Within each wave, nodes execute simultaneously via `asyncio.gather()`
- Between waves, dependency checks identify the next batch
- Failed nodes are marked but don't block independent nodes

---

## Swarm Merger

**Location:** `app/swarm/execution.py`

When multiple agent results need to be combined into a coherent response, the `SwarmMerger` uses an LLM to synthesize:

```python
class SwarmMerger:
    def __init__(self, llm):
        self.llm = llm

    async def merge(self, results: Dict[str, Any], original_query: str) -> str:
        """Merge multiple agent results into a coherent response."""
        results_text = "\n\n".join(
            f"=== Result from '{node_id}' ===\n{result}"
            for node_id, result in results.items()
            if not str(result).startswith("Error:")
        )

        prompt = f"""You have received results from multiple specialized agents working on this query:
        "{original_query}"

        Agent results:
        {results_text}

        Synthesize these results into a single, coherent, comprehensive response.
        Resolve any conflicts by preferring the most detailed and well-supported information.
        Do not mention the individual agents or that results were merged."""

        return await self.llm.ask(prompt, system="You are an expert synthesizer.")
```

**Merge strategy:**
1. Filter out failed results (those starting with "Error:")
2. Format all successful results with source attribution
3. Ask the LLM to synthesize into a single coherent answer
4. Conflict resolution: prefer more detailed, better-supported information
5. The merged result hides the multi-agent architecture from the user

---

## Concurrency Limits

### System-Level Limits

| Limiter | Location | Default | Scope |
|---------|----------|---------|-------|
| `MAX_SWARM_AGENTS` | `app/swarm/execution.py` | 5 | Per-execution parallel agents |
| `MAX_PARALLEL_TASKS` | `app/swarm/execution.py` | 10 | Per-execution parallel tasks |
| `AgentExecutionLimiter` | `app/reliability/load_guard.py` | 20 | System-wide concurrent agents |
| `SwarmThrottle` | `app/reliability/load_guard.py` | Dynamic | Pressure-based parallelism |

### SwarmThrottle (Pressure-Based)

```python
class SwarmThrottle:
    """Dynamically adjust parallelism based on system pressure."""

    def __init__(self, max_parallel: int = 10):
        self.max_parallel = max_parallel
        self.current_load = 0

    def get_allowed_parallelism(self) -> int:
        """Return the number of agents that can run in parallel."""
        if self.current_load > 0.8 * self.max_parallel:
            return 2  # High pressure: limit to 2
        elif self.current_load > 0.5 * self.max_parallel:
            return 5  # Medium pressure: limit to 5
        else:
            return self.max_parallel  # Low pressure: full parallelism
```

### AgentExecutionLimiter

```python
class AgentExecutionLimiter:
    """System-wide semaphore for concurrent agent execution."""

    def __init__(self, max_concurrent: int = 20):
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def acquire(self):
        await self.semaphore.acquire()

    def release(self):
        self.semaphore.release()
```

---

## Integration with Reasoning Graph

The swarm execution system is invoked by the `ReasoningGraphEngine`, not directly by the orchestrator:

```
ChatOrchestrator
  └── PlannerAgent.plan(query)
        └── Returns ReasoningGraph (DAG of nodes)
              └── ReasoningGraphEngine.execute(graph)
                    │
                    ├── graph.get_ready_nodes()
                    │     └── Are there multiple ready nodes?
                    │           ├── YES → SwarmExecution.execute(batch)
                    │           └── NO  → SequentialExecution.execute(node)
                    │
                    └── Loop until graph.is_complete()
```

The strategy selection happens at the engine level based on node dependencies, not at the orchestrator level.

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_SWARM_AGENTS` | 5 | Max parallel agents per wave |
| `MAX_PARALLEL_TASKS` | 10 | Max parallel tasks per execution |
| `AgentExecutionLimiter` | 20 | System-wide concurrent agents |
| `SwarmThrottle` | Dynamic | Pressure-based parallelism cap |
| Strategy selection | Automatic | Engine chooses based on DAG structure |

---

## Failure Modes

| Failure | Impact | Recovery |
|---------|--------|----------|
| Single agent failure | One node fails in wave | Other nodes unaffected; failed node marked |
| All agents in wave fail | Entire wave fails | Execution continues to next wave (independent nodes) |
| Merge LLM failure | Cannot synthesize results | Return concatenated results as fallback |
| Semaphore exhaustion | New agents queued | Agents wait on semaphore, timeout from watchdog |
| asyncio.gather timeout | Wave takes too long | AgentWatchdog kills the execution |
| Memory overflow | Too many parallel results | MAX_SWARM_AGENTS prevents unbounded growth |

---

## Conflict Resolution Strategy

When multiple agents produce contradictory answers, the `SwarmMerger` resolves conflicts using LLM-based synthesis:

```
Agent A: "Python 3.12 is the latest version"
Agent B: "Python 3.13 is the latest version"
Agent C: "Python was created in 1991"
                    │
                    ▼
         ┌─────────────────────┐
         │    SwarmMerger      │
         │                     │
         │ 1. Filter errors    │  ← Remove any result starting with "Error:"
         │ 2. Collect answers  │  ← Format all successful results
         │ 3. LLM synthesis    │  ← Ask LLM to merge, preferring
         │                     │     "most detailed and well-supported"
         │ 4. Hide agents      │  ← Final answer doesn't mention agents
         └─────────┬───────────┘
                    │
                    ▼
         "Python 3.13 is the latest stable release.
          Python was originally created in 1991."
```

### Resolution Rules

1. **Failed results excluded** — Results starting with `"Error:"` are dropped before merging
2. **LLM judgment** — The merger LLM uses its own reasoning to resolve contradictions, preferring "detailed and well-supported" information
3. **No voting** — There is no count-based voting or confidence scoring between agents
4. **Transparent to users** — The merged result never mentions the multi-agent process

---

## Partial Failure Scenarios

### What happens when N out of M agents fail?

```
4/4 succeed → SwarmMerger synthesizes all 4 → Full answer
3/4 succeed → SwarmMerger merges 3 results  → Mostly complete, no error shown
1/4 succeed → Single result returned         → May be incomplete
0/4 succeed → No results to merge            → CriticAgent downgrades confidence
```

### Timeout behavior

Each agent execution is bounded by the `AgentWatchdog`:
- **Per-agent timeout**: `MAX_RUNTIME_SECONDS` (default: 30s)
- **Per-wave**: All agents in a wave share the watchdog's wall-clock limit
- **On timeout**: `asyncio.gather(return_exceptions=True)` catches `TimeoutError` and marks the node as `FAILED`

---

## When to Use Swarm vs Sequential

```
Is the query simple? (e.g., "What is Python?")
  └── YES → Sequential (single agent, no overhead)

Does the planner create multiple independent DAG nodes?
  └── NO  → Sequential (linear chain)
  └── YES → Swarm

Are system resources constrained?
  └── YES → Sequential (save resources)
  └── NO  → Swarm (maximize parallelism)
```

### Trade-off Comparison

| Dimension | Sequential | Swarm |
|-----------|-----------|-------|
| **Latency** | Sum of all node times | Max of parallel node times |
| **LLM API calls** | N calls | N calls + 1 merge call |
| **Cost** | Lower | Higher (merge overhead) |
| **Reliability** | Failure stops chain | Partial failures tolerated |
| **Best for** | Simple, linear queries | Complex multi-faceted queries |

### Example

Query: "Compare Python and Rust for web dev, covering performance, ecosystem, and learning curve"

```
PlannerAgent creates:
  Node 1: "Research Python"     ─┐
  Node 2: "Research Rust"        ├── Independent → Swarm (3s parallel)
  Node 3: "Compare performance"  ─┘
  Node 4: "Synthesize" (depends on 1,2,3)    → Sequential (3s)

Sequential total: ~12s   vs   Swarm total: ~6s — 2× faster
```
    async def execute(self, agent_fn, *args, **kwargs):
        async with self.semaphore:
            return await agent_fn(*args, **kwargs)
```

---

## Integration with Reasoning Graph

The `ReasoningGraphEngine` selects the execution strategy:

```python
class ReasoningGraphEngine:
    def __init__(self, strategy: ExecutionStrategy = None):
        self.strategy = strategy or SwarmExecution()

    async def execute(self, graph: ReasoningGraph, state: AgentState) -> dict:
        return await self.strategy.execute(graph, state, self._execute_node)

    async def _execute_node(self, node: ReasoningNode, state: AgentState):
        """Execute a single node based on its type."""
        if node.type == NodeType.REASONING:
            return await self.reasoning_agent.execute(node, state)
        elif node.type == NodeType.TOOL_CALL:
            return await self.tool_runner.execute(node.task)
        elif node.type == NodeType.MEMORY_LOOKUP:
            return await self.memory_retriever.retrieve_context(node.task)
```

### Execution Example

Given this DAG:

```
      ┌──── mem_lookup ────┐
      │                    │
query ├──── web_search ────┼──── reasoning ──── response
      │                    │
      └──── rag_search ────┘
```

**Sequential:** `mem_lookup → web_search → rag_search → reasoning → response`

**Swarm:**
- Wave 1: `mem_lookup`, `web_search`, `rag_search` (parallel)
- Wave 2: `reasoning` (depends on all three)
- Wave 3: `response` (depends on reasoning)

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_SWARM_AGENTS` | 5 | Max parallel agents per wave |
| `MAX_PARALLEL_TASKS` | 10 | Max parallel tasks per execution |
| `AgentExecutionLimiter` | 20 | System-wide concurrent agents |
| `SwarmThrottle` | Dynamic | Pressure-based parallelism cap |
| Strategy selection | `SwarmExecution` | Default execution strategy |

---

## Failure Modes

| Failure | Impact | Recovery |
|---------|--------|----------|
| Single agent failure | One node fails in wave | Other nodes unaffected; failed node marked |
| All agents in wave fail | Entire wave fails | Execution continues to next wave (independent nodes) |
| Merge LLM failure | Cannot synthesize results | Return concatenated results as fallback |
| Semaphore exhaustion | New agents queued | Agents wait on semaphore, timeout from watchdog |
| asyncio.gather timeout | Wave takes too long | AgentWatchdog kills the execution |
| Memory overflow | Too many parallel results | MAX_SWARM_AGENTS prevents unbounded growth |

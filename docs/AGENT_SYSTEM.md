# Agent System

> Complete documentation of the multi-agent orchestration system, including planning, execution, routing, and self-evaluation.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Agent Types](#agent-types)
  - [Planner Agent](#planner-agent)
  - [Reasoning Agent](#reasoning-agent)
  - [Research Agent](#research-agent)
  - [Coding Agent](#coding-agent)
  - [Critic Agent](#critic-agent)
- [Agent Router](#agent-router)
- [Agent State](#agent-state)
- [Task Graph](#task-graph)
- [Reasoning Graph](#reasoning-graph)
- [Agent Lifecycle](#agent-lifecycle)
- [Execution Strategies](#execution-strategies)
- [Agent Watchdog](#agent-watchdog)
- [Prompt Evolution Integration](#prompt-evolution-integration)
- [Failure Modes](#failure-modes)
- [Configuration](#configuration)

---

## Overview

The agent system implements a **plan-execute-evaluate** pattern:

1. The **PlannerAgent** decomposes a user query into a directed acyclic graph (DAG) of reasoning steps
2. The **ReasoningGraphEngine** executes nodes in dependency order using specialized agents
3. Results are aggregated and passed to the **CriticAgent** for quality evaluation
4. If the critic score is below threshold, the system iterates with corrections

This architecture allows the system to handle complex, multi-step queries that require parallel information gathering, sequential reasoning, and tool interactions.

---

## Architecture

```
User Query
     │
     ▼
┌──────────────┐
│ PlannerAgent │ ──→ ReasoningGraph (DAG)
└──────────────┘
     │
     ▼
┌──────────────────────────────────────────────┐
│         ReasoningGraphEngine                  │
│                                              │
│  ┌─────────┐  ┌─────────┐  ┌─────────────┐  │
│  │ Node 1  │──│ Node 2  │──│   Node 3    │  │
│  │(memory) │  │(search) │  │ (reasoning) │  │
│  └─────────┘  └─────────┘  └─────────────┘  │
│       │            │              │          │
│       ▼            ▼              ▼          │
│  AgentRouter dispatches to registered agents │
└──────────────────────────────────────────────┘
     │
     ▼
┌──────────────┐
│ CriticAgent  │ ──→ Score, Feedback, Corrections
└──────────────┘
     │
     ▼
Final Response (or iterate if score < 0.6)
```

---

## Agent Types

### Planner Agent

**Location:** `app/agents/planner_agent.py`

**Purpose:** Decomposes user queries into a structured DAG of reasoning steps with declared dependencies.

**How it works:**
1. Receives the user's natural language query
2. Sends a structured prompt to the LLM asking for a JSON DAG
3. Parses the LLM response into a `ReasoningGraph` with `ReasoningNode` objects
4. Falls back to a single-node graph if parsing fails

**Prompt structure:**

The planner prompt instructs the LLM to produce:
```json
{
  "nodes": [
    {
      "id": "mem",
      "type": "memory_lookup",
      "task": "latest AI news query history",
      "dependencies": []
    },
    {
      "id": "search",
      "type": "tool_call",
      "task": "web_search(query='latest AI news today')",
      "dependencies": ["mem"]
    },
    {
      "id": "summary",
      "type": "reasoning",
      "task": "Summarize the key trends from search results",
      "dependencies": ["search"]
    }
  ]
}
```

**Node types supported:**

| Type | Description | Execution |
|------|-------------|-----------|
| `reasoning` | Analytical steps or logic | LLM call with context |
| `tool_call` | External actions | Parsed and dispatched to ToolRunner |
| `memory_lookup` | Context from memory | Dispatched to MemoryRetriever |

**Observability:**
- Emits `agent.plan.start` and `agent.plan.complete` events
- Records `AGENT_EXECUTION_TIME` (planner label)
- Traced via `@traced("agent.plan")` decorator

---

### Reasoning Agent

**Location:** `app/agents/reasoning_agent.py`

**Purpose:** Analysis, summarization, and logical reasoning over context.

**How it works:**
1. Receives a `TaskNode` and current `AgentState`
2. Extracts the execution context (previous step results)
3. Generates a prompt with the task and context
4. Calls the LLM with an "expert analytical assistant" system prompt
5. Returns the result and prompt version ID

**Prompt template:**
```
You are a Reasoning Agent. Your task is to analyze/summarize: {task}
Information available: {context}
Provide a clear, logical, and concise result based strictly on the context.
```

**Use cases:** Summarization, comparison, trend analysis, logical deduction.

---

### Research Agent

**Location:** `app/agents/research_agent.py`

**Purpose:** Information gathering with tool access (web search, RAG).

**How it works:**
1. Receives a `TaskNode` and current `AgentState`
2. Constructs a research prompt from the task and context
3. Enters an **internal tool loop** (max 3 turns):
   - Calls LLM with the prompt
   - Checks output for `<tool_call: name(args)>` patterns
   - If tool call detected: executes tool, appends observation, re-prompts
   - If no tool call: returns the result
4. Falls back with error if max turns exceeded

**Internal tool loop:**
```
Turn 1: LLM outputs "<tool_call: web_search(query="AI news")>"
         → Execute web_search → Get results
Turn 2: LLM outputs analysis based on search results
         → No tool call → Return result
```

**Tool call pattern:** `<tool_call: tool_name(arg1="value", arg2=123)>`

---

### Coding Agent

**Location:** `app/agents/coding_agent.py`

**Purpose:** Code generation and execution.

**How it works:**
1. Same internal tool loop as ResearchAgent (max 3 turns)
2. Uses a code-focused system prompt: "You are a senior software engineer"
3. Can invoke `code_execution` tool for running code
4. Detects tool calls using `StreamingToolRunner.TOOL_PATTERN`

**Prompt template:**
```
You are a Coding Agent. Your task is to write/execute code for: {task}
Context: {context}
If outputting code, prefix with ```python.
If you need to execute code, output <tool_call: code_execution(code="...")>.
```

---

### Critic Agent

**Location:** `app/agents/critic_agent.py`

**Purpose:** Self-evaluation of responses for quality, completeness, and hallucination detection.

**How it works:**
1. Receives the draft response and full execution trace from `AgentState`
2. Constructs an evaluation prompt
3. Calls LLM with "strict quality control auditor" system prompt
4. Parses JSON output with scoring

**Output schema:**
```json
{
  "score": 0.85,
  "feedback": "Response is well-grounded with clear reasoning",
  "needs_revision": false,
  "corrected_response": null
}
```

**Evaluation criteria:**
1. **Logical correctness** — Are conclusions supported by premises?
2. **Hallucinations** — Are facts grounded in the provided context?
3. **Completeness** — Does the response address all aspects of the query?

**Score interpretation:**

| Score | Interpretation | Action |
|-------|---------------|--------|
| ≥ 0.8 | High quality | Accept response |
| 0.6–0.8 | Acceptable | Accept with logging |
| < 0.6 | Needs revision | Re-execute with corrections |

---

## Agent Router

**Location:** `app/agents/agent_router.py`

The `AgentRouter` maintains a registry of agent implementations and dispatches task nodes to the appropriate agent.

```python
class AgentRouter:
    def register_agent(self, name: str, agent_fn)
    async def route_and_execute(self, node: TaskNode, state: AgentState) -> Tuple[Any, str]
```

**Registration:**
```python
router = AgentRouter()
router.register_agent("research_agent", research_agent.execute)
router.register_agent("coding_agent", coding_agent.execute)
router.register_agent("reasoning_agent", reasoning_agent.execute)
```

**Execution flow:**
1. Looks up agent function by `node.agent` name
2. Logs trace entry in `AgentState`
3. Sets node status to `"running"`
4. Calls agent function with `(node, state)`
5. On success: status → `"completed"`, records result
6. On failure: status → `"failed"`, records error

---

## Agent State

**Location:** `app/agents/agent_state.py`

`AgentState` is a Pydantic model that tracks the entire multi-agent execution session:

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | str | Session identifier |
| `task_graph` | dict | Serialized task graph |
| `completed_steps` | list[str] | IDs of completed steps |
| `intermediate_results` | dict[str, Any] | Results keyed by step ID |
| `tool_calls` | list[dict] | Record of all tool invocations |
| `reasoning_trace` | list[str] | Timestamped trace log |
| `start_time` | datetime | Execution start |
| `max_steps` | int | Step limit (default: 8) |
| `max_tool_calls` | int | Tool call limit (default: 10) |
| `current_step_count` | int | Current step counter |
| `tool_call_count` | int | Current tool call counter |

**Key methods:**

| Method | Description |
|--------|-------------|
| `add_result(step_id, result)` | Record completion of a step |
| `increment_tool_count()` | Increment tool call counter |
| `is_complete()` | Check if step or tool limits reached |
| `add_trace(message)` | Add timestamped trace entry |
| `get_context_for_agent()` | Summarize results for next agent |

---

## Task Graph

**Location:** `app/agents/task_graph.py`

`TaskGraph` is a DAG data structure for legacy agent orchestration (parallel to `ReasoningGraph` for the new system):

```python
class TaskNode(BaseModel):
    id: str
    agent: str        # "research_agent", "coding_agent", etc.
    task: str
    dependencies: List[str]
    result: Optional[Any]
    status: str       # "pending", "running", "completed", "failed"

class TaskGraph(BaseModel):
    nodes: Dict[str, TaskNode]

    def get_ready_tasks(self) -> List[TaskNode]
    def mark_completed(self, node_id: str, result: Any)
    def is_complete(self) -> bool
```

**Dependency resolution:** `get_ready_tasks()` returns all nodes whose dependencies are all `"completed"`.

---

## Reasoning Graph

**Location:** `app/reasoning_graph/models.py`

The `ReasoningGraph` is the production DAG used by the `ReasoningGraphEngine`:

```python
class ReasoningNode(BaseModel):
    id: str
    type: NodeType         # REASONING, TOOL_CALL, MEMORY_LOOKUP
    task: str
    dependencies: List[str]
    result: Optional[Any]
    status: NodeStatus     # PENDING, RUNNING, COMPLETED, FAILED
    depth: int
    metadata: Dict[str, Any]
```

**Safety limits:**
- `MAX_NODES = 50` — Prevents runaway planning
- `MAX_DEPTH = 10` — Prevents infinitely deep dependency chains
- Depth is auto-calculated from dependencies when adding nodes

---

## Agent Lifecycle

### Complete Execution Flow

```
1. ChatOrchestrator.generate_answer(query)
   │
2. PlannerAgent.plan(query) → ReasoningGraph
   │
3. ReasoningGraphEngine.execute(graph, state)
   │
   ├── For each batch of ready nodes:
   │   ├── Strategy: Sequential → one at a time
   │   └── Strategy: Swarm → asyncio.gather (max 5 parallel)
   │       │
   │       ├── REASONING node → LLM call with task + context
   │       ├── TOOL_CALL node → Parse tool name/args, execute
   │       └── MEMORY_LOOKUP node → MemoryRetriever.retrieve_context()
   │
   ├── After all nodes complete:
   │   └── Aggregate results
   │
4. CriticAgent.evaluate(aggregated_result, state)
   │
   ├── Score ≥ 0.6 → Accept
   └── Score < 0.6 → Iterate (up to max_iterations)
   │
5. ResponseValidator.validate(final_response)
   │
6. Return to caller
```

---

## Execution Strategies

**Location:** `app/swarm/execution.py`

Two pluggable strategies are available:

### Sequential Execution

Executes nodes one at a time in dependency order. Suitable for simple queries or when resource conservation is needed.

```python
class SequentialExecution(ExecutionStrategy):
    async def execute(self, graph, state, executor_fn):
        while not graph.is_complete():
            ready = graph.get_ready_nodes()
            for node in ready:
                result = await executor_fn(node, state)
                graph.nodes[node.id].status = NodeStatus.COMPLETED
                graph.nodes[node.id].result = result
```

### Swarm Execution

Spawns agents in parallel using `asyncio.gather()`:

```python
class SwarmExecution(ExecutionStrategy):
    async def execute(self, graph, state, executor_fn):
        while not graph.is_complete():
            ready = graph.get_ready_nodes()[:MAX_SWARM_AGENTS]
            tasks = [executor_fn(node, state) for node in ready]
            results = await asyncio.gather(*tasks, return_exceptions=True)
```

**Limits:**
- `MAX_SWARM_AGENTS = 5` — Maximum parallel agents
- `MAX_PARALLEL_TASKS = 10` — Maximum parallel tasks per batch

See [SWARM_EXECUTION.md](SWARM_EXECUTION.md) for detailed swarm documentation.

---

## Agent Watchdog

**Location:** `app/orchestrator/watchdog.py`

The watchdog provides hard limits on agent execution to prevent runaway loops.

**Budget limits:**

| Limit | Default | Description |
|-------|---------|-------------|
| `MAX_AGENT_ITERATIONS` | 10 | Maximum DAG iteration cycles |
| `MAX_TOOL_CALLS` | 20 | Maximum tool invocations |
| `MAX_RUNTIME_SECONDS` | 30 | Wall-clock execution limit |

**Enforcement mechanism:**
1. `AgentWatchdog.register(session_id)` creates an `AgentExecutionContext`
2. Background monitor polls every 1 second
3. If any limit is exceeded, the monitor calls `context.cancel()` which calls `asyncio.Task.cancel()`
4. The execution raises `AgentBudgetExceeded` with detailed context
5. Partial results are preserved for recovery

**Termination reasons:**

| Reason | Trigger |
|--------|---------|
| `COMPLETED` | Normal completion |
| `ITERATION_LIMIT` | Exceeded max iterations |
| `TOOL_CALL_LIMIT` | Exceeded max tool calls |
| `RUNTIME_LIMIT` | Exceeded wall-clock time |
| `CANCELLED` | Manual cancellation |
| `ERROR` | Unhandled exception |

---

## Prompt Evolution Integration

Each agent integrates with the prompt evolution system:

1. Each agent has a `PROMPT_KEY` (e.g., `"planner_agent"`, `"critic_agent"`)
2. On initialization, agents call `prompt_manager.initialize_prompt(key, default)`
3. On execution, agents call `prompt_manager.get_prompt_with_id(key)` which:
   - Returns the active prompt template
   - With 20% probability, returns a candidate (A/B testing)
   - Returns the `version_id` for feedback tracking
4. After execution, the orchestrator calls `prompt_manager.record_feedback(key, version_id, score)`
5. The manager promotes or rejects candidates based on accumulated scores

---

## Failure Modes

### Planning Failures

| Failure | Cause | Recovery |
|---------|-------|----------|
| JSON parse error | LLM returns non-JSON | Single-node fallback graph |
| Empty graph | LLM returns no nodes | Single-node fallback |
| Circular dependencies | Invalid DAG structure | Validated by `add_node()` depth calculation |
| Excessive nodes | LLM generates too many | `MAX_NODES=50` enforced |

### Execution Failures

| Failure | Cause | Recovery |
|---------|-------|----------|
| Agent function error | Exception in agent logic | Node marked "failed", execution continues |
| Tool call failure | Tool raises exception | Error recorded in result, continue |
| LLM timeout | Provider unavailable | Circuit breaker + retry policy |
| Budget exceeded | Runaway loop | AgentWatchdog terminates, partial results preserved |

### Evaluation Failures

| Failure | Cause | Recovery |
|---------|-------|----------|
| Critic parse error | Non-JSON evaluation | Default score 0.5 |
| Low score | Quality below threshold | Iterate with corrections (up to limit) |

---

## Configuration

### Agent System Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| Agent iterations | 10 max | Per-execution DAG cycles |
| Tool calls | 20 max | Per-execution tool invocations |
| Runtime | 30s max | Wall-clock execution limit |
| Internal tool turns | 3 max | Per-agent tool loops (Research, Coding) |
| Concurrent agents | 20 max | System-wide agent limit (AgentExecutionLimiter) |
| Swarm agents | 5 max | Per-execution parallel agents |
| Graph nodes | 50 max | Per-planning output |
| Graph depth | 10 max | Maximum dependency chain depth |

### Agent LLM Models

| Agent | Config Key | Default Model |
|-------|-----------|---------------|
| Planner | `HF_MODEL` / `OPENAI_MODEL` | Mistral-7B-Instruct-v0.2 |
| Reasoning | `MODEL_REASONING` | Mistral-7B-Instruct-v0.2 |
| Coding | `MODEL_CODING` | CodeLlama-13b-Instruct-hf |
| Research | `HF_MODEL` / `OPENAI_MODEL` | Mistral-7B-Instruct-v0.2 |
| Critic | `HF_MODEL` / `OPENAI_MODEL` | Mistral-7B-Instruct-v0.2 |

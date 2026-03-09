# Prompt Evolution

> Complete documentation of the prompt versioning, A/B testing, performance tracking, automated mutation, and promotion system.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prompt Version Model](#prompt-version-model)
- [Prompt Evolution Manager](#prompt-evolution-manager)
- [A/B Testing](#ab-testing)
- [Performance Tracking](#performance-tracking)
- [Promotion and Rejection](#promotion-and-rejection)
- [LLM Mutation](#llm-mutation)
- [Integration with Agents](#integration-with-agents)
- [Persistence](#persistence)
- [Configuration](#configuration)
- [Failure Modes](#failure-modes)

---

## Overview

The prompt evolution system enables continuous improvement of agent prompts through automated A/B testing, performance tracking, and LLM-driven mutation. Instead of static prompts, each agent prompt has:

- An **active** version (currently serving most traffic)
- An optional **candidate** version (being tested on 20% of traffic)
- A **baseline** version (reference for comparison)
- A **version history** with performance metrics

---

## Architecture

```
                                    ┌──────────────────┐
                                    │ /data/prompts/   │
                                    │  evolution.json  │
                                    └────────┬─────────┘
                                             │
                                    ┌────────▼─────────┐
                                    │ PromptEvolution  │
                                    │ Manager          │
                                    └────────┬─────────┘
                                             │
                    ┌────────────────────────┼──────────────────────┐
                    │                        │                      │
           ┌────────▼────────┐     ┌────────▼────────┐    ┌───────▼────────┐
           │ Active Prompt   │     │ Candidate Prompt│    │ Baseline       │
           │ (80% traffic)   │     │ (20% traffic)   │    │ (reference)    │
           └─────────────────┘     └─────────────────┘    └────────────────┘
                    │                        │
                    └────────┬───────────────┘
                             │
                    ┌────────▼────────┐
                    │ Performance     │
                    │ Feedback Loop   │
                    └────────┬────────┘
                             │
                 ┌───────────┼───────────┐
                 │                       │
        ┌────────▼───────┐      ┌───────▼────────┐
        │ Promote?       │      │ Reject?        │
        │ (outperforms   │      │ (10% worse     │
        │  after 5 evals)│      │  than active)  │
        └────────────────┘      └────────────────┘
```

---

## Prompt Version Model

**Location:** `app/prompts/evolution/models.py`

```python
class PromptVersion(BaseModel):
    version_id: str          # UUID
    template: str            # The prompt template text
    performance: PromptPerformance

class PromptPerformance(BaseModel):
    total_uses: int = 0
    total_score: float = 0.0
    average_score: float = 0.0

class PromptEvolution(BaseModel):
    versions: Dict[str, PromptVersion]    # version_id → PromptVersion
    active_id: str                         # Currently serving
    baseline_id: Optional[str]             # Reference version
    candidate_id: Optional[str]            # Being tested
```

---

## Prompt Evolution Manager

**Location:** `app/prompts/evolution/manager.py`

### Initialization

```python
class PromptEvolutionManager:
    def __init__(self, persist_dir: str = "data/prompts"):
        self.persist_dir = persist_dir
        self.evolutions: Dict[str, PromptEvolution] = {}
        self._load()

    def initialize_prompt(self, key: str, default_template: str):
        """Initialize a prompt with its default template if not already tracked."""
        if key not in self.evolutions:
            version_id = str(uuid.uuid4())
            version = PromptVersion(
                version_id=version_id,
                template=default_template,
                performance=PromptPerformance()
            )
            self.evolutions[key] = PromptEvolution(
                versions={version_id: version},
                active_id=version_id,
                baseline_id=version_id,
                candidate_id=None,
            )
            self._save()
```

### Prompt Retrieval (with A/B Split)

```python
def get_prompt_with_id(self, key: str) -> Tuple[str, str]:
    """Get the prompt template and version ID (with 20% candidate probability)."""
    evolution = self.evolutions[key]

    # 20% of the time, return candidate if available
    if evolution.candidate_id and random.random() < 0.2:
        version = evolution.versions[evolution.candidate_id]
        return version.template, version.version_id

    # 80% of the time, return active
    version = evolution.versions[evolution.active_id]
    return version.template, version.version_id
```

---

## A/B Testing

### Traffic Split

| Version | Traffic Share | Purpose |
|---------|-------------|---------|
| Active | 80% | Production-serving prompt |
| Candidate | 20% | Experimental prompt under evaluation |

### How Candidates Are Created

Candidates are created through LLM-driven mutation (see [LLM Mutation](#llm-mutation)):

```python
async def create_candidate(self, key: str, feedback: str = ""):
    """Create a new candidate prompt using LLM mutation."""
    evolution = self.evolutions[key]
    active = evolution.versions[evolution.active_id]

    # Use LLM to generate a mutation
    mutated_template = await self._mutate_prompt(active.template, feedback)

    candidate_id = str(uuid.uuid4())
    candidate = PromptVersion(
        version_id=candidate_id,
        template=mutated_template,
        performance=PromptPerformance()
    )

    evolution.versions[candidate_id] = candidate
    evolution.candidate_id = candidate_id
    self._save()
```

---

## Performance Tracking

### Recording Feedback

```python
def record_feedback(self, key: str, version_id: str, score: float):
    """Record performance feedback for a specific prompt version."""
    evolution = self.evolutions[key]
    version = evolution.versions.get(version_id)

    if version:
        version.performance.total_uses += 1
        version.performance.total_score += score
        version.performance.average_score = (
            version.performance.total_score / version.performance.total_uses
        )
        self._save()

        # Check for promotion or rejection
        self._evaluate_candidate(key)
```

### Score Sources

Feedback scores come from:
1. **CriticAgent evaluation** — Score from the critic's quality assessment (0.0–1.0)
2. **ResponseGrader** — Multi-dimensional grading (correctness, completeness, reasoning)

---

## Promotion and Rejection

### Evaluation Logic

```python
def _evaluate_candidate(self, key: str):
    """Check if candidate should be promoted or rejected."""
    evolution = self.evolutions[key]

    if not evolution.candidate_id:
        return

    candidate = evolution.versions[evolution.candidate_id]
    active = evolution.versions[evolution.active_id]

    # Need at least 5 evaluations before deciding
    if candidate.performance.total_uses < 5:
        return

    # Promotion: candidate outperforms active
    if candidate.performance.average_score > active.performance.average_score:
        self._promote(key)

    # Rejection: candidate is 10% worse than active
    elif candidate.performance.average_score < active.performance.average_score * 0.9:
        self._reject(key)
```

### Promotion

```python
def _promote(self, key: str):
    """Promote candidate to active."""
    evolution = self.evolutions[key]

    # Active becomes baseline
    evolution.baseline_id = evolution.active_id

    # Candidate becomes active
    evolution.active_id = evolution.candidate_id

    # Clear candidate slot
    evolution.candidate_id = None

    self._save()
    logger.info(f"Prompt '{key}': candidate promoted to active")
```

### Rejection

```python
def _reject(self, key: str):
    """Reject candidate (keep current active)."""
    evolution = self.evolutions[key]
    evolution.candidate_id = None
    self._save()
    logger.info(f"Prompt '{key}': candidate rejected")
```

### Decision Thresholds

| Decision | Condition | Minimum Evaluations |
|----------|-----------|---------------------|
| **Promote** | `candidate_avg > active_avg` | 5 |
| **Reject** | `candidate_avg < active_avg × 0.9` | 5 |
| **Continue testing** | Neither condition met | N/A |

---

## LLM Mutation

### Mutation Process

```python
async def _mutate_prompt(self, template: str, feedback: str = "") -> str:
    """Use LLM to generate a mutation of the current prompt."""
    mutation_prompt = f"""You are a prompt engineering expert.
    Given this prompt template and feedback, generate an improved version.

    Current template:
    {template}

    Feedback:
    {feedback if feedback else "No specific feedback. Try to improve clarity and effectiveness."}

    Return ONLY the improved prompt template, nothing else."""

    mutated = await self.llm.ask(mutation_prompt)
    return mutated.strip()
```

### Mutation Triggers

Mutations can be triggered:
1. **Automatically** — When the active prompt's average score drops below threshold
2. **Manually** — Via API call with specific feedback
3. **Scheduled** — As part of periodic optimization

---

## Integration with Agents

Each agent integrates with the prompt evolution system:

```python
# In agent initialization
class ReasoningAgent:
    PROMPT_KEY = "reasoning_agent"
    DEFAULT_PROMPT = "You are a Reasoning Agent. Your task is to analyze: {task}..."

    def __init__(self, prompt_manager: PromptEvolutionManager):
        self.prompt_manager = prompt_manager
        self.prompt_manager.initialize_prompt(self.PROMPT_KEY, self.DEFAULT_PROMPT)

    async def execute(self, node, state):
        # Get prompt (with A/B split)
        template, version_id = self.prompt_manager.get_prompt_with_id(self.PROMPT_KEY)

        # Format with task-specific data
        prompt = template.format(task=node.task, context=state.get_context_for_agent())

        # Execute
        result = await self.llm.ask(prompt)

        # Return result with version_id for feedback tracking
        return result, version_id
```

After execution, the orchestrator records feedback:

```python
# In ChatOrchestrator
score = critic_result.get("score", 0.5)
self.prompt_manager.record_feedback(agent.PROMPT_KEY, version_id, score)
```

### Registered Prompt Keys

| Key | Agent | Default Template |
|-----|-------|-----------------|
| `planner_agent` | PlannerAgent | DAG generation prompt |
| `reasoning_agent` | ReasoningAgent | Analysis/summarization prompt |
| `coding_agent` | CodingAgent | Code generation prompt |
| `research_agent` | ResearchAgent | Research with tools prompt |
| `critic_agent` | CriticAgent | Quality evaluation prompt |

---

## Persistence

### File Structure

```
data/prompts/
└── evolution.json
```

### JSON Schema

```json
{
    "reasoning_agent": {
        "versions": {
            "uuid-1": {
                "version_id": "uuid-1",
                "template": "You are a Reasoning Agent...",
                "performance": {
                    "total_uses": 150,
                    "total_score": 127.5,
                    "average_score": 0.85
                }
            },
            "uuid-2": {
                "version_id": "uuid-2",
                "template": "You are an expert analytical assistant...",
                "performance": {
                    "total_uses": 30,
                    "total_score": 26.1,
                    "average_score": 0.87
                }
            }
        },
        "active_id": "uuid-1",
        "baseline_id": "uuid-1",
        "candidate_id": "uuid-2"
    }
}
```

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| Persist directory | `data/prompts` | Directory for evolution.json |
| A/B split ratio | 80/20 | Active/candidate traffic split |
| Minimum evaluations | 5 | Required before promotion/rejection decision |
| Rejection threshold | 10% degradation | Reject if candidate is 10% worse |
| Promotion threshold | Any improvement | Promote if candidate outperforms |

---

## Failure Modes

| Failure | Impact | Recovery |
|---------|--------|----------|
| evolution.json corrupt | Prompt versions lost | Re-initialize from default templates |
| LLM mutation fails | No candidate created | Keep active prompt, retry later |
| No feedback recorded | Cannot evaluate candidate | Candidate persists until evaluations accumulate |
| Candidate keeps losing | Repeated rejections | candidate_id cleared; re-mutation needed |
| Active prompt regresses | Quality degrades | Rollback to baseline_id |
| File write failure | Unsaved state | In-memory state preserved; retry save on next operation |

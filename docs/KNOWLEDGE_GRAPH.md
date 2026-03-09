# Knowledge Graph

> Complete documentation of the entity extraction, graph storage, trust evaluation, and knowledge querying system.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Entity Extractor](#entity-extractor)
- [Graph Store](#graph-store)
- [Source Trust Evaluator](#source-trust-evaluator)
- [Querying the Graph](#querying-the-graph)
- [Integration Points](#integration-points)
- [Data Schemas](#data-schemas)
- [Configuration](#configuration)
- [Failure Modes](#failure-modes)

---

## Overview

The knowledge graph captures structured entity-relationship triples extracted from conversations and ingested documents. Unlike vector memory (which stores semantic embeddings), the graph stores explicit factual relationships with trust scores, enabling precise factual recall and conflict detection.

**Core capabilities:**
- LLM-based entity extraction from natural language
- Count-based relationship filtering (trust threshold)
- Domain-based source trust scoring with content quality heuristics
- Keyword-based querying with trust-weighted results
- JSON file persistence

---

## Architecture

```
Source Content                      Query
      │                               │
      ▼                               ▼
┌──────────────┐              ┌──────────────┐
│ EntityExtract│              │ GraphStore   │
│ or (LLM)     │──triples──▶ │  .query()    │──▶ Trust-weighted results
└──────────────┘              └──────┬───────┘
                                     │
                              ┌──────▼───────┐
                              │ JSON File    │
                              │ Persistence  │
                              └──────────────┘

┌───────────────────┐
│ SourceTrustEval   │ ◀── Evaluates domain reputation
│ uator             │     + content quality for each source
└───────────────────┘
```

---

## Entity Extractor

**Location:** `app/knowledge_graph/entity_extractor.py`

### Extraction Process

```python
class EntityExtractor:
    async def extract(self, text: str) -> List[dict]:
        """Extract entity-relationship triples from text using LLM."""

        prompt = f"""Extract all factual entity relationships from this text.
        Return as JSON array of triples:
        [{{"subject": "...", "predicate": "...", "object": "..."}}]

        Text: {text}"""

        response = await self.llm.ask(prompt, system="You are an expert at extracting factual relationships.")

        triples = self._parse_triples(response)
        return self._filter_by_count(triples)
```

### LLM Prompt

The extraction prompt instructs the LLM to identify:
- **Subject:** The entity being described
- **Predicate:** The relationship type (e.g., "is_a", "works_at", "created_by")
- **Object:** The target entity or value

### Example

Input: `"Python was created by Guido van Rossum at CWI in the Netherlands"`

Output:
```json
[
    {"subject": "Python", "predicate": "created_by", "object": "Guido van Rossum"},
    {"subject": "Guido van Rossum", "predicate": "worked_at", "object": "CWI"},
    {"subject": "CWI", "predicate": "located_in", "object": "Netherlands"}
]
```

### Count-Based Filtering

Triples are filtered based on a count threshold — relationships that appear multiple times across different sources are considered more reliable:

```python
def _filter_by_count(self, triples: List[dict], min_count: int = 1) -> List[dict]:
    """Filter triples by occurrence count (trust threshold)."""
    counts = Counter((t["subject"], t["predicate"], t["object"]) for t in triples)
    return [t for t in triples if counts[(t["subject"], t["predicate"], t["object"])] >= min_count]
```

---

## Graph Store

**Location:** `app/knowledge_graph/graph_store.py`

### Storage Model

The graph is stored as a dictionary of entities, each with a list of relationships:

```python
class GraphStore:
    def __init__(self, path: str = "data/knowledge_graph.json"):
        self.path = path
        self.graph = {}  # {entity: [{"predicate": ..., "object": ..., "trust": ..., "source": ...}]}
```

### Adding Triples

```python
async def add_triple(self, subject: str, predicate: str, obj: str, trust: float = 0.5, source: str = ""):
    """Add a relationship to the graph."""
    if subject not in self.graph:
        self.graph[subject] = []

    relationship = {
        "predicate": predicate,
        "object": obj,
        "trust": trust,
        "source": source,
        "timestamp": datetime.utcnow().isoformat()
    }

    # Avoid duplicates
    if not self._exists(subject, predicate, obj):
        self.graph[subject].append(relationship)
        self._save()
```

### Querying

```python
async def query(self, query_text: str) -> List[dict]:
    """Search the graph using keyword matching."""
    results = []
    keywords = query_text.lower().split()

    for entity, relationships in self.graph.items():
        if any(kw in entity.lower() for kw in keywords):
            for rel in relationships:
                results.append({
                    "subject": entity,
                    "predicate": rel["predicate"],
                    "object": rel["object"],
                    "trust": rel.get("trust", 0.5),
                    "source": rel.get("source", "")
                })

    # Sort by trust score descending
    return sorted(results, key=lambda x: x["trust"], reverse=True)
```

### Persistence

```python
def _save(self):
    """Persist graph to JSON file."""
    with open(self.path, 'w') as f:
        json.dump(self.graph, f, indent=2)

def _load(self):
    """Load graph from JSON file."""
    if os.path.exists(self.path):
        with open(self.path, 'r') as f:
            self.graph = json.load(f)
```

---

## Source Trust Evaluator

**Location:** `app/knowledge_graph/trust.py`

The `SourceTrustEvaluator` assigns trust scores to content sources using a composite of domain reputation and content quality signals.

### Domain Reputation

```python
TRUSTED_DOMAINS = {
    "wikipedia.org": 0.9,
    "docs.python.org": 0.95,
    "arxiv.org": 0.85,
    "github.com": 0.8,
    "stackoverflow.com": 0.75,
    "developer.mozilla.org": 0.9,
    "microsoft.com": 0.85,
    "google.com": 0.8,
    # ... 20+ whitelisted domains with scores
}
```

Unknown domains receive a default score of 0.3.

### Content Quality Heuristics

The evaluator computes five quality signals:

| Signal | Description | Weight |
|--------|-------------|--------|
| **Alpha ratio** | Fraction of alphabetic characters vs. total | 0.2 |
| **Word count** | Number of words (normalized against ideal range) | 0.15 |
| **Trigram uniqueness** | Fraction of unique character trigrams (detects repetition) | 0.25 |
| **Shannon entropy** | Information density of the text | 0.2 |
| **Domain score** | Pre-assigned domain reputation | 0.2 |

### Scoring Algorithm

```python
class SourceTrustEvaluator:
    def evaluate(self, url: str, content: str = "") -> float:
        """Compute composite trust score for a source."""
        domain = self._extract_domain(url)
        domain_score = TRUSTED_DOMAINS.get(domain, 0.3)

        if not content:
            return domain_score

        # Content quality signals
        alpha = self._alpha_ratio(content)        # 0.0 - 1.0
        words = self._word_score(content)          # 0.0 - 1.0
        trigram = self._trigram_uniqueness(content) # 0.0 - 1.0
        entropy = self._shannon_entropy(content)   # 0.0 - 1.0 (normalized)

        # Weighted composite
        quality = (
            alpha * 0.2 +
            words * 0.15 +
            trigram * 0.25 +
            entropy * 0.2 +
            domain_score * 0.2
        )

        return round(quality, 3)
```

### Shannon Entropy

```python
def _shannon_entropy(self, text: str) -> float:
    """Calculate Shannon entropy as a measure of information density."""
    freq = Counter(text)
    length = len(text)
    entropy = -sum((count / length) * math.log2(count / length) for count in freq.values())
    # Normalize to 0-1 range (max entropy for ASCII is ~6.6 bits)
    return min(entropy / 6.6, 1.0)
```

### Trust Scoring: Complete Pseudocode

```
FUNCTION evaluate_trust(url, content):
    domain = extract_domain(url)                    # e.g., "wikipedia.org"
    domain_score = TRUSTED_DOMAINS.get(domain, 0.3) # Default 0.3 for unknown

    IF content is empty:
        RETURN domain_score                         # Domain-only score

    # Signal 1: Alpha ratio (0.0 - 1.0)
    alpha = count_alphabetic(content) / length(content)
    # Text with lots of numbers/symbols scores lower

    # Signal 2: Word count (0.0 - 1.0)
    words = count_words(content)
    IF words < 50:   word_score = words / 50        # Too short → penalized
    ELIF words > 500: word_score = 1.0              # Long enough → full score
    ELSE:            word_score = words / 500        # Proportional

    # Signal 3: Trigram uniqueness (0.0 - 1.0)
    trigrams = all_3char_substrings(content)
    trigram_score = count_unique(trigrams) / count_total(trigrams)
    # Repetitive text (copy-paste spam) scores low

    # Signal 4: Shannon entropy (0.0 - 1.0)
    entropy = shannon_entropy(content) / 6.6
    # Low entropy = repetitive content; high = information-dense

    # Weighted composite
    trust = (alpha   × 0.20)    # 20% weight
          + (words   × 0.15)    # 15% weight
          + (trigram  × 0.25)   # 25% weight (most important)
          + (entropy  × 0.20)   # 20% weight
          + (domain   × 0.20)   # 20% weight

    RETURN round(trust, 3)      # e.g., 0.847
```

**Worked example:**

```
URL: "https://docs.python.org/3/tutorial/classes.html"
Content: "9. Classes — Python 3.12 documentation. Classes provide a means..."
  (2000 words of well-written documentation)

domain_score = 0.95           (docs.python.org is whitelisted)
alpha_ratio  = 0.88           (mostly text, some code)
word_score   = 1.0            (2000 words > 500 threshold)
trigram_uniq  = 0.92          (diverse vocabulary)
entropy      = 0.85           (information-dense)

trust = (0.88 × 0.20) + (1.0 × 0.15) + (0.92 × 0.25) + (0.85 × 0.20) + (0.95 × 0.20)
      = 0.176 + 0.15 + 0.23 + 0.17 + 0.19
      = 0.916

Result: Trust score 0.916 — high trust, will be used by MemoryAuthorityResolver
```

---

## Querying the Graph

### Query Flow

```
User Query: "Who created Python?"
     │
     ▼
GraphStore.query("Who created Python")
     │
     ▼
Keyword match: "python" matches entity "Python"
     │
     ▼
Results:
  ┌────────────────────────────────────────────────────┐
  │ subject: "Python"                                  │
  │ predicate: "created_by"                            │
  │ object: "Guido van Rossum"                         │
  │ trust: 0.92                                        │
  │ source: "wikipedia.org"                            │
  └────────────────────────────────────────────────────┘
```

### Trust Threshold in Memory Authority

When the `MemoryAuthorityResolver` evaluates KG results, only relationships with trust ≥ 0.8 are considered authoritative:

```python
# From MemoryAuthorityResolver
if kg_results and kg_results[0]["trust"] >= 0.8:
    return {"answer": kg_results[0], "source": "knowledge_graph", "confidence": kg_results[0]["trust"]}
```

---

## Integration Points

### 1. Orchestrator Integration

The `ChatOrchestrator` extracts entities from every response and adds them to the graph:

```python
# Step 7 of the orchestrator pipeline
triples = await entity_extractor.extract(response)
for triple in triples:
    trust = trust_evaluator.evaluate(source_url, source_content)
    await graph_store.add_triple(
        triple["subject"], triple["predicate"], triple["object"],
        trust=trust
    )
```

### 2. Memory System Integration

The `UnifiedMemoryController` queries the graph as one of three memory sources:

```python
conversation, vectors, kg_results = await asyncio.gather(
    self.memory.get_recent(session_id),
    self.vector.search(query, top_k=5),
    self.graph.query(query),        # ← Knowledge graph query
)
```

### 3. Knowledge Builder Worker

The `knowledge_builder` Celery worker expands the graph from crawled content:

```python
@celery_app.task
def expand_knowledge_graph():
    """Extract entities from recently crawled content."""
    for doc in recent_documents:
        triples = entity_extractor.extract(doc.content)
        for triple in triples:
            trust = trust_evaluator.evaluate(doc.url, doc.content)
            graph_store.add_triple(triple["subject"], triple["predicate"], triple["object"], trust=trust)
```

---

## Data Schemas

### Triple

```python
{
    "subject": "Python",
    "predicate": "created_by",
    "object": "Guido van Rossum"
}
```

### Stored Relationship

```python
{
    "predicate": "created_by",
    "object": "Guido van Rossum",
    "trust": 0.92,
    "source": "wikipedia.org",
    "timestamp": "2024-01-15T10:30:00.000000"
}
```

### Graph File Structure

```json
{
    "Python": [
        {"predicate": "created_by", "object": "Guido van Rossum", "trust": 0.92, "source": "wikipedia.org"},
        {"predicate": "is_a", "object": "programming language", "trust": 0.95, "source": "docs.python.org"}
    ],
    "Guido van Rossum": [
        {"predicate": "worked_at", "object": "CWI", "trust": 0.85, "source": "wikipedia.org"}
    ]
}
```

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `graph_path` | `data/knowledge_graph.json` | Graph persistence file |
| Trust threshold (authority) | 0.8 | Minimum trust for KG authority |
| Default domain score | 0.3 | Score for unknown domains |
| Quality weight: alpha | 0.2 | Weight for alphabetic ratio |
| Quality weight: words | 0.15 | Weight for word count score |
| Quality weight: trigrams | 0.25 | Weight for trigram uniqueness |
| Quality weight: entropy | 0.2 | Weight for Shannon entropy |
| Quality weight: domain | 0.2 | Weight for domain reputation |

---

## Failure Modes

| Failure | Impact | Recovery |
|---------|--------|----------|
| LLM extraction fails | No new triples extracted | Falls back to empty list; logged |
| JSON parse error | LLM returns non-JSON | Returns empty triple list |
| Graph file corrupted | Graph data lost | Empty graph initialized; rebuild from sources |
| Unknown domain | Low trust score (0.3) | Expected behavior; content quality can compensate |
| Duplicate triples | Redundant data in graph | `_exists()` check prevents exact duplicates |
| Query returns no results | No matching entities | Memory system falls back to vector/conversation sources |

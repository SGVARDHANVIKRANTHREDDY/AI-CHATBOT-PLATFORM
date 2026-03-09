# Memory System

> Complete documentation of the multi-tier memory architecture: session cache, conversation persistence, vector memory, summarization, authority resolution, and unified memory coordination.

---

## Table of Contents

- [Overview](#overview)
- [Memory Architecture](#memory-architecture)
- [Memory Service](#memory-service)
- [Session Cache](#session-cache)
- [Conversation Store](#conversation-store)
- [Summarizer](#summarizer)
- [Memory Authority Resolver](#memory-authority-resolver)
- [Unified Memory Controller](#unified-memory-controller)
- [Vector Memory Integration](#vector-memory-integration)
- [Memory Consistency Model](#memory-consistency-model)
- [Memory Lifecycle](#memory-lifecycle)
- [Data Schemas](#data-schemas)
- [Configuration](#configuration)
- [Failure Modes](#failure-modes)

---

## Overview

The memory system provides persistent, contextual memory across conversations through a multi-tier architecture:

| Tier | Store | Latency | TTL | Purpose |
|------|-------|---------|-----|---------|
| Session (L1) | Redis List | ~1ms | 3600s | Current conversation context |
| Short-term (L2) | Redis Hash | ~1ms | Configurable | Recent interaction metadata |
| Long-term (L3) | PostgreSQL | ~5ms | Permanent | Full conversation history |
| Vector (L4) | FAISS / Qdrant | ~10ms | Permanent | Semantic similarity search |
| Knowledge Graph | JSON file | ~5ms | Permanent | Entity relationships |

---

## Memory Architecture

```
                    ┌───────────────────────────────┐
                    │   UnifiedMemoryController     │
                    │   (Coordinator + Conflict Res) │
                    └─────────────┬─────────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
    ┌─────────▼────────┐  ┌──────▼───────┐  ┌───────▼────────┐
    │  MemoryService   │  │ VectorMemory │  │  GraphStore    │
    │ (Redis + PG)     │  │ (Embeddings) │  │ (Entities)     │
    └───────┬──────────┘  └──────────────┘  └────────────────┘
            │
    ┌───────┼───────┐
    │       │       │
  ┌─▼──┐ ┌─▼──┐ ┌──▼──────────────┐
  │Redis│ │ PG │ │ SessionCache    │
  │     │ │    │ │ (Redis List)    │
  └─────┘ └────┘ └─────────────────┘
```

---

## Memory Service

**Location:** `app/memory/memory_service.py`

The `MemoryService` bridges short-term (Redis) and long-term (PostgreSQL) memory.

### Storage Interface

```python
class MemoryService:
    def __init__(self, settings):
        self.redis = aioredis.from_url(settings.REDIS_URL)
        self.pg_pool = None  # Lazily initialized asyncpg pool

    async def save(self, session_id: str, role: str, content: str)
    async def get_recent(self, session_id: str, limit: int = 20) -> List[dict]
    async def search_history(self, session_id: str, query: str) -> List[dict]
```

### Short-Term Storage (Redis)

```python
# Key format: memory:{session_id}
# Structure: Redis Hash with fields
#   - messages: JSON list of recent messages
#   - metadata: JSON dict with session metadata

async def save(self, session_id, role, content):
    key = f"memory:{session_id}"
    msg = {"role": role, "content": content, "ts": datetime.utcnow().isoformat()}
    await self.redis.rpush(key, json.dumps(msg))
    await self.redis.ltrim(key, -50, -1)  # Keep last 50 messages
```

### Long-Term Storage (PostgreSQL)

```python
# Table: interactions
# Schema:
#   id          SERIAL PRIMARY KEY
#   session_id  TEXT NOT NULL
#   role        TEXT NOT NULL
#   content     TEXT NOT NULL
#   timestamp   TIMESTAMP DEFAULT NOW()

async def _ensure_pg_pool(self):
    if not self.pg_pool:
        self.pg_pool = await asyncpg.create_pool(self.settings.DATABASE_URL, min_size=2, max_size=10)
        await self._create_table()
```

### Dual Write Pattern

Every memory save writes to both tiers:
1. `redis.rpush()` for immediate availability
2. `pg_pool.execute(INSERT)` for permanent storage

The Redis list is trimmed to 50 entries to bound memory usage while PostgreSQL retains the complete history.

---

## Session Cache

**Location:** `app/memory/session_cache.py`

The `SessionCache` provides ultra-low-latency access to the current conversation window.

```python
class SessionCache:
    def __init__(self, redis_url: str, ttl: int = 3600):
        self.redis = aioredis.from_url(redis_url)
        self.ttl = ttl

    async def add_message(self, session_id: str, message: dict):
        key = f"session:{session_id}"
        await self.redis.rpush(key, json.dumps(message))
        await self.redis.expire(key, self.ttl)

    async def get_messages(self, session_id: str, limit: int = 20) -> List[dict]:
        key = f"session:{session_id}"
        raw = await self.redis.lrange(key, -limit, -1)
        return [json.loads(m) for m in raw]

    async def clear(self, session_id: str):
        await self.redis.delete(f"session:{session_id}")
```

**Key characteristics:**
- **Data structure:** Redis List — preserves message ordering
- **TTL:** 3600 seconds (1 hour) — auto-expires inactive sessions
- **Serialization:** JSON encoding for each message
- **Trimming:** Caller-controlled via `limit` parameter

---

## Conversation Store

**Location:** `app/memory/conversation_store.py`

The `ConversationStore` handles permanent storage in PostgreSQL.

### Schema

```sql
CREATE TABLE IF NOT EXISTS interactions (
    id          SERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    timestamp   TIMESTAMP DEFAULT NOW()
);
```

### Methods

```python
class ConversationStore:
    async def save_interaction(self, session_id: str, role: str, content: str):
        """Insert a single interaction into PostgreSQL."""

    async def get_history(self, session_id: str, limit: int = 20) -> List[dict]:
        """Retrieve recent interactions ordered by timestamp DESC."""

    async def search_interactions(self, session_id: str, query: str) -> List[dict]:
        """Full-text search using ILIKE pattern matching."""
```

### Connection Management

```python
# Uses asyncpg connection pool
self.pool = await asyncpg.create_pool(
    self.settings.DATABASE_URL,
    min_size=2,
    max_size=10
)
```

The pool is lazily initialized on first use. Table creation runs automatically on first connection.

---

## Summarizer

**Location:** `app/memory/summarizer.py`

The `Summarizer` compresses conversation history to fit within token budgets.

```python
class Summarizer:
    async def summarize(self, messages: List[dict]) -> str:
        """Compress a list of messages into a concise summary."""
        prompt = f"Summarize this conversation concisely:\n{formatted_messages}"
        return await self.llm.ask(prompt, system="You are a helpful summarizer.")
```

**When summarization triggers:**
- When conversation history exceeds the allocated token budget
- When the `ContextBuilder` needs to fit more content into the context window
- The `AdaptiveTokenBudgeter` allocates 40% of the non-system-non-user budget to memory

**Summarization flow:**
1. `ContextBuilder` requests history from `MemoryService`
2. If combined tokens exceed budget, `Summarizer.summarize()` is called
3. The summary replaces the full history in the prompt
4. The summary is cached in session for reuse

---

## Memory Authority Resolver

**Location:** `app/memory/authority.py`

The `MemoryAuthorityResolver` resolves conflicts when multiple memory sources return contradictory information.

### Authority Hierarchy

```
Priority 1 (Highest): Conversation Memory
    └── Direct statements from the user in this session

Priority 2: Knowledge Graph (if confidence ≥ 0.8)
    └── Entity-relationship triples with trust scores

Priority 3: Vector Memory
    └── Semantically similar past interactions
```

### Resolution Logic

```python
class MemoryAuthorityResolver:
    async def resolve(self, question: str, sources: dict) -> dict:
        """
        Resolve conflicting memory sources.

        sources = {
            "conversation": [...],       # Direct conversation context
            "knowledge_graph": [...],    # KG triples with confidence scores
            "vector": [...]              # Semantic similarity results
        }

        Returns:
            {"answer": str, "source": str, "confidence": float}
        """
```

**Resolution rules:**
1. If conversation memory contains a direct answer → use it (source: `"conversation"`)
2. If KG has a triple with confidence ≥ 0.8 → use it (source: `"knowledge_graph"`)
3. Otherwise → use vector memory results (source: `"vector"`)
4. Result includes the winning source and confidence for transparency

---

## Unified Memory Controller

**Location:** `app/memory/memory_controller.py`

The `UnifiedMemoryController` orchestrates all memory subsystems with parallel access and conflict resolution.

### Architecture

```python
class UnifiedMemoryController:
    def __init__(self, memory_service, vector_memory, graph_store, authority_resolver):
        self.memory = memory_service
        self.vector = vector_memory
        self.graph = graph_store
        self.authority = authority_resolver
```

### Parallel Fetch

```python
async def retrieve_context(self, session_id: str, query: str) -> dict:
    """Fetch from all memory sources in parallel."""

    conversation, vectors, kg_results = await asyncio.gather(
        self.memory.get_recent(session_id),
        self.vector.search(query, top_k=5),
        self.graph.query(query),
        return_exceptions=True
    )

    # Handle individual failures gracefully
    if isinstance(conversation, Exception):
        conversation = []
    if isinstance(vectors, Exception):
        vectors = []
    if isinstance(kg_results, Exception):
        kg_results = []

    return {
        "conversation": conversation,
        "vector": vectors,
        "knowledge_graph": kg_results
    }
```

### Conflict Resolution

When sources disagree, the controller delegates to the `MemoryAuthorityResolver`:

```python
async def get_authoritative_context(self, session_id: str, query: str) -> dict:
    sources = await self.retrieve_context(session_id, query)
    resolved = await self.authority.resolve(query, sources)
    return resolved
```

### Session Locking

The controller implements session-level locking to prevent concurrent modifications:

```python
async def acquire_session_lock(self, session_id: str) -> bool:
    """Acquire a Redis-based distributed lock for session operations."""
    lock_key = f"lock:session:{session_id}"
    acquired = await self.redis.set(lock_key, "1", nx=True, ex=30)
    return bool(acquired)
```

---

## Vector Memory Integration

The memory system integrates with the vector database layer for semantic search:

1. **Save path:** After saving to Redis/PG, the controller optionally embeds the interaction and stores it in vector memory
2. **Retrieve path:** During context retrieval, vector memory is queried in parallel with other sources
3. **Memory types:** The vector store supports three memory types:
   - `episodic` — Specific interactions and events
   - `semantic` — General knowledge and facts
   - `profile` — User preferences and attributes

See [VECTOR_DATABASE.md](VECTOR_DATABASE.md) for complete vector memory documentation.

---

## Memory Consistency Model

The platform's memory is eventually consistent across four distinct storage systems. Understanding the consistency guarantees is critical for reasoning about system behavior.

### Consistency Guarantees by Tier

| Operation | Redis (Session) | PostgreSQL (History) | Vector Memory | Knowledge Graph |
|-----------|:---------------:|:--------------------:|:-------------:|:---------------:|
| **Write latency** | <1ms | 2-5ms | 50-200ms (async) | 100-500ms (async) |
| **Read-after-write** | Immediate | Immediate | Eventual | Eventual |
| **Durability** | Volatile (30min TTL) | Durable (WAL) | Durable (file/gRPC) | Durable (JSON) |
| **Cross-session** | No | Yes | Yes | Yes |
| **Multi-replica** | Shared (single Redis) | Shared (single PG) | Per-backend | Shared (file) |

### How the Three Memory Systems Interact

```
┌──────────────────────────────────────────────────────────────┐
│                  Conversation Memory                          │
│  Redis (recent turns) → PostgreSQL (full history)             │
│  Summarizer compresses when token budget exceeded             │
└──────────────────────────┬───────────────────────────────────┘
                          │
            Feeds into context via MemoryService
                          │
┌──────────────────────────┼───────────────────────────────────┐
│                  Vector Memory                                │
│  Episodic (past interactions by embedding similarity)         │
│  Semantic (learned facts and knowledge)                       │
│  Profile  (user preferences and attributes)                   │
│  Cross-session, persisted to FAISS/Qdrant                     │
└──────────────────────────┼───────────────────────────────────┘
                          │
            Entity extraction feeds into KG
                          │
┌──────────────────────────┼───────────────────────────────────┐
│                  Knowledge Graph                              │
│  Entity→Relationship→Entity triples (NetworkX)                │
│  Trust-scored: domain 0.3, consistency 0.25,                  │
│    recency 0.2, citations 0.15, feedback 0.1                  │
│  Queried for entity context during requests                   │
└──────────────────────────────────────────────────────────────┘
```

### Convergence Behavior

- **Session-scoped queries** primarily draw from Redis (fast, recent) with PostgreSQL as fallback
- **Cross-session queries** rely on vector memory similarity search and knowledge graph entity lookup
- **Entity corrections** propagate immediately to the knowledge graph but reach vector memory only after the next maintenance cycle (default: daily at 03:00 UTC)
- **Summarization** triggers when conversation history exceeds the token budget; the summary replaces raw history in future context assembly

### Staleness Windows

| Data Type | Max Staleness | Cause |
|-----------|:-------------:|-------|
| Session messages | 0 (real-time) | Direct Redis write |
| Conversation history | 0 (real-time) | Synchronous PG write |
| Vector memory embeddings | Up to 24h | Background Celery task |
| Knowledge graph entities | Up to 24h | Post-response extraction (async) |
| Semantic cache | 24h TTL | TTL-based expiration |

---

## Memory Lifecycle

### Write Path

```
User Message
     │
     ├──→ SessionCache.add_message()     [Redis List, TTL 3600s]
     │
     ├──→ MemoryService.save()
     │    ├──→ Redis Hash (short-term)
     │    └──→ PostgreSQL INSERT (long-term)
     │
     └──→ VectorMemory.store()           [FAISS/Qdrant embedding]
          └──→ EmbeddingService.embed()
```

### Read Path

```
Context Request
     │
     ▼
UnifiedMemoryController.retrieve_context()
     │
     ├──→ SessionCache.get_messages()     [Fastest, current window]
     ├──→ MemoryService.get_recent()      [Recent history]
     ├──→ VectorMemory.search()           [Semantic similarity]
     └──→ GraphStore.query()              [Entity relationships]
     │
     ▼
MemoryAuthorityResolver.resolve()          [Conflict resolution]
     │
     ▼
ContextBuilder.build_prompt()              [Token-budgeted assembly]
```

### Cleanup Path

```
Maintenance Worker (daily at 03:00 UTC)
     │
     ├──→ VectorMaintenanceManager.deduplicate()    [≥0.98 cosine]
     ├──→ VectorMaintenanceManager.remove_stale()   [>90 days]
     └──→ VectorMaintenanceManager.reindex()        [Rebuild FAISS index]
```

---

## Data Schemas

### Session Message

```python
{
    "role": "user" | "assistant" | "system",
    "content": "Message text content",
    "ts": "2024-01-15T10:30:00.000000"
}
```

### Conversation Interaction (PostgreSQL)

```python
{
    "id": 12345,
    "session_id": "sess_abc123",
    "role": "user",
    "content": "What is machine learning?",
    "timestamp": "2024-01-15T10:30:00"
}
```

### Vector Memory Entry

```python
{
    "id": "vec_xyz789",
    "content": "Machine learning is a subset of AI...",
    "embedding": [0.123, -0.456, ...],  # 384 dimensions
    "metadata": {
        "session_id": "sess_abc123",
        "memory_type": "semantic",
        "timestamp": "2024-01-15T10:30:00"
    }
}
```

### Authority Resolution Result

```python
{
    "answer": "Resolved answer text",
    "source": "conversation" | "knowledge_graph" | "vector",
    "confidence": 0.92,
    "sources_consulted": ["conversation", "knowledge_graph", "vector"]
}
```

---

## Configuration

### Redis Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| Session TTL | 3600s | Session cache expiration |
| Message limit | 50 | Maximum messages in Redis list |
| Lock TTL | 30s | Session lock expiration |

### PostgreSQL Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://...` | PostgreSQL connection URL |
| Pool min size | 2 | Minimum connections |
| Pool max size | 10 | Maximum connections |

### Memory Budget

| Parameter | Default | Description |
|-----------|---------|-------------|
| Memory % of context | 40% | Token budget allocated to memory (of RAG+memory budget) |
| RAG % of context | 60% | Token budget allocated to RAG (of RAG+memory budget) |
| Summarization threshold | Budget exceeded | When to trigger summarization |

---

## Failure Modes

| Failure | Impact | Recovery |
|---------|--------|----------|
| Redis down | No session cache, no short-term memory | Falls back to PostgreSQL for history |
| PostgreSQL down | No long-term persistence | Messages queue in Redis, manual replay needed |
| Vector DB down | No semantic search | Conversation + KG context only |
| KG unavailable | No entity relationships | Conversation + vector context only |
| All sources fail | No memory context at all | `asyncio.gather(return_exceptions=True)` returns empty lists; system continues without context |
| Session lock conflict | Concurrent writes blocked | 30s lock TTL auto-releases; caller retries |
| Token budget exceeded | History too large for prompt | Summarizer compresses history to fit budget |

---

## Memory Consistency Scenarios

The system is eventually consistent across four stores. Here's what happens when individual components fail:

### Scenario 1: Redis Crash After Write, Before PostgreSQL

```
User sends message → Redis RPUSH succeeds → Redis crashes before PG INSERT
```

- **Impact:** Message exists in volatile Redis only; lost on restart
- **User experience:** After Redis restart, message disappears from session context
- **Recovery:** Query proceeds with PostgreSQL history only; new messages resume normally
- **Risk window:** Time between Redis write and PG write (~2ms typical)

### Scenario 2: PostgreSQL Down, Redis Up

```
User sends message → Redis RPUSH succeeds → PG INSERT fails
```

- **Impact:** Messages accumulate in Redis (up to 50-message trim limit)
- **Recovery:** When PG comes back online, future messages persist normally. Messages stored only in Redis during outage are lost if Redis restarts before PG recovers
- **Mitigation:** Celery retry task with exponential backoff replays failed PG writes

### Scenario 3: Vector DB Down During Retrieval

```
Memory retrieval → asyncio.gather(conversation, vector, kg) → vector search fails
```

- **Impact:** No semantic memory search; conversation + KG context still available
- **User experience:** Slightly less contextual answers; no error shown to user
- **Recovery:** `asyncio.gather(return_exceptions=True)` catches the error; system continues with available sources

---

## Token Budget Calculation

Understanding how the 40%/60% memory/RAG split works:

```
Example: 128K token context window

Fixed allocations:
  System prompt:          ~10K tokens
  Current user message:   ~2K tokens
  Remaining for context:  ~116K tokens

RAG + Memory budget:      ~116K tokens
  RAG allocation (60%):    69.6K tokens → document chunks
  Memory allocation (40%): 46.4K tokens → conversation history

If conversation history exceeds 46.4K tokens:
  → Summarizer triggered
  → Full history compressed to ~5K token summary
  → Complete history preserved in PostgreSQL for audit
  → Summary injected into prompt instead of raw messages
```

**Adjusting the split:** Increase memory percentage for conversational apps with long sessions. Increase RAG percentage for knowledge-heavy apps. Change via `MEMORY_BUDGET_PERCENT` in settings.

---

## When to Use Each Memory Tier

| Scenario | Best Tier | Why |
|----------|-----------|-----|
| "What did I just ask?" | Session Cache (L1) | Sub-millisecond, current turn context |
| "What did we discuss yesterday?" | Conversation Store (L3) | Full history, cross-session |
| "Find conversations about Python" | Vector Memory (L4) | Semantic search across all sessions |
| "What do I prefer for coding?" | Vector Memory (L4, profile type) | User preference discovery |
| "Who is John Smith?" | Knowledge Graph | Entity relationships and trust scores |
| Building audit trail | Conversation Store (L3) | PostgreSQL, complete and permanent |
| Personalizing responses | Vector Memory (L4) + KG | Combine preference vectors with entity data |

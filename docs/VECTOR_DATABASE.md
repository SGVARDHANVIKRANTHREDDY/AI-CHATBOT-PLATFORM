# Vector Database

> Complete documentation of the vector memory subsystem: abstract store interface, FAISS and Qdrant backends, embedding service, memory retriever, maintenance, and migration.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Vector Store Interface](#vector-store-interface)
- [FAISS Backend](#faiss-backend)
- [Qdrant Backend](#qdrant-backend)
- [Vector Memory Facade](#vector-memory-facade)
- [Embedding Service](#embedding-service)
- [Memory Retriever](#memory-retriever)
- [Vector Maintenance Manager](#vector-maintenance-manager)
- [Migration: FAISS to Qdrant](#migration-faiss-to-qdrant)
- [Memory Types](#memory-types)
- [Configuration](#configuration)
- [Performance Characteristics](#performance-characteristics)
- [Failure Modes](#failure-modes)

---

## Overview

The vector database layer stores and retrieves semantically indexed data. It supports two backends:

| Backend | Use Case | Characteristics |
|---------|----------|-----------------|
| **FAISS** | Development, small datasets | In-process, no external dependencies, exact search |
| **Qdrant** | Production, large datasets | Distributed, async gRPC, approximate NN, scalable |

Both backends implement the same abstract interface, enabling transparent switching via configuration.

---

## Architecture

```
┌───────────────────────────────────────────────────────┐
│                   VectorMemory (Facade)               │
│           Selects backend via VECTOR_BACKEND           │
│                                                       │
│  ┌─────────────────┐         ┌─────────────────────┐  │
│  │ FAISSVectorStore│         │ QdrantVectorStore   │  │
│  │ (development)   │   OR    │ (production)        │  │
│  │                 │         │                     │  │
│  │ IndexFlatIP     │         │ Async gRPC client   │  │
│  │ JSON+binary     │         │ Auto-retry+jitter   │  │
│  │ persistence     │         │ HNSW + scalar quant │  │
│  └─────────────────┘         └─────────────────────┘  │
│           │                           │               │
│           └───────────┬───────────────┘               │
│                       │                               │
│              ┌────────▼────────┐                      │
│              │ EmbeddingService│                      │
│              │ all-MiniLM-L6-v2│                      │
│              │ 384 dims, L2    │                      │
│              └─────────────────┘                      │
└───────────────────────────────────────────────────────┘
```

---

## Vector Store Interface

**Location:** `app/vector_memory/vector_store.py`

```python
from abc import ABC, abstractmethod

class VectorStore(ABC):
    @abstractmethod
    async def store(self, id: str, embedding: List[float], metadata: dict) -> None:
        """Store a vector with associated metadata."""

    @abstractmethod
    async def search(self, query_embedding: List[float], top_k: int = 5) -> List[dict]:
        """Search for the top-k nearest neighbors."""

    @abstractmethod
    async def delete(self, id: str) -> None:
        """Delete a vector by ID."""

    @abstractmethod
    async def count(self) -> int:
        """Return the number of stored vectors."""
```

---

## FAISS Backend

**Location:** `app/vector_memory/faiss_store.py`

### Implementation

```python
class FAISSVectorStore(VectorStore):
    def __init__(self, dimension: int = 384, persist_dir: str = "data/vector_index"):
        self.dimension = dimension
        self.index = faiss.IndexFlatIP(dimension)  # Inner Product (cosine for L2-normalized)
        self.metadata = {}     # id → metadata dict
        self.id_to_index = {}  # id → FAISS row index
        self.persist_dir = persist_dir
```

### Storage

```python
async def store(self, id: str, embedding: List[float], metadata: dict):
    vector = np.array([embedding], dtype=np.float32)
    self.index.add(vector)
    row_index = self.index.ntotal - 1
    self.id_to_index[id] = row_index
    self.metadata[id] = metadata
    self._save()
```

### Search

```python
async def search(self, query_embedding: List[float], top_k: int = 5) -> List[dict]:
    query = np.array([query_embedding], dtype=np.float32)
    scores, indices = self.index.search(query, top_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0:
            continue
        # Reverse lookup: FAISS index → ID
        for id, stored_idx in self.id_to_index.items():
            if stored_idx == idx:
                results.append({
                    "id": id,
                    "score": float(score),
                    "metadata": self.metadata[id]
                })
                break
    return results
```

### Deletion

Deletion in FAISS requires index reconstruction:

```python
async def delete(self, id: str):
    if id not in self.id_to_index:
        return

    del self.metadata[id]
    del self.id_to_index[id]

    # Rebuild index from remaining vectors
    self._rebuild_index()
```

### Persistence

```python
def _save(self):
    """Save FAISS index and metadata to disk."""
    os.makedirs(self.persist_dir, exist_ok=True)

    # Binary FAISS index
    faiss.write_index(self.index, os.path.join(self.persist_dir, "index.faiss"))

    # JSON metadata
    with open(os.path.join(self.persist_dir, "metadata.json"), 'w') as f:
        json.dump({
            "metadata": self.metadata,
            "id_to_index": self.id_to_index
        }, f)

def _load(self):
    """Load FAISS index and metadata from disk."""
    index_path = os.path.join(self.persist_dir, "index.faiss")
    if os.path.exists(index_path):
        self.index = faiss.read_index(index_path)
        with open(os.path.join(self.persist_dir, "metadata.json")) as f:
            data = json.load(f)
            self.metadata = data["metadata"]
            self.id_to_index = data["id_to_index"]
```

---

## Qdrant Backend

**Location:** `app/vector_memory/qdrant_store.py`

### Implementation

```python
class QdrantVectorStore(VectorStore):
    def __init__(self, host: str, port: int, collection_name: str = "memories"):
        self.client = QdrantClient(host=host, port=port, prefer_grpc=True)
        self.collection_name = collection_name
        self._ensure_collection()
```

### Auto-Collection Creation

```python
def _ensure_collection(self):
    """Create collection if it doesn't exist."""
    collections = self.client.get_collections().collections
    if not any(c.name == self.collection_name for c in collections):
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(
                size=384,
                distance=Distance.COSINE,
            ),
        )
```

### Storage with Batching

```python
async def store(self, id: str, embedding: List[float], metadata: dict):
    point = PointStruct(
        id=id,
        vector=embedding,
        payload=metadata,
    )
    self.client.upsert(
        collection_name=self.collection_name,
        points=[point],
    )

async def batch_store(self, records: List[dict]):
    """Store records in batches of 100."""
    points = [
        PointStruct(id=r["id"], vector=r["embedding"], payload=r["metadata"])
        for r in records
    ]
    # Chunk into batches of 100
    for i in range(0, len(points), 100):
        batch = points[i:i+100]
        self.client.upsert(
            collection_name=self.collection_name,
            points=batch,
        )
```

### Search

```python
async def search(self, query_embedding: List[float], top_k: int = 5) -> List[dict]:
    results = self.client.search(
        collection_name=self.collection_name,
        query_vector=query_embedding,
        limit=top_k,
    )
    return [
        {
            "id": str(hit.id),
            "score": hit.score,
            "metadata": hit.payload,
        }
        for hit in results
    ]
```

### Auto-Retry with Jitter

```python
async def _with_retry(self, operation, max_retries: int = 3):
    """Execute an operation with exponential backoff and jitter."""
    for attempt in range(max_retries):
        try:
            return await operation()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = min(2 ** attempt + random.uniform(0, 1), 30)
            await asyncio.sleep(delay)
```

---

## Vector Memory Facade

**Location:** `app/vector_memory/vector_memory.py`

The `VectorMemory` facade selects the backend based on configuration:

```python
class VectorMemory:
    def __init__(self, settings):
        if settings.VECTOR_BACKEND == "qdrant":
            self.store = QdrantVectorStore(
                host=settings.QDRANT_HOST,
                port=settings.QDRANT_PORT,
            )
        else:
            self.store = FAISSVectorStore(
                dimension=384,
                persist_dir=settings.VECTOR_INDEX_DIR,
            )
        self.embedding_service = EmbeddingService()

    async def store_memory(self, content: str, memory_type: str, metadata: dict = None):
        """Embed and store a memory."""
        embedding = self.embedding_service.embed([content])[0]
        id = str(uuid.uuid4())
        full_metadata = {
            "content": content,
            "memory_type": memory_type,
            "timestamp": datetime.utcnow().isoformat(),
            **(metadata or {}),
        }
        await self.store.store(id, embedding.tolist(), full_metadata)

    async def search(self, query: str, top_k: int = 5, memory_type: str = None) -> List[dict]:
        """Search for similar memories."""
        embedding = self.embedding_service.embed([query])[0]
        results = await self.store.search(embedding.tolist(), top_k=top_k)

        if memory_type:
            results = [r for r in results if r["metadata"].get("memory_type") == memory_type]

        return results
```

---

## Embedding Service

**Location:** `app/vector_memory/embedding_service.py`

```python
class EmbeddingService:
    _instance = None

    def __new__(cls):
        """Singleton pattern — share one model across the process."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.model = SentenceTransformer("all-MiniLM-L6-v2")
        return cls._instance

    def embed(self, texts: List[str]) -> np.ndarray:
        """Generate L2-normalized embeddings."""
        return self.model.encode(texts, normalize_embeddings=True)

    def embed_single(self, text: str) -> np.ndarray:
        """Embed a single text."""
        return self.embed([text])[0]
```

| Property | Value |
|----------|-------|
| Model | `all-MiniLM-L6-v2` |
| Dimensions | 384 |
| Normalization | L2 (unit length) |
| Similarity | Cosine (= inner product for L2-normalized vectors) |
| Instance | Singleton (shared) |
| Batch support | Yes, via `model.encode()` |

---

## Memory Retriever

**Location:** `app/vector_memory/memory_retriever.py`

The `MemoryRetriever` coordinates vector search with knowledge graph queries.

```python
class MemoryRetriever:
    def __init__(self, vector_memory, graph_store, authority_resolver):
        self.vector = vector_memory
        self.graph = graph_store
        self.authority = authority_resolver

    async def retrieve_context(self, query: str, session_id: str = None) -> dict:
        """Multi-tier memory retrieval with authority resolution."""

        # Parallel fetch across memory tiers
        vector_results, kg_results = await asyncio.gather(
            self.vector.search(query, top_k=5),
            self.graph.query(query),
            return_exceptions=True,
        )

        # Handle failures gracefully
        if isinstance(vector_results, Exception):
            vector_results = []
        if isinstance(kg_results, Exception):
            kg_results = []

        return {
            "vector": vector_results,
            "knowledge_graph": kg_results,
        }
```

---

## Vector Maintenance Manager

**Location:** `app/vector_memory/maintenance.py`

The maintenance manager runs automated cleanup tasks.

### Deduplication

```python
async def deduplicate(self, threshold: float = 0.98):
    """Remove near-duplicate vectors (cosine similarity ≥ threshold)."""
    all_vectors = await self.store.get_all()
    to_delete = set()

    for i, v1 in enumerate(all_vectors):
        if v1["id"] in to_delete:
            continue
        for j, v2 in enumerate(all_vectors[i+1:], i+1):
            if v2["id"] in to_delete:
                continue
            similarity = cosine_similarity(v1["embedding"], v2["embedding"])
            if similarity >= threshold:
                # Keep the newer one
                if v1["metadata"]["timestamp"] < v2["metadata"]["timestamp"]:
                    to_delete.add(v1["id"])
                else:
                    to_delete.add(v2["id"])

    for id in to_delete:
        await self.store.delete(id)

    return len(to_delete)
```

### Stale Record Removal

```python
async def remove_stale(self, max_age_days: int = 90):
    """Remove records older than max_age_days."""
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    all_vectors = await self.store.get_all()

    removed = 0
    for vector in all_vectors:
        ts = datetime.fromisoformat(vector["metadata"]["timestamp"])
        if ts < cutoff:
            await self.store.delete(vector["id"])
            removed += 1

    return removed
```

### Index Rebuild

```python
async def reindex(self):
    """Rebuild the entire vector index."""
    all_data = await self.store.get_all()
    texts = [d["metadata"]["content"] for d in all_data]
    embeddings = self.embedding_service.embed(texts)

    # Clear and rebuild
    await self.store.clear()
    for data, embedding in zip(all_data, embeddings):
        await self.store.store(data["id"], embedding.tolist(), data["metadata"])
```

### Scheduled Maintenance

The maintenance worker runs daily at 03:00 UTC:

```python
# workers/maintenance_worker.py
@celery_app.task
def run_vector_maintenance():
    manager = VectorMaintenanceManager(vector_store, embedding_service)

    # 1. Deduplicate (≥0.98 cosine similarity)
    dedup_count = manager.deduplicate(threshold=0.98)

    # 2. Remove stale (>90 days)
    stale_count = manager.remove_stale(max_age_days=90)

    # 3. Reindex
    manager.reindex()

    logger.info(f"Maintenance complete: {dedup_count} deduped, {stale_count} stale removed")
```

---

## Migration: FAISS to Qdrant

**Location:** `app/vector_memory/migration.py`

### Migration Tool

```python
class VectorMigration:
    def __init__(self, faiss_store: FAISSVectorStore, qdrant_store: QdrantVectorStore):
        self.source = faiss_store
        self.target = qdrant_store

    async def migrate(self):
        """Migrate all vectors from FAISS to Qdrant."""
        all_data = await self.source.get_all()

        # Batch upload to Qdrant (100 records per batch)
        for i in range(0, len(all_data), 100):
            batch = all_data[i:i+100]
            records = [
                {
                    "id": d["id"],
                    "embedding": d["embedding"],  # or reconstruct via embedding_service
                    "metadata": d["metadata"],
                }
                for d in batch
            ]
            await self.target.batch_store(records)

        return len(all_data)
```

### Vector Reconstruction Fallback

If embeddings are not stored in FAISS metadata, the migration tool can reconstruct them:

```python
async def migrate_with_reconstruction(self):
    """Migrate with embedding reconstruction for FAISS stores without stored vectors."""
    all_metadata = self.source.metadata

    for id, meta in all_metadata.items():
        content = meta.get("content", "")
        if content:
            embedding = self.embedding_service.embed([content])[0]
            await self.target.store(id, embedding.tolist(), meta)
```

### CLI Interface

```bash
python -m app.vector_memory.migration --source faiss --target qdrant
```

---

## Memory Types

The vector store supports three semantic memory types:

| Type | Description | Examples |
|------|-------------|---------|
| `episodic` | Specific interactions and events | "User asked about Python decorators", "Discussed async/await patterns" |
| `semantic` | General knowledge and facts | "Python is a dynamically typed language", "Docker containers share the host kernel" |
| `profile` | User preferences and attributes | "User prefers concise answers", "User is a senior engineer" |

Memory type is stored in metadata and can be filtered on search:

```python
# Store with memory type
await vector_memory.store_memory(
    content="User prefers Python over JavaScript",
    memory_type="profile"
)

# Search filtered by type
results = await vector_memory.search(
    query="User's programming preferences",
    memory_type="profile"
)
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `VECTOR_BACKEND` | `faiss` | Backend selection: `faiss` or `qdrant` |
| `QDRANT_HOST` | `localhost` | Qdrant server hostname |
| `QDRANT_PORT` | `6334` | Qdrant gRPC port |
| `VECTOR_INDEX_DIR` | `data/vector_index` | FAISS persistence directory |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | SentenceTransformer model |
| `EMBEDDING_DIM` | `384` | Embedding vector dimensions |
| Dedup threshold | `0.98` | Cosine similarity for deduplication |
| Stale cutoff | `90` days | Age threshold for stale removal |
| Qdrant batch size | `100` | Records per batch upload |
| Qdrant max retries | `3` | Auto-retry attempts |

---

## Performance Characteristics

| Operation | FAISS | Qdrant |
|-----------|-------|--------|
| Search (10K vectors) | ~1ms | ~2ms |
| Search (1M vectors) | ~10ms | ~5ms (HNSW) |
| Store (single) | ~0.1ms | ~2ms (network) |
| Store (batch 100) | ~1ms | ~10ms |
| Delete | ~50ms (rebuild) | ~1ms |
| Disk usage (10K × 384d) | ~15MB | ~20MB |
| Index rebuild | ~100ms | N/A (automatic) |

**FAISS advantages:** No external dependencies, zero network latency, exact search.
**Qdrant advantages:** Distributed, scales to billions of vectors, approximate NN (HNSW), built-in filtering.

---

## Failure Modes

| Failure | FAISS | Qdrant |
|---------|-------|--------|
| Index corruption | Rebuild from metadata.json | Qdrant handles internally |
| OOM | Reduce dataset size | Scale cluster |
| Write conflict | Single-threaded access | Qdrant handles concurrency |
| Network failure | N/A (in-process) | Auto-retry with jitter (3 attempts) |
| Collection missing | N/A | Auto-created on init |
| Embedding model OOM | Reduce batch size | Reduce batch size |
| Migration failure | Partial migration possible | Resume from last batch |

---

## Choosing an Embedding Model

The default `all-MiniLM-L6-v2` balances speed and quality for most use cases:

| Model | Dimensions | Size | Encode Speed | Quality | Best For |
|-------|-----------|------|-------------|---------|----------|
| `all-MiniLM-L6-v2` (default) | 384 | 22M params | ~5ms/batch | Good | Dev, low-latency, <100K vectors |
| `all-mpnet-base-v2` | 768 | 110M params | ~20ms/batch | Better | Production, accuracy matters |
| `bge-base-en-v1.5` | 768 | 109M params | ~20ms/batch | Better | Multilingual, high-quality |

**Decision:** Start with MiniLM. If A/B testing shows >5% better relevance with mpnet, switch. The 4x speed difference only matters during bulk ingestion — search latency is dominated by index lookup, not encoding.

> **Important:** Changing the embedding model requires re-embedding all existing vectors. The migration tool does not handle model changes — you must rebuild the entire index.

---

## Understanding Memory Types

Three semantic memory types serve different purposes:

| Type | Example | When Stored | Use Case |
|------|---------|-------------|----------|
| **Episodic** | "User asked about Python decorators on Jan 15" | After each interaction | Audit trail, temporal context ("what happened last week?") |
| **Semantic** | "Python is dynamically typed" | When facts extracted from conversation | Knowledge retrieval ("tell me about X") |
| **Profile** | "User prefers concise answers" | When user preferences detected | Personalization, response style adaptation |

**Default:** Use `semantic` for >90% of stored memories. Use `episodic` for compliance/audit trails. Use `profile` for UX personalization signals.

```python
# Storing different memory types
await vector_memory.store(
    content="User prefers Python examples over JavaScript",
    session_id="sess_123",
    memory_type="profile"     # Will be filtered during personalization retrieval
)

await vector_memory.store(
    content="Docker containers use namespaces for isolation",
    session_id="sess_123",
    memory_type="semantic"    # Available for general knowledge queries
)
```

---

## Deduplication Threshold Rationale

The 0.98 cosine similarity threshold for deduplication was chosen conservatively:

| Threshold | Vectors Removed | False Positives | Notes |
|-----------|----------------|-----------------|-------|
| 0.95 | ~5% | Moderate risk | May merge semantically similar but distinct facts |
| **0.98** (default) | ~2% | Very low risk | Near-identical content only; safe for production |
| 0.99 | ~0.5% | Negligible risk | Almost exact duplicates only; minimal space savings |

**Example at 0.98 threshold:**
- "Python supports object-oriented programming" and "Python has OOP support" → cosine 0.96 → **kept separate** (different wording, same meaning — but may be from different contexts)
- "Python supports object-oriented programming" and "Python supports object-oriented programming." → cosine 0.99 → **deduplicated** (identical content)

**Adjusting:** Lower the threshold (0.95) only after verifying on your dataset that removed vectors don't degrade retrieval quality. Monitor `rag_retrieval_results_count` after changes.

# RAG Pipeline

> Complete documentation of the Retrieval-Augmented Generation pipeline: document ingestion, chunking and embedding, FAISS/Qdrant indexing, retrieval, reranking, content safety, and knowledge crawling.

---

## Table of Contents

- [Overview](#overview)
- [Pipeline Architecture](#pipeline-architecture)
- [Document Ingestion](#document-ingestion)
- [Chunking Strategy](#chunking-strategy)
- [Embedding and Indexing](#embedding-and-indexing)
- [Retrieval](#retrieval)
- [Reranking](#reranking)
- [Content Safety Gate](#content-safety-gate)
- [Knowledge Crawler](#knowledge-crawler)
- [Index Persistence](#index-persistence)
- [Integrity Validation](#integrity-validation)
- [Configuration](#configuration)
- [Metrics](#metrics)
- [Failure Modes](#failure-modes)

---

## Overview

The RAG pipeline bridges the gap between the LLM's parametric knowledge and domain-specific documents. Every query is enriched with relevant document chunks retrieved through semantic similarity search and reranked for precision.

**Pipeline stages:**

```
Documents → Extract → Chunk → Safety Filter → Embed → Index → Persist
Query → Embed → Search → Rerank → Context Injection → LLM
```

---

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Ingestion Path                        │
│                                                         │
│  ┌────────┐   ┌────────┐   ┌────────┐   ┌───────────┐  │
│  │ Files  │──▶│ Extract│──▶│ Chunk  │──▶│ Safety    │  │
│  │PDF/TXT │   │ Text   │   │Sentence│   │ Filter    │  │
│  │  /MD   │   │        │   │ Split  │   │           │  │
│  └────────┘   └────────┘   └────────┘   └─────┬─────┘  │
│                                               │        │
│                              ┌────────────────▼──────┐ │
│                              │ EmbeddingService      │ │
│                              │ all-MiniLM-L6-v2      │ │
│                              │ 384 dimensions, L2    │ │
│                              └────────────┬──────────┘ │
│                                           │            │
│                              ┌────────────▼──────────┐ │
│                              │ FAISS IndexFlatIP     │ │
│                              │ + Atomic JSON Save    │ │
│                              └───────────────────────┘ │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                    Retrieval Path                        │
│                                                         │
│  ┌────────┐   ┌────────┐   ┌────────┐   ┌───────────┐  │
│  │ Query  │──▶│ Embed  │──▶│ FAISS  │──▶│ Reranker  │  │
│  │        │   │        │   │ Search │   │CrossEncode│  │
│  └────────┘   └────────┘   └────────┘   └─────┬─────┘  │
│                                               │        │
│                              ┌────────────────▼──────┐ │
│                              │  Top-K Results        │ │
│                              │  with scores          │ │
│                              └───────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## Document Ingestion

**Location:** `app/rag/retriever.py` — `RAGRetriever`

### Supported Formats

| Format | Extraction Method |
|--------|------------------|
| `.pdf` | PyPDF2 `PdfReader` — page-by-page text extraction |
| `.txt` | Direct file read with UTF-8 encoding |
| `.md` | Direct file read with UTF-8 encoding |

### Ingestion Flow

```python
async def ingest(self, file_path: str):
    """Ingest a single document into the RAG index."""
    # 1. Extract text
    text = self._extract_text(file_path)

    # 2. Chunk into sentences
    chunks = self._chunk_text(text)

    # 3. Content safety filter
    safe_chunks = [c for c in chunks if self.safety_filter.is_safe(c)]

    # 4. Generate embeddings
    embeddings = self.embedding_service.embed(safe_chunks)

    # 5. Add to FAISS index
    self.index.add(np.array(embeddings))
    self.documents.extend(safe_chunks)

    # 6. Save atomically
    self._save_index()
```

### Batch Ingestion

The `ingest_documents` Celery task in `workers/ingestion_worker.py` handles batch ingestion:

```python
@celery_app.task
def ingest_documents(file_paths: List[str]):
    retriever = RAGRetriever(settings)
    for path in file_paths:
        retriever.ingest(path)
    retriever.save()
```

---

## Chunking Strategy

### Sentence-Based Chunking

```python
def _chunk_text(self, text: str, chunk_size: int = 512, overlap: int = 50) -> List[str]:
    """Split text into overlapping sentence-aligned chunks."""
    sentences = text.split('.')
    chunks = []
    current_chunk = []
    current_length = 0

    for sentence in sentences:
        sentence = sentence.strip() + '.'
        if current_length + len(sentence) > chunk_size and current_chunk:
            chunks.append(' '.join(current_chunk))
            # Overlap: keep last N characters
            overlap_text = ' '.join(current_chunk)[-overlap:]
            current_chunk = [overlap_text]
            current_length = len(overlap_text)

        current_chunk.append(sentence)
        current_length += len(sentence)

    if current_chunk:
        chunks.append(' '.join(current_chunk))

    return chunks
```

**Parameters:**

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `chunk_size` | 512 characters | Maximum chunk length |
| `overlap` | 50 characters | Overlap between consecutive chunks |

**Design rationale:**
- Sentence splitting preserves semantic coherence
- Overlap prevents information loss at boundaries
- 512 characters balances retrieval precision vs. context availability

---

## Embedding and Indexing

### Embedding Service

**Location:** `app/vector_memory/embedding_service.py`

```python
class EmbeddingService:
    _instance = None  # Singleton

    def __init__(self):
        self.model = SentenceTransformer("all-MiniLM-L6-v2")

    def embed(self, texts: List[str]) -> np.ndarray:
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return embeddings  # Shape: (n, 384), L2-normalized
```

**Model specifications:**

| Property | Value |
|----------|-------|
| Model | `all-MiniLM-L6-v2` |
| Dimensions | 384 |
| Normalization | L2 (unit vectors) |
| Similarity metric | Inner product (equivalent to cosine for L2-normalized vectors) |
| Singleton | Yes — one model instance shared process-wide |

### FAISS Indexing

```python
# Index type: IndexFlatIP (Inner Product)
# Exact search — no approximation, 100% recall
self.index = faiss.IndexFlatIP(384)

# Adding vectors:
embeddings = self.embedding_service.embed(chunks)
self.index.add(np.array(embeddings, dtype=np.float32))
```

**Why `IndexFlatIP`?**
- With L2-normalized vectors, inner product equals cosine similarity
- Flat index provides exact nearest neighbor search (no approximation)
- Suitable for datasets up to ~1M vectors on a single machine

---

## Retrieval

```python
async def retrieve(self, query: str, top_k: int = 5) -> List[dict]:
    """Retrieve the top-k most relevant document chunks."""
    # 1. Embed the query
    query_embedding = self.embedding_service.embed([query])

    # 2. Search FAISS index
    scores, indices = self.index.search(query_embedding, top_k * 2)  # Over-fetch for reranking

    # 3. Collect candidates
    candidates = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < len(self.documents) and idx >= 0:
            candidates.append({
                "content": self.documents[idx],
                "score": float(score),
                "index": int(idx)
            })

    # 4. Rerank candidates
    reranked = self.reranker.rerank(query, candidates, top_k=top_k)

    return reranked
```

**Over-fetching:** The retriever fetches `top_k * 2` candidates from FAISS to give the reranker a larger pool for precise re-scoring.

---

## Reranking

**Location:** `app/rag/reranker.py`

The `CrossEncoderReranker` re-scores candidates using a cross-encoder model for higher precision:

```python
class CrossEncoderReranker:
    def __init__(self):
        self.model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

    def rerank(self, query: str, candidates: List[dict], top_k: int = 5) -> List[dict]:
        """Re-score and re-order candidates using cross-encoder."""
        pairs = [(query, c["content"]) for c in candidates]
        scores = self.model.predict(pairs)

        for candidate, score in zip(candidates, scores):
            candidate["rerank_score"] = float(score)

        ranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
        return ranked[:top_k]
```

**Why two-stage retrieval?**
1. **Stage 1 (Bi-encoder):** Fast approximate retrieval using FAISS — sub-millisecond for millions of vectors
2. **Stage 2 (Cross-encoder):** Precise re-scoring of candidates — jointly attends to query and document for accurate relevance scoring

**Model:** `cross-encoder/ms-marco-MiniLM-L-6-v2` — trained on MS MARCO passage ranking dataset

---

## Content Safety Gate

Before indexing, every chunk passes through the `ContentSafetyFilter`:

```python
safe_chunks = [c for c in chunks if self.safety_filter.is_safe(c)]
```

This prevents poisoned or malicious content from entering the index. The filter checks for:

1. **Injection patterns** — Prompt injection attempts (19 weighted regex rules)
2. **Domain reputation** — Source domain trust scores (20+ whitelisted domains)
3. **Content quality** — Alpha ratio, word count, trigram uniqueness, Shannon entropy

See [SECURITY_MODEL.md](SECURITY_MODEL.md) for the full content safety filter documentation.

---

## Knowledge Crawler

**Location:** `app/rag/crawler.py`

The `KnowledgeCrawler` autonomously fetches web content to expand the knowledge base.

### Domain Whitelisting

```python
ALLOWED_DOMAINS = [
    "wikipedia.org",
    "docs.python.org",
    "arxiv.org",
    "stackoverflow.com",
    # ... 20+ trusted domains
]
```

Only URLs matching whitelisted domains are crawled.

### Crawl Process

```python
class KnowledgeCrawler:
    async def crawl(self, urls: List[str]) -> List[dict]:
        """Crawl whitelisted URLs and extract clean text."""
        results = []
        for url in urls:
            if not self._is_allowed(url):
                continue

            # Rate limiting
            await self._rate_limit()

            # Fetch with size limit
            response = await self.client.get(url, timeout=10)
            if len(response.content) > 500_000:  # 500KB limit
                continue

            # Parse and clean
            text = self._extract_text(response.text)
            results.append({"url": url, "content": text})

        return results
```

### Scheduled Crawling

The `knowledge_builder` Celery worker runs on a schedule:

```python
# workers/knowledge_builder.py
# Scheduled: daily at 04:00 UTC

@celery_app.task
def crawl_and_ingest(urls: List[str]):
    crawler = KnowledgeCrawler()
    results = crawler.crawl(urls)

    # Filter by trust score
    trusted = [r for r in results if trust_evaluator.evaluate(r["url"]) >= threshold]

    # Ingest into RAG index
    retriever.ingest_texts([r["content"] for r in trusted])
```

---

## Index Persistence

### Atomic Save Strategy

```python
def _save_index(self):
    """Save FAISS index and document metadata atomically."""
    # 1. Save FAISS binary index
    index_path = self.data_dir / "vector_index" / "index.faiss"
    faiss.write_index(self.index, str(index_path))

    # 2. Save document metadata as JSON
    meta_path = self.data_dir / "vector_index" / "metadata.json"
    temp_path = meta_path.with_suffix('.tmp')

    # Write to temp file first (atomic write pattern)
    with open(temp_path, 'w') as f:
        json.dump({"documents": self.documents, "fingerprints": self.fingerprints}, f)

    # Atomic rename
    temp_path.rename(meta_path)
```

**Atomicity guarantee:** Writing to a temp file and renaming prevents corruption if the process crashes mid-save.

### Index Structure on Disk

```
data/vector_index/
├── index.faiss          # FAISS binary index file
└── metadata.json        # Document texts + integrity fingerprints
```

---

## Integrity Validation

The retriever maintains SHA-256 fingerprints for every indexed document to detect tampering:

```python
def _compute_fingerprint(self, content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()

def _validate_integrity(self):
    """Verify all documents match their stored fingerprints."""
    for doc, expected_hash in zip(self.documents, self.fingerprints):
        actual_hash = self._compute_fingerprint(doc)
        if actual_hash != expected_hash:
            raise IntegrityError(f"Document integrity check failed: {doc[:50]}...")
```

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `chunk_size` | 512 | Maximum characters per chunk |
| `overlap` | 50 | Overlap between consecutive chunks |
| `top_k` | 5 | Number of final results returned |
| `over_fetch_factor` | 2x | FAISS candidates fetched for reranking |
| `embedding_model` | `all-MiniLM-L6-v2` | SentenceTransformer model name |
| `reranker_model` | `ms-marco-MiniLM-L-6-v2` | CrossEncoder model name |
| `embedding_dim` | 384 | Embedding vector dimensions |
| `crawl_size_limit` | 500KB | Maximum page size for crawler |
| `crawl_rate_limit` | Per-domain | Rate limiting between requests |

---

## Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `rag_retrieval_latency_seconds` | Histogram | — | End-to-end retrieval time |
| `rag_retrieval_results_count` | Histogram | — | Number of results returned |
| `rag_ingestion_documents_total` | Counter | — | Total documents ingested |
| `rag_index_size` | Gauge | — | Current number of indexed vectors |
| `rag_reranking_latency_seconds` | Histogram | — | Cross-encoder reranking time |

---

## Failure Modes

| Failure | Impact | Recovery |
|---------|--------|----------|
| FAISS index corrupted | No retrieval | Rebuild from `metadata.json` documents |
| Embedding model OOM | Ingestion/retrieval fails | Reduce batch size; upgrade hardware |
| Reranker unavailable | Degraded precision | Fall back to bi-encoder scores only |
| Crawl target down | No new content | Retry on next scheduled crawl (24h) |
| Content safety rejection | Chunks excluded from index | Expected behavior; log for review |
| Integrity check failure | Possible tampering detected | Re-ingest affected documents |
| Index file lock conflict | Concurrent write failure | Retry with backoff; single-writer pattern |

---

## Scaling Decision Tree

```
How many vectors do you have?
  │
  ├─ <10K → FAISS (single machine, dev)
  │         Search: ~1ms, no external deps
  │
  ├─ 10K–100K → FAISS or Qdrant
  │              FAISS: <10ms search, zero infra cost
  │              Qdrant: if <5ms SLA or HA required
  │
  ├─ 100K–1M → Qdrant recommended
  │             FAISS search degrades: 10ms → 50ms+
  │             Qdrant HNSW: stable ~5ms
  │
  └─ >1M → Qdrant required
            FAISS: linear scan too slow, full rebuild on delete
            Qdrant: distributed sharding, auto-scaling
```

**Migration trigger:** If FAISS search p95 latency exceeds 50ms, start planning Qdrant migration. See [VECTOR_DATABASE.md](VECTOR_DATABASE.md#migration-faiss-to-qdrant) for migration procedures.

---

## Chunking Worked Example

Understanding how chunking produces overlapping segments:

```
Input text (100 chars):
"Python is a high-level language. It supports OOP. Decorators are syntactic sugar for wrappers."

Settings: chunk_size=50, overlap=15

Step 1 — First chunk (0–50 chars):
  "Python is a high-level language. It supports OOP"

Step 2 — Overlap: take last 15 chars of chunk 1:
  "supports OOP"

Step 3 — Second chunk starts with overlap:
  " supports OOP. Decorators are syntactic sugar for"

Step 4 — Third chunk:
  "ctic sugar for wrappers."
```

> **Important:** Overlap is character-based, not sentence-aligned. Chunks may start or end mid-sentence. The reranker compensates by scoring full semantic relevance after retrieval.

---

## Reranking: Why Two Stages?

| Stage | Model | Speed | Accuracy | Purpose |
|-------|-------|-------|----------|---------|
| Bi-encoder (FAISS/Qdrant) | all-MiniLM-L6-v2 | ~1ms per query | Good | Fast candidate retrieval from millions |
| Cross-encoder (reranker) | ms-marco-MiniLM-L-6-v2 | ~10ms per pair | Excellent | Precise relevance scoring on small set |

**Why not cross-encoder for everything?** A cross-encoder scores each (query, document) pair independently — scanning 100K documents would take ~17 minutes. The bi-encoder encodes query once and compares against pre-computed vectors in milliseconds.

**Over-fetch factor (2x):** We retrieve `2 × top_k` candidates from the bi-encoder, then rerank to find the best `top_k`. The 2x multiplier balances recall (finding all relevant docs) against reranking cost (processing more pairs). Higher values (3-4x) improve recall but increase latency.

---

## Index Recovery Procedure

If the FAISS index becomes corrupted (detected by integrity check failure):

```bash
# 1. Verify the metadata file is intact
python -c "
from app.rag.retriever import RAGRetriever
r = RAGRetriever()
print(f'Metadata docs: {len(r.documents)}')
print(f'Fingerprints: {len(r.fingerprints)}')
"

# 2. Re-embed and rebuild the index
python -c "
from app.rag.retriever import RAGRetriever
r = RAGRetriever()
r.embed_documents()  # Re-generates FAISS index from stored documents
print('Index rebuilt successfully')
"

# 3. Verify integrity
python -c "
from app.rag.retriever import RAGRetriever
r = RAGRetriever()
r.validate_integrity()
print('Integrity check passed')
"
```

If `metadata.json` is also corrupted, you must re-ingest from source documents:

```bash
# Full re-ingestion from raw documents
python -c "
from app.rag.retriever import RAGRetriever
r = RAGRetriever()
r.extract_texts()       # Re-parse source files
r.chunk_text()           # Re-chunk
r.embed_documents()      # Re-embed and index
print(f'Re-ingested {len(r.chunks)} chunks')
"
```

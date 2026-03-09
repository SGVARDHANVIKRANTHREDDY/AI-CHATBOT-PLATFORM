"""
Semantic Cache — Redis-backed embedding cache for LLM call reduction.

Stores query embeddings and their LLM responses in Redis.  On each
incoming query the cache computes cosine similarity against all stored
embeddings and returns the cached response when similarity exceeds a
configurable threshold (default 0.92).

Features:
    - Redis as sole backend (no on-disk FAISS index)
    - LRU eviction when the cache exceeds ``max_entries``
    - Cosine similarity over normalized embeddings
    - TTL-based automatic expiry
    - Prometheus metrics: hit rate, lookup latency, LLM call reduction

Design rationale:
    Moving from FAISS-on-disk to Redis removes a class of startup/
    persistence bugs and enables horizontal scaling — every app
    instance shares the same cache via Redis without file locking.
    LRU eviction prevents unbounded memory growth while keeping the
    hottest queries warm.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional

import numpy as np
import redis.asyncio as redis

from app.config.settings import settings
from app.shared.monitoring import (
    SEMANTIC_CACHE_HITS,
    SEMANTIC_CACHE_LATENCY,
    SEMANTIC_CACHE_LLM_SAVINGS,
    SEMANTIC_CACHE_SIZE,
)
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

# Redis key prefixes
_PREFIX_EMB = "semcache:emb:"      # hash → embedding bytes
_PREFIX_RESP = "semcache:resp:"    # hash → response text
_PREFIX_TS = "semcache:ts:"        # hash → last-access timestamp
_INDEX_KEY = "semcache:index"      # sorted set (score = last-access ts)


class SemanticCache:
    """Embedding-based LLM response cache backed entirely by Redis.

    Args:
        threshold: Minimum cosine similarity to count as a cache hit.
        max_entries: Maximum number of cached entries (LRU eviction).
        ttl: Time-to-live in seconds for individual entries.
    """

    def __init__(
        self,
        threshold: float = settings.SEMANTIC_CACHE_THRESHOLD,
        max_entries: int = getattr(settings, "SEMANTIC_CACHE_MAX_ENTRIES", 1000),
        ttl: int = settings.SEMANTIC_CACHE_TTL,
    ) -> None:
        self.threshold = threshold
        self.max_entries = max_entries
        self.ttl = ttl
        self._redis: redis.Redis = redis.from_url(
            settings.REDIS_URL, decode_responses=False
        )
        self._model: Any = None

    # ── Embedding helpers ─────────────────────────────────────────

    def _get_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(settings.EMBEDDING_MODEL)
        return self._model

    def _encode(self, text: str) -> np.ndarray:
        model = self._get_model()
        emb = model.encode([text], normalize_embeddings=True)
        return np.asarray(emb, dtype="float32").flatten()

    @staticmethod
    def _query_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]

    # ── Core API ──────────────────────────────────────────────────

    async def get(self, query: str) -> Optional[str]:
        """Look up a semantically similar cached response.

        Returns the response string on hit, ``None`` on miss.
        """
        if not settings.SEMANTIC_CACHE_ENABLED:
            return None

        t0 = time.perf_counter()
        q_emb = self._encode(query)

        # Fetch all cached embeddings
        members = await self._redis.zrangebyscore(_INDEX_KEY, "-inf", "+inf")
        if not members:
            SEMANTIC_CACHE_HITS.labels(status="miss").inc()
            SEMANTIC_CACHE_LATENCY.observe(time.perf_counter() - t0)
            return None

        best_score = -1.0
        best_key: Optional[str] = None

        # Pipeline: fetch all embeddings in one round-trip
        pipe = self._redis.pipeline(transaction=False)
        decoded_members: List[str] = []
        for m in members:
            key = m.decode("utf-8") if isinstance(m, bytes) else m
            decoded_members.append(key)
            pipe.get(f"{_PREFIX_EMB}{key}")
        raw_embs = await pipe.execute()

        for key, raw in zip(decoded_members, raw_embs):
            if raw is None:
                continue
            cached_emb = np.frombuffer(raw, dtype="float32")
            # Cosine similarity (embeddings are L2-normalized)
            score = float(np.dot(q_emb, cached_emb))
            if score > best_score:
                best_score = score
                best_key = key

        if best_key is not None and best_score >= self.threshold:
            raw_resp = await self._redis.get(f"{_PREFIX_RESP}{best_key}")
            if raw_resp is not None:
                # Touch LRU timestamp
                now = time.time()
                await self._redis.zadd(_INDEX_KEY, {best_key: now})
                await self._redis.set(f"{_PREFIX_TS}{best_key}", str(now))

                response = raw_resp.decode("utf-8") if isinstance(raw_resp, bytes) else raw_resp
                _LOG.info(
                    "Semantic cache HIT (score=%.4f, key=%s)", best_score, best_key
                )
                SEMANTIC_CACHE_HITS.labels(status="hit").inc()
                SEMANTIC_CACHE_LLM_SAVINGS.inc()
                SEMANTIC_CACHE_LATENCY.observe(time.perf_counter() - t0)
                return response

        SEMANTIC_CACHE_HITS.labels(status="miss").inc()
        SEMANTIC_CACHE_LATENCY.observe(time.perf_counter() - t0)
        return None

    async def set(self, query: str, response: str) -> None:
        """Cache a query-response pair.

        Stores the embedding, response, and timestamp in Redis.
        Evicts LRU entries if the cache exceeds ``max_entries``.
        """
        if not settings.SEMANTIC_CACHE_ENABLED:
            return

        q_emb = self._encode(query)
        key = self._query_hash(query)
        now = time.time()

        pipe = self._redis.pipeline(transaction=True)
        pipe.set(f"{_PREFIX_EMB}{key}", q_emb.tobytes(), ex=self.ttl)
        pipe.set(f"{_PREFIX_RESP}{key}", response.encode("utf-8"), ex=self.ttl)
        pipe.set(f"{_PREFIX_TS}{key}", str(now), ex=self.ttl)
        pipe.zadd(_INDEX_KEY, {key: now})
        await pipe.execute()

        # LRU eviction
        await self._evict_if_needed()

        size = await self._redis.zcard(_INDEX_KEY)
        SEMANTIC_CACHE_SIZE.set(size)
        _LOG.info("Cached semantic response (key=%s, total=%d)", key, size)

    async def invalidate(self, query: str) -> bool:
        """Remove a specific entry from the cache."""
        key = self._query_hash(query)
        return await self._remove_key(key)

    async def clear(self) -> int:
        """Remove all cache entries. Returns count removed."""
        members = await self._redis.zrangebyscore(_INDEX_KEY, "-inf", "+inf")
        count = 0
        for m in members:
            key = m.decode("utf-8") if isinstance(m, bytes) else m
            await self._remove_key(key)
            count += 1
        return count

    async def size(self) -> int:
        """Return current number of cached entries."""
        return await self._redis.zcard(_INDEX_KEY)

    # ── LRU eviction ─────────────────────────────────────────────

    async def _evict_if_needed(self) -> int:
        """Evict least-recently-used entries when cache exceeds max_entries."""
        current_size = await self._redis.zcard(_INDEX_KEY)
        if current_size <= self.max_entries:
            return 0

        to_evict = current_size - self.max_entries
        # Sorted set is ordered by score (timestamp) — lowest = oldest
        victims = await self._redis.zrange(_INDEX_KEY, 0, to_evict - 1)
        evicted = 0
        for v in victims:
            key = v.decode("utf-8") if isinstance(v, bytes) else v
            await self._remove_key(key)
            evicted += 1

        if evicted:
            _LOG.info("LRU eviction: removed %d entries", evicted)
        return evicted

    async def _remove_key(self, key: str) -> bool:
        """Remove a single cache entry by key."""
        pipe = self._redis.pipeline(transaction=True)
        pipe.delete(f"{_PREFIX_EMB}{key}")
        pipe.delete(f"{_PREFIX_RESP}{key}")
        pipe.delete(f"{_PREFIX_TS}{key}")
        pipe.zrem(_INDEX_KEY, key)
        results = await pipe.execute()
        return any(results)


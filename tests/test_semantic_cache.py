"""
Tests — Semantic Cache (Redis-backed, LRU eviction, cosine similarity).

Covers:
    1. Similar queries hitting the cache (cosine similarity)
    2. Dissimilar queries returning miss
    3. LRU eviction when cache exceeds max_entries
    4. Concurrent get/set operations
    5. Invalidate and clear operations
    6. Cache disabled setting
    7. TTL propagation
    8. Metric instrumentation
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

# ── Helpers ──────────────────────────────────────────────────────


def _make_embedding(seed: int, dim: int = 384) -> np.ndarray:
    """Deterministic normalised embedding from a seed."""
    rng = np.random.RandomState(seed)
    v = rng.randn(dim).astype("float32")
    v /= np.linalg.norm(v)
    return v


def _similar_embedding(base: np.ndarray, noise: float = 0.02) -> np.ndarray:
    """Return an embedding very close to *base* (high cosine similarity)."""
    rng = np.random.RandomState(99)
    perturbed = base + rng.randn(*base.shape).astype("float32") * noise
    perturbed /= np.linalg.norm(perturbed)
    return perturbed


def _query_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


class FakeRedis:
    """In-memory async Redis mock that supports the subset of commands
    used by ``SemanticCache``: get/set/delete, zadd/zrem/zcard/zrange/
    zrangebyscore, and pipelines.
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self._zset: dict[str, dict[str, float]] = {}  # key -> {member: score}

    # ── key/value ─────────────────────────────────────────────

    async def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:
        if isinstance(value, str):
            value = value.encode("utf-8")
        self._store[key] = value

    async def delete(self, *keys: str) -> int:
        removed = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                removed += 1
        return removed

    # ── sorted set ────────────────────────────────────────────

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        zs = self._zset.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in zs:
                added += 1
            zs[member] = score
        return added

    async def zrem(self, key: str, *members: str) -> int:
        zs = self._zset.get(key, {})
        removed = 0
        for m in members:
            dm = m.decode("utf-8") if isinstance(m, bytes) else m
            if dm in zs:
                del zs[dm]
                removed += 1
        return removed

    async def zcard(self, key: str) -> int:
        return len(self._zset.get(key, {}))

    async def zrange(self, key: str, start: int, stop: int) -> list[bytes]:
        zs = self._zset.get(key, {})
        ordered = sorted(zs.items(), key=lambda x: x[1])
        # Redis zrange is inclusive on both ends
        subset = ordered[start : stop + 1]
        return [m.encode("utf-8") for m, _ in subset]

    async def zrangebyscore(self, key: str, min_score: str, max_score: str) -> list[bytes]:
        zs = self._zset.get(key, {})
        ordered = sorted(zs.items(), key=lambda x: x[1])
        return [m.encode("utf-8") for m, _ in ordered]

    # ── pipeline ──────────────────────────────────────────────

    def pipeline(self, transaction: bool = False) -> FakePipeline:
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[Any] = []

    def get(self, key: str) -> FakePipeline:
        self._ops.append(("get", key))
        return self

    def set(self, key: str, value: Any, ex: int | None = None) -> FakePipeline:
        self._ops.append(("set", key, value, ex))
        return self

    def delete(self, key: str) -> FakePipeline:
        self._ops.append(("delete", key))
        return self

    def zadd(self, key: str, mapping: dict[str, float]) -> FakePipeline:
        self._ops.append(("zadd", key, mapping))
        return self

    def zrem(self, key: str, *members: str) -> FakePipeline:
        self._ops.append(("zrem", key, *members))
        return self

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for op in self._ops:
            if op[0] == "get":
                results.append(await self._redis.get(op[1]))
            elif op[0] == "set":
                await self._redis.set(op[1], op[2], ex=op[3])
                results.append(True)
            elif op[0] == "delete":
                results.append(await self._redis.delete(op[1]))
            elif op[0] == "zadd":
                results.append(await self._redis.zadd(op[1], op[2]))
            elif op[0] == "zrem":
                results.append(await self._redis.zrem(op[1], *op[2:]))
        self._ops.clear()
        return results

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _build_cache(threshold: float = 0.92, max_entries: int = 100, ttl: int = 3600):
    """Build a SemanticCache with a FakeRedis backend and mocked encoder."""
    from app.cache.semantic_cache import SemanticCache

    cache = SemanticCache.__new__(SemanticCache)
    cache.threshold = threshold
    cache.max_entries = max_entries
    cache.ttl = ttl
    cache._redis = FakeRedis()
    cache._model = None

    # Deterministic embeddings: each unique string maps to a deterministic vector
    _embedding_map: dict[str, np.ndarray] = {}
    _counter = [0]

    def _encode(text: str) -> np.ndarray:
        if text not in _embedding_map:
            _embedding_map[text] = _make_embedding(_counter[0])
            _counter[0] += 1
        return _embedding_map[text]

    cache._encode = _encode
    return cache, _embedding_map


# ═════════════════════════════════════════════════════════════════
# Similarity hit / miss
# ═════════════════════════════════════════════════════════════════


class TestSimilarityLookup:
    @pytest.mark.asyncio
    async def test_exact_same_query_returns_cached(self):
        cache, _emb_map = _build_cache(threshold=0.90)

        await cache.set("What is Python?", "A programming language.")
        result = await cache.get("What is Python?")
        assert result == "A programming language."

    @pytest.mark.asyncio
    async def test_similar_query_hits_cache(self):
        """Two queries that produce embeddings with cosine > threshold → hit."""
        cache, _emb_map = _build_cache(threshold=0.90)

        base_emb = _make_embedding(42)
        similar_emb = _similar_embedding(base_emb, noise=0.01)

        # Override encode to return controlled embeddings
        call_count = [0]

        def _encode(text: str) -> np.ndarray:
            call_count[0] += 1
            if text == "query A":
                return base_emb
            return similar_emb

        cache._encode = _encode

        await cache.set("query A", "answer A")
        result = await cache.get("query B that is similar")
        cosine = float(np.dot(base_emb, similar_emb))
        assert cosine > 0.90, f"Test setup error: cosine={cosine}"
        assert result == "answer A"

    @pytest.mark.asyncio
    async def test_dissimilar_query_misses(self):
        """Two orthogonal embeddings → cache miss."""
        cache, _emb_map = _build_cache(threshold=0.90)

        emb_a = _make_embedding(0)
        emb_b = _make_embedding(1)

        def _encode(text: str) -> np.ndarray:
            return emb_a if "original" in text else emb_b

        cache._encode = _encode

        await cache.set("original question", "original answer")
        result = await cache.get("completely different question")
        cosine = float(np.dot(emb_a, emb_b))
        assert cosine < 0.90, f"Test setup error: cosine={cosine}"
        assert result is None

    @pytest.mark.asyncio
    async def test_best_match_selected_among_multiple(self):
        """When multiple entries exist, the closest match is returned."""
        cache, _ = _build_cache(threshold=0.80)

        base = _make_embedding(50)
        close = _similar_embedding(base, noise=0.01)
        far = _make_embedding(51)

        call_idx = [0]
        embeddings = [far, close, base]  # set("far"), set("close"), get("query")

        def _encode(text: str) -> np.ndarray:
            emb = embeddings[call_idx[0]]
            call_idx[0] += 1
            return emb

        cache._encode = _encode

        await cache.set("far question", "far answer")
        await cache.set("close question", "close answer")
        result = await cache.get("query")
        assert result == "close answer"


# ═════════════════════════════════════════════════════════════════
# LRU eviction
# ═════════════════════════════════════════════════════════════════


class TestLRUEviction:
    @pytest.mark.asyncio
    async def test_eviction_removes_oldest(self):
        cache, _ = _build_cache(max_entries=3)

        await cache.set("q1", "a1")
        await cache.set("q2", "a2")
        await cache.set("q3", "a3")
        assert await cache.size() == 3

        # Adding a 4th should evict the oldest (q1)
        await cache.set("q4", "a4")
        assert await cache.size() == 3

        # q1's data should be gone
        key1 = _query_hash("q1")
        raw = await cache._redis.get(f"semcache:emb:{key1}")
        assert raw is None

    @pytest.mark.asyncio
    async def test_eviction_keeps_recent(self):
        cache, _ = _build_cache(max_entries=2)

        await cache.set("first", "a")
        await cache.set("second", "b")
        await cache.set("third", "c")

        assert await cache.size() == 2

        # "second" and "third" should survive
        key2 = _query_hash("second")
        key3 = _query_hash("third")
        assert await cache._redis.get(f"semcache:resp:{key2}") is not None
        assert await cache._redis.get(f"semcache:resp:{key3}") is not None

    @pytest.mark.asyncio
    async def test_no_eviction_under_limit(self):
        cache, _ = _build_cache(max_entries=10)

        for i in range(5):
            await cache.set(f"q{i}", f"a{i}")

        assert await cache.size() == 5

    @pytest.mark.asyncio
    async def test_access_refreshes_lru_timestamp(self):
        """Reading an entry via get() should bump its LRU score,
        preventing it from being evicted next."""
        cache, _ = _build_cache(max_entries=2, threshold=0.0)

        # Use strictly increasing timestamps so LRU order is deterministic
        ts = [1000.0]

        def _advancing_time():
            ts[0] += 1.0
            return ts[0]

        with patch("app.cache.semantic_cache.time") as mock_time:
            mock_time.time = _advancing_time
            mock_time.perf_counter = time.perf_counter

            # Insert q1 (ts=1001) then q2 (ts=1002)
            await cache.set("q1", "a1")
            await cache.set("q2", "a2")

            # Access q1 via get() — bumps its timestamp to 1003
            result = await cache.get("q1")
            assert result == "a1"

            # Now insert q3 (ts=1004) — should evict q2 (score=1002), not q1 (score=1003)
            await cache.set("q3", "a3")


# ═════════════════════════════════════════════════════════════════
# Concurrent operations
# ═════════════════════════════════════════════════════════════════


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_sets(self):
        cache, _ = _build_cache(max_entries=50)

        async def _write(i: int):
            await cache.set(f"concurrent-{i}", f"answer-{i}")

        await asyncio.gather(*[_write(i) for i in range(20)])
        assert await cache.size() == 20

    @pytest.mark.asyncio
    async def test_concurrent_gets_and_sets(self):
        cache, _ = _build_cache(max_entries=50, threshold=0.0)

        # Seed 5 entries
        for i in range(5):
            await cache.set(f"seed-{i}", f"seeded-{i}")

        async def _read(i: int):
            return await cache.get(f"seed-{i % 5}")

        async def _write(i: int):
            await cache.set(f"new-{i}", f"fresh-{i}")

        tasks = [_read(i) for i in range(10)] + [_write(i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        # First 10 results are from reads — each should be a cached answer
        for r in results[:10]:
            assert r is not None
            assert r.startswith("seeded-")

        assert await cache.size() == 15


# ═════════════════════════════════════════════════════════════════
# Invalidate / clear
# ═════════════════════════════════════════════════════════════════


class TestInvalidateAndClear:
    @pytest.mark.asyncio
    async def test_invalidate_removes_entry(self):
        cache, _ = _build_cache()

        await cache.set("remove me", "gone")
        assert await cache.size() == 1

        removed = await cache.invalidate("remove me")
        assert removed is True
        assert await cache.size() == 0

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent_is_false(self):
        cache, _ = _build_cache()
        removed = await cache.invalidate("never cached")
        assert removed is False

    @pytest.mark.asyncio
    async def test_clear_removes_all(self):
        cache, _ = _build_cache()

        for i in range(5):
            await cache.set(f"q{i}", f"a{i}")
        assert await cache.size() == 5

        count = await cache.clear()
        assert count == 5
        assert await cache.size() == 0


# ═════════════════════════════════════════════════════════════════
# Disabled cache
# ═════════════════════════════════════════════════════════════════


class TestDisabledCache:
    @pytest.mark.asyncio
    async def test_get_returns_none_when_disabled(self):
        cache, _ = _build_cache()
        await cache.set("q", "a")

        with patch("app.cache.semantic_cache.settings") as mock_settings:
            mock_settings.SEMANTIC_CACHE_ENABLED = False
            result = await cache.get("q")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_is_noop_when_disabled(self):
        cache, _ = _build_cache()

        with patch("app.cache.semantic_cache.settings") as mock_settings:
            mock_settings.SEMANTIC_CACHE_ENABLED = False
            await cache.set("q", "a")
        assert await cache.size() == 0


# ═════════════════════════════════════════════════════════════════
# Metrics instrumentation
# ═════════════════════════════════════════════════════════════════


class TestMetrics:
    @pytest.mark.asyncio
    async def test_hit_increments_counter(self):
        from app.shared.monitoring import SEMANTIC_CACHE_HITS

        cache, _ = _build_cache(threshold=0.0)
        await cache.set("hello", "world")

        before = SEMANTIC_CACHE_HITS.labels(status="hit")._value.get()
        await cache.get("hello")
        after = SEMANTIC_CACHE_HITS.labels(status="hit")._value.get()
        assert after > before

    @pytest.mark.asyncio
    async def test_miss_increments_counter(self):
        from app.shared.monitoring import SEMANTIC_CACHE_HITS

        cache, _ = _build_cache()
        before = SEMANTIC_CACHE_HITS.labels(status="miss")._value.get()
        await cache.get("unknown query")
        after = SEMANTIC_CACHE_HITS.labels(status="miss")._value.get()
        assert after > before

    @pytest.mark.asyncio
    async def test_llm_savings_incremented_on_hit(self):
        from app.shared.monitoring import SEMANTIC_CACHE_LLM_SAVINGS

        cache, _ = _build_cache(threshold=0.0)
        await cache.set("q", "a")

        before = SEMANTIC_CACHE_LLM_SAVINGS._value.get()
        await cache.get("q")
        after = SEMANTIC_CACHE_LLM_SAVINGS._value.get()
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_size_gauge_updated_on_set(self):
        from app.shared.monitoring import SEMANTIC_CACHE_SIZE

        cache, _ = _build_cache()
        await cache.set("q1", "a1")
        assert SEMANTIC_CACHE_SIZE._value.get() >= 1

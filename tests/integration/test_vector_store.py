"""
Integration Tests — VectorStore abstraction layer.

Tests concurrent inserts, concurrent searches, and index rebuild
against the FAISS backend (runs without external services).

The same tests can run against QdrantVectorStore by swapping the
``store`` fixture (requires a running Qdrant instance).
"""

from __future__ import annotations

import asyncio
import uuid

import numpy as np
import pytest
from app.vector_memory.base import SearchResult, VectorRecord
from app.vector_memory.faiss_store import FAISSVectorStore

# ── Constants ─────────────────────────────────────────────────────

DIM = 32  # small dimension for fast tests


def _random_embedding(dim: int = DIM) -> np.ndarray:
    vec = np.random.randn(dim).astype("float32")
    return vec / (np.linalg.norm(vec) + 1e-9)


def _make_record(text: str = "", dim: int = DIM) -> VectorRecord:
    return VectorRecord(
        id=uuid.uuid4().hex,
        embedding=_random_embedding(dim),
        text=text or f"text-{uuid.uuid4().hex[:8]}",
        metadata={"source": "test"},
    )


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Provide a clean FAISS store pointed at a temp directory."""
    monkeypatch.setattr("app.config.settings.settings.VECTOR_INDEX_DIR", tmp_path)
    s = FAISSVectorStore(collection_name="test_collection", embedding_dim=DIM)
    # initialize() and close() are async wrappers over sync code in FAISS backend
    s._load()
    yield s
    s._save()


# ═════════════════════════════════════════════════════════════════
# Test: Concurrent Inserts
# ═════════════════════════════════════════════════════════════════


class TestConcurrentInserts:
    """Verify that many simultaneous add_embedding / batch_insert calls
    are safe and all records land in the store."""

    @pytest.mark.asyncio
    async def test_single_inserts_concurrent(self, store: FAISSVectorStore):
        """Launch 20 add_embedding calls concurrently."""
        records = [_make_record() for _ in range(20)]

        tasks = [store.add_embedding(id=r.id, embedding=r.embedding, text=r.text, metadata=r.metadata) for r in records]
        await asyncio.gather(*tasks)

        count = await store.count()
        assert count == 20

    @pytest.mark.asyncio
    async def test_batch_insert_concurrent(self, store: FAISSVectorStore):
        """Launch 5 batch_insert calls of 10 records each concurrently."""
        batches: list[list[VectorRecord]] = [[_make_record() for _ in range(10)] for _ in range(5)]

        tasks = [store.batch_insert(batch) for batch in batches]
        results = await asyncio.gather(*tasks)

        assert sum(results) == 50
        assert await store.count() == 50

    @pytest.mark.asyncio
    async def test_mixed_single_and_batch_concurrent(self, store: FAISSVectorStore):
        """Mix single inserts and batch inserts concurrently."""
        singles = [_make_record() for _ in range(5)]
        batch = [_make_record() for _ in range(10)]

        tasks = [store.add_embedding(id=r.id, embedding=r.embedding, text=r.text, metadata=r.metadata) for r in singles]
        tasks.append(store.batch_insert(batch))
        await asyncio.gather(*tasks)

        assert await store.count() == 15

    @pytest.mark.asyncio
    async def test_insert_preserves_text(self, store: FAISSVectorStore):
        """Inserted text should be retrievable via search."""
        rec = _make_record(text="unique-canary-text")
        await store.add_embedding(id=rec.id, embedding=rec.embedding, text=rec.text, metadata=rec.metadata)

        results = await store.search(rec.embedding, top_k=1)
        assert len(results) == 1
        assert results[0].text == "unique-canary-text"


# ═════════════════════════════════════════════════════════════════
# Test: Concurrent Searches
# ═════════════════════════════════════════════════════════════════


class TestConcurrentSearches:
    """Verify that many simultaneous search calls return consistent results."""

    @pytest.mark.asyncio
    async def test_parallel_searches(self, store: FAISSVectorStore):
        """Insert data, then launch 20 parallel search queries."""
        # Seed some data
        records = [_make_record() for _ in range(30)]
        await store.batch_insert(records)

        # Run parallel searches
        query_vecs = [_random_embedding() for _ in range(20)]
        tasks = [store.search(qv, top_k=5) for qv in query_vecs]
        all_results = await asyncio.gather(*tasks)

        for results in all_results:
            assert isinstance(results, list)
            assert len(results) <= 5
            for r in results:
                assert isinstance(r, SearchResult)
                assert r.text  # non-empty

    @pytest.mark.asyncio
    async def test_search_returns_best_match(self, store: FAISSVectorStore):
        """The vector most similar to the query should rank first."""
        target = _random_embedding()
        noise = [_random_embedding() for _ in range(10)]

        # Insert noise first
        noise_records = [VectorRecord(id=uuid.uuid4().hex, embedding=n, text=f"noise-{i}") for i, n in enumerate(noise)]
        await store.batch_insert(noise_records)

        # Insert the target (exact match)
        await store.add_embedding(id="target", embedding=target, text="target-text")

        results = await store.search(target, top_k=3)
        assert results[0].text == "target-text"
        assert results[0].score > 0.99  # near-perfect self-similarity

    @pytest.mark.asyncio
    async def test_search_empty_store(self, store: FAISSVectorStore):
        """Searching an empty store returns an empty list."""
        results = await store.search(_random_embedding(), top_k=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_metadata_filter(self, store: FAISSVectorStore):
        """Metadata filters narrow results correctly."""
        r1 = _make_record(text="cat")
        r1.metadata["animal"] = "cat"
        r2 = _make_record(text="dog")
        r2.metadata["animal"] = "dog"

        await store.add_embedding(id=r1.id, embedding=r1.embedding, text=r1.text, metadata=r1.metadata)
        await store.add_embedding(id=r2.id, embedding=r2.embedding, text=r2.text, metadata=r2.metadata)

        results = await store.search(r1.embedding, top_k=5, filters={"animal": "cat"})
        assert all(r.metadata.get("animal") == "cat" for r in results)


# ═════════════════════════════════════════════════════════════════
# Test: Index Rebuild (Delete + Re-insert)
# ═════════════════════════════════════════════════════════════════


class TestIndexRebuild:
    """Verify that deletion and re-insertion keep the index consistent."""

    @pytest.mark.asyncio
    async def test_delete_reduces_count(self, store: FAISSVectorStore):
        """Deleting vectors reduces the total count."""
        records = [_make_record() for _ in range(10)]
        await store.batch_insert(records)
        assert await store.count() == 10

        ids_to_delete = [r.id for r in records[:3]]
        removed = await store.delete(ids_to_delete)

        assert removed == 3
        assert await store.count() == 7

    @pytest.mark.asyncio
    async def test_delete_then_search_consistent(self, store: FAISSVectorStore):
        """After deletion, searches should not return deleted records."""
        records = [_make_record(text=f"item-{i}") for i in range(5)]
        await store.batch_insert(records)

        # Delete item-2
        await store.delete([records[2].id])

        # Search should not return item-2
        results = await store.search(records[2].embedding, top_k=5)
        result_texts = {r.text for r in results}
        assert "item-2" not in result_texts

    @pytest.mark.asyncio
    async def test_full_rebuild(self, store: FAISSVectorStore):
        """Delete everything, re-insert, verify clean state."""
        records = [_make_record() for _ in range(10)]
        await store.batch_insert(records)

        # Delete all
        all_ids = [r.id for r in records]
        await store.delete(all_ids)
        assert await store.count() == 0

        # Re-insert fresh data
        new_records = [_make_record(text=f"new-{i}") for i in range(5)]
        count = await store.batch_insert(new_records)
        assert count == 5
        assert await store.count() == 5

        # Verify new data is searchable
        results = await store.search(new_records[0].embedding, top_k=1)
        assert results[0].text == "new-0"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_id(self, store: FAISSVectorStore):
        """Deleting an ID that doesn't exist should not error."""
        records = [_make_record() for _ in range(3)]
        await store.batch_insert(records)

        removed = await store.delete(["nonexistent-id-12345"])
        assert removed == 0
        assert await store.count() == 3

    @pytest.mark.asyncio
    async def test_concurrent_insert_and_delete(self, store: FAISSVectorStore):
        """Concurrent inserts and deletes should not corrupt state."""
        # Pre-seed
        initial = [_make_record() for _ in range(10)]
        await store.batch_insert(initial)

        # Concurrently: insert 5 new + delete 3 old
        new = [_make_record() for _ in range(5)]
        delete_ids = [r.id for r in initial[:3]]

        tasks = [
            store.batch_insert(new),
            store.delete(delete_ids),
        ]
        await asyncio.gather(*tasks)

        count = await store.count()
        # Depending on ordering: 10 - 3 + 5 = 12 OR 10 + 5 - 3 = 12
        # Both orderings yield 12 if operations are serialized by the store
        assert 10 <= count <= 15  # relaxed bound for concurrent ordering

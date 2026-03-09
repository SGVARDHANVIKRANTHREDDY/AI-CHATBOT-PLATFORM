"""Tests for RAGRetriever: chunking logic and end-to-end index + search.

A toy 3-dimensional embedder is injected via monkeypatch so the tests
never download sentence-transformer weights.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from app.config.settings import settings
from app.rag.retriever import RAGRetriever


def _toy_encode(texts: list[str]) -> np.ndarray:
    """Deterministic 3-d embeddings: (length, vowel_count, hash_mod)."""
    out = []
    for t in texts:
        t2 = t.lower()
        v = sum(t2.count(ch) for ch in "aeiou")
        out.append([float(len(t2)), float(v), float(abs(hash(t2)) % 997)])
    arr = np.asarray(out, dtype="float32")
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


@pytest.fixture()
def rag_in_tmpdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RAGRetriever:
    """RAGRetriever with overridden paths, toy embedder, and reranking off."""
    monkeypatch.setattr(settings, "RERANKING_ENABLED", False)
    monkeypatch.setattr(settings, "CONTENT_SAFETY_ENABLED", False)

    rag = RAGRetriever()
    rag.raw_docs_dir = tmp_path / "docs"
    rag.index_dir = tmp_path / "idx"
    rag.index_dir.mkdir(parents=True)
    rag.chunks_file = rag.index_dir / "embeddings.json"
    rag.faiss_index_file = rag.index_dir / "faiss.index"
    rag.raw_docs_dir.mkdir(parents=True)

    monkeypatch.setattr(rag, "_encode", _toy_encode)
    return rag


# -- Chunking -------------------------------------------------------------


def test_chunking_sentence_overlap(rag_in_tmpdir: RAGRetriever):
    """Overlap should cause sentence boundaries to be reused across chunks."""
    text = "A one. B two. C three. D four."
    chunks = rag_in_tmpdir.chunk_text(text, max_words=2, overlap_sentences=1)
    assert chunks
    assert any("B two" in c for c in chunks)


def test_chunking_empty_text(rag_in_tmpdir: RAGRetriever):
    assert rag_in_tmpdir.chunk_text("", max_words=50, overlap_sentences=1) == []


def test_chunking_single_sentence(rag_in_tmpdir: RAGRetriever):
    chunks = rag_in_tmpdir.chunk_text("Only one sentence here.", max_words=50, overlap_sentences=1)
    assert len(chunks) == 1


# -- Index build + search -------------------------------------------------


def test_index_build_and_search_txt_doc(rag_in_tmpdir: RAGRetriever):
    doc_path = rag_in_tmpdir.raw_docs_dir / "doc1.txt"
    doc_path.write_text(
        "Python is a programming language. It is used for data science.",
        encoding="utf-8",
    )

    rag_in_tmpdir.embed_documents()
    assert rag_in_tmpdir.is_loaded

    hits = rag_in_tmpdir.search("What is Python used for?", top_k=2)
    assert hits
    assert "source" in hits[0] and "score" in hits[0]
    assert hits[0]["source"] == "doc1.txt"


def test_search_returns_empty_on_unloaded(rag_in_tmpdir: RAGRetriever):
    """Search on an unloaded retriever with no documents returns empty list."""
    hits = rag_in_tmpdir.search("anything", top_k=3)
    assert hits == []


def test_search_empty_query(rag_in_tmpdir: RAGRetriever):
    doc_path = rag_in_tmpdir.raw_docs_dir / "doc2.txt"
    doc_path.write_text("Some content here.", encoding="utf-8")
    rag_in_tmpdir.embed_documents()
    assert rag_in_tmpdir.search("", top_k=3) == []
    assert rag_in_tmpdir.search("   ", top_k=3) == []

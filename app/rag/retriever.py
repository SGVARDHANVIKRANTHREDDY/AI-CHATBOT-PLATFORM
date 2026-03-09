from __future__ import annotations
import glob
import hashlib
import os
import re
import json
import time
import numpy as np
import faiss
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from pathlib import Path

from pdfminer.high_level import extract_text
from app.config.settings import settings
from app.shared.utils import get_logger, emit_observability_event
from app.shared.tracing import start_span
from app.shared.monitoring import (
    CONTENT_SAFETY_SCANS,
    CONTENT_SAFETY_INJECTION_SCORE,
    CONTENT_SAFETY_QUARANTINE_SIZE,
    VECTOR_SEARCH_LATENCY,
    RAG_HIT_COUNT,
)
from app.rag.reranker import CrossEncoderReranker

_LOG = get_logger(__name__)

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _file_fingerprint(path: str) -> str:
    try:
        st = os.stat(path)
        return f"{os.path.basename(path)}|{int(st.st_size)}|{int(st.st_mtime)}"
    except Exception:
        return f"{os.path.basename(path)}|unknown"

def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()

@dataclass(frozen=True)
class RAGHit:
    text: str
    source: str
    chunk_id: int
    score: float
    score_raw: float

class RAGRetriever:
    """Hardened RAG Retriever with modular ingestion and index validation."""
    
    def __init__(self):
        self.raw_docs_dir = Path(settings.UPLOADED_DOCS_DIR)
        self.index_dir = Path(settings.VECTOR_INDEX_DIR)
        self.model_name = settings.EMBEDDING_MODEL
        self._model = None
        self.reranker = CrossEncoderReranker()

        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_file = self.index_dir / "embeddings.json"
        self.faiss_index_file = self.index_dir / "faiss.index"

        self.chunks: List[Dict[str, Any]] = []
        self.index: Optional[faiss.Index] = None
        self.meta: Dict[str, Any] = {}

    @property
    def is_loaded(self) -> bool:
        return self.index is not None and bool(self.chunks)

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _encode(self, texts: List[str]) -> np.ndarray:
        model = self._get_model()
        emb = model.encode(texts, normalize_embeddings=True, show_progress_bar=len(texts) >= 64)
        return np.asarray(emb, dtype="float32")

    def _list_docs(self) -> List[str]:
        patterns = ["*.pdf", "*.txt", "*.md"]
        paths: List[str] = []
        for pat in patterns:
            paths.extend(glob.glob(str(self.raw_docs_dir / pat)))
        return sorted(set(paths))

    def _process_pdf(self, path: str) -> str:
        try:
            return extract_text(path) or ""
        except Exception as e:
            _LOG.error(f"PDF extraction failed for {path}: {e}")
            return ""

    def _process_text(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            _LOG.error(f"Text extraction failed for {path}: {e}")
            return ""

    def extract_texts(self) -> List[Tuple[str, str]]:
        docs = self._list_docs()
        out: List[Tuple[str, str]] = []
        for p in docs:
            source = os.path.basename(p)
            if p.lower().endswith(".pdf"):
                text = self._process_pdf(p)
            else:
                text = self._process_text(p)
            
            cleaned = (text or "").strip()
            if cleaned:
                out.append((source, cleaned))
            else:
                _LOG.warning(f"Skipping empty document: {source}")
        return out

    _SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")

    def _split_sentences(self, text: str) -> List[str]:
        t = re.sub(r"\s+", " ", (text or "").strip())
        if not t: return []
        parts = self._SENT_SPLIT_RE.split(t)
        return [p.strip() for p in parts if p and p.strip()]

    def chunk_text(self, text: str, *, max_words: int, overlap_sentences: int) -> List[str]:
        sentences = self._split_sentences(text)
        if not sentences: return []
        chunks: List[str] = []
        start = 0
        while start < len(sentences):
            words = 0
            end = start
            buf: List[str] = []
            while end < len(sentences):
                s = sentences[end]
                s_words = max(1, len(s.split()))
                if buf and (words + s_words) > max_words: break
                buf.append(s)
                words += s_words
                end += 1
            chunk = " ".join(buf).strip()
            if chunk: chunks.append(chunk)
            if end >= len(sentences): break
            start = max(end - overlap_sentences, start + 1)
        return chunks

    def _get_safety_filter(self):
        """Lazy-init content safety filter."""
        if not settings.CONTENT_SAFETY_ENABLED:
            return None
        from app.security.content_safety import ContentSafetyFilter
        return ContentSafetyFilter(
            injection_threshold=settings.CONTENT_SAFETY_INJECTION_THRESHOLD,
            quarantine_threshold=settings.CONTENT_SAFETY_QUARANTINE_THRESHOLD,
            quality_floor=settings.CONTENT_SAFETY_QUALITY_FLOOR,
        )

    def embed_documents(self) -> None:
        """Full ingestion pipeline: Extract -> Chunk -> Embed -> Index -> Save."""
        _LOG.info("Starting RAG ingestion pipeline...")
        max_words = settings.RAG_CHUNK_MAX_WORDS
        overlap = settings.RAG_CHUNK_OVERLAP_SENTENCES
        
        extracted = self.extract_texts()
        if not extracted:
            _LOG.warning("No documents found for ingestion.")
            return

        chunks: List[Dict[str, Any]] = []
        for source, text in extracted:
            parts = self.chunk_text(text, max_words=max_words, overlap_sentences=overlap)
            for i, chunk in enumerate(parts):
                chunks.append({
                    "text": chunk,
                    "source": source,
                    "chunk_id": i,
                    "text_sha1": _sha1(chunk),
                })

        if not chunks: return

        # ── Content safety gate ───────────────────────────────────
        safety = self._get_safety_filter()
        if safety is not None:
            safe_chunks: List[Dict[str, Any]] = []
            for chunk in chunks:
                verdict = safety.scan(chunk)
                CONTENT_SAFETY_INJECTION_SCORE.observe(verdict.prompt_injection_score)
                if verdict.rejected:
                    CONTENT_SAFETY_SCANS.labels(result="rejected").inc()
                    _LOG.warning("Chunk rejected by safety filter: source=%s reasons=%s",
                                 chunk.get("source"), verdict.reasons)
                elif verdict.quarantined:
                    CONTENT_SAFETY_SCANS.labels(result="quarantined").inc()
                else:
                    CONTENT_SAFETY_SCANS.labels(result="accepted").inc()
                    safe_chunks.append(chunk)
            CONTENT_SAFETY_QUARANTINE_SIZE.set(len(safety.quarantine))
            rejected_count = len(chunks) - len(safe_chunks) - len(safety.quarantine)
            if rejected_count > 0 or len(safety.quarantine) > 0:
                _LOG.info("Content safety: %d accepted, %d rejected, %d quarantined out of %d",
                          len(safe_chunks), rejected_count, len(safety.quarantine), len(chunks))
            chunks = safe_chunks
            if not chunks:
                _LOG.warning("All chunks rejected by safety filter.")
                return

        _LOG.info(f"Encoding {len(chunks)} chunks using {self.model_name}")
        texts = [c["text"] for c in chunks]
        emb = self._encode(texts)
        
        index = faiss.IndexFlatIP(int(emb.shape[1]))
        index.add(emb)

        self.index = index
        self.chunks = chunks
        self.meta = {
            "created_at": _utc_now_iso(),
            "model_name": self.model_name,
            "dim": int(emb.shape[1]),
            "doc_fingerprints": [_file_fingerprint(p) for p in self._list_docs()],
        }

        self._save_atomic()
        _LOG.info("RAG ingestion pipeline completed.")

    def _save_atomic(self):
        """Atomic save to prevent index corruption."""
        temp_index = f"{self.faiss_index_file}.tmp"
        temp_chunks = f"{self.chunks_file}.tmp"
        
        try:
            faiss.write_index(self.index, temp_index)
            with open(temp_chunks, "w", encoding="utf-8") as f:
                json.dump({"chunks": self.chunks, "meta": self.meta}, f, indent=2)
                
            if self.faiss_index_file.exists(): self.faiss_index_file.unlink()
            if self.chunks_file.exists(): self.chunks_file.unlink()
            
            Path(temp_index).rename(self.faiss_index_file)
            Path(temp_chunks).rename(self.chunks_file)
        except Exception as e:
            _LOG.error(f"Atomic save failed: {e}")
            if os.path.exists(temp_index): os.remove(temp_index)
            if os.path.exists(temp_chunks): os.remove(temp_chunks)
            raise

    def load_index(self) -> None:
        if not self.faiss_index_file.exists() or not self.chunks_file.exists():
            raise FileNotFoundError("RAG index missing")
        
        try:
            self.index = faiss.read_index(str(self.faiss_index_file))
            with open(self.chunks_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.chunks = loaded.get("chunks") or []
            self.meta = loaded.get("meta") or {}
            
            # Validation
            self._validate_integrity()
        except Exception as e:
            _LOG.error(f"Failed to load RAG index: {e}")
            raise

    def _validate_integrity(self):
        current_fps = [_file_fingerprint(p) for p in self._list_docs()]
        stored_fps = self.meta.get("doc_fingerprints") or []
        if set(current_fps) != set(stored_fps):
            _LOG.warning("RAG index is stale: document fingerprints mismatch.")

    def ensure_loaded(self, *, rebuild_if_missing: bool = True) -> bool:
        if self.is_loaded: return True
        try:
            self.load_index()
            return self.is_loaded
        except FileNotFoundError:
            if not rebuild_if_missing or not self._list_docs(): return False
            self.embed_documents()
            return self.is_loaded
        except Exception:
            return False

    def search(self, query: str, *, top_k: int = 3) -> List[Dict[str, Any]]:
        if not self.ensure_loaded(): return []
        q = (query or "").strip()
        if not q: return []

        with start_span("rag.retrieve", {"query_length": len(q), "top_k": top_k}) as span:
            # 1. Vector Search for Candidates
            k_candidates = settings.RERANKER_CANDIDATES if settings.RERANKING_ENABLED else top_k * 4
            k = min(len(self.chunks), k_candidates)

            qemb = self._encode([q])
            vec_t0 = time.perf_counter()
            scores, idxs = self.index.search(qemb, k)
            vec_elapsed = time.perf_counter() - vec_t0
            VECTOR_SEARCH_LATENCY.labels(backend="faiss").observe(vec_elapsed)

            seen_keys: set[str] = set()
            candidate_hits: List[Dict[str, Any]] = []
            for score_raw, i in zip(scores[0].tolist(), idxs[0].tolist()):
                if i < 0 or i >= len(self.chunks): continue
                c = self.chunks[i]
                key = f"{c.get('source')}::{c.get('chunk_id')}"
                if key in seen_keys: continue
                seen_keys.add(key)
                score = max(0.0, min(1.0, (float(score_raw) + 1.0) / 2.0))
                candidate_hits.append({
                    "text": str(c.get('text')), 
                    "source": str(c.get('source')), 
                    "chunk_id": int(c.get('chunk_id')), 
                    "score": score, 
                    "score_raw": float(score_raw)
                })

            # 2. Re-Ranking Pass
            if settings.RERANKING_ENABLED:
                _LOG.info(f"Re-ranking {len(candidate_hits)} candidates for query: {q[:50]}...")
                final_hits = self.reranker.rerank(q, candidate_hits, top_k=top_k)
            else:
                final_hits = candidate_hits[:top_k]
                final_hits.sort(key=lambda h: h["score"], reverse=True)

            # Emit structured event with retrieval sources
            sources = [h.get("source", "unknown") for h in final_hits]
            top_score = final_hits[0].get("score", 0.0) if final_hits else 0.0
            RAG_HIT_COUNT.labels(status="hit" if final_hits else "miss").inc()
            emit_observability_event(
                _LOG, event="rag.retrieve", category="rag",
                duration_ms=vec_elapsed * 1000,
                candidate_count=len(candidate_hits),
                result_count=len(final_hits),
                top_score=round(top_score, 4),
                sources=sources,
            )
            if span:
                span.set_attribute("rag.result_count", len(final_hits))
                span.set_attribute("rag.top_score", top_score)

        return final_hits

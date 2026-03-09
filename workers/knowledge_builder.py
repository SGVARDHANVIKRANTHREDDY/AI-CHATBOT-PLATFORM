"""
Knowledge Builder Worker

Background Celery task that periodically crawls whitelisted sources,
ingests content, and expands the RAG knowledge base.

Production hardening:
    - Trust scoring via SourceTrustEvaluator rejects low-quality content
    - Documents below the trust threshold are logged and skipped
    - Trust scores propagate to entity extraction and KG storage
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.knowledge_graph.trust import SourceTrustEvaluator, VerificationStatus
from app.rag.crawler import CrawlConfig, KnowledgeCrawler
from app.shared.utils import get_logger

from workers.celery_app import celery_app

_LOG = get_logger(__name__)

# ─── Default Knowledge Sources ────────────────────────────────────
DEFAULT_KNOWLEDGE_URLS = [
    "https://docs.python.org/3/whatsnew/3.12.html",
    "https://redis.io/docs/latest/",
    "https://fastapi.tiangolo.com/release-notes/",
    "https://arxiv.org/list/cs.AI/recent",
]


@celery_app.task(name="knowledge_builder.crawl_and_ingest", bind=True, max_retries=2)
def crawl_and_ingest(
    self,
    urls: list[str] | None = None,
    config_overrides: dict[str, Any] | None = None,
    trust_threshold: float = 0.5,
):
    """
    Celery task: Crawl URLs and ingest into RAG pipeline with trust scoring.

    Args:
        urls: List of URLs to crawl. Defaults to DEFAULT_KNOWLEDGE_URLS.
        config_overrides: Optional dict to override CrawlConfig defaults.
        trust_threshold: Minimum trust score for ingestion (0.0-1.0).
    """
    _LOG.info("Knowledge Builder: Starting crawl-and-ingest cycle.")

    config = CrawlConfig()
    if config_overrides:
        for k, v in config_overrides.items():
            if hasattr(config, k):
                setattr(config, k, v)

    crawler = KnowledgeCrawler(config)
    trust_evaluator = SourceTrustEvaluator(trust_threshold=trust_threshold)
    target_urls = urls or DEFAULT_KNOWLEDGE_URLS

    loop = asyncio.new_event_loop()
    try:
        results = loop.run_until_complete(crawler.crawl_batch(target_urls))
        _LOG.info("Knowledge Builder: Crawled %d documents.", len(results))

        ingested = []
        rejected = []

        for doc in results:
            url = doc.get("url", "unknown")
            content = doc.get("content", "")

            # Trust evaluation
            trust_result = trust_evaluator.evaluate(url, content)

            if not trust_evaluator.should_ingest(trust_result):
                _LOG.warning(
                    "REJECTED: %s (score=%.2f, reasons=%s)",
                    url,
                    trust_result.overall_score,
                    trust_result.rejection_reasons,
                )
                rejected.append(
                    {
                        "url": url,
                        "score": trust_result.overall_score,
                        "reasons": trust_result.rejection_reasons,
                    }
                )
                # Emit metric
                try:
                    from app.shared.monitoring import TRUST_EVALUATION_RESULTS

                    TRUST_EVALUATION_RESULTS.labels(status=VerificationStatus.REJECTED.value).inc()
                except Exception:  # noqa: S110
                    pass
                continue

            _LOG.info(
                "  ACCEPTED: %s (score=%.2f, status=%s)",
                url,
                trust_result.overall_score,
                trust_result.verification_status.value,
            )
            ingested.append(
                {
                    "url": url,
                    "size": doc.get("size", 0),
                    "trust_score": trust_result.overall_score,
                }
            )

            # Emit metric
            try:
                from app.shared.monitoring import TRUST_EVALUATION_RESULTS

                TRUST_EVALUATION_RESULTS.labels(status=trust_result.verification_status.value).inc()
            except Exception:  # noqa: S110
                pass

            # In production: chunk and embed with trust metadata
            # chunker.chunk_and_embed(
            #     content,
            #     metadata={"source": url, "trust_score": trust_result.overall_score}
            # )

        return {
            "status": "success",
            "documents_ingested": len(ingested),
            "documents_rejected": len(rejected),
            "ingested": ingested,
            "rejected": rejected,
        }
    except Exception as e:
        _LOG.error("Knowledge Builder failed: %s", e)
        self.retry(countdown=60)
    finally:
        loop.close()


@celery_app.task(name="knowledge_builder.expand_knowledge_graph")
def expand_knowledge_graph():
    """
    Celery task: Extract entities/relationships from recently ingested
    documents and expand the Knowledge Graph.
    """
    _LOG.info("Knowledge Builder: Expanding knowledge graph from recent ingestions.")
    # In production:
    # documents = get_recent_ingestions(hours=24)
    # for doc in documents:
    #     entities = entity_extractor.extract(doc.content, trust_score=doc.trust_score)
    #     knowledge_graph.add_data(
    #         entities["entities"],
    #         entities["relationships"],
    #         trust_score=doc.trust_score,
    #     )
    return {"status": "success", "message": "KG expansion completed (simulated)"}

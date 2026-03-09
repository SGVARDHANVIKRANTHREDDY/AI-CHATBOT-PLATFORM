"""
Vector Maintenance Worker — Scheduled Celery task for FAISS index lifecycle.

Runs VectorMaintenanceManager.run_full_maintenance() on a schedule
(daily by default) to deduplicate, remove stale entries, reindex,
and compress old vectors.
"""

from __future__ import annotations

from typing import Any

from app.shared.utils import get_logger

from workers.celery_app import celery_app

_LOG = get_logger(__name__)


@celery_app.task(
    name="maintenance_worker.run_vector_maintenance",
    bind=True,
    max_retries=1,
    soft_time_limit=600,
    time_limit=660,
)
def run_vector_maintenance(
    self,
    memory_types: list | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Celery task: Run full vector maintenance for all memory types.

    Args:
        memory_types: List of memory types to maintain.
                      Defaults to ["episodic", "semantic", "profile"].
        config_overrides: Optional overrides for VectorMaintenanceManager
                          (e.g. {"stale_days": 60, "dedup_threshold": 0.95}).

    Returns:
        Summary dict with maintenance results per memory type.
    """
    from app.vector_memory.maintenance import VectorMaintenanceManager

    types = memory_types or ["episodic", "semantic", "profile"]
    results: dict[str, Any] = {}

    for mt in types:
        _LOG.info("Running maintenance for memory type: %s", mt)
        try:
            kwargs: dict[str, Any] = {"memory_type": mt}
            if config_overrides:
                kwargs.update(config_overrides)
            manager = VectorMaintenanceManager(**kwargs)
            results[mt] = manager.run_full_maintenance()
        except Exception as e:
            _LOG.error("Maintenance failed for %s: %s", mt, e)
            results[mt] = {"error": str(e)}
            # Don't retry on individual type failure — continue others

    _LOG.info("Vector maintenance complete: %s", results)
    return {"status": "success", "results": results}

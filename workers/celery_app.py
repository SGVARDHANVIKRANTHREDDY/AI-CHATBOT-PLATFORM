from __future__ import annotations

from app.config.settings import settings
from celery import Celery
from celery.schedules import crontab

celery_app = Celery(
    "ai_assistant_workers",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "workers.ingestion_worker",
        "workers.knowledge_builder",
        "workers.maintenance_worker",
        "workers.indexing_worker",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Beat schedule for periodic tasks
    beat_schedule={
        "daily-vector-maintenance": {
            "task": "maintenance_worker.run_vector_maintenance",
            "schedule": crontab(hour=3, minute=0),  # 03:00 UTC daily
            "kwargs": {
                "memory_types": ["episodic", "semantic", "profile"],
            },
        },
        "daily-knowledge-crawl": {
            "task": "knowledge_builder.crawl_and_ingest",
            "schedule": crontab(hour=4, minute=0),  # 04:00 UTC daily
        },
    },
)

if __name__ == "__main__":
    celery_app.start()

import json
import logging
import os
import traceback
from datetime import UTC, datetime

_LOGGING_CONFIGURED = False


class ELKJsonFormatter(logging.Formatter):
    """Structured JSON formatter compatible with ELK / Filebeat / Logstash.

    Produces one JSON object per line with fields that map directly to
    the Elastic Common Schema (ECS) so logs can be ingested without
    extra parsing pipelines.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "@timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "log.level": record.levelname,
            "log.logger": record.name,
            "message": record.getMessage(),
            "service.name": "ai-platform",
        }

        # Attach structured fields passed via `extra={"event_data": {...}}`
        event_data = getattr(record, "event_data", None)
        if isinstance(event_data, dict):
            payload.update(event_data)

        # Attach exception info
        if record.exc_info and record.exc_info[0] is not None:
            payload["error.type"] = record.exc_info[0].__name__
            payload["error.message"] = str(record.exc_info[1])
            payload["error.stack_trace"] = "".join(traceback.format_exception(*record.exc_info))

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(*, level: str | None = None) -> None:
    """Configure global logging once."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    lvl = (level or os.getenv("LOG_LEVEL", "INFO")).strip().upper() or "INFO"
    numeric = getattr(logging, lvl, logging.INFO)
    log_format = (os.getenv("LOG_FORMAT", "json") or "json").strip().lower()

    root = logging.getLogger()
    root.setLevel(numeric)
    # Remove default handlers
    root.handlers.clear()

    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(ELKJsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    root.addHandler(handler)
    _LOGGING_CONFIGURED = True

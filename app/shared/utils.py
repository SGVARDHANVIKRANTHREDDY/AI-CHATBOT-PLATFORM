import logging
import json
import re
import time
from typing import Any, Dict, Optional

def clean_markdown(text: str) -> str:
    if not isinstance(text, str):
        return "[ERROR] Input must be a string."
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'(\*\*|__)(.*?)\1', r'\2', text)
    text = re.sub(r'(\*|_)(.*?)\1', r'\2', text)
    text = re.sub(r'`([^`]*)`', r'\1', text)
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*+]\s+', '- ', text, flags=re.MULTILINE)
    return text.strip()

def get_logger(name: str) -> logging.Logger:
    from app.config.logging_config import configure_logging
    configure_logging()
    return logging.getLogger(name)

def log_event(logger: logging.Logger, event: str, **fields) -> None:
    """Emit a structured JSON event log line (ELK‑compatible).

    All key‑value pairs in *fields* are merged into the JSON payload
    via the ``event_data`` extra key so the ELKJsonFormatter picks
    them up automatically.
    """
    from app.api.middleware.correlation import get_request_id
    event_data: Dict[str, Any] = {
        "event.action": event,
        "trace.id": get_request_id(),
    }
    for k, v in fields.items():
        if v is not None:
            event_data[str(k)] = v
    logger.info(event, extra={"event_data": event_data})


def emit_observability_event(
    logger: logging.Logger,
    *,
    event: str,
    category: str,
    duration_ms: Optional[float] = None,
    **attrs: Any,
) -> None:
    """High‑level structured event for observability dashboards.

    Categories: ``agent``, ``tool``, ``rag``, ``llm``, ``api``, ``prompt``.
    """
    from app.api.middleware.correlation import get_request_id
    event_data: Dict[str, Any] = {
        "event.action": event,
        "event.category": category,
        "trace.id": get_request_id(),
    }
    if duration_ms is not None:
        event_data["event.duration_ms"] = round(duration_ms, 2)
    for k, v in attrs.items():
        if v is not None:
            event_data[str(k)] = v
    logger.info(event, extra={"event_data": event_data})

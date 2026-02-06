"""Structured logging configuration for the Discovery Engine.

Provides:
- ``configure_logging()`` — stdlib-based setup with optional JSON output
- ``RequestIdFilter`` — injects ``request_id`` from contextvars into log records
- ``set_request_id()`` / ``get_request_id()`` — context variable helpers
- ``startup_check()`` — validates environment before app starts
"""

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, List, Optional

# ---------------------------------------------------------------------------
# Context variable for request ID propagation
# ---------------------------------------------------------------------------

_request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def set_request_id(request_id: str):
    """Set the current request ID in context. Returns a token for reset."""
    return _request_id_var.set(request_id)


def get_request_id() -> Optional[str]:
    return _request_id_var.get()


# ---------------------------------------------------------------------------
# Request ID log filter
# ---------------------------------------------------------------------------


class RequestIdFilter(logging.Filter):
    """Inject ``request_id`` from the current context into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()  # type: ignore[attr-defined]
        return True


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Produce one JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "request_id") and record.request_id:
            entry["request_id"] = record.request_id
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


# ---------------------------------------------------------------------------
# Main configuration
# ---------------------------------------------------------------------------


def configure_logging(
    json_format: bool = False,
    stream: Optional[IO] = None,
) -> None:
    """Configure the root logger.

    Parameters
    ----------
    json_format:
        If True, use JSON formatter. Also enabled via ``LOG_FORMAT=json`` env var.
    stream:
        Output stream (defaults to ``sys.stderr``).
    """
    use_json = json_format or os.getenv("LOG_FORMAT", "").lower() == "json"
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(level)
    handler.addFilter(RequestIdFilter())

    if use_json:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root.addHandler(handler)


# ---------------------------------------------------------------------------
# Startup check
# ---------------------------------------------------------------------------


def startup_check(db_path: Optional[str] = None) -> List[str]:
    """Validate critical environment before the app starts.

    Returns a list of warning strings (empty = all good).
    """
    issues: List[str] = []
    path = db_path or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    if not Path(path).exists():
        issues.append(f"Database file not found: {path}")
    return issues

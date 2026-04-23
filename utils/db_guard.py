"""External watermark guard for the production signals DB.

Persist the last-known-good signal count *outside* ``signals.db`` so it
survives the same failure mode that truncated the DB on 2026-04-04.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
WATERMARK_PATH = _REPO_ROOT / ".omx" / "state" / "db_watermark.json"


def load_watermark() -> dict:
    """Reads the watermark file, returns ``{}`` if missing or corrupt."""
    if not WATERMARK_PATH.exists():
        return {}
    try:
        return json.loads(WATERMARK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_watermark(signal_count: int, schema_version: int, timestamp: str) -> None:
    """Persist watermark with signal count, schema version, and timestamp."""
    WATERMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "signal_count": signal_count,
        "schema_version": schema_version,
        "timestamp": timestamp,
    }
    WATERMARK_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_current_signal_count(db_path: str) -> tuple[int | None, str | None]:
    """Return the current signal count or a read error for *db_path*."""
    try:
        conn = sqlite3.connect(
            f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=1
        )
        try:
            count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            return int(count), None
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - surfaced through guard tests
        return None, str(exc)


def check_db_health(db_path: str) -> tuple[bool, str]:
    """Compare current DB signal count against watermark.

    Returns:
        ``(ok, message)`` where *message* is one of:

        - ``"watermark_missing"`` – watermark did not exist and was
          auto-initialized from the current DB count.
        - ``"healthy"`` – current count >= 50 % of watermark count.
        - ``"catastrophic_drop_detected"`` – current count < 50 % of
          watermark count.
        - ``"db_read_error: ..."`` – could not read the DB.
    """
    watermark = load_watermark()
    if not watermark:
        count, error = read_current_signal_count(db_path)
        if error:
            return True, "watermark_missing"
        save_watermark(
            signal_count=count,
            schema_version=0,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        return True, "watermark_missing"

    baseline = watermark.get("signal_count", 0)
    current, error = read_current_signal_count(db_path)
    if error:
        return False, f"db_read_error: {error}"

    if current >= baseline * 0.5:
        return True, "healthy"
    return False, "catastrophic_drop_detected"


def guard_command(
    db_path: str, command_type: str, allow_override: bool = False
) -> bool:
    """Check DB health and allow/deny command based on type.

    Args:
        db_path: Path to the SQLite database.
        command_type: ``"read"`` or ``"write"``.
        allow_override: If ``True``, allows write commands through even
            when the guard is tripped.

    Returns:
        ``True`` if the command should proceed, ``False`` if blocked.
    """
    ok, message = check_db_health(db_path)
    if ok:
        return True

    if command_type == "read":
        logger.warning(
            "DB guard warning (%s): allowing read command on %s",
            message,
            db_path,
        )
        return True

    if command_type == "write":
        if allow_override:
            logger.warning(
                "DB guard AUDIT (%s): allowing write command on %s "
                "with --recovery-override",
                message,
                db_path,
            )
            return True
        logger.error(
            "DB guard blocked (%s): write command on %s denied",
            message,
            db_path,
        )
        return False

    logger.error(
        "DB guard blocked (%s): unknown command_type '%s'",
        message,
        command_type,
    )
    return False

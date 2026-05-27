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
from typing import Any

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


def read_schema_version(db_path: str) -> tuple[int | None, str | None]:
    """Return MAX(schema_migrations.version) or a read error for *db_path*."""
    try:
        conn = sqlite3.connect(
            f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=1
        )
        try:
            row = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()
            if row is None or row[0] is None:
                return None, "schema_migrations_empty"
            return int(row[0]), None
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - surfaced through guard tests
        return None, str(exc)


def sqlite_durability_check(
    db_path: str,
    *,
    min_signals: int = 0,
    expected_schema_version: int | None = None,
    require_watermark: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """Run local SQLite durability checks and optionally enforce watermark health.

    This is a read-only diagnostic companion to the existing DB guard. It does
    not initialize or repair the external watermark; when ``require_watermark``
    is true, ``watermark_missing`` remains a fail-closed state.
    """
    errors: list[str] = []
    evidence: dict[str, Any] = {
        "db_path": str(db_path),
        "integrity_check": None,
        "schema_version": None,
        "signal_count": None,
        "min_signals": min_signals,
        "expected_schema_version": expected_schema_version,
        "watermark": None,
        "errors": errors,
    }

    try:
        conn = sqlite3.connect(
            f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=1
        )
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            integrity = str(row[0]) if row else "missing"
            evidence["integrity_check"] = integrity
            if integrity != "ok":
                errors.append("integrity_check")

            try:
                schema_row = conn.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()
            except sqlite3.OperationalError:
                errors.append("schema_migrations")
                schema_row = None
            if schema_row is not None and schema_row[0] is not None:
                evidence["schema_version"] = int(schema_row[0])
            else:
                errors.append("schema_migrations")

            try:
                signal_row = conn.execute("SELECT COUNT(*) FROM signals").fetchone()
            except sqlite3.OperationalError:
                errors.append("signals")
                signal_row = None
            if signal_row is not None:
                signal_count = int(signal_row[0])
                evidence["signal_count"] = signal_count
                if signal_count < min_signals:
                    errors.append("signal lower bound")
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - exercised by corrupt DB tests
        evidence["read_error"] = str(exc)
        errors.append("db_read_error")

    schema_version = evidence["schema_version"]
    if (
        expected_schema_version is not None
        and schema_version is not None
        and schema_version != expected_schema_version
    ):
        errors.append("schema_version")

    if require_watermark:
        watermark_ok, watermark_message = check_db_health(db_path)
        evidence["watermark"] = {
            "ok": watermark_ok,
            "message": watermark_message,
        }
        if not watermark_ok:
            errors.append(watermark_message)

    return not errors, evidence


def check_db_health(db_path: str) -> tuple[bool, str]:
    """Compare current DB signal count against watermark.

    Strict explicit-init contract (Phase 2 hotfix Day 2.5): a missing watermark
    is reported as unhealthy and is **never** auto-initialized from the live
    DB. The only path that creates ``WATERMARK_PATH`` is the
    ``init-watermark`` operator command (``run_pipeline.py:8117-8132``). See
    ``.omx/wave6/db_guard_runbook.md``.

    Returns:
        ``(ok, message)`` where *message* is one of:

        - ``"watermark_missing"`` – watermark file absent; operator must run
          ``python run_pipeline.py init-watermark``.
        - ``"healthy"`` – current count >= 50 % of watermark count.
        - ``"catastrophic_drop_detected"`` – current count < 50 % of
          watermark count.
        - ``"db_read_error: ..."`` – could not read the DB.
    """
    watermark = load_watermark()
    if not watermark:
        return False, "watermark_missing"

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

    Strict explicit-init contract (Phase 2 hotfix Day 2.5): missing watermark
    blocks writes regardless of ``allow_override``. The override path is
    scoped to ``catastrophic_drop_detected`` — i.e. an existing watermark
    whose baseline has been tripped — and is the documented escape hatch for
    controlled incident response. Bootstrapping a missing watermark is a
    separate operator action (``init-watermark``) that emits an audit record.

    Args:
        db_path: Path to the SQLite database.
        command_type: ``"read"`` or ``"write"``.
        allow_override: If ``True``, allows write commands through when the
            guard is tripped on a catastrophic drop. Has no effect on
            ``watermark_missing`` or ``db_read_error`` states.

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
        if allow_override and message == "catastrophic_drop_detected":
            logger.warning(
                "DB guard AUDIT (%s): allowing write command on %s "
                "with --recovery-override",
                message,
                db_path,
            )
            return True
        if message == "watermark_missing":
            logger.error(
                "DB guard blocked (watermark_missing): write command on %s "
                "denied. Run `python run_pipeline.py init-watermark` to "
                "bootstrap.",
                db_path,
            )
            return False
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

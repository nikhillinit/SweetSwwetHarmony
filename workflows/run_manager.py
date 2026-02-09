"""
Generic run/job abstraction for all async workflows.

Provides a single start/poll/result pattern used by:
- Hunter runs (W3)
- Canary scoring (W2)
- Entity resolution scans (W2)
- ACH builds (W1)

API contract:
    POST /runs       → returns run_id + status "queued"
    GET  /runs/{id}  → poll status (queued → running → completed/failed)
    GET  /runs/{id}/result → retrieve output

DDL lives in storage/migrations/v35_platform_hardening.py
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


# =============================================================================
# MODELS
# =============================================================================

class RunStatus(str, Enum):
    """Lifecycle states for a run."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunType(str, Enum):
    """Types of async workflows tracked as runs."""

    HUNTER = "hunter"
    CANARY = "canary"
    ENTITY_RESOLUTION = "entity_resolution"
    ACH_BUILD = "ach_build"
    BATCH_PUBLISH = "batch_publish"
    DRIFT_CHECK = "drift_check"
    CUSTOM = "custom"


class RunRecord(BaseModel):
    """Stored run record."""

    id: str = Field(..., description="Unique run ID (UUID)")
    run_type: str = Field(..., description="Workflow type")
    status: RunStatus = Field(default=RunStatus.QUEUED)
    actor_id: Optional[str] = Field(default=None, description="Who initiated")
    actor_email: Optional[str] = None
    inputs_summary: Optional[str] = Field(
        default=None, description="JSON summary of run inputs"
    )
    inputs_hash: Optional[str] = Field(
        default=None, description="SHA256[:16] of inputs for reproducibility"
    )
    result: Optional[dict[str, Any]] = Field(
        default=None, description="Run output (populated on completion)"
    )
    error_message: Optional[str] = None
    progress_pct: Optional[int] = Field(
        default=None, ge=0, le=100, description="Progress percentage"
    )
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None
    correlation_id: Optional[str] = None


# =============================================================================
# WRITE OPERATIONS
# =============================================================================

async def create_run(
    store: "SignalStore",
    *,
    run_type: str,
    actor_id: Optional[str] = None,
    actor_email: Optional[str] = None,
    inputs_summary: Optional[dict[str, Any]] = None,
    inputs_hash: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> RunRecord:
    """Create a new run in QUEUED status. Returns the RunRecord."""
    run_id = uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc).isoformat()

    db = store._db
    await db.execute(
        """
        INSERT INTO run_history (
            id, run_type, status,
            actor_id, actor_email,
            inputs_summary, inputs_hash,
            correlation_id,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            run_type,
            RunStatus.QUEUED.value,
            actor_id,
            actor_email,
            json.dumps(inputs_summary) if inputs_summary else None,
            inputs_hash,
            correlation_id,
            now,
        ),
    )
    await db.commit()
    logger.info("Run created: id=%s type=%s", run_id, run_type)

    return RunRecord(
        id=run_id,
        run_type=run_type,
        status=RunStatus.QUEUED,
        actor_id=actor_id,
        actor_email=actor_email,
        inputs_hash=inputs_hash,
        created_at=now,
        correlation_id=correlation_id,
    )


async def start_run(store: "SignalStore", run_id: str) -> None:
    """Transition a run from QUEUED to RUNNING."""
    now = datetime.now(timezone.utc).isoformat()
    db = store._db
    cursor = await db.execute(
        """
        UPDATE run_history
        SET status = ?, started_at = ?
        WHERE id = ? AND status = ?
        """,
        (RunStatus.RUNNING.value, now, run_id, RunStatus.QUEUED.value),
    )
    await db.commit()
    if cursor.rowcount == 0:
        logger.warning("start_run: run %s not in QUEUED state", run_id)


async def complete_run(
    store: "SignalStore",
    run_id: str,
    *,
    result: Optional[dict[str, Any]] = None,
) -> None:
    """Transition a run to COMPLETED with optional result."""
    now = datetime.now(timezone.utc).isoformat()
    db = store._db
    await db.execute(
        """
        UPDATE run_history
        SET status = ?, completed_at = ?, result = ?, progress_pct = 100
        WHERE id = ? AND status = ?
        """,
        (
            RunStatus.COMPLETED.value,
            now,
            json.dumps(result) if result else None,
            run_id,
            RunStatus.RUNNING.value,
        ),
    )
    await db.commit()
    logger.info("Run completed: %s", run_id)


async def fail_run(
    store: "SignalStore",
    run_id: str,
    *,
    error_message: str,
) -> None:
    """Transition a run to FAILED with error details."""
    now = datetime.now(timezone.utc).isoformat()
    db = store._db
    await db.execute(
        """
        UPDATE run_history
        SET status = ?, completed_at = ?, error_message = ?
        WHERE id = ? AND status IN (?, ?)
        """,
        (
            RunStatus.FAILED.value,
            now,
            error_message,
            run_id,
            RunStatus.QUEUED.value,
            RunStatus.RUNNING.value,
        ),
    )
    await db.commit()
    logger.warning("Run failed: %s — %s", run_id, error_message)


async def cancel_run(store: "SignalStore", run_id: str) -> None:
    """Transition a queued/running run to CANCELLED."""
    now = datetime.now(timezone.utc).isoformat()
    db = store._db
    await db.execute(
        """
        UPDATE run_history
        SET status = ?, completed_at = ?
        WHERE id = ? AND status IN (?, ?)
        """,
        (
            RunStatus.CANCELLED.value,
            now,
            run_id,
            RunStatus.QUEUED.value,
            RunStatus.RUNNING.value,
        ),
    )
    await db.commit()


async def update_progress(
    store: "SignalStore", run_id: str, progress_pct: int
) -> None:
    """Update progress percentage for a running run."""
    db = store._db
    await db.execute(
        "UPDATE run_history SET progress_pct = ? WHERE id = ?",
        (min(max(progress_pct, 0), 100), run_id),
    )
    await db.commit()


# =============================================================================
# READ OPERATIONS
# =============================================================================

async def get_run(store: "SignalStore", run_id: str) -> Optional[RunRecord]:
    """Fetch a run by ID."""
    db = store._db
    cursor = await db.execute(
        """
        SELECT id, run_type, status,
               actor_id, actor_email,
               inputs_summary, inputs_hash,
               result, error_message, progress_pct,
               started_at, completed_at, created_at,
               correlation_id
        FROM run_history WHERE id = ?
        """,
        (run_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return _row_to_record(row)


async def list_runs(
    store: "SignalStore",
    *,
    run_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> list[RunRecord]:
    """List runs with optional filters, newest first."""
    conditions: list[str] = []
    params: list[Any] = []

    if run_type:
        conditions.append("run_type = ?")
        params.append(run_type)
    if status:
        conditions.append("status = ?")
        params.append(status)

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    db = store._db
    cursor = await db.execute(
        f"""
        SELECT id, run_type, status,
               actor_id, actor_email,
               inputs_summary, inputs_hash,
               result, error_message, progress_pct,
               started_at, completed_at, created_at,
               correlation_id
        FROM run_history
        WHERE {where}
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
        """,
        params,
    )
    rows = await cursor.fetchall()
    return [_row_to_record(row) for row in rows]


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _parse_json(val: Any) -> Optional[dict]:
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


def _row_to_record(row) -> RunRecord:
    return RunRecord(
        id=row[0],
        run_type=row[1],
        status=RunStatus(row[2]),
        actor_id=row[3],
        actor_email=row[4],
        inputs_summary=row[5],  # stored as JSON string, leave as-is for summary display
        inputs_hash=row[6],
        result=_parse_json(row[7]),
        error_message=row[8],
        progress_pct=row[9],
        started_at=row[10],
        completed_at=row[11],
        created_at=row[12],
        correlation_id=row[13],
    )

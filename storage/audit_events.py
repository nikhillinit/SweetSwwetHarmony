"""
Immutable audit event log for the Discovery Engine.

Enhanced event schema beyond the basic v27 audit_log:
- who: operator identity (user_id, email, role)
- when: ISO 8601 UTC timestamp
- what: action_type + entity_type + entity_id
- before/after: JSON snapshots of changed state
- reason: operator-supplied justification
- correlation_id: X-Request-ID for tracing across API calls

Events are INSERT-only — never updated or deleted.

DDL lives in storage/migrations/v35_platform_hardening.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


# =============================================================================
# EVENT MODEL
# =============================================================================

class AuditEvent(BaseModel):
    """Immutable audit event — one per state-changing action."""

    id: Optional[int] = None
    action_type: str = Field(
        ...,
        description="Verb describing the action, e.g. 'triage_approve', 'entity_merge'",
    )
    entity_type: str = Field(
        ..., description="Type of entity acted upon, e.g. 'signal', 'company', 'batch'"
    )
    entity_id: str = Field(..., description="ID of the affected entity")
    actor_id: str = Field(..., description="User ID or system identifier")
    actor_email: Optional[str] = Field(default=None, description="Actor email")
    actor_role: Optional[str] = Field(
        default=None, description="Actor role at time of action"
    )
    before_state: Optional[dict[str, Any]] = Field(
        default=None, description="Snapshot of relevant state before action"
    )
    after_state: Optional[dict[str, Any]] = Field(
        default=None, description="Snapshot of relevant state after action"
    )
    reason: Optional[str] = Field(
        default=None, description="Operator-supplied justification"
    )
    correlation_id: Optional[str] = Field(
        default=None, description="X-Request-ID for cross-call tracing"
    )
    metadata: Optional[dict[str, Any]] = Field(
        default=None, description="Action-specific metadata"
    )
    created_at: Optional[str] = Field(
        default=None, description="ISO 8601 UTC (auto-set on insert)"
    )


# =============================================================================
# WRITE OPERATIONS
# =============================================================================

async def insert_event(
    conn,
    *,
    action_type: str,
    entity_type: str,
    entity_id: str,
    actor_id: str,
    actor_email: Optional[str] = None,
    actor_role: Optional[str] = None,
    before_state: Optional[dict[str, Any]] = None,
    after_state: Optional[dict[str, Any]] = None,
    reason: Optional[str] = None,
    correlation_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> int:
    """Insert an audit event within an existing transaction.

    Unlike record_event(), this does NOT commit — the caller owns
    the transaction lifecycle. Use inside store.transaction_immediate()
    or store.transaction() blocks.

    Args:
        conn: The aiosqlite.Connection from a transaction context manager.
        (all other args match record_event)

    Returns:
        The auto-generated event ID.
    """
    now = datetime.now(timezone.utc).isoformat()
    cursor = await conn.execute(
        """
        INSERT INTO audit_events (
            action_type, entity_type, entity_id,
            actor_id, actor_email, actor_role,
            before_state, after_state,
            reason, correlation_id, metadata,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            action_type,
            entity_type,
            entity_id,
            actor_id,
            actor_email,
            actor_role,
            json.dumps(before_state) if before_state else None,
            json.dumps(after_state) if after_state else None,
            reason,
            correlation_id,
            json.dumps(metadata) if metadata else None,
            now,
        ),
    )
    event_id = cursor.lastrowid
    logger.debug(
        "Audit event %d (tx-aware): %s %s/%s by %s",
        event_id,
        action_type,
        entity_type,
        entity_id,
        actor_email or actor_id,
    )
    return event_id


async def record_event(
    store: "SignalStore",
    *,
    action_type: str,
    entity_type: str,
    entity_id: str,
    actor_id: str,
    actor_email: Optional[str] = None,
    actor_role: Optional[str] = None,
    before_state: Optional[dict[str, Any]] = None,
    after_state: Optional[dict[str, Any]] = None,
    reason: Optional[str] = None,
    correlation_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> int:
    """Insert an immutable audit event (auto-commits).

    For use outside of an explicit transaction. If you need to insert
    an event within an existing transaction, use insert_event() instead.

    Returns the auto-generated event ID.
    """
    db = store._db
    event_id = await insert_event(
        db,
        action_type=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        actor_email=actor_email,
        actor_role=actor_role,
        before_state=before_state,
        after_state=after_state,
        reason=reason,
        correlation_id=correlation_id,
        metadata=metadata,
    )
    await db.commit()
    return event_id


async def record_event_from_context(
    store: "SignalStore",
    *,
    action_type: str,
    entity_type: str,
    entity_id: str,
    operator: Any,
    before_state: Optional[dict[str, Any]] = None,
    after_state: Optional[dict[str, Any]] = None,
    reason: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> int:
    """Insert an audit event using an OperatorContext object.

    Convenience wrapper around record_event() for use in API handlers.
    """
    return await record_event(
        store,
        action_type=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=operator.user_id,
        actor_email=operator.email,
        actor_role=operator.role.value if hasattr(operator.role, "value") else str(operator.role),
        before_state=before_state,
        after_state=after_state,
        reason=reason,
        correlation_id=getattr(operator, "request_id", None),
        metadata=metadata,
    )


# =============================================================================
# READ OPERATIONS
# =============================================================================

async def get_events(
    store: "SignalStore",
    *,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    action_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
) -> list[AuditEvent]:
    """Query audit events with optional filters.

    Returns newest-first.
    """
    conditions: list[str] = []
    params: list[Any] = []

    if entity_type:
        conditions.append("entity_type = ?")
        params.append(entity_type)
    if entity_id:
        conditions.append("entity_id = ?")
        params.append(entity_id)
    if action_type:
        conditions.append("action_type = ?")
        params.append(action_type)
    if actor_id:
        conditions.append("actor_id = ?")
        params.append(actor_id)
    if since:
        conditions.append("created_at >= ?")
        params.append(since)

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    db = store._db
    cursor = await db.execute(
        f"""
        SELECT id, action_type, entity_type, entity_id,
               actor_id, actor_email, actor_role,
               before_state, after_state,
               reason, correlation_id, metadata,
               created_at
        FROM audit_events
        WHERE {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        params,
    )
    rows = await cursor.fetchall()
    return [_row_to_event(row) for row in rows]


async def get_event_by_id(
    store: "SignalStore", event_id: int
) -> Optional[AuditEvent]:
    """Fetch a single audit event by ID."""
    db = store._db
    cursor = await db.execute(
        """
        SELECT id, action_type, entity_type, entity_id,
               actor_id, actor_email, actor_role,
               before_state, after_state,
               reason, correlation_id, metadata,
               created_at
        FROM audit_events
        WHERE id = ?
        """,
        (event_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return _row_to_event(row)


async def count_events(
    store: "SignalStore",
    *,
    action_type: Optional[str] = None,
    since: Optional[str] = None,
) -> int:
    """Count audit events matching filters."""
    conditions: list[str] = []
    params: list[Any] = []

    if action_type:
        conditions.append("action_type = ?")
        params.append(action_type)
    if since:
        conditions.append("created_at >= ?")
        params.append(since)

    where = " AND ".join(conditions) if conditions else "1=1"

    db = store._db
    cursor = await db.execute(
        f"SELECT COUNT(*) FROM audit_events WHERE {where}", params
    )
    row = await cursor.fetchone()
    return row[0] if row else 0


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _row_to_event(row) -> AuditEvent:
    """Convert a DB row to AuditEvent."""

    def _parse_json(val):
        if val is None:
            return None
        if isinstance(val, dict):
            return val
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return None

    return AuditEvent(
        id=row[0],
        action_type=row[1],
        entity_type=row[2],
        entity_id=row[3],
        actor_id=row[4],
        actor_email=row[5],
        actor_role=row[6],
        before_state=_parse_json(row[7]),
        after_state=_parse_json(row[8]),
        reason=row[9],
        correlation_id=row[10],
        metadata=_parse_json(row[11]),
        created_at=row[12],
    )

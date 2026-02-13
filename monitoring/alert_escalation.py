"""Alert Escalation Workflow — CAS-based state machine (Wave 5).

Manages drift alert lifecycle transitions:
  open → acknowledged → snoozed → resolved
  open → snoozed → open (auto-reopen)
  open → resolved

All transitions use CAS (Compare-And-Swap) via conditional UPDATE for
concurrency safety. Idempotent: repeated identical transition returns
success without duplicate audit events (D10).

Design decisions:
- D10: Idempotent retries (already in target state → 200, no duplicate audit)
- D14: snoozed_until as ISO 8601 TEXT with validation
- D27: CANARY_RUN permission for all mutations
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

# Valid transitions: source_state → set of allowed target_states
VALID_TRANSITIONS = {
    "open": {"acknowledged", "snoozed", "resolved"},
    "acknowledged": {"snoozed", "resolved"},
    "snoozed": {"open", "resolved"},
}
# resolved → nothing (terminal)


@dataclass
class TransitionResult:
    """Result of a state transition attempt."""
    success: bool
    alert_id: int
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    idempotent: bool = False  # True if already in target state (D10)
    error: Optional[str] = None


async def _read_alert(store: "SignalStore", alert_id: int) -> Optional[dict]:
    """Read current alert state."""
    db = store._db
    cursor = await db.execute(
        "SELECT id, status, acknowledged_by, acknowledged_at, resolved_by, "
        "resolved_at, resolution, snoozed_until, snooze_count FROM canary_drift_alerts WHERE id = ?",
        (alert_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "status": row[1], "acknowledged_by": row[2],
        "acknowledged_at": row[3], "resolved_by": row[4],
        "resolved_at": row[5], "resolution": row[6],
        "snoozed_until": row[7], "snooze_count": row[8],
    }


async def _record_transition_audit(
    store: "SignalStore",
    alert_id: int,
    action_type: str,
    actor_id: str,
    old_status: str,
    new_status: str,
    reason: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Record an audit event for the transition."""
    from storage.audit_events import record_event
    await record_event(
        store,
        action_type=action_type,
        entity_type="canary_drift_alert",
        entity_id=str(alert_id),
        actor_id=actor_id,
        before_state={"status": old_status},
        after_state={"status": new_status},
        reason=reason,
        metadata=metadata,
    )


async def acknowledge_alert(
    store: "SignalStore",
    alert_id: int,
    operator: str,
    reason: str,
) -> TransitionResult:
    """Acknowledge an open alert.

    CAS: WHERE id=? AND status IN ('open')
    """
    db = store._db
    now = datetime.now(timezone.utc).isoformat()

    cursor = await db.execute(
        "UPDATE canary_drift_alerts SET status='acknowledged', "
        "acknowledged_by=?, acknowledged_at=? "
        "WHERE id=? AND status IN ('open')",
        (operator, now, alert_id),
    )
    await db.commit()

    if cursor.rowcount == 1:
        await _record_transition_audit(
            store, alert_id, "alert_acknowledged", operator,
            "open", "acknowledged", reason=reason,
        )
        return TransitionResult(success=True, alert_id=alert_id,
                                old_status="open", new_status="acknowledged")

    # CAS missed — check current state (D10)
    alert = await _read_alert(store, alert_id)
    if alert is None:
        return TransitionResult(success=False, alert_id=alert_id,
                                error="Alert not found")
    if alert["status"] == "acknowledged":
        return TransitionResult(success=True, alert_id=alert_id,
                                old_status="acknowledged", new_status="acknowledged",
                                idempotent=True)
    return TransitionResult(success=False, alert_id=alert_id,
                            old_status=alert["status"], new_status="acknowledged",
                            error=f"Cannot transition from {alert['status']} to acknowledged")


async def snooze_alert(
    store: "SignalStore",
    alert_id: int,
    operator: str,
    hours: int,
    reason: Optional[str] = None,
) -> TransitionResult:
    """Snooze an alert for N hours.

    CAS: WHERE id=? AND status IN ('open','acknowledged')
    Validates 1 ≤ hours ≤ 168.
    """
    if hours < 1 or hours > 168:
        return TransitionResult(success=False, alert_id=alert_id,
                                error=f"Snooze hours must be 1-168, got {hours}")

    db = store._db
    now = datetime.now(timezone.utc)
    snoozed_until = (now + timedelta(hours=hours)).isoformat()

    cursor = await db.execute(
        "UPDATE canary_drift_alerts SET status='snoozed', "
        "snoozed_until=?, snooze_count=snooze_count+1 "
        "WHERE id=? AND status IN ('open','acknowledged')",
        (snoozed_until, alert_id),
    )
    await db.commit()

    if cursor.rowcount == 1:
        # Read what the old status was for audit
        alert = await _read_alert(store, alert_id)
        await _record_transition_audit(
            store, alert_id, "alert_snoozed", operator,
            "open", "snoozed", reason=reason,
            metadata={"snooze_hours": hours, "snoozed_until": snoozed_until},
        )
        return TransitionResult(success=True, alert_id=alert_id,
                                old_status="open", new_status="snoozed")

    # CAS missed — check current state (D10)
    alert = await _read_alert(store, alert_id)
    if alert is None:
        return TransitionResult(success=False, alert_id=alert_id,
                                error="Alert not found")
    if alert["status"] == "snoozed":
        return TransitionResult(success=True, alert_id=alert_id,
                                old_status="snoozed", new_status="snoozed",
                                idempotent=True)
    return TransitionResult(success=False, alert_id=alert_id,
                            old_status=alert["status"], new_status="snoozed",
                            error=f"Cannot transition from {alert['status']} to snoozed")


async def resolve_alert(
    store: "SignalStore",
    alert_id: int,
    operator: str,
    resolution: str,
) -> TransitionResult:
    """Resolve an alert.

    CAS: WHERE id=? AND status IN ('open','acknowledged','snoozed')
    """
    db = store._db
    now = datetime.now(timezone.utc).isoformat()

    cursor = await db.execute(
        "UPDATE canary_drift_alerts SET status='resolved', "
        "resolved_by=?, resolved_at=?, resolution=? "
        "WHERE id=? AND status IN ('open','acknowledged','snoozed')",
        (operator, now, resolution, alert_id),
    )
    await db.commit()

    if cursor.rowcount == 1:
        await _record_transition_audit(
            store, alert_id, "alert_resolved", operator,
            "open", "resolved", reason=resolution,
        )
        return TransitionResult(success=True, alert_id=alert_id,
                                old_status="open", new_status="resolved")

    # CAS missed (D10)
    alert = await _read_alert(store, alert_id)
    if alert is None:
        return TransitionResult(success=False, alert_id=alert_id,
                                error="Alert not found")
    if alert["status"] == "resolved":
        return TransitionResult(success=True, alert_id=alert_id,
                                old_status="resolved", new_status="resolved",
                                idempotent=True)
    return TransitionResult(success=False, alert_id=alert_id,
                            old_status=alert["status"], new_status="resolved",
                            error=f"Cannot transition from {alert['status']} to resolved")


async def auto_reopen_expired_snoozes(store: "SignalStore") -> int:
    """Reopen alerts whose snooze has expired.

    WHERE status='snoozed' AND snoozed_until < now
    Uses idx_cda_snooze_reopen index (D20).

    Returns count of reopened alerts.
    """
    db = store._db
    now = datetime.now(timezone.utc).isoformat()

    cursor = await db.execute(
        "UPDATE canary_drift_alerts SET status='open', snoozed_until=NULL "
        "WHERE status='snoozed' AND snoozed_until < ?",
        (now,),
    )
    count = cursor.rowcount
    await db.commit()

    if count > 0:
        logger.info("Auto-reopened %d expired snoozed alerts", count)

    return count


async def compute_mtta(store: "SignalStore", lookback_days: int = 30) -> dict:
    """Compute Mean Time To Acknowledge (MTTA) statistics.

    Returns dict with mean, p50, p95 in seconds. NULL if no acknowledged alerts.
    """
    db = store._db
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    cursor = await db.execute(
        """SELECT
            (julianday(acknowledged_at) - julianday(created_at)) * 86400.0 AS tta_seconds
        FROM canary_drift_alerts
        WHERE acknowledged_at IS NOT NULL AND created_at >= ?
        ORDER BY tta_seconds""",
        (since,),
    )
    rows = await cursor.fetchall()

    if not rows:
        return {"mean": None, "p50": None, "p95": None, "count": 0}

    ttas = [r[0] for r in rows if r[0] is not None and r[0] >= 0]
    if not ttas:
        return {"mean": None, "p50": None, "p95": None, "count": 0}

    n = len(ttas)
    mean = sum(ttas) / n
    p50 = ttas[n // 2]
    p95_idx = min(int(n * 0.95), n - 1)
    p95 = ttas[p95_idx]

    return {"mean": round(mean, 1), "p50": round(p50, 1),
            "p95": round(p95, 1), "count": n}

"""Governance event writer — the ONLY legal way to write governance events.

All governance events (feature_promote, regret_check, feature_demote)
MUST flow through these functions. They:
1. Validate metadata against contracts.py (Pydantic — fails fast)
2. Delegate to insert_event() (tx-aware) or record_event() (standalone)

Usage:
    # Inside an existing transaction:
    async with store.transaction_immediate() as tx:
        await record_feature_promote(tx, operator, ...)

    # Standalone (auto-commit):
    await record_feature_promote(store, operator, ...)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING, Union

import aiosqlite

from governance.contracts import (
    FeatureDemoteMetadata,
    FeatureEvalMetadata,
    FeaturePromoteMetadata,
    RegretCheckMetadata,
)
from storage.audit_events import insert_event, record_event

if TYPE_CHECKING:
    from api.auth.rbac import OperatorContext
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


async def record_feature_promote(
    store_or_conn: Union["SignalStore", aiosqlite.Connection],
    operator: "OperatorContext",
    *,
    feature_name: str,
    from_state: str,
    to_state: str,
    regret_due_at: str,
    reason: str,
    config_snapshot_hash: str,
    config_snapshot_flags: Optional[Dict[str, Any]] = None,
) -> int:
    """Record a feature promotion event.

    Validates metadata via FeaturePromoteMetadata contract before insert.
    Raises pydantic.ValidationError if metadata is invalid (no DB write).
    """
    contract = FeaturePromoteMetadata(
        feature_name=feature_name,
        from_state=from_state,
        to_state=to_state,
        regret_due_at=regret_due_at,
        config_snapshot_hash=config_snapshot_hash,
        config_snapshot_flags=config_snapshot_flags,
    )
    return await _write_event(
        store_or_conn,
        operator=operator,
        action_type="feature_promote",
        entity_type="feature_flag",
        entity_id=feature_name,
        reason=reason,
        metadata=contract.model_dump(),
    )


async def record_regret_check(
    store_or_conn: Union["SignalStore", aiosqlite.Connection],
    operator: "OperatorContext",
    *,
    feature_name: str,
    verdict: str,
    canary_verdict: str,
    drift_status: str,
    reason: str,
    window_days: int = 14,
) -> int:
    """Record a regret check event.

    Validates metadata via RegretCheckMetadata contract before insert.
    """
    contract = RegretCheckMetadata(
        verdict=verdict,
        canary_verdict=canary_verdict,
        drift_status=drift_status,
        window_days=window_days,
    )
    return await _write_event(
        store_or_conn,
        operator=operator,
        action_type="regret_check",
        entity_type="feature_flag",
        entity_id=feature_name,
        reason=reason,
        metadata=contract.model_dump(),
    )


async def record_feature_demote(
    store_or_conn: Union["SignalStore", aiosqlite.Connection],
    operator: "OperatorContext",
    *,
    feature_name: str,
    from_state: str,
    to_state: str,
    reason: str,
    rollback_ticket: Optional[str] = None,
    incident_id: Optional[str] = None,
) -> int:
    """Record a feature demotion event.

    Validates metadata via FeatureDemoteMetadata contract before insert.
    """
    contract = FeatureDemoteMetadata(
        from_state=from_state,
        to_state=to_state,
        rollback_ticket=rollback_ticket,
        incident_id=incident_id,
    )
    return await _write_event(
        store_or_conn,
        operator=operator,
        action_type="feature_demote",
        entity_type="feature_flag",
        entity_id=feature_name,
        reason=reason,
        metadata=contract.model_dump(),
    )


async def record_evaluation_result(
    store_or_conn: Union["SignalStore", aiosqlite.Connection],
    operator: "OperatorContext",
    *,
    feature_name: str,
    result: Dict[str, Any],
    reason: str,
) -> int:
    """Record a feature evaluation result (advisory, no DB trigger)."""
    contract = FeatureEvalMetadata(
        recommendation=result.get("recommendation", "unknown"),
        decision_reason=result.get("decision_reason", ""),
        n_entities_evaluated=result.get("n_entities_evaluated", 0),
        n_time_slices=result.get("n_time_slices", 0),
    )
    return await _write_event(
        store_or_conn,
        operator=operator,
        action_type="feature_eval_completed",
        entity_type="feature_flag",
        entity_id=feature_name,
        reason=reason,
        metadata={**contract.model_dump(), **result},
    )


async def _write_event(
    store_or_conn: Union["SignalStore", aiosqlite.Connection],
    *,
    operator: "OperatorContext",
    action_type: str,
    entity_type: str,
    entity_id: str,
    reason: str,
    metadata: Dict[str, Any],
) -> int:
    """Internal dispatcher: tx-aware (conn) vs auto-commit (store)."""
    actor_role = (
        operator.role.value
        if hasattr(operator.role, "value")
        else str(operator.role)
    )

    kwargs = dict(
        action_type=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=operator.user_id,
        actor_email=operator.email,
        actor_role=actor_role,
        reason=reason,
        correlation_id=getattr(operator, "request_id", None),
        metadata=metadata,
    )

    if isinstance(store_or_conn, aiosqlite.Connection):
        event_id = await insert_event(store_or_conn, **kwargs)
    else:
        event_id = await record_event(store_or_conn, **kwargs)

    logger.info(
        "Governance event %d: %s %s by %s",
        event_id, action_type, entity_id,
        operator.email or operator.user_id,
    )
    return event_id

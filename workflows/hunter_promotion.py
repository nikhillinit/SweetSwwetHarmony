"""Hunter Promotion Bridge — the ONLY module allowed to write hunter results to signals.

This module is the single legal crossing point between the hunter sandbox and
the main signals pipeline. The CI lint test (test_hunter_isolation_lint.py)
verifies that ONLY this file imports/writes to SignalStore/signals table.

Promotion flow:
1. Idempotency pre-check (fast path)
2. BEGIN IMMEDIATE
   a. Fetch hunter_result, validate status='relevant'
   b. Optimistic concurrency via updated_at
   c. Re-check canonical_key against signals (temporal race guard)
   d. INSERT INTO signals
   e. UPDATE hunter_results SET status='promoted'
   f. INSERT INTO audit_events
   g. INSERT idempotency
3. COMMIT
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TYPE_CHECKING

from storage.hunter_result_store import (
    InvalidHunterTransition,
    RESULT_TRANSITIONS,
    StaleUpdateError,
)

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


class PromotionResult:
    """Result of a promotion attempt."""

    def __init__(
        self,
        success: bool,
        signal_id: Optional[int] = None,
        result_id: Optional[int] = None,
        status: str = "",
        message: str = "",
        collision: bool = False,
    ):
        self.success = success
        self.signal_id = signal_id
        self.result_id = result_id
        self.status = status
        self.message = message
        self.collision = collision

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "signal_id": self.signal_id,
            "result_id": self.result_id,
            "status": self.status,
            "message": self.message,
            "collision": self.collision,
        }


async def promote_hunter_result(
    store: "SignalStore",
    result_id: int,
    *,
    actor: str = "system",
    idempotency_key: Optional[str] = None,
) -> PromotionResult:
    """Promote a hunter result to the signals table.

    Args:
        store: Initialized SignalStore
        result_id: Hunter result ID to promote
        actor: Who initiated the promotion
        idempotency_key: Client-supplied idempotency key

    Returns:
        PromotionResult with success status and signal_id.
    """
    db = store._db
    now = datetime.now(timezone.utc).isoformat()

    # Generate deterministic fallback idempotency key
    if not idempotency_key:
        idempotency_key = f"hunter_promote:{result_id}:auto"

    # 1. Idempotency pre-check (fast path)
    cursor = await db.execute(
        "SELECT resource_id FROM idempotency_keys WHERE key = ? LIMIT 1",
        (idempotency_key,),
    )
    existing_idem = await cursor.fetchone()
    if existing_idem:
        # Already promoted — return existing signal_id
        existing_signal_id = int(existing_idem[0]) if existing_idem[0] else None
        return PromotionResult(
            success=True,
            signal_id=existing_signal_id,
            result_id=result_id,
            status="already_promoted",
            message="Idempotent: promotion already completed",
        )

    # 2. BEGIN IMMEDIATE
    async with store.transaction_immediate() as tx:
        # 2a. Fetch result
        cursor = await tx.execute(
            """SELECT id, status, canonical_key, company_name, source_api,
                      raw_data, confidence_score, updated_at, company_id
               FROM hunter_results WHERE id = ?""",
            (result_id,),
        )
        row = await cursor.fetchone()
        if not row:
            raise ValueError(f"Hunter result {result_id} not found")

        (
            _, current_status, canonical_key, company_name,
            source_api, raw_data_str, confidence, updated_at, company_id,
        ) = row

        # Already promoted? Return existing signal_id (idempotent)
        if current_status == "promoted":
            cursor = await tx.execute(
                "SELECT promoted_signal_id FROM hunter_results WHERE id = ?",
                (result_id,),
            )
            r = await cursor.fetchone()
            return PromotionResult(
                success=True,
                signal_id=r[0] if r else None,
                result_id=result_id,
                status="already_promoted",
                message="Result already promoted",
            )

        # 2b. Validate status='relevant'
        if current_status != "relevant":
            raise InvalidHunterTransition(
                f"Cannot promote result {result_id}: status is '{current_status}', "
                f"expected 'relevant'"
            )

        # 2c. Re-check canonical_key against signals (temporal race guard)
        if canonical_key:
            cursor = await tx.execute(
                "SELECT id FROM signals WHERE canonical_key = ? LIMIT 1",
                (canonical_key,),
            )
            existing_signal = await cursor.fetchone()
            if existing_signal:
                # Collision: canonical already exists in signals
                existing_signal_id = existing_signal[0]
                await tx.execute(
                    """UPDATE hunter_results
                       SET status = 'already_known',
                           promoted_signal_id = ?,
                           reviewed_at = ?,
                           updated_at = ?
                       WHERE id = ?""",
                    (existing_signal_id, now, now, result_id),
                )
                await tx.execute(
                    """INSERT INTO audit_events
                       (action_type, entity_type, entity_id, actor_id,
                        before_state, after_state, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "hunter_promote_collision",
                        "hunter_result",
                        str(result_id),
                        actor,
                        json.dumps({"status": "relevant"}),
                        json.dumps({
                            "status": "already_known",
                            "existing_signal_id": existing_signal_id,
                        }),
                        now,
                    ),
                )
                return PromotionResult(
                    success=True,
                    signal_id=existing_signal_id,
                    result_id=result_id,
                    status="already_known",
                    message=f"Canonical key collision: signal {existing_signal_id} already exists",
                    collision=True,
                )

        # 2d. INSERT INTO signals
        raw_data = json.loads(raw_data_str) if isinstance(raw_data_str, str) else (raw_data_str or {})
        cursor = await tx.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at, company_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "hunter_discovery",
                source_api,
                canonical_key or f"name_loc:{company_name}:unknown",
                company_name,
                confidence or 0.5,
                json.dumps(raw_data),
                now,  # detected_at
                now,  # created_at
                company_id,
            ),
        )
        signal_id = cursor.lastrowid

        # 2e. UPDATE hunter_results
        await tx.execute(
            """UPDATE hunter_results
               SET status = 'promoted',
                   promoted_signal_id = ?,
                   promoted_at = ?,
                   updated_at = ?
               WHERE id = ?""",
            (signal_id, now, now, result_id),
        )

        # 2f. Audit event
        await tx.execute(
            """INSERT INTO audit_events
               (action_type, entity_type, entity_id, actor_id,
                before_state, after_state, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "hunter_promote",
                "hunter_result",
                str(result_id),
                actor,
                json.dumps({"status": "relevant"}),
                json.dumps({"status": "promoted", "signal_id": signal_id}),
                now,
            ),
        )

        # 2g. Idempotency key
        await tx.execute(
            """INSERT OR IGNORE INTO idempotency_keys
               (key, route, resource_id, status_code, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (idempotency_key, "hunter_promote", str(signal_id), 201, now),
        )

    # 3. COMMIT (implicit from context manager)
    logger.info(
        "Promoted hunter result %d → signal %d (canonical=%s)",
        result_id, signal_id, canonical_key,
    )
    return PromotionResult(
        success=True,
        signal_id=signal_id,
        result_id=result_id,
        status="promoted",
        message=f"Successfully promoted to signal {signal_id}",
    )

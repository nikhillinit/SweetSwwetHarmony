"""
Phase G Entity Resolution Readiness Checker.

Separate from activation_gate.py (which stays integer steps 1-4).
Evaluates whether the Phase G entity identity system is ready for activation.

Checks:
- entity_tables_present: entity_aliases, entity_migrations, entity_key_aliases exist
- blocking_index_populated: entity_blocking_index has >0 rows (shadow data exists)
- shadow_merge_quality: merge_suggestions rejection rate <10%
- no_orphaned_entities: all entity_ids in signals resolve to valid roots
- claim_facts_consistent: claim_facts has no contradictions (if USE_CLAIM_FACTS)

Usage:
    from monitoring.phase_g_readiness import check_phase_g_readiness
    result = await check_phase_g_readiness(store)
    print(result.verdict)  # "ready" | "warn" | "blocked"
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

REQUIRED_TABLES = [
    "entity_aliases",
    "entity_migrations",
    "entity_key_aliases",
    "entity_blocking_index",
]

CLAIM_FACTS_TABLE = "claim_facts"

# Rejection rate above this blocks activation
MAX_REJECTION_RATE = 0.10


@dataclass
class PhaseGReadinessResult:
    verdict: str  # "ready" | "warn" | "blocked"
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    checked_at: str = ""

    @property
    def can_proceed(self) -> bool:
        return self.verdict in ("ready", "warn")

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reasons": self.reasons,
            "can_proceed": self.can_proceed,
            "metrics": self.metrics,
            "checked_at": self.checked_at,
        }


async def check_phase_g_readiness(store) -> PhaseGReadinessResult:
    """Check whether Phase G entity resolution is ready to activate.

    Args:
        store: SignalStore instance with initialized DB.

    Returns:
        PhaseGReadinessResult with verdict, reasons, and metrics.
    """
    result = PhaseGReadinessResult(
        verdict="ready",
        checked_at=datetime.now(timezone.utc).isoformat(),
    )

    db = store._db

    # -----------------------------------------------------------------------
    # 1. entity_tables_present
    # -----------------------------------------------------------------------
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ) as cursor:
        existing = {row[0] for row in await cursor.fetchall()}

    missing = [t for t in REQUIRED_TABLES if t not in existing]
    result.metrics["tables_present"] = len(REQUIRED_TABLES) - len(missing)
    result.metrics["tables_required"] = len(REQUIRED_TABLES)

    if missing:
        result.verdict = "blocked"
        result.reasons.append(f"Missing tables: {', '.join(missing)}")
        # If tables are missing, skip remaining checks
        return result

    # -----------------------------------------------------------------------
    # 2. blocking_index_populated
    # -----------------------------------------------------------------------
    async with db.execute(
        "SELECT COUNT(*) FROM entity_blocking_index"
    ) as cursor:
        blocking_count = (await cursor.fetchone())[0]

    result.metrics["blocking_index_rows"] = blocking_count

    if blocking_count == 0:
        if result.verdict != "blocked":
            result.verdict = "warn"
        result.reasons.append(
            "Blocking index empty — no shadow entity resolution data yet"
        )

    # -----------------------------------------------------------------------
    # 3. shadow_merge_quality
    # -----------------------------------------------------------------------
    if "merge_suggestions" in existing:
        async with db.execute("""
            SELECT
                COUNT(*) as total,
                COALESCE(SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END), 0) as rejected
            FROM merge_suggestions
        """) as cursor:
            row = await cursor.fetchone()
            total, rejected = row[0], row[1]

        result.metrics["merge_suggestions_total"] = total
        result.metrics["merge_suggestions_rejected"] = rejected

        if total > 0:
            rejection_rate = rejected / total
            result.metrics["merge_rejection_rate"] = round(rejection_rate, 3)

            if rejection_rate > MAX_REJECTION_RATE:
                result.verdict = "blocked"
                result.reasons.append(
                    f"Merge suggestion rejection rate {rejection_rate:.1%} "
                    f"exceeds {MAX_REJECTION_RATE:.0%} threshold"
                )
        else:
            result.metrics["merge_rejection_rate"] = 0.0
    else:
        result.metrics["merge_suggestions_total"] = 0
        result.metrics["merge_suggestions_rejected"] = 0
        result.metrics["merge_rejection_rate"] = 0.0

    # -----------------------------------------------------------------------
    # 4. no_orphaned_entities
    # -----------------------------------------------------------------------
    # Check if any signals point to entity_ids that have been merged away
    # (i.e., appear in entity_migrations.from_entity_id but NOT as to_entity_id)
    async with db.execute("""
        SELECT COUNT(DISTINCT s.company_id) FROM signals s
        WHERE s.company_id IS NOT NULL
          AND s.company_id IN (
              SELECT from_entity_id FROM entity_migrations
          )
          AND s.company_id NOT IN (
              SELECT to_entity_id FROM entity_migrations
          )
    """) as cursor:
        orphaned = (await cursor.fetchone())[0]

    result.metrics["orphaned_entity_ids"] = orphaned

    if orphaned > 0:
        if result.verdict != "blocked":
            result.verdict = "warn"
        result.reasons.append(
            f"{orphaned} signal(s) point to merged-away entity_ids"
        )

    # -----------------------------------------------------------------------
    # 5. claim_facts_consistent (only if USE_CLAIM_FACTS is enabled)
    # -----------------------------------------------------------------------
    use_claim_facts = os.getenv("USE_CLAIM_FACTS", "false").lower() in ("true", "1")

    if use_claim_facts and CLAIM_FACTS_TABLE in existing:
        # Check for contradictions: multiple active facts for same (entity_id, predicate)
        async with db.execute("""
            SELECT entity_id, predicate, COUNT(*) as cnt
            FROM claim_facts
            WHERE valid_until IS NULL AND is_retracted = 0
            GROUP BY entity_id, predicate
            HAVING cnt > 1
        """) as cursor:
            contradictions = await cursor.fetchall()

        contradiction_count = len(contradictions)
        result.metrics["claim_fact_contradictions"] = contradiction_count

        if contradiction_count > 0:
            if result.verdict != "blocked":
                result.verdict = "warn"
            result.reasons.append(
                f"{contradiction_count} claim fact contradiction(s) "
                f"(multiple active facts for same entity+predicate)"
            )
    else:
        result.metrics["claim_facts_checked"] = False

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    if not result.reasons:
        result.reasons.append("All Phase G readiness checks passed")

    return result

"""
Step 3B Readiness Gate — lightweight check for Step 3B activation.

Three predicates:
  1. multi_source_company_files >= MULTI_SOURCE_THRESHOLD (default 5)
  2. latest canary verdict in {"pass", "degraded"}
  3. Phase G readiness verdict == "ready"

Follows the same pattern as activation_gate.py and phase_g_readiness.py.

Usage:
    from monitoring.step3b_readiness import check_step3b_readiness
    result = await check_step3b_readiness(store)
    print(result.verdict)  # "ready" | "blocked"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

MULTI_SOURCE_THRESHOLD = 5


@dataclass
class Step3BReadinessResult:
    verdict: str = "blocked"  # "ready" | "blocked"
    blockers: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    checked_at: str = ""

    @property
    def can_proceed(self) -> bool:
        return self.verdict == "ready"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "blockers": self.blockers,
            "can_proceed": self.can_proceed,
            "metrics": self.metrics,
            "checked_at": self.checked_at,
        }


async def check_step3b_readiness(
    store,
    *,
    multi_source_threshold: int = MULTI_SOURCE_THRESHOLD,
) -> Step3BReadinessResult:
    """Check whether the system is ready for Step 3B activation.

    Args:
        store: SignalStore instance with initialized DB.
        multi_source_threshold: Minimum promoted company_files with 2+ sources.

    Returns:
        Step3BReadinessResult with verdict, blockers, and metrics.
    """
    now = datetime.now(timezone.utc)
    result = Step3BReadinessResult(checked_at=now.isoformat())
    db = store._db

    # ------------------------------------------------------------------
    # Predicate 1: multi-source company files
    # ------------------------------------------------------------------
    cursor = await db.execute(
        """SELECT COUNT(*) FROM company_files
           WHERE status = 'promoted'
             AND json_array_length(source_apis) >= 2"""
    )
    multi_source_count = (await cursor.fetchone())[0]
    result.metrics["multi_source_promoted"] = multi_source_count
    result.metrics["multi_source_threshold"] = multi_source_threshold

    if multi_source_count < multi_source_threshold:
        result.blockers.append(
            f"Only {multi_source_count} multi-source promoted company files "
            f"(need >= {multi_source_threshold})"
        )

    # ------------------------------------------------------------------
    # Predicate 2: latest canary verdict
    # ------------------------------------------------------------------
    cursor = await db.execute(
        """SELECT verdict, pass_rate FROM canary_runs
           ORDER BY created_at DESC, id DESC
           LIMIT 1"""
    )
    canary_row = await cursor.fetchone()

    if canary_row is None:
        result.metrics["canary_verdict"] = None
        result.metrics["canary_pass_rate"] = None
        result.blockers.append("No canary run data available")
    else:
        canary_verdict, canary_pass_rate = canary_row[0], canary_row[1]
        result.metrics["canary_verdict"] = canary_verdict
        result.metrics["canary_pass_rate"] = canary_pass_rate

        if canary_verdict not in ("pass", "degraded"):
            result.blockers.append(
                f"Canary verdict is '{canary_verdict}' "
                f"(pass_rate={canary_pass_rate}); need 'pass' or 'degraded'"
            )

    # ------------------------------------------------------------------
    # Predicate 3: Phase G readiness
    # ------------------------------------------------------------------
    from monitoring.phase_g_readiness import check_phase_g_readiness

    phase_g = await check_phase_g_readiness(store)
    result.metrics["phase_g_verdict"] = phase_g.verdict
    result.metrics["phase_g_reasons"] = phase_g.reasons

    if phase_g.verdict != "ready":
        result.blockers.append(
            f"Phase G readiness is '{phase_g.verdict}': "
            + "; ".join(phase_g.reasons)
        )

    # ------------------------------------------------------------------
    # Final verdict
    # ------------------------------------------------------------------
    result.verdict = "ready" if not result.blockers else "blocked"

    logger.info(
        "step3b_readiness verdict=%s blockers=%d metrics=%s",
        result.verdict,
        len(result.blockers),
        result.metrics,
    )
    return result

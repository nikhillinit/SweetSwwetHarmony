"""
Canary framework for quality regression detection.

A "canary" is a golden set of hand-verified signals that get re-scored
periodically. If scores drift beyond thresholds, an alert fires.

Wave 0 scope (scaffold):
- Golden set definition (YAML/dict-based)
- Re-score harness (CLI-only)
- Score comparison with per-signal deltas
- Pass/fail verdict

Wave 2 will extend this with:
- Stratified golden sets (by archetype/collector/confidence band)
- API endpoint + drift_alerts table
- Canary run history storage

Usage (CLI):
    python -m monitoring.canary_checker run --db signals.db
    python -m monitoring.canary_checker status --db signals.db
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

# Maximum acceptable drift before alerting
DEFAULT_DRIFT_THRESHOLD = 0.15  # 15% absolute shift in confidence score
DEFAULT_PASS_RATE_THRESHOLD = 0.80  # 80% of golden set must pass


# =============================================================================
# MODELS
# =============================================================================

@dataclass
class GoldenSignal:
    """A hand-verified signal in the golden set."""

    signal_id: int
    canonical_key: str
    expected_label: str  # "TP" or "FP"
    expected_confidence_min: float  # e.g., 0.7 for a known TP
    expected_confidence_max: float  # e.g., 1.0
    notes: str = ""


@dataclass
class CanaryResult:
    """Result of re-scoring one golden signal."""

    signal_id: int
    canonical_key: str
    expected_label: str
    actual_confidence: Optional[float]
    expected_confidence_min: float
    expected_confidence_max: float
    passed: bool
    delta: Optional[float] = None
    reason: str = ""


@dataclass
class CanaryRunResult:
    """Aggregate result of a canary run."""

    run_id: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    pass_rate: float = 0.0
    verdict: str = "unknown"  # "pass", "fail", "degraded"
    results: list[CanaryResult] = field(default_factory=list)
    run_at: str = ""
    duration_ms: float = 0.0

    @property
    def is_passing(self) -> bool:
        return self.verdict == "pass"


# =============================================================================
# GOLDEN SET MANAGEMENT
# =============================================================================

class GoldenSet:
    """Manages the golden set of verified signals for canary checks."""

    def __init__(self) -> None:
        self._signals: list[GoldenSignal] = []

    def add(self, signal: GoldenSignal) -> None:
        self._signals.append(signal)

    def add_tp(
        self,
        signal_id: int,
        canonical_key: str,
        min_confidence: float = 0.6,
        max_confidence: float = 1.0,
        notes: str = "",
    ) -> None:
        """Add a known true positive to the golden set."""
        self._signals.append(
            GoldenSignal(
                signal_id=signal_id,
                canonical_key=canonical_key,
                expected_label="TP",
                expected_confidence_min=min_confidence,
                expected_confidence_max=max_confidence,
                notes=notes,
            )
        )

    def add_fp(
        self,
        signal_id: int,
        canonical_key: str,
        max_confidence: float = 0.4,
        notes: str = "",
    ) -> None:
        """Add a known false positive to the golden set."""
        self._signals.append(
            GoldenSignal(
                signal_id=signal_id,
                canonical_key=canonical_key,
                expected_label="FP",
                expected_confidence_min=0.0,
                expected_confidence_max=max_confidence,
                notes=notes,
            )
        )

    @property
    def signals(self) -> list[GoldenSignal]:
        return list(self._signals)

    def __len__(self) -> int:
        return len(self._signals)

    def to_dict(self) -> list[dict[str, Any]]:
        return [
            {
                "signal_id": s.signal_id,
                "canonical_key": s.canonical_key,
                "expected_label": s.expected_label,
                "expected_confidence_min": s.expected_confidence_min,
                "expected_confidence_max": s.expected_confidence_max,
                "notes": s.notes,
            }
            for s in self._signals
        ]

    @classmethod
    def from_list(cls, items: list[dict[str, Any]]) -> "GoldenSet":
        gs = cls()
        for item in items:
            gs.add(
                GoldenSignal(
                    signal_id=item["signal_id"],
                    canonical_key=item["canonical_key"],
                    expected_label=item["expected_label"],
                    expected_confidence_min=item.get("expected_confidence_min", 0.0),
                    expected_confidence_max=item.get("expected_confidence_max", 1.0),
                    notes=item.get("notes", ""),
                )
            )
        return gs


# =============================================================================
# CANARY CHECKER
# =============================================================================

class CanaryChecker:
    """Re-scores golden signals and checks for drift."""

    def __init__(
        self,
        golden_set: GoldenSet,
        drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
        pass_rate_threshold: float = DEFAULT_PASS_RATE_THRESHOLD,
    ):
        self.golden_set = golden_set
        self.drift_threshold = drift_threshold
        self.pass_rate_threshold = pass_rate_threshold

    async def run(self, store: "SignalStore") -> CanaryRunResult:
        """Execute a canary run: re-read scores and compare against golden set."""
        import time

        start = time.perf_counter()
        now = datetime.now(timezone.utc).isoformat()
        results: list[CanaryResult] = []

        db = store._db

        for golden in self.golden_set.signals:
            cursor = await db.execute(
                "SELECT confidence FROM signals WHERE id = ?",
                (golden.signal_id,),
            )
            row = await cursor.fetchone()

            if row is None:
                results.append(
                    CanaryResult(
                        signal_id=golden.signal_id,
                        canonical_key=golden.canonical_key,
                        expected_label=golden.expected_label,
                        actual_confidence=None,
                        expected_confidence_min=golden.expected_confidence_min,
                        expected_confidence_max=golden.expected_confidence_max,
                        passed=False,
                        reason="signal_not_found",
                    )
                )
                continue

            actual = row[0]
            if actual is None:
                results.append(
                    CanaryResult(
                        signal_id=golden.signal_id,
                        canonical_key=golden.canonical_key,
                        expected_label=golden.expected_label,
                        actual_confidence=None,
                        expected_confidence_min=golden.expected_confidence_min,
                        expected_confidence_max=golden.expected_confidence_max,
                        passed=False,
                        reason="no_confidence_score",
                    )
                )
                continue

            in_range = golden.expected_confidence_min <= actual <= golden.expected_confidence_max
            # Compute delta from midpoint of expected range
            midpoint = (golden.expected_confidence_min + golden.expected_confidence_max) / 2
            delta = actual - midpoint

            passed = in_range
            reason = "" if passed else "confidence_out_of_range"

            results.append(
                CanaryResult(
                    signal_id=golden.signal_id,
                    canonical_key=golden.canonical_key,
                    expected_label=golden.expected_label,
                    actual_confidence=actual,
                    expected_confidence_min=golden.expected_confidence_min,
                    expected_confidence_max=golden.expected_confidence_max,
                    passed=passed,
                    delta=round(delta, 4),
                    reason=reason,
                )
            )

        elapsed_ms = (time.perf_counter() - start) * 1000
        total = len(results)
        passed_count = sum(1 for r in results if r.passed)
        failed_count = sum(1 for r in results if not r.passed and r.actual_confidence is not None)
        skipped = sum(1 for r in results if r.actual_confidence is None)

        scorable = total - skipped
        pass_rate = passed_count / scorable if scorable > 0 else 0.0

        if total == 0:
            verdict = "no_data"
        elif scorable == 0:
            verdict = "no_data"
        elif pass_rate >= self.pass_rate_threshold:
            verdict = "pass"
        elif pass_rate >= self.pass_rate_threshold * 0.8:
            verdict = "degraded"
        else:
            verdict = "fail"

        result = CanaryRunResult(
            total=total,
            passed=passed_count,
            failed=failed_count,
            skipped=skipped,
            pass_rate=round(pass_rate, 4),
            verdict=verdict,
            results=results,
            run_at=now,
            duration_ms=round(elapsed_ms, 2),
        )

        logger.info(
            "Canary run: %d/%d passed (%.1f%%), verdict=%s, %.0fms",
            passed_count,
            total,
            pass_rate * 100,
            verdict,
            elapsed_ms,
        )

        return result


# =============================================================================
# GOLDEN SET LOADER (from DB labels)
# =============================================================================

async def build_golden_set_from_labels(
    store: "SignalStore",
    min_labels: int = 5,
) -> GoldenSet:
    """Build a golden set from manually labeled signals in the DB.

    Uses signal_quality_metrics labels (TP/FP) as ground truth.
    """
    gs = GoldenSet()
    db = store._db

    cursor = await db.execute(
        """
        SELECT sqm.signal_id, s.canonical_key, sqm.label
        FROM signal_quality_metrics sqm
        JOIN signals s ON s.id = sqm.signal_id
        WHERE sqm.label IN ('TP', 'FP')
        ORDER BY sqm.created_at DESC
        LIMIT 200
        """,
    )
    rows = await cursor.fetchall()

    for row in rows:
        signal_id, canonical_key, label = row[0], row[1], row[2]
        if not canonical_key:
            continue
        if label == "TP":
            gs.add_tp(signal_id, canonical_key)
        elif label == "FP":
            gs.add_fp(signal_id, canonical_key)

    if len(gs) < min_labels:
        logger.warning(
            "Golden set has only %d signals (min: %d). Canary results may be unreliable.",
            len(gs),
            min_labels,
        )

    return gs

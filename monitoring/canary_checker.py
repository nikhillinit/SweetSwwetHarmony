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

import hashlib
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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
                """
                SELECT tc.thesis_fit_score, s.confidence
                FROM signals s
                LEFT JOIN thesis_classifications tc ON tc.signal_id = s.id
                WHERE s.id = ?
                ORDER BY tc.classified_at DESC, tc.id DESC
                LIMIT 1
                """,
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

            # Prefer thesis_fit_score when available; otherwise fall back to raw
            # collector confidence for backward compatibility.
            actual = row[0] if row[0] is not None else row[1]
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
        SELECT sqm.signal_id, s.canonical_key, sqm.human_label
        FROM signal_quality_metrics sqm
        JOIN signals s ON s.id = sqm.signal_id
        WHERE sqm.human_label IN ('TP', 'FP')
        ORDER BY sqm.labeled_at DESC
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


# =============================================================================
# STRATIFIED GOLDEN SET (Wave 2)
# =============================================================================

@dataclass
class StratifiedGoldenSet:
    """Golden set with stratification metadata."""

    golden_set: GoldenSet
    by_archetype: Dict[str, List[GoldenSignal]] = field(default_factory=dict)
    by_collector: Dict[str, List[GoldenSignal]] = field(default_factory=dict)
    by_confidence_band: Dict[str, List[GoldenSignal]] = field(default_factory=dict)
    overall: List[GoldenSignal] = field(default_factory=list)

    @property
    def golden_set_hash(self) -> str:
        """SHA256[:16] of sorted signal IDs for comparability."""
        ids = sorted(str(s.signal_id) for s in self.golden_set.signals)
        payload = "\x1f".join(ids)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def build_stratified_golden_set(
    store: "SignalStore",
    min_labels: int = 5,
) -> StratifiedGoldenSet:
    """Build a stratified golden set from labeled signals.

    Joins signals for source_api/confidence, thesis_classifications for archetype.
    """
    gs = await build_golden_set_from_labels(store, min_labels)
    result = StratifiedGoldenSet(golden_set=gs, overall=list(gs.signals))

    db = store._db

    # Enrich with signal metadata
    signal_ids = [s.signal_id for s in gs.signals]
    if not signal_ids:
        return result

    placeholders = ",".join("?" * len(signal_ids))

    # Get source_api and confidence for stratification
    cursor = await db.execute(
        f"""
        SELECT s.id, s.source_api, s.confidence
        FROM signals s
        WHERE s.id IN ({placeholders})
        """,
        signal_ids,
    )
    signal_meta: Dict[int, Dict[str, Any]] = {}
    for row in await cursor.fetchall():
        signal_meta[row[0]] = {"source_api": row[1], "confidence": row[2]}

    # Get archetype from thesis_classifications
    try:
        cursor = await db.execute(
            f"""
            SELECT signal_id, category
            FROM thesis_classifications
            WHERE signal_id IN ({placeholders})
            """,
            signal_ids,
        )
        for row in await cursor.fetchall():
            if row[0] in signal_meta:
                signal_meta[row[0]]["archetype"] = row[1]
    except Exception:
        pass  # thesis_classifications may not exist

    # Build stratifications
    for signal in gs.signals:
        meta = signal_meta.get(signal.signal_id, {})

        # By collector
        collector = meta.get("source_api", "unknown")
        result.by_collector.setdefault(collector, []).append(signal)

        # By confidence band
        confidence = meta.get("confidence")
        if confidence is not None:
            if confidence >= 0.7:
                band = "high"
            elif confidence >= 0.4:
                band = "medium"
            else:
                band = "low"
        else:
            band = "unknown"
        result.by_confidence_band.setdefault(band, []).append(signal)

        # By archetype
        archetype = meta.get("archetype", "unknown")
        result.by_archetype.setdefault(archetype, []).append(signal)

    return result


# =============================================================================
# CANARY RUN PERSISTENCE (Wave 2)
# =============================================================================

async def store_canary_run(
    store: "SignalStore",
    checker: CanaryChecker,
    run_result: CanaryRunResult,
    stratified: Optional[StratifiedGoldenSet] = None,
) -> int:
    """Persist a canary run with full run_history lifecycle.

    create_run(CANARY) → start_run() → complete_run()/fail_run()

    Returns:
        canary_runs.id
    """
    from workflows.run_manager import (
        RunType, create_run, start_run, complete_run, fail_run,
    )

    now = datetime.now(timezone.utc).isoformat()

    # Compute hashes
    golden_set_hash = ""
    if stratified:
        golden_set_hash = stratified.golden_set_hash
    elif checker.golden_set:
        ids = sorted(str(s.signal_id) for s in checker.golden_set.signals)
        payload = "\x1f".join(ids)
        golden_set_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    config_hash = hashlib.sha256(json.dumps({
        "drift_threshold": checker.drift_threshold,
        "pass_rate_threshold": checker.pass_rate_threshold,
    }, sort_keys=True).encode()).hexdigest()[:16]

    # Run lifecycle
    run_record = await create_run(
        store,
        run_type=RunType.CANARY.value,
        inputs_hash=golden_set_hash,
    )
    await start_run(store, run_record.id)

    # Prepare stratification JSON
    strat_json = None
    if stratified:
        strat_data = {}
        for key, signals in stratified.by_archetype.items():
            passed = sum(1 for s in signals for r in run_result.results
                        if r.signal_id == s.signal_id and r.passed)
            total = len(signals)
            strat_data[f"archetype:{key}"] = {
                "count": total,
                "passed": passed,
                "pass_rate": passed / total if total > 0 else 0.0,
            }
        for key, signals in stratified.by_collector.items():
            passed = sum(1 for s in signals for r in run_result.results
                        if r.signal_id == s.signal_id and r.passed)
            total = len(signals)
            strat_data[f"collector:{key}"] = {
                "count": total,
                "passed": passed,
                "pass_rate": passed / total if total > 0 else 0.0,
            }
        strat_json = json.dumps(strat_data)

    # Results JSON
    results_json = json.dumps([
        {
            "signal_id": r.signal_id,
            "canonical_key": r.canonical_key,
            "expected_label": r.expected_label,
            "actual_confidence": r.actual_confidence,
            "passed": r.passed,
            "delta": r.delta,
            "reason": r.reason,
        }
        for r in run_result.results
    ])

    # Insert canary_runs row
    db = store._db
    cursor = await db.execute(
        """
        INSERT INTO canary_runs (
            run_id, golden_set_size, golden_set_hash, golden_set_version,
            config_hash, total_scored, passed, failed, skipped,
            pass_rate, verdict, drift_threshold, pass_rate_threshold,
            duration_ms, results_json, stratification_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_record.id,
            len(checker.golden_set),
            golden_set_hash,
            None,  # golden_set_version
            config_hash,
            run_result.total,
            run_result.passed,
            run_result.failed,
            run_result.skipped,
            run_result.pass_rate,
            run_result.verdict,
            checker.drift_threshold,
            checker.pass_rate_threshold,
            run_result.duration_ms,
            results_json,
            strat_json,
            now,
        ),
    )
    await db.commit()
    canary_run_id = cursor.lastrowid

    # Complete run lifecycle
    await complete_run(
        store,
        run_record.id,
        result={
            "verdict": run_result.verdict,
            "pass_rate": run_result.pass_rate,
            "total": run_result.total,
            "canary_run_id": canary_run_id,
        },
    )

    return canary_run_id


# =============================================================================
# CLI ENTRY POINT (Wave 2)
# =============================================================================

async def _cli_run(db_path: str, store_results: bool = False) -> None:
    """CLI: run canary check and optionally store results."""
    from storage.signal_store import SignalStore

    store = SignalStore(db_path)
    await store.initialize()

    try:
        stratified = await build_stratified_golden_set(store)
        checker = CanaryChecker(stratified.golden_set)
        result = await checker.run(store)

        print(f"Verdict: {result.verdict}")
        print(f"Pass rate: {result.pass_rate:.2%}")
        print(f"Total: {result.total} | Passed: {result.passed} | Failed: {result.failed} | Skipped: {result.skipped}")
        print(f"Duration: {result.duration_ms:.0f}ms")

        if store_results:
            canary_run_id = await store_canary_run(store, checker, result, stratified)
            print(f"Stored as canary_run_id={canary_run_id}")

            # Run drift detection
            from monitoring.drift_detector import detect_drift, store_drift_alerts
            drift = await detect_drift(
                store,
                canary_run_id,
                stratified.golden_set_hash,
            )
            print(f"Drift verdict: {drift.verdict}")
            if drift.alerts:
                alert_count = await store_drift_alerts(store, canary_run_id, drift.alerts)
                print(f"Stored {alert_count} drift alerts")
            if drift.baseline_message:
                print(f"Baseline: {drift.baseline_message}")
    finally:
        await store.close()


async def _cli_status(db_path: str) -> None:
    """CLI: show latest canary status."""
    from storage.signal_store import SignalStore

    store = SignalStore(db_path)
    await store.initialize()

    try:
        db = store._db
        cursor = await db.execute(
            """
            SELECT id, verdict, pass_rate, golden_set_size, created_at
            FROM canary_runs
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
        )
        row = await cursor.fetchone()
        if row:
            print(f"Latest canary run #{row[0]}: verdict={row[1]}, "
                  f"pass_rate={row[2]:.2%}, golden_set={row[3]}, at={row[4]}")
        else:
            print("No canary runs found.")
    finally:
        await store.close()


async def _cli_history(db_path: str, limit: int = 10) -> None:
    """CLI: show canary run history."""
    from storage.signal_store import SignalStore

    store = SignalStore(db_path)
    await store.initialize()

    try:
        db = store._db
        cursor = await db.execute(
            """
            SELECT id, verdict, pass_rate, golden_set_size, duration_ms, created_at
            FROM canary_runs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        if not rows:
            print("No canary runs found.")
            return

        print(f"{'ID':>4} {'Verdict':>12} {'Pass Rate':>10} {'Golden':>6} {'Duration':>10} {'Created At'}")
        print("-" * 70)
        for row in rows:
            pr = f"{row[2]:.2%}" if row[2] is not None else "N/A"
            dur = f"{row[4]:.0f}ms" if row[4] is not None else "N/A"
            print(f"{row[0]:>4} {row[1]:>12} {pr:>10} {row[3]:>6} {dur:>10} {row[5]}")
    finally:
        await store.close()


if __name__ == "__main__":
    import asyncio
    import argparse
    import sys

    sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(description="Canary Checker CLI")
    parser.add_argument("command", choices=["run", "status", "history"])
    parser.add_argument("--db", default="signals.db", help="Database path")
    parser.add_argument("--store-results", action="store_true", help="Store results to DB")
    parser.add_argument("--limit", type=int, default=10, help="History limit")

    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(_cli_run(args.db, store_results=args.store_results))
    elif args.command == "status":
        asyncio.run(_cli_status(args.db))
    elif args.command == "history":
        asyncio.run(_cli_history(args.db, limit=args.limit))

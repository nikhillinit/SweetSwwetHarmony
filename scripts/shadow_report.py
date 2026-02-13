#!/usr/bin/env python3
"""
Shadow Report CLI - Analyze v2 shadow mode comparison data.

Phase 0B-3 tooling for:
- Exporting thesis_match shadow logs from SignalStore
- Generating parity/tuning reports
- Running gate checks for YAML policy changes

Usage:
    # Export shadow logs
    python scripts/shadow_report.py export --since-days 7 --out shadow.jsonl

    # Generate report from exported data
    python scripts/shadow_report.py report --input shadow.jsonl --out-dir artifacts/shadow/

    # Run gate check
    python scripts/shadow_report.py gate-check --input shadow.jsonl --mode parity
    python scripts/shadow_report.py gate-check --input shadow.jsonl --mode tuning

Exit codes:
    0 - Gate passed / Success
    1 - Gate failed
    2 - Error (e.g., file not found)
    3 - Insufficient samples for meaningful analysis (min_n not met)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ShadowRecord:
    """A single shadow comparison record."""
    canonical_key: str
    signal_id: Optional[int]
    logged_at: str
    policy_hash: Optional[str]
    v1_score: float
    v1_routing: str
    v1_penalty_raw: float
    v1_negative_keywords: List[str]
    v2_score: float
    v2_routing: str
    v2_penalty_raw: float
    v2_negative_keywords: List[str]
    delta_score: float
    would_change_routing: bool
    would_change_is_fit: bool
    keyword_score: Optional[float] = None
    keyword_category: Optional[str] = None

    @classmethod
    def from_shadow_log(cls, log: Dict[str, Any]) -> Optional["ShadowRecord"]:
        """Parse a shadow log entry into a ShadowRecord.

        Args:
            log: Raw shadow log from SignalStore

        Returns:
            ShadowRecord if v2_shadow is present, None otherwise
        """
        computed = log.get("computed_value", {})
        v2_shadow = computed.get("v2_shadow")

        if not v2_shadow:
            return None

        v1 = v2_shadow.get("v1", {})
        v2 = v2_shadow.get("v2", {})

        logged_at = log.get("logged_at")
        if isinstance(logged_at, datetime):
            logged_at = logged_at.isoformat()

        return cls(
            canonical_key=log.get("canonical_key", ""),
            signal_id=log.get("signal_id"),
            logged_at=logged_at or "",
            policy_hash=v2_shadow.get("policy_hash"),
            v1_score=v1.get("score", 0.0),
            v1_routing=v1.get("routing", "UNKNOWN"),
            v1_penalty_raw=v1.get("penalty_raw", 0.0),
            v1_negative_keywords=v1.get("negative_keywords", []),
            v2_score=v2.get("score", 0.0),
            v2_routing=v2.get("routing", "UNKNOWN"),
            v2_penalty_raw=v2.get("penalty_raw", 0.0),
            v2_negative_keywords=v2.get("negative_keywords", []),
            delta_score=v2_shadow.get("delta_score", 0.0),
            would_change_routing=v2_shadow.get("would_change_routing", False),
            would_change_is_fit=v2_shadow.get("would_change_is_fit", False),
            keyword_score=computed.get("keyword_score"),
            keyword_category=computed.get("keyword_category"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GateResult:
    """Result of a gate check."""
    passed: bool
    mode: str
    total_records: int
    min_n_required: int
    metrics: Dict[str, Any]
    failures: List[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.total_records < self.min_n_required:
            return 3  # Insufficient samples
        return 0 if self.passed else 1


@dataclass
class ReportSummary:
    """Summary statistics for shadow comparison."""
    total_records: int
    records_with_routing_change: int
    records_with_is_fit_change: int
    routing_change_rate: float
    is_fit_change_rate: float
    delta_score_mean: float
    delta_score_p50: float
    delta_score_p95: float
    max_abs_delta: float
    transition_matrix: Dict[str, Dict[str, int]]
    policy_hash: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# EXPORT COMMAND
# =============================================================================

async def cmd_export(args: argparse.Namespace) -> int:
    """Export shadow logs from SignalStore to JSONL."""
    from storage.signal_store import SignalStore

    logger.info(f"Exporting thesis_match shadow logs from last {args.since_days} days")

    store = None
    try:
        store = SignalStore(db_path=args.db_path)
        await store.initialize()

        since = datetime.now(timezone.utc) - timedelta(days=args.since_days)

        logs = await store.get_shadow_logs(
            feature_name="thesis_match",
            since=since,
            limit=args.limit,
        )

        logger.info(f"Retrieved {len(logs)} shadow logs")

        records = []
        skipped = 0
        for log in logs:
            record = ShadowRecord.from_shadow_log(log)
            if record:
                records.append(record)
            else:
                skipped += 1

        if skipped > 0:
            logger.info(f"Skipped {skipped} logs without v2_shadow data")

        logger.info(f"Parsed {len(records)} shadow records with v2_shadow")

        # Write to JSONL
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            for record in records:
                f.write(json.dumps(record.to_dict()) + "\n")

        logger.info(f"Exported {len(records)} records to {output_path}")
        return 0

    except Exception as e:
        logger.error(f"Export failed: {e}")
        return 2
    finally:
        if store is not None:
            await store.close()


# =============================================================================
# REPORT COMMAND
# =============================================================================

def load_records(input_path: Path, decode_legacy: bool = True) -> List[ShadowRecord]:
    """Load shadow records from JSONL file."""
    records = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)

            # Handle legacy double-encoded data if needed
            if decode_legacy and isinstance(data, str):
                data = json.loads(data)

            records.append(ShadowRecord(**data))
    return records


def compute_report(records: List[ShadowRecord]) -> ReportSummary:
    """Compute summary statistics from shadow records."""
    if not records:
        return ReportSummary(
            total_records=0,
            records_with_routing_change=0,
            records_with_is_fit_change=0,
            routing_change_rate=0.0,
            is_fit_change_rate=0.0,
            delta_score_mean=0.0,
            delta_score_p50=0.0,
            delta_score_p95=0.0,
            max_abs_delta=0.0,
            transition_matrix={},
            policy_hash=None,
        )

    # Compute metrics
    routing_changes = sum(1 for r in records if r.would_change_routing)
    is_fit_changes = sum(1 for r in records if r.would_change_is_fit)
    deltas = [r.delta_score for r in records]

    # Sort for quantiles
    sorted_deltas = sorted(deltas)
    n = len(sorted_deltas)

    # Transition matrix: v1_routing -> v2_routing -> count
    transition_matrix: Dict[str, Dict[str, int]] = {}
    for r in records:
        if r.v1_routing not in transition_matrix:
            transition_matrix[r.v1_routing] = {}
        v2_routing = r.v2_routing
        transition_matrix[r.v1_routing][v2_routing] = (
            transition_matrix[r.v1_routing].get(v2_routing, 0) + 1
        )

    # Get policy hash (use most common)
    policy_hashes = [r.policy_hash for r in records if r.policy_hash]
    policy_hash = max(set(policy_hashes), key=policy_hashes.count) if policy_hashes else None

    return ReportSummary(
        total_records=n,
        records_with_routing_change=routing_changes,
        records_with_is_fit_change=is_fit_changes,
        routing_change_rate=routing_changes / n if n > 0 else 0.0,
        is_fit_change_rate=is_fit_changes / n if n > 0 else 0.0,
        delta_score_mean=sum(deltas) / n if n > 0 else 0.0,
        delta_score_p50=sorted_deltas[n // 2] if n > 0 else 0.0,
        delta_score_p95=sorted_deltas[int(n * 0.95)] if n > 0 else 0.0,
        max_abs_delta=max(abs(d) for d in deltas) if deltas else 0.0,
        transition_matrix=transition_matrix,
        policy_hash=policy_hash,
    )


def generate_markdown_report(summary: ReportSummary, records: List[ShadowRecord]) -> str:
    """Generate markdown report from summary."""
    lines = [
        "# Shadow Mode Report",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Policy Hash:** `{summary.policy_hash or 'N/A'}`",
        f"**Total Records:** {summary.total_records}",
        "",
        "## Summary Metrics",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Routing Change Rate | {summary.routing_change_rate:.2%} ({summary.records_with_routing_change}/{summary.total_records}) |",
        f"| Is-Fit Change Rate | {summary.is_fit_change_rate:.2%} ({summary.records_with_is_fit_change}/{summary.total_records}) |",
        f"| Delta Score Mean | {summary.delta_score_mean:.4f} |",
        f"| Delta Score P50 | {summary.delta_score_p50:.4f} |",
        f"| Delta Score P95 | {summary.delta_score_p95:.4f} |",
        f"| Max Absolute Delta | {summary.max_abs_delta:.4f} |",
        "",
        "## Transition Matrix (v1 -> v2)",
        "",
    ]

    # Build transition matrix table
    all_routings = sorted(set(
        list(summary.transition_matrix.keys()) +
        [r for d in summary.transition_matrix.values() for r in d.keys()]
    ))

    if all_routings:
        header = "| v1 \\ v2 | " + " | ".join(all_routings) + " |"
        sep = "|---------|" + "|".join(["-----"] * len(all_routings)) + "|"
        lines.extend([header, sep])

        for v1_routing in all_routings:
            row = [v1_routing]
            for v2_routing in all_routings:
                count = summary.transition_matrix.get(v1_routing, {}).get(v2_routing, 0)
                row.append(str(count) if count > 0 else "-")
            lines.append("| " + " | ".join(row) + " |")

    # Add sample of routing changes if any
    if summary.records_with_routing_change > 0:
        lines.extend([
            "",
            "## Sample Routing Changes (up to 10)",
            "",
            "| Canonical Key | v1 Score | v2 Score | v1 Routing | v2 Routing |",
            "|---------------|----------|----------|------------|------------|",
        ])

        changes = [r for r in records if r.would_change_routing][:10]
        for r in changes:
            lines.append(
                f"| {r.canonical_key[:30]}... | {r.v1_score:.3f} | {r.v2_score:.3f} | "
                f"{r.v1_routing} | {r.v2_routing} |"
            )

    return "\n".join(lines)


async def cmd_report(args: argparse.Namespace) -> int:
    """Generate report from shadow log JSONL file."""
    input_path = Path(args.input)

    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return 2

    try:
        records = load_records(input_path, decode_legacy=args.decode_legacy)
        logger.info(f"Loaded {len(records)} records from {input_path}")

        summary = compute_report(records)

        # Create output directory
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Write JSON summary
        json_path = out_dir / "summary.json"
        with open(json_path, "w") as f:
            json.dump(summary.to_dict(), f, indent=2)
        logger.info(f"Wrote JSON summary to {json_path}")

        # Write Markdown report
        md_path = out_dir / "report.md"
        md_content = generate_markdown_report(summary, records)
        with open(md_path, "w") as f:
            f.write(md_content)
        logger.info(f"Wrote Markdown report to {md_path}")

        # Print summary to console
        print("\n=== Shadow Report Summary ===")
        print(f"Total Records: {summary.total_records}")
        print(f"Routing Change Rate: {summary.routing_change_rate:.2%}")
        print(f"Max Abs Delta: {summary.max_abs_delta:.4f}")
        print(f"Policy Hash: {summary.policy_hash or 'N/A'}")

        return 0

    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        return 2


# =============================================================================
# GATE-CHECK COMMAND
# =============================================================================

def check_parity_gate(records: List[ShadowRecord]) -> GateResult:
    """Check parity gate - v1 and v2 should produce identical results."""
    MIN_N = 200

    metrics = {
        "routing_change_count": sum(1 for r in records if r.would_change_routing),
        "max_abs_delta": max(abs(r.delta_score) for r in records) if records else 0.0,
    }

    failures = []

    if metrics["routing_change_count"] > 0:
        failures.append(
            f"routing_change_count={metrics['routing_change_count']} (expected 0)"
        )

    if metrics["max_abs_delta"] > 1e-9:
        failures.append(
            f"max_abs_delta={metrics['max_abs_delta']:.6f} (expected <= 1e-9)"
        )

    return GateResult(
        passed=len(failures) == 0,
        mode="parity",
        total_records=len(records),
        min_n_required=MIN_N,
        metrics=metrics,
        failures=failures,
    )


def check_tuning_gate(records: List[ShadowRecord]) -> GateResult:
    """Check tuning gate - v2 changes should be within acceptable limits."""
    MIN_N = 1000
    MAX_ROUTING_CHANGE_RATE = 0.01  # 1%
    MAX_DOWNGRADE_RATE = 0.002  # 0.2%
    MAX_QUALIFIED_TO_REJECTED = 0.0005  # 0.05%

    n = len(records)

    # Count transitions
    routing_changes = sum(1 for r in records if r.would_change_routing)

    # Downgrades: QUALIFIED -> HELD or QUALIFIED -> REJECTED or HELD -> REJECTED
    downgrades = sum(
        1 for r in records
        if (r.v1_routing == "QUALIFIED" and r.v2_routing in ("HELD", "REJECTED"))
        or (r.v1_routing == "HELD" and r.v2_routing == "REJECTED")
    )

    # Qualified to Rejected (worst case)
    qualified_to_rejected = sum(
        1 for r in records
        if r.v1_routing == "QUALIFIED" and r.v2_routing == "REJECTED"
    )

    metrics = {
        "routing_change_count": routing_changes,
        "routing_change_rate": routing_changes / n if n > 0 else 0.0,
        "downgrade_count": downgrades,
        "downgrade_rate": downgrades / n if n > 0 else 0.0,
        "qualified_to_rejected_count": qualified_to_rejected,
        "qualified_to_rejected_rate": qualified_to_rejected / n if n > 0 else 0.0,
    }

    failures = []

    if metrics["routing_change_rate"] > MAX_ROUTING_CHANGE_RATE:
        failures.append(
            f"routing_change_rate={metrics['routing_change_rate']:.2%} (max {MAX_ROUTING_CHANGE_RATE:.2%})"
        )

    if metrics["downgrade_rate"] > MAX_DOWNGRADE_RATE:
        failures.append(
            f"downgrade_rate={metrics['downgrade_rate']:.2%} (max {MAX_DOWNGRADE_RATE:.2%})"
        )

    if n >= 2000:
        # Full threshold check
        if metrics["qualified_to_rejected_rate"] > MAX_QUALIFIED_TO_REJECTED:
            failures.append(
                f"qualified_to_rejected_rate={metrics['qualified_to_rejected_rate']:.2%} "
                f"(max {MAX_QUALIFIED_TO_REJECTED:.2%})"
            )
    else:
        # Small-N fallback: absolute cap
        if qualified_to_rejected > 0:
            failures.append(
                f"qualified_to_rejected_count={qualified_to_rejected} (expected 0 for N<2000)"
            )

    return GateResult(
        passed=len(failures) == 0,
        mode="tuning",
        total_records=n,
        min_n_required=MIN_N,
        metrics=metrics,
        failures=failures,
    )


async def cmd_gate_check(args: argparse.Namespace) -> int:
    """Run gate check on shadow log JSONL file."""
    input_path = Path(args.input)

    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return 2

    try:
        records = load_records(input_path, decode_legacy=args.decode_legacy)
        logger.info(f"Loaded {len(records)} records from {input_path}")

        if args.mode == "parity":
            result = check_parity_gate(records)
        elif args.mode == "tuning":
            result = check_tuning_gate(records)
        else:
            logger.error(f"Unknown mode: {args.mode}")
            return 2

        # Print result
        print(f"\n=== Gate Check: {result.mode.upper()} ===")
        print(f"Total Records: {result.total_records}")
        print(f"Min N Required: {result.min_n_required}")

        if result.total_records < result.min_n_required:
            print(f"\n[!] INSUFFICIENT SAMPLES")
            print(f"    Need {result.min_n_required}, have {result.total_records}")
            return 3

        print("\nMetrics:")
        for key, value in result.metrics.items():
            if isinstance(value, float):
                print(f"  {key}: {value:.4f}")
            else:
                print(f"  {key}: {value}")

        if result.passed:
            print(f"\n[PASS] GATE PASSED")
        else:
            print(f"\n[FAIL] GATE FAILED")
            for failure in result.failures:
                print(f"   - {failure}")

        return result.exit_code

    except Exception as e:
        logger.error(f"Gate check failed: {e}")
        return 2


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Shadow Report CLI - Analyze v2 shadow mode comparison data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Export command
    export_parser = subparsers.add_parser(
        "export",
        help="Export shadow logs from SignalStore to JSONL",
    )
    export_parser.add_argument(
        "--since-days",
        type=int,
        default=7,
        help="Export logs from the last N days (default: 7)",
    )
    export_parser.add_argument(
        "--out",
        type=str,
        default="shadow_logs.jsonl",
        help="Output file path (default: shadow_logs.jsonl)",
    )
    export_parser.add_argument(
        "--limit",
        type=int,
        default=10000,
        help="Maximum records to export (default: 10000)",
    )
    export_parser.add_argument(
        "--db-path",
        type=str,
        default="signals.db",
        help="Path to SignalStore database (default: signals.db)",
    )

    # Report command
    report_parser = subparsers.add_parser(
        "report",
        help="Generate report from shadow log JSONL",
    )
    report_parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Input JSONL file from export command",
    )
    report_parser.add_argument(
        "--out-dir",
        type=str,
        default="artifacts/shadow",
        help="Output directory for reports (default: artifacts/shadow)",
    )
    report_parser.add_argument(
        "--decode-legacy",
        action="store_true",
        default=True,
        help="Decode legacy double-encoded records (default: True)",
    )
    report_parser.add_argument(
        "--no-decode-legacy",
        action="store_false",
        dest="decode_legacy",
        help="Disable legacy decoding",
    )

    # Gate-check command
    gate_parser = subparsers.add_parser(
        "gate-check",
        help="Run gate check on shadow log data",
    )
    gate_parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Input JSONL file from export command",
    )
    gate_parser.add_argument(
        "--mode",
        type=str,
        choices=["parity", "tuning"],
        required=True,
        help="Gate check mode: parity (exact match) or tuning (within limits)",
    )
    gate_parser.add_argument(
        "--decode-legacy",
        action="store_true",
        default=True,
        help="Decode legacy double-encoded records (default: True)",
    )
    gate_parser.add_argument(
        "--no-decode-legacy",
        action="store_false",
        dest="decode_legacy",
        help="Disable legacy decoding",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Dispatch to command handler
    if args.command == "export":
        exit_code = asyncio.run(cmd_export(args))
    elif args.command == "report":
        exit_code = asyncio.run(cmd_report(args))
    elif args.command == "gate-check":
        exit_code = asyncio.run(cmd_gate_check(args))
    else:
        parser.print_help()
        exit_code = 2

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

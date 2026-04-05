"""
Compare Step 3/4 activation readiness under active SPC settings versus defaults.

This is an operator-facing decision helper for temporary SPC bootstrap overrides.
It snapshots the source DB once, recomputes daily quality metrics on scratch copies
for each SPC profile, and then runs the normal activation gate against those copies.

Usage:
    python scripts/spc_override_decision.py --db signals.db --json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Bootstrap project root so this script works from any CWD.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from monitoring.activation_gate import STEP_POLICY, check_activation_readiness
from monitoring.daily_aggregator import backfill_daily_metrics
from storage.signal_store import SignalStore
from utils.db_path_helper import resolve_db_path_env

SPC_DEFAULTS = {
    "SPC_MIN_BASELINE_DAYS": "14",
    "SPC_MIN_TOTAL_SAMPLES": "100",
    "SPC_MIN_LABELED_PER_DAY": "10",
    "SPC_RECOMPUTE_WINDOW_DAYS": "7",
}


def _current_spc_settings() -> dict[str, str]:
    return {key: os.environ.get(key, default) for key, default in SPC_DEFAULTS.items()}


@contextlib.contextmanager
def _temporary_env(overrides: dict[str, str]):
    original = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = str(value)
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _snapshot_db(source_db: Path, dest_db: Path) -> None:
    source = sqlite3.connect(str(source_db), timeout=5)
    dest = sqlite3.connect(str(dest_db), timeout=5)
    try:
        source.execute("PRAGMA busy_timeout=5000")
        dest.execute("PRAGMA busy_timeout=5000")
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def _recompute_quality_metrics(db_path: Path, days: int) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("DELETE FROM quality_metrics_daily")
        result = backfill_daily_metrics(conn, days=days)
        conn.commit()
        return {
            "computed": int(result.get("computed", 0)),
            "skipped": int(result.get("skipped", 0)),
        }
    finally:
        conn.close()


async def _run_activation_checks(db_path: Path, steps: list[int]) -> dict[str, dict[str, Any]]:
    store = SignalStore(str(db_path))
    await store.initialize()
    try:
        results: dict[str, dict[str, Any]] = {}
        for step in steps:
            result = await check_activation_readiness(store, step=step)
            results[str(step)] = result.to_dict()
        return results
    finally:
        await store.close()


def _evaluate_profile(
    base_snapshot: Path,
    settings: dict[str, str],
    steps: list[int],
    backfill_days: int,
    label: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"spc-{label}-") as tmp_dir:
        scratch_db = Path(tmp_dir) / base_snapshot.name
        _snapshot_db(base_snapshot, scratch_db)
        with _temporary_env(settings):
            backfill_result = _recompute_quality_metrics(scratch_db, backfill_days)
            step_results = asyncio.run(_run_activation_checks(scratch_db, steps))
        return {
            "settings": {key: int(value) for key, value in settings.items()},
            "backfill_days": backfill_days,
            "backfill_result": backfill_result,
            "steps": step_results,
        }


def _collect_required_metric_losses(
    active_steps: dict[str, dict[str, Any]],
    default_steps: dict[str, dict[str, Any]],
    steps: list[int],
) -> dict[str, list[str]]:
    lost: dict[str, list[str]] = {}
    for step in steps:
        required = STEP_POLICY.get(step, {}).get("required_spc_metrics", [])
        lost_metrics = []
        for metric in required:
            active_status = active_steps[str(step)]["drift_coverage"].get(metric)
            default_status = default_steps[str(step)]["drift_coverage"].get(metric)
            if active_status == "ok" and default_status != "ok":
                lost_metrics.append(metric)
        if lost_metrics:
            lost[str(step)] = lost_metrics
    return lost


def _collect_non_spc_differences(
    active_steps: dict[str, dict[str, Any]],
    default_steps: dict[str, dict[str, Any]],
    steps: list[int],
) -> list[str]:
    differences: list[str] = []
    for step in steps:
        active = active_steps[str(step)]
        default = default_steps[str(step)]
        if active["canary"] != default["canary"]:
            differences.append(f"step {step}: canary state differs")
        if active["alerts"] != default["alerts"]:
            differences.append(f"step {step}: alert counts differ")
    return differences


def evaluate_override_decision(
    db_path: str | Path,
    steps: tuple[int, ...] = (3, 4),
    backfill_days: int = 90,
) -> dict[str, Any]:
    source_db = Path(db_path)
    if not source_db.exists():
        raise FileNotFoundError(f"Database not found: {source_db}")

    steps_list = sorted(set(int(step) for step in steps))
    if any(step not in STEP_POLICY for step in steps_list):
        raise ValueError(f"Invalid step selection: {steps_list}")

    active_settings = _current_spc_settings()
    default_settings = dict(SPC_DEFAULTS)

    with tempfile.TemporaryDirectory(prefix="spc-decision-") as tmp_dir:
        base_snapshot = Path(tmp_dir) / source_db.name
        _snapshot_db(source_db, base_snapshot)

        active_profile = _evaluate_profile(
            base_snapshot=base_snapshot,
            settings=active_settings,
            steps=steps_list,
            backfill_days=backfill_days,
            label="active",
        )
        default_profile = _evaluate_profile(
            base_snapshot=base_snapshot,
            settings=default_settings,
            steps=steps_list,
            backfill_days=backfill_days,
            label="defaults",
        )

    active_steps = active_profile["steps"]
    default_steps = default_profile["steps"]
    active_can_proceed = all(active_steps[str(step)]["can_proceed"] for step in steps_list)
    default_can_proceed = all(default_steps[str(step)]["can_proceed"] for step in steps_list)

    required_metric_losses = _collect_required_metric_losses(active_steps, default_steps, steps_list)
    non_spc_differences = _collect_non_spc_differences(active_steps, default_steps, steps_list)
    all_lost_metrics = sorted({metric for metrics in required_metric_losses.values() for metric in metrics})

    if default_can_proceed:
        outcome = "proceed_without_exception"
        rationale = [
            "Default SPC settings also pass the selected activation steps.",
            "Bootstrap overrides are unnecessary for this promotion window.",
        ]
    elif active_can_proceed and all_lost_metrics == ["overall_fp_rate"] and not non_spc_differences:
        outcome = "proceed_with_exception"
        rationale = [
            "Active SPC settings keep the selected activation steps green.",
            "Default SPC settings lose required overall_fp_rate coverage and do not change non-SPC gate inputs.",
            "Document the bootstrap overrides as a temporary promotion exception, not permanent policy.",
        ]
    else:
        outcome = "hold"
        rationale = [
            "The selected steps are not defensible under the current SPC comparison.",
            "Either active SPC settings still do not clear the gate, defaults fail for broader reasons, or non-SPC gate inputs changed.",
        ]

    active_overrides = {
        key: int(value)
        for key, value in active_settings.items()
        if str(value) != SPC_DEFAULTS[key]
    }

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(source_db),
        "steps": steps_list,
        "outcome": outcome,
        "rationale": rationale,
        "active_overrides": active_overrides,
        "required_metric_losses": required_metric_losses,
        "non_spc_differences": non_spc_differences,
        "profiles": {
            "active": active_profile,
            "defaults": default_profile,
        },
    }


def _print_human_report(report: dict[str, Any]) -> None:
    print("SPC Override Decision")
    print("=" * 60)
    print(f"Outcome: {report['outcome']}")
    print(f"DB: {report['db_path']}")
    print(f"Steps: {', '.join(str(step) for step in report['steps'])}")
    if report["active_overrides"]:
        overrides = ", ".join(f"{key}={value}" for key, value in sorted(report["active_overrides"].items()))
        print(f"Active overrides: {overrides}")
    else:
        print("Active overrides: none")
    print()

    for profile_name, profile in report["profiles"].items():
        print(f"{profile_name.upper()} PROFILE")
        settings = ", ".join(f"{key}={value}" for key, value in sorted(profile["settings"].items()))
        print(f"  Settings: {settings}")
        print(
            "  Backfill: "
            f"computed={profile['backfill_result']['computed']}, "
            f"skipped={profile['backfill_result']['skipped']}"
        )
        for step in report["steps"]:
            result = profile["steps"][str(step)]
            print(
                f"  Step {step}: {result['verdict'].upper()} "
                f"(can_proceed={result['can_proceed']})"
            )
            if result["drift_coverage"]:
                coverage = ", ".join(
                    f"{metric}={status}"
                    for metric, status in sorted(result["drift_coverage"].items())
                )
                print(f"    SPC: {coverage}")
        print()

    if report["required_metric_losses"]:
        print("Required metric losses under defaults:")
        for step, metrics in sorted(report["required_metric_losses"].items()):
            print(f"  Step {step}: {', '.join(metrics)}")
        print()

    if report["non_spc_differences"]:
        print("Non-SPC differences:")
        for diff in report["non_spc_differences"]:
            print(f"  - {diff}")
        print()

    print("Rationale:")
    for line in report["rationale"]:
        print(f"  - {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare Step 3/4 activation readiness with active SPC settings versus defaults"
    )
    parser.add_argument("--db", default=resolve_db_path_env(), help="Database path")
    parser.add_argument(
        "--steps",
        type=int,
        nargs="+",
        default=[3, 4],
        help="Activation steps to compare (default: 3 4)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="How many days of daily metrics to backfill on scratch DB copies (default: 90)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument(
        "--report-path",
        default=None,
        help="Optional path to write the JSON report",
    )
    args = parser.parse_args(argv)

    report = evaluate_override_decision(args.db, steps=tuple(args.steps), backfill_days=args.days)
    if args.report_path:
        Path(args.report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human_report(report)

    return 0 if report["outcome"] != "hold" else 1


if __name__ == "__main__":
    sys.exit(main())

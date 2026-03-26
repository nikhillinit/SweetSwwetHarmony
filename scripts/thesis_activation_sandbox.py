"""
Validate the thesis-activation rollout plan on a scratch database copy.

This script answers three operator questions without touching the live queue:
1. Are Step 3 / Step 4 activation gates currently green?
2. What is the real pending thesis state by source?
3. If requested, does a scratch-only process run create fresh LLM thesis rows?

Usage:
    python scripts/thesis_activation_sandbox.py --db-path signals.db --json
    python scripts/thesis_activation_sandbox.py --db-path signals.db --source-api hacker_news --execute-process --json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from monitoring.activation_gate import check_activation_readiness
from monitoring.feature_gate import get_overdue_regret_checks
from storage.signal_store import SignalStore
from utils.db_path_helper import add_db_path_args, resolve_db_path
from utils.thesis_filter import ThesisFilterConfig
from workflows.pipeline import DiscoveryPipeline, PipelineConfig


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


@contextlib.contextmanager
def _temporary_env(overrides: dict[str, str | None]) -> Iterator[None]:
    original = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _fetch_pending_by_source(db_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        rows = conn.execute(
            """
            WITH pending AS (
                SELECT s.id, s.source_api
                FROM signals s
                INNER JOIN signal_processing p ON p.signal_id = s.id
                WHERE p.status = 'pending'
            ),
            latest_thesis AS (
                SELECT *
                FROM (
                    SELECT
                        tc.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY tc.signal_id
                            ORDER BY tc.classified_at DESC, tc.id DESC
                        ) AS rn
                    FROM thesis_classifications tc
                )
                WHERE rn = 1
            )
            SELECT
                pending.source_api AS source_api,
                COUNT(*) AS pending_total,
                SUM(CASE WHEN latest_thesis.signal_id IS NULL THEN 1 ELSE 0 END) AS missing_thesis,
                SUM(
                    CASE
                        WHEN latest_thesis.signal_id IS NOT NULL
                         AND latest_thesis.model IS NULL
                         AND latest_thesis.thesis_fit_score IS NULL
                        THEN 1 ELSE 0
                    END
                ) AS keyword_only_latest,
                SUM(
                    CASE
                        WHEN latest_thesis.model IS NOT NULL
                          OR latest_thesis.thesis_fit_score IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS llm_latest,
                SUM(
                    CASE
                        WHEN latest_thesis.signal_id IS NOT NULL
                         AND COALESCE(latest_thesis.keyword_score, -1.0) = 0.0
                        THEN 1 ELSE 0
                    END
                ) AS keyword_zero_latest
            FROM pending
            LEFT JOIN latest_thesis ON latest_thesis.signal_id = pending.id
            GROUP BY pending.source_api
            ORDER BY pending_total DESC, pending.source_api ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _zero_source_state(source_api: str) -> dict[str, Any]:
    return {
        "source_api": source_api,
        "pending_total": 0,
        "missing_thesis": 0,
        "keyword_only_latest": 0,
        "llm_latest": 0,
        "keyword_zero_latest": 0,
    }


def _fetch_source_state(db_path: Path, source_api: str) -> dict[str, Any]:
    for row in _fetch_pending_by_source(db_path):
        if row["source_api"] == source_api:
            return row
    return _zero_source_state(source_api)


def _fetch_pending_signal_ids(db_path: Path, source_api: str, limit: int) -> list[int]:
    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        rows = conn.execute(
            """
            SELECT s.id
            FROM signals s
            INNER JOIN signal_processing p ON p.signal_id = s.id
            WHERE p.status = 'pending' AND s.source_api = ?
            ORDER BY s.detected_at DESC, s.id DESC
            LIMIT ?
            """,
            (source_api, limit),
        ).fetchall()
        return [int(row[0]) for row in rows]
    finally:
        conn.close()


def _fetch_signals_missing_any_thesis(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        total = conn.execute(
            """
            SELECT COUNT(*)
            FROM signals s
            LEFT JOIN thesis_classifications tc ON tc.signal_id = s.id
            WHERE tc.signal_id IS NULL
            """
        ).fetchone()[0]
        by_source = conn.execute(
            """
            SELECT s.source_api AS source_api, COUNT(*) AS missing_total
            FROM signals s
            LEFT JOIN thesis_classifications tc ON tc.signal_id = s.id
            WHERE tc.signal_id IS NULL
            GROUP BY s.source_api
            ORDER BY missing_total DESC, s.source_api ASC
            """
        ).fetchall()
        return {
            "total": int(total),
            "by_source": [dict(row) for row in by_source],
        }
    finally:
        conn.close()


def _fetch_status_counts(db_path: Path, signal_ids: list[int]) -> dict[str, int]:
    if not signal_ids:
        return {}

    placeholders = ",".join("?" for _ in signal_ids)
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        rows = conn.execute(
            f"""
            SELECT p.status AS status, COUNT(*) AS count
            FROM signal_processing p
            WHERE p.signal_id IN ({placeholders})
            GROUP BY p.status
            ORDER BY p.status ASC
            """,
            signal_ids,
        ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}
    finally:
        conn.close()


def _fetch_signal_set_latest_summary(db_path: Path, signal_ids: list[int]) -> dict[str, int]:
    if not signal_ids:
        return {
            "signal_count": 0,
            "missing_latest_thesis": 0,
            "keyword_only_latest": 0,
            "llm_latest": 0,
            "keyword_zero_latest": 0,
        }

    placeholders = ",".join("?" for _ in signal_ids)
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        row = conn.execute(
            f"""
            WITH target AS (
                SELECT id
                FROM signals
                WHERE id IN ({placeholders})
            ),
            latest_thesis AS (
                SELECT *
                FROM (
                    SELECT
                        tc.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY tc.signal_id
                            ORDER BY tc.classified_at DESC, tc.id DESC
                        ) AS rn
                    FROM thesis_classifications tc
                    WHERE tc.signal_id IN ({placeholders})
                )
                WHERE rn = 1
            )
            SELECT
                COUNT(*) AS signal_count,
                SUM(CASE WHEN latest_thesis.signal_id IS NULL THEN 1 ELSE 0 END) AS missing_latest_thesis,
                SUM(
                    CASE
                        WHEN latest_thesis.signal_id IS NOT NULL
                         AND latest_thesis.model IS NULL
                         AND latest_thesis.thesis_fit_score IS NULL
                        THEN 1 ELSE 0
                    END
                ) AS keyword_only_latest,
                SUM(
                    CASE
                        WHEN latest_thesis.model IS NOT NULL
                          OR latest_thesis.thesis_fit_score IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS llm_latest,
                SUM(
                    CASE
                        WHEN latest_thesis.signal_id IS NOT NULL
                         AND COALESCE(latest_thesis.keyword_score, -1.0) = 0.0
                        THEN 1 ELSE 0
                    END
                ) AS keyword_zero_latest
            FROM target
            LEFT JOIN latest_thesis ON latest_thesis.signal_id = target.id
            """,
            signal_ids + signal_ids,
        ).fetchone()
        return {
            "signal_count": int(row["signal_count"] or 0),
            "missing_latest_thesis": int(row["missing_latest_thesis"] or 0),
            "keyword_only_latest": int(row["keyword_only_latest"] or 0),
            "llm_latest": int(row["llm_latest"] or 0),
            "keyword_zero_latest": int(row["keyword_zero_latest"] or 0),
        }
    finally:
        conn.close()


def _fetch_new_thesis_activity(
    db_path: Path,
    signal_ids: list[int],
    started_at: str,
) -> dict[str, int]:
    if not signal_ids:
        return {
            "new_thesis_rows": 0,
            "new_llm_rows": 0,
            "new_confidence_ledger_rows": 0,
        }

    placeholders = ",".join("?" for _ in signal_ids)
    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        new_thesis_rows = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM thesis_classifications
            WHERE signal_id IN ({placeholders}) AND classified_at >= ?
            """,
            signal_ids + [started_at],
        ).fetchone()[0]
        new_llm_rows = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM thesis_classifications
            WHERE signal_id IN ({placeholders})
              AND classified_at >= ?
              AND (model IS NOT NULL OR thesis_fit_score IS NOT NULL)
            """,
            signal_ids + [started_at],
        ).fetchone()[0]
        new_confidence_ledger_rows = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM confidence_ledger
            WHERE canonical_key IN (
                SELECT DISTINCT canonical_key
                FROM signals
                WHERE id IN ({placeholders})
            )
            AND created_at >= ?
            """,
            signal_ids + [started_at],
        ).fetchone()[0]
        return {
            "new_thesis_rows": int(new_thesis_rows or 0),
            "new_llm_rows": int(new_llm_rows or 0),
            "new_confidence_ledger_rows": int(new_confidence_ledger_rows or 0),
        }
    finally:
        conn.close()


async def _run_activation_checks(db_path: Path, steps: tuple[int, ...] = (3, 4)) -> dict[str, Any]:
    store = SignalStore(str(db_path))
    await store.initialize()
    try:
        results: dict[str, Any] = {}
        for step in steps:
            results[str(step)] = (await check_activation_readiness(store, step=step)).to_dict()
        return results
    finally:
        await store.close()


async def _run_sandbox_process(
    scratch_db: Path,
    source_api: str,
    batch_size: int,
) -> dict[str, Any]:
    config = PipelineConfig.from_env()
    config.db_path = str(scratch_db)
    config.batch_size = batch_size
    config.notion_api_key = None
    config.notion_database_id = None
    config.watchlist_database_id = None
    config.warmup_suppression_cache = False

    pipeline = DiscoveryPipeline(config)
    try:
        await pipeline.initialize()
        result = await pipeline.process_pending(dry_run=True, source_api=source_api)
        return {"ok": True, "result": result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        await pipeline.close()


def _build_observations(
    gate_steps: dict[str, Any],
    overdue: dict[str, Any],
    effective_skip_threshold: float,
    target_state: dict[str, Any],
    target_source_api: str,
) -> list[str]:
    observations: list[str] = []
    step4 = gate_steps.get("4", {})
    step3 = gate_steps.get("3", {})

    if overdue.get("count", 0) == 0 and (not step3.get("can_proceed") or not step4.get("can_proceed")):
        observations.append(
            "Current rollout blocker is activation-gate readiness, not overdue regret checks."
        )

    all_reasons = step3.get("reasons", []) + step4.get("reasons", [])
    if any("Canary run is" in reason for reason in all_reasons):
        observations.append("Refresh the canary before any live activation change.")

    if os.environ.get("THESIS_SKIP_LLM_BELOW") is None:
        observations.append(
            f"THESIS_SKIP_LLM_BELOW is unset; the effective code default is {effective_skip_threshold:.1f}, not 0.45."
        )

    if target_state.get("keyword_only_latest", 0) > 0:
        observations.append(
            f"{target_source_api} has pending signals with keyword-only latest thesis rows; "
            "thesis-classify-batch will not revisit them because it only selects signals missing any thesis row."
        )

    if target_state.get("missing_thesis", 0) > 0:
        observations.append(
            f"{target_source_api} also has pending signals with no thesis row at all, so plan the backlog by source state, not just keyword_score=0."
        )

    return observations


def evaluate_activation_sandbox(
    db_path: str | Path,
    *,
    source_api: str = "hacker_news",
    batch_size: int | None = None,
    llm_mode: str = "shadow",
    skip_llm_below: float = 0.0,
    execute_process: bool = False,
) -> dict[str, Any]:
    source_db = Path(db_path)
    if not source_db.exists():
        raise FileNotFoundError(f"Database not found: {source_db}")

    pending_by_source = _fetch_pending_by_source(source_db)
    target_state = _fetch_source_state(source_db, source_api)
    target_batch_size = batch_size or min(max(target_state["pending_total"], 1), 50)
    sample_signal_ids = _fetch_pending_signal_ids(source_db, source_api, limit=min(target_batch_size, 5))
    effective_thesis_config = ThesisFilterConfig.from_env()
    gate_steps = asyncio.run(_run_activation_checks(source_db))
    overdue = get_overdue_regret_checks(str(source_db), strict=False)

    report: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(source_db),
        "target_source_api": source_api,
        "current_env": {
            "LLM_THESIS_MODE": os.getenv("LLM_THESIS_MODE", "off"),
            "THESIS_SKIP_LLM_BELOW_raw": os.getenv("THESIS_SKIP_LLM_BELOW"),
            "THESIS_SKIP_LLM_BELOW_effective": effective_thesis_config.skip_llm_if_keyword_below,
            "DELIVERY_MODE": os.getenv("DELIVERY_MODE", "staging_only"),
            "MERGE_WRITES_ENABLED": os.getenv("MERGE_WRITES_ENABLED", "disabled"),
        },
        "gate": {
            "activation_steps": gate_steps,
            "overdue_regret_checks": overdue,
        },
        "backlog": {
            "pending_by_source": pending_by_source,
            "signals_missing_any_thesis": _fetch_signals_missing_any_thesis(source_db),
        },
        "target_source": {
            "current_pending_state": target_state,
            "suggested_batch_size": target_batch_size,
            "sample_pending_signal_ids": sample_signal_ids,
        },
    }
    report["observations"] = _build_observations(
        gate_steps=gate_steps,
        overdue=overdue,
        effective_skip_threshold=effective_thesis_config.skip_llm_if_keyword_below,
        target_state=target_state,
        target_source_api=source_api,
    )

    if not execute_process:
        return report

    tmp_dir = Path(tempfile.mkdtemp(prefix="thesis-activation-sandbox-"))
    scratch_db = tmp_dir / source_db.name
    cleanup_status = "deleted"
    target_signal_ids: list[int] = []
    pre_summary = _fetch_signal_set_latest_summary(source_db, [])
    pre_status_counts: dict[str, int] = {}
    post_summary = _fetch_signal_set_latest_summary(source_db, [])
    post_status_counts: dict[str, int] = {}
    new_activity = _fetch_new_thesis_activity(source_db, [], datetime.now(timezone.utc).isoformat())
    process_result: dict[str, Any] = {"ok": False, "error": "sandbox process not started"}
    try:
        _snapshot_db(source_db, scratch_db)
        target_signal_ids = _fetch_pending_signal_ids(scratch_db, source_api, limit=target_batch_size)
        pre_summary = _fetch_signal_set_latest_summary(scratch_db, target_signal_ids)
        pre_status_counts = _fetch_status_counts(scratch_db, target_signal_ids)
        started_at = datetime.now(timezone.utc).isoformat()

        process_env = {
            "DISCOVERY_DB_PATH": str(scratch_db),
            "LLM_THESIS_MODE": llm_mode,
            "THESIS_SKIP_LLM_BELOW": str(skip_llm_below),
            "NOTION_API_KEY": None,
            "NOTION_DATABASE_ID": None,
            "NOTION_WATCHLIST_DATABASE_ID": None,
            "WARMUP_SUPPRESSION_CACHE": "false",
        }
        with _temporary_env(process_env):
            process_result = asyncio.run(
                _run_sandbox_process(
                    scratch_db=scratch_db,
                    source_api=source_api,
                    batch_size=target_batch_size,
                )
            )

        post_summary = _fetch_signal_set_latest_summary(scratch_db, target_signal_ids)
        post_status_counts = _fetch_status_counts(scratch_db, target_signal_ids)
        new_activity = _fetch_new_thesis_activity(
            scratch_db,
            signal_ids=target_signal_ids,
            started_at=started_at,
        )
    finally:
        try:
            shutil.rmtree(tmp_dir)
        except OSError:
            cleanup_status = "retained_due_to_open_handle"

    report["sandbox_run"] = {
        "target_signal_ids": target_signal_ids,
        "scratch_db_path": str(scratch_db),
        "cleanup_status": cleanup_status,
        "settings": {
            "llm_mode": llm_mode,
            "skip_llm_below": skip_llm_below,
            "pipeline_dry_run": True,
        },
        "process_result": process_result,
        "before": {
            "latest_thesis_state": pre_summary,
            "status_counts": pre_status_counts,
        },
        "after": {
            "latest_thesis_state": post_summary,
            "status_counts": post_status_counts,
        },
        "proof": {
            **new_activity,
            "llm_fired": bool(new_activity["new_llm_rows"] > 0),
        },
    }
    return report


def _print_human_report(report: dict[str, Any]) -> None:
    print("Thesis Activation Sandbox")
    print("=" * 60)
    print(f"DB: {report['db_path']}")
    print(f"Target source: {report['target_source_api']}")
    print(
        "Env: "
        f"LLM_THESIS_MODE={report['current_env']['LLM_THESIS_MODE']}, "
        f"THESIS_SKIP_LLM_BELOW={report['current_env']['THESIS_SKIP_LLM_BELOW_effective']}"
    )
    print()

    print("Gate status:")
    for step in ("3", "4"):
        step_result = report["gate"]["activation_steps"][step]
        print(
            f"  Step {step}: {step_result['verdict']} "
            f"(can_proceed={step_result['can_proceed']})"
        )
        for reason in step_result["reasons"]:
            print(f"    - {reason}")
    print(
        "  Overdue regret checks: "
        f"{report['gate']['overdue_regret_checks']['count']}"
    )
    print()

    print("Pending by source:")
    for row in report["backlog"]["pending_by_source"]:
        print(
            "  "
            f"{row['source_api']}: pending={row['pending_total']}, "
            f"missing_thesis={row['missing_thesis']}, "
            f"keyword_only={row['keyword_only_latest']}, "
            f"llm_latest={row['llm_latest']}, "
            f"kw_zero={row['keyword_zero_latest']}"
        )
    print()

    if report["observations"]:
        print("Observations:")
        for item in report["observations"]:
            print(f"  - {item}")
        print()

    sandbox = report.get("sandbox_run")
    if sandbox:
        print("Scratch process:")
        print(f"  Target signal ids: {sandbox['target_signal_ids']}")
        print(f"  LLM mode: {sandbox['settings']['llm_mode']}")
        process_result = sandbox["process_result"]
        if process_result.get("ok"):
            result = process_result["result"]
            print(
                "  Pipeline result: "
                f"processed={result.get('processed', 0)}, "
                f"held={result.get('held', 0)}, "
                f"rejected={result.get('rejected', 0)}"
            )
        else:
            print(f"  Pipeline result: ERROR {process_result.get('error')}")
        proof = sandbox["proof"]
        print(
            "  Proof: "
            f"new_thesis_rows={proof['new_thesis_rows']}, "
            f"new_llm_rows={proof['new_llm_rows']}, "
            f"new_confidence_ledger_rows={proof['new_confidence_ledger_rows']}, "
            f"llm_fired={proof['llm_fired']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the thesis activation rollout on a scratch database copy."
    )
    add_db_path_args(parser)
    parser.add_argument(
        "--source-api",
        default="hacker_news",
        help="Target source for optional scratch proof run (default: hacker_news)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Scratch proof batch size (default: min(target pending, 50))",
    )
    parser.add_argument(
        "--llm-mode",
        choices=["off", "shadow", "active"],
        default="shadow",
        help="LLM mode to apply on the scratch proof run (default: shadow)",
    )
    parser.add_argument(
        "--skip-llm-below",
        type=float,
        default=0.0,
        help="THESIS_SKIP_LLM_BELOW override for the scratch proof run (default: 0.0)",
    )
    parser.add_argument(
        "--execute-process",
        action="store_true",
        help="Run a scratch-only process pass after reporting current state",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a human-readable report",
    )
    args = parser.parse_args(argv)

    db_path = resolve_db_path(args)
    report = evaluate_activation_sandbox(
        db_path=db_path,
        source_api=args.source_api,
        batch_size=args.batch_size,
        llm_mode=args.llm_mode,
        skip_llm_below=args.skip_llm_below,
        execute_process=bool(args.execute_process),
    )

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_human_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

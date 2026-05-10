"""Collector Health CLI (read-only).

Surfaces a snapshot of collector health by combining three inputs:

* Static configuration (``config/collectors.yaml`` via ``ops.collector_config``).
* Runtime heartbeat state (``state/collectors.json`` via
  ``ops.collector_heartbeat.load_collector_state``) which already computes
  ``effective_status`` for each collector.
* A 90-day aggregation over the live ``signals`` table grouped by
  ``signal_type`` and ``source_api``.

The CLI is strictly read-only against the signals database — it never
performs ``INSERT``/``UPDATE``/``DELETE`` and never touches schema. It is the
collector-side counterpart to the heartbeat writer: heartbeat reports what the
runner saw; health reports whether the database actually grew.

Silent collectors are configured-and-enabled collectors that produced zero
rows in the lookback window. Disabled / blocked / missing-key collectors are
intentional and never flagged as silent.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from ops.collector_config import (
    INTENTIONAL_CONFIGURED_STATUSES,
    CollectorConfig,
    load_collector_config,
)
from ops.collector_heartbeat import load_collector_state

REPORT_SCHEMA_VERSION = 1
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_DB_PATH = "signals.db"

# Collectors whose ``source_api`` value(s) in the signals table differ from the
# collector name, or which legitimately emit multiple ``source_api`` rows.
# Anything not listed here defaults to ``(collector_name,)``.
_OVERRIDES: dict[str, tuple[str, ...]] = {
    "job_postings": ("greenhouse_jobs", "ashby_jobs", "lever_jobs"),
    # Module-only collectors emit no signals on their own.
    "community_keywords": (),
}

_KNOWN_COLLECTORS: tuple[str, ...] = (
    "github",
    "github_activity",
    "sec_edgar",
    "companies_house",
    "domain_whois",
    "product_hunt",
    "hacker_news",
    "arxiv",
    "job_postings",
    "linkedin",
    "crunchbase",
    "uspto",
    "opencorporates",
    "telegram",
    "discord",
    "news_api",
    "rss_feeds",
    "changedetection",
    "brand_launch",
    "capterra",
    "g2crowd",
    "plugandplay",
    "community_keywords",
)


def _build_default_mapping() -> dict[str, tuple[str, ...]]:
    mapping: dict[str, tuple[str, ...]] = {}
    for name in _KNOWN_COLLECTORS:
        mapping[name] = _OVERRIDES.get(name, (name,))
    return mapping


DEFAULT_EXPECTED_SOURCE_APIS_BY_COLLECTOR: dict[str, tuple[str, ...]] = (
    _build_default_mapping()
)

# Effective-status values that should NOT trigger a "silent" warning.
_NON_SILENT_STATUSES = {
    "disabled_intentional",
    "disabled_missing_key",
    "blocked_access",
    "deprecated",
}
_PROGRESS_RUNTIME_STATUSES = {"success", "partial_success", "dry_run"}


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def aggregate_signal_counts(
    db_path: str | os.PathLike[str],
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[dict[str, Any]]:
    """Aggregate signal counts in the lookback window.

    Returns a list of ``{signal_type, source_api, count}`` rows ordered by
    count descending. A missing ``signals`` table is treated as no rows so the
    CLI can still render the heartbeat side of the report.
    """
    path = Path(db_path)
    if not path.exists():
        return []

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        cursor = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signals'"
        )
        if cursor.fetchone() is None:
            return []
        rows = con.execute(
            """
            SELECT signal_type, source_api, COUNT(*) AS cnt
            FROM signals
            WHERE detected_at > datetime('now', ?)
            GROUP BY signal_type, source_api
            ORDER BY cnt DESC
            """,
            (f"-{int(lookback_days)} days",),
        ).fetchall()
    finally:
        con.close()

    return [
        {
            "signal_type": r["signal_type"],
            "source_api": r["source_api"],
            "count": int(r["cnt"]),
        }
        for r in rows
    ]


def aggregate_outbox_health(
    db_path: str | os.PathLike[str],
    *,
    stale_processing_minutes: int = 60,
) -> dict[str, Any]:
    """Summarize Notion outbox drain state without modifying the database."""
    empty = {
        "available": False,
        "pending_count": 0,
        "pending_due_count": 0,
        "processing_count": 0,
        "stale_processing_count": 0,
        "failed_count": 0,
        "last_successful_send_at": None,
        "oldest_due_at": None,
    }
    path = Path(db_path)
    if not path.exists():
        return empty

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        table = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notion_outbox'"
        ).fetchone()
        if table is None:
            return empty

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        stale_threshold = (
            now - timedelta(minutes=int(stale_processing_minutes))
        ).isoformat()
        counts = con.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                SUM(
                    CASE
                        WHEN status = 'pending'
                         AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                        THEN 1 ELSE 0
                    END
                ) AS pending_due_count,
                SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) AS processing_count,
                SUM(
                    CASE
                        WHEN status = 'processing' AND updated_at < ?
                        THEN 1 ELSE 0
                    END
                ) AS stale_processing_count,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count
            FROM notion_outbox
            """,
            (now_iso, stale_threshold),
        ).fetchone()
        last_successful_send_at = con.execute(
            "SELECT MAX(updated_at) FROM notion_outbox WHERE status = 'sent'"
        ).fetchone()[0]
        oldest_due_at = con.execute(
            """
            SELECT MIN(COALESCE(next_attempt_at, created_at))
            FROM notion_outbox
            WHERE status = 'pending'
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            """,
            (now_iso,),
        ).fetchone()[0]
    finally:
        con.close()

    return {
        "available": True,
        "pending_count": int(counts["pending_count"] or 0),
        "pending_due_count": int(counts["pending_due_count"] or 0),
        "processing_count": int(counts["processing_count"] or 0),
        "stale_processing_count": int(counts["stale_processing_count"] or 0),
        "failed_count": int(counts["failed_count"] or 0),
        "last_successful_send_at": last_successful_send_at,
        "oldest_due_at": oldest_due_at,
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _observed_count(
    expected_source_apis: Sequence[str],
    counts_by_source_api: Mapping[str, int],
) -> int:
    return sum(counts_by_source_api.get(api, 0) for api in expected_source_apis)


def _compute_override_active(
    state_entry: Mapping[str, Any],
    collector_config: Optional[CollectorConfig],
) -> bool:
    """Derived: is the operator's sticky configured_status overriding YAML intent?

    A collector heartbeat preserves ``disabled_intentional`` and
    ``blocked_access`` as sticky operator state (see
    ``ops.collector_heartbeat._configured_fields_for``). When the YAML config
    declares the collector as ``enabled`` (or any non-intentional value) but
    the persisted state has been stickily flipped to an intentional status,
    we surface this as ``override_active=True`` so health/dashboard consumers
    can flag it without inspecting the YAML themselves.

    Returns False when:
    * state is not in {disabled_intentional, blocked_access}
    * YAML itself declares the collector as intentionally disabled/blocked
      (state simply matches YAML)
    * collector is unknown to the YAML (cannot determine intent)
    """
    state_status = str(state_entry.get("configured_status") or "")
    if state_status not in INTENTIONAL_CONFIGURED_STATUSES:
        return False
    if collector_config is None:
        return False
    return collector_config.configured_status not in INTENTIONAL_CONFIGURED_STATUSES


def _has_progress_proof(entry: Mapping[str, Any]) -> bool:
    return any(
        _optional_int(entry.get(field)) is not None
        for field in (
            "data_version_before",
            "data_version_after",
            "rows_inserted_this_iter",
        )
    )


def _has_producer_progress(entry: Mapping[str, Any]) -> bool:
    status = str(entry.get("last_run_status") or "")
    if status not in _PROGRESS_RUNTIME_STATUSES:
        return False

    before = _optional_int(entry.get("data_version_before"))
    after = _optional_int(entry.get("data_version_after"))
    inserted = _optional_int(entry.get("rows_inserted_this_iter"))
    if inserted is not None:
        return inserted > 0
    return (
        before is not None
        and after is not None
        and after > before
    )


def _is_alive_but_no_db_progress(
    entry: Mapping[str, Any],
) -> bool:
    status = str(entry.get("last_run_status") or "")
    if status not in _PROGRESS_RUNTIME_STATUSES or not _has_progress_proof(entry):
        return False

    before = _optional_int(entry.get("data_version_before"))
    after = _optional_int(entry.get("data_version_after"))
    inserted = _optional_int(entry.get("rows_inserted_this_iter"))
    if inserted is not None:
        return inserted <= 0

    return before is not None and after is not None and after <= before


def build_health_report(
    state: Mapping[str, Any],
    signal_counts: Iterable[Mapping[str, Any]],
    *,
    expected_source_apis_by_collector: Optional[
        Mapping[str, Sequence[str]]
    ] = None,
    db_path: Optional[str | os.PathLike[str]] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    now: Optional[datetime] = None,
    configs: Optional[Mapping[str, CollectorConfig]] = None,
    config_path: Optional[str | os.PathLike[str]] = None,
    outbox_health: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Combine heartbeat state and signal counts into a structured report.

    ``configs`` (or ``config_path`` for live loads) supplies the YAML intent
    used to derive each collector's ``override_active`` field. Pass ``configs``
    explicitly in tests; live callers can rely on ``load_collector_config()``.
    """
    if expected_source_apis_by_collector is None:
        expected_source_apis_by_collector = DEFAULT_EXPECTED_SOURCE_APIS_BY_COLLECTOR
    if configs is None:
        configs = load_collector_config(config_path)

    counts_list = [
        {
            "signal_type": str(row["signal_type"]),
            "source_api": str(row["source_api"]),
            "count": int(row["count"]),
        }
        for row in signal_counts
    ]
    counts_by_source_api: dict[str, int] = {}
    for row in counts_list:
        counts_by_source_api[row["source_api"]] = (
            counts_by_source_api.get(row["source_api"], 0) + row["count"]
        )

    raw_collectors = state.get("collectors") if isinstance(state, Mapping) else {}
    if not isinstance(raw_collectors, Mapping):
        raw_collectors = {}

    collectors: list[dict[str, Any]] = []
    by_status: dict[str, int] = {}
    warnings: list[str] = []
    silent_count = 0
    stale_count = 0
    failing_count = 0
    override_active_count = 0
    override_active_collectors: list[str] = []
    alive_but_no_db_progress_count = 0
    alive_but_no_db_progress_collectors: list[str] = []
    producer_progress_seen = False
    used_source_apis: set[str] = set()

    for name in sorted(raw_collectors):
        entry = raw_collectors[name]
        if not isinstance(entry, Mapping):
            continue

        effective_status = str(entry.get("effective_status") or "unknown")
        is_stale = effective_status == "stale"
        is_failing = effective_status == "failing"
        expected_apis = tuple(expected_source_apis_by_collector.get(name, (name,)))
        observed = _observed_count(expected_apis, counts_by_source_api)
        used_source_apis.update(expected_apis)

        is_silent = (
            bool(expected_apis)
            and observed == 0
            and effective_status not in _NON_SILENT_STATUSES
        )
        override_active = _compute_override_active(entry, configs.get(name))
        is_no_db_progress = _is_alive_but_no_db_progress(entry)
        producer_progress_seen = producer_progress_seen or _has_producer_progress(entry)

        if is_silent:
            silent_count += 1
            warnings.append(
                f"{name}: configured ({effective_status}) but produced 0 rows in last "
                f"{lookback_days} days for source_api {list(expected_apis)}"
            )
        if is_stale:
            stale_count += 1
            warnings.append(
                f"{name}: heartbeat marks STALE (cadence "
                f"{entry.get('expected_cadence_hours')}h)"
            )
        if is_failing:
            failing_count += 1
            warnings.append(
                f"{name}: heartbeat marks FAILING — "
                f"{entry.get('error_message') or 'see error_messages'}"
            )
        if override_active:
            override_active_count += 1
            override_active_collectors.append(name)
            yaml_intent = configs[name].configured_status
            warnings.append(
                f"{name}: operator override active — state is "
                f"{entry.get('configured_status')} but YAML intent is {yaml_intent}"
            )
        if is_no_db_progress:
            alive_but_no_db_progress_count += 1
            alive_but_no_db_progress_collectors.append(name)
            warnings.append(
                f"{name}: heartbeat completed but proof fields show no DB progress"
            )

        by_status[effective_status] = by_status.get(effective_status, 0) + 1

        collectors.append(
            {
                "name": name,
                "configured_status": entry.get("configured_status"),
                "configured_status_reason": entry.get("configured_status_reason"),
                "expected_cadence_hours": entry.get("expected_cadence_hours"),
                "last_run_status": entry.get("last_run_status"),
                "last_finished_at": entry.get("last_finished_at"),
                "last_success_at": entry.get("last_success_at"),
                "consecutive_failures": entry.get("consecutive_failures", 0),
                "data_version_before": _optional_int(
                    entry.get("data_version_before")
                ),
                "data_version_after": _optional_int(entry.get("data_version_after")),
                "rows_inserted_this_iter": _optional_int(
                    entry.get("rows_inserted_this_iter")
                ),
                "rows_total_last_24h": _optional_int(
                    entry.get("rows_total_last_24h")
                ),
                "collector_class": entry.get("collector_class"),
                "effective_status": effective_status,
                "expected_source_apis": list(expected_apis),
                "observed_signal_count": observed,
                "is_stale": is_stale,
                "is_silent": is_silent,
                "is_failing": is_failing,
                "is_alive_but_no_db_progress": is_no_db_progress,
                "override_active": override_active,
            }
        )

    unmapped_source_apis = sorted(
        api for api in counts_by_source_api if api not in used_source_apis
    )
    outbox_summary = dict(outbox_health or {"available": False})
    outbox_has_stalled_work = bool(
        int(outbox_summary.get("pending_due_count") or 0) > 0
        or int(outbox_summary.get("failed_count") or 0) > 0
        or int(outbox_summary.get("stale_processing_count") or 0) > 0
    )
    db_progressed_but_drain_stalled = (
        producer_progress_seen and outbox_has_stalled_work
    )
    if db_progressed_but_drain_stalled:
        warnings.append(
            "producer proof shows DB progress but Notion outbox has due, failed, "
            "or stale processing work"
        )

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": _utc_now_iso() if now is None else now.astimezone(timezone.utc).isoformat(),
        "db_path": str(db_path) if db_path is not None else None,
        "lookback_days": lookback_days,
        "signal_counts": counts_list,
        "outbox_health": outbox_summary,
        "collectors": collectors,
        "summary": {
            "total": len(collectors),
            "by_effective_status": dict(sorted(by_status.items())),
            "silent_count": silent_count,
            "stale_count": stale_count,
            "failing_count": failing_count,
            "alive_but_no_db_progress_count": alive_but_no_db_progress_count,
            "alive_but_no_db_progress_collectors": (
                alive_but_no_db_progress_collectors
            ),
            "producer_progress_seen": producer_progress_seen,
            "db_progressed_but_drain_stalled": db_progressed_but_drain_stalled,
            "override_active_count": override_active_count,
            "override_active_collectors": override_active_collectors,
            "unmapped_source_apis": unmapped_source_apis,
            "warnings": warnings,
        },
    }


def render_table(report: Mapping[str, Any]) -> str:
    """Render a human-readable table from the structured report."""
    rows: list[tuple[str, ...]] = [
        (
            "collector",
            "effective",
            "last_run",
            "cadence_h",
            "fails",
            "obs_signals",
            "flags",
        )
    ]
    for c in report.get("collectors", []):
        flags = []
        if c.get("is_silent"):
            flags.append("SILENT")
        if c.get("is_stale"):
            flags.append("STALE")
        if c.get("is_failing"):
            flags.append("FAILING")
        if c.get("is_alive_but_no_db_progress"):
            flags.append("NO_DB_PROGRESS")
        if c.get("override_active"):
            flags.append("OVERRIDE")
        rows.append(
            (
                str(c["name"]),
                str(c.get("effective_status", "")),
                str(c.get("last_run_status", "")),
                str(c.get("expected_cadence_hours", "")),
                str(c.get("consecutive_failures", "")),
                str(c.get("observed_signal_count", "")),
                ",".join(flags) or "-",
            )
        )

    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out_lines: list[str] = []
    for idx, r in enumerate(rows):
        line = "  ".join(r[i].ljust(widths[i]) for i in range(len(r)))
        out_lines.append(line)
        if idx == 0:
            out_lines.append("  ".join("-" * widths[i] for i in range(len(r))))

    summary = report.get("summary", {})
    out_lines.append("")
    out_lines.append(
        f"Totals: {summary.get('total', 0)} collectors | "
        f"silent={summary.get('silent_count', 0)} | "
        f"stale={summary.get('stale_count', 0)} | "
        f"failing={summary.get('failing_count', 0)} | "
        f"no_db_progress={summary.get('alive_but_no_db_progress_count', 0)} | "
        f"drain_stalled={summary.get('db_progressed_but_drain_stalled', False)} | "
        f"override_active={summary.get('override_active_count', 0)}"
    )
    outbox = report.get("outbox_health") or {}
    if outbox.get("available"):
        out_lines.append(
            "Outbox: "
            f"pending_due={outbox.get('pending_due_count', 0)} | "
            f"failed={outbox.get('failed_count', 0)} | "
            f"stale_processing={outbox.get('stale_processing_count', 0)} | "
            f"last_successful_send_at={outbox.get('last_successful_send_at')}"
        )
    by_status = summary.get("by_effective_status", {})
    if by_status:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(by_status.items()))
        out_lines.append(f"By effective_status: {parts}")
    unmapped = summary.get("unmapped_source_apis", [])
    if unmapped:
        out_lines.append(f"Unmapped source_apis observed: {', '.join(unmapped)}")
    warnings = summary.get("warnings", [])
    if warnings:
        out_lines.append("Warnings:")
        for w in warnings:
            out_lines.append(f"  - {w}")
    return "\n".join(out_lines)


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="collector-health",
        description=(
            "Read-only collector health snapshot combining heartbeat state and "
            "signal-volume aggregations from the signals database."
        ),
    )
    parser.add_argument(
        "--db",
        default=os.getenv("DISCOVERY_DB_PATH", DEFAULT_DB_PATH),
        help="Path to signals.db (default: $DISCOVERY_DB_PATH or signals.db).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"Lookback window in days (default: {DEFAULT_LOOKBACK_DAYS}).",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="Override collector state path (default: $COLLECTOR_STATE_PATH or state/collectors.json).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Override collector config path (default: $COLLECTOR_CONFIG_PATH or config/collectors.yaml).",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format (default: table).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    state = load_collector_state(args.state, config_path=args.config)
    counts = aggregate_signal_counts(args.db, lookback_days=args.lookback_days)
    outbox_health = aggregate_outbox_health(args.db)
    report = build_health_report(
        state,
        counts,
        db_path=args.db,
        lookback_days=args.lookback_days,
        config_path=args.config,
        outbox_health=outbox_health,
    )
    if args.format == "json":
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_table(report) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

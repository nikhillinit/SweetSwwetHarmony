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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _observed_count(
    expected_source_apis: Sequence[str],
    counts_by_source_api: Mapping[str, int],
) -> int:
    return sum(counts_by_source_api.get(api, 0) for api in expected_source_apis)


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
) -> dict[str, Any]:
    """Combine heartbeat state and signal counts into a structured report."""
    if expected_source_apis_by_collector is None:
        expected_source_apis_by_collector = DEFAULT_EXPECTED_SOURCE_APIS_BY_COLLECTOR

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
                "effective_status": effective_status,
                "expected_source_apis": list(expected_apis),
                "observed_signal_count": observed,
                "is_stale": is_stale,
                "is_silent": is_silent,
                "is_failing": is_failing,
            }
        )

    unmapped_source_apis = sorted(
        api for api in counts_by_source_api if api not in used_source_apis
    )

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": _utc_now_iso() if now is None else now.astimezone(timezone.utc).isoformat(),
        "db_path": str(db_path) if db_path is not None else None,
        "lookback_days": lookback_days,
        "signal_counts": counts_list,
        "collectors": collectors,
        "summary": {
            "total": len(collectors),
            "by_effective_status": dict(sorted(by_status.items())),
            "silent_count": silent_count,
            "stale_count": stale_count,
            "failing_count": failing_count,
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
        f"failing={summary.get('failing_count', 0)}"
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
    report = build_health_report(
        state,
        counts,
        db_path=args.db,
        lookback_days=args.lookback_days,
    )
    if args.format == "json":
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_table(report) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

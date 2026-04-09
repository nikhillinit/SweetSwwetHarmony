#!/usr/bin/env python3
"""
freshness_watchdog.py — per-collector freshness guard for the Discovery Engine.

Implements REQ LIV-02 from .planning/REQUIREMENTS.md and the Phase 1 success
criterion 1 of .planning/ROADMAP.md:

    "Pipeline freshness restored: python scripts/red-team-hybrid/freshness_watchdog.py
     exits 0 by 2026-04-13 (R19 fix verified)"

Context
-------
R19 (see docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md) is the
Showstopper-class risk that the collection pipeline silently froze on
2026-03-01 while the existing health check (which uses a 30-day rolling
lookback window) happily reported HEALTHY. This watchdog closes that gap:
it exits non-zero the moment any listed operational collector stops
producing fresh rows, rather than quietly dropping off the window.

What "operational" means
------------------------
An operational collector is one that (a) is configured to run at daily+
cadence and (b) is expected to produce at least one new signal row within
the freshness threshold under normal pipeline operation. Rare-event or
key-gated collectors (SEC Form D filings, USPTO patents, github trending)
are NOT in the default operational set because low-volume quiet days would
cause false-positive freshness alerts. Those collectors are still reported
as INFORMATIONAL and can be promoted to strict by the --operational flag.

Freshness is measured against signals.created_at (row insert time), NOT
signals.detected_at. detected_at is the collector's semantic timestamp
(e.g. publication date for news_api, paper date for arxiv), which may lag
actual ingest time. Row insert time is what actually tells us whether the
collector ran and wrote data.

Usage
-----
    # Default: 36h threshold, default operational set, text output
    python scripts/red-team-hybrid/freshness_watchdog.py

    # CI / automation: JSON output, non-zero exit on any stale operational
    python scripts/red-team-hybrid/freshness_watchdog.py --json

    # Custom threshold (e.g. weekly cron)
    python scripts/red-team-hybrid/freshness_watchdog.py --threshold-hours 168

    # Override operational set
    python scripts/red-team-hybrid/freshness_watchdog.py --operational hacker_news,arxiv

    # Alternate DB path
    python scripts/red-team-hybrid/freshness_watchdog.py --db /path/to/signals.db

Exit codes
----------
    0  all listed operational collectors fresh within threshold
    1  at least one listed operational collector is stale
    2  operational error (DB unreadable, schema mismatch, etc.)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Default operational collectors. These are the collectors that produce new
# signals at daily+ cadence under normal pipeline operation. Changes here
# require updating .planning/REQUIREMENTS.md LIV-02 and the Phase 1 success
# criterion rationale.
DEFAULT_OPERATIONAL_COLLECTORS: tuple[str, ...] = (
    "hacker_news",
    "arxiv",
    "rss_feeds",
    "news_api",
)

DEFAULT_THRESHOLD_HOURS: int = 36
DEFAULT_DB_PATH: str = "signals.db"


def _parse_iso(ts: str) -> datetime:
    """
    Parse an ISO-8601 timestamp string as stored in signals.created_at.

    The column contains a mix of formats observed in the current DB:
        2026-04-08T01:28:24.486672+00:00   (microseconds, +00:00 offset)
        2026-02-25T19:11:14-05:00          (no microseconds, -05:00 offset)
        2026-01-10T12:18:09.019836         (no offset, naive)

    datetime.fromisoformat handles all of these in Python 3.11+. For naive
    timestamps we assume UTC, matching how recent rows are written.
    """
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def query_freshness(db_path: Path) -> dict[str, datetime]:
    """
    Return {source_api: max_created_at_as_aware_datetime}.

    Raises sqlite3.Error if the DB is unreadable or the schema does not match.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"signals DB not found: {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT source_api, MAX(created_at) "
            "FROM signals "
            "GROUP BY source_api"
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    result: dict[str, datetime] = {}
    for source_api, max_created in rows:
        if source_api is None or max_created is None:
            continue
        result[source_api] = _parse_iso(max_created)
    return result


def classify(
    freshness: dict[str, datetime],
    operational: tuple[str, ...],
    threshold: timedelta,
    now: datetime,
) -> list[dict[str, Any]]:
    """
    Build per-collector status records.

    Each record has:
        source_api:    collector name
        category:      "operational" | "informational"
        last_created:  ISO string or None
        age_hours:     float or None
        status:        "FRESH" | "STALE" | "MISSING" | "UNKNOWN"
    """
    all_collectors = set(freshness.keys()) | set(operational)
    records: list[dict[str, Any]] = []

    for source_api in sorted(all_collectors):
        is_operational = source_api in operational
        category = "operational" if is_operational else "informational"
        last_created = freshness.get(source_api)

        if last_created is None:
            status = "MISSING" if is_operational else "UNKNOWN"
            records.append(
                {
                    "source_api": source_api,
                    "category": category,
                    "last_created": None,
                    "age_hours": None,
                    "status": status,
                }
            )
            continue

        age = now - last_created
        age_hours = age.total_seconds() / 3600.0
        if is_operational:
            status = "FRESH" if age <= threshold else "STALE"
        else:
            # Informational collectors always report UNKNOWN for the gate;
            # we still surface the age so operators can eyeball them.
            status = "UNKNOWN"

        records.append(
            {
                "source_api": source_api,
                "category": category,
                "last_created": last_created.isoformat(),
                "age_hours": round(age_hours, 2),
                "status": status,
            }
        )
    return records


def verdict(records: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """
    Decide the overall exit code and gather failure reasons.

    Returns (exit_code, failure_reasons).
    """
    failures: list[str] = []
    for rec in records:
        if rec["category"] != "operational":
            continue
        if rec["status"] == "STALE":
            failures.append(
                f"{rec['source_api']}: {rec['age_hours']}h since last ingest"
            )
        elif rec["status"] == "MISSING":
            failures.append(
                f"{rec['source_api']}: no signals in DB (operational collector)"
            )
    return (1 if failures else 0, failures)


def render_text(
    records: list[dict[str, Any]],
    threshold: timedelta,
    now: datetime,
    failures: list[str],
) -> str:
    """Human-readable output for terminal / PR comments."""
    lines: list[str] = []
    lines.append("Freshness watchdog - signals.db per-collector ingest times")
    lines.append(f"  checked at:        {now.isoformat()}")
    lines.append(f"  threshold:         {int(threshold.total_seconds() // 3600)}h")
    lines.append("")

    op_records = [r for r in records if r["category"] == "operational"]
    info_records = [r for r in records if r["category"] == "informational"]

    lines.append("  OPERATIONAL (gate):")
    if not op_records:
        lines.append("    (none configured)")
    for rec in op_records:
        age = (
            f"{rec['age_hours']}h"
            if rec["age_hours"] is not None
            else "(no rows)"
        )
        lines.append(
            f"    [{rec['status']:<7s}] {rec['source_api']:<16s} "
            f"last_created={rec['last_created']} age={age}"
        )

    lines.append("")
    lines.append("  INFORMATIONAL (no gate):")
    if not info_records:
        lines.append("    (no other collectors seen in DB)")
    for rec in info_records:
        age = (
            f"{rec['age_hours']}h"
            if rec["age_hours"] is not None
            else "(no rows)"
        )
        lines.append(
            f"    [{rec['status']:<7s}] {rec['source_api']:<16s} "
            f"last_created={rec['last_created']} age={age}"
        )

    lines.append("")
    if failures:
        lines.append("FAIL: operational collectors stale or missing:")
        for reason in failures:
            lines.append(f"  - {reason}")
    else:
        lines.append("OK: all operational collectors fresh within threshold.")
    return "\n".join(lines)


def render_json(
    records: list[dict[str, Any]],
    threshold: timedelta,
    now: datetime,
    failures: list[str],
    exit_code: int,
) -> str:
    """Machine-readable output for CI, cron, dashboards."""
    payload = {
        "checked_at": now.isoformat(),
        "threshold_hours": int(threshold.total_seconds() // 3600),
        "exit_code": exit_code,
        "status": "OK" if exit_code == 0 else "FAIL",
        "collectors": records,
        "failures": failures,
    }
    return json.dumps(payload, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Freshness watchdog for the Discovery Engine signals DB. "
            "Exits non-zero if any operational collector is older than "
            "the threshold. See REQ LIV-02."
        )
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"path to signals.db (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--threshold-hours",
        type=float,
        default=DEFAULT_THRESHOLD_HOURS,
        help=f"freshness threshold in hours (default: {DEFAULT_THRESHOLD_HOURS})",
    )
    parser.add_argument(
        "--operational",
        default=",".join(DEFAULT_OPERATIONAL_COLLECTORS),
        help=(
            "comma-separated list of operational collectors to gate on "
            f"(default: {','.join(DEFAULT_OPERATIONAL_COLLECTORS)})"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of human-readable text",
    )
    args = parser.parse_args(argv)

    operational = tuple(
        name.strip() for name in args.operational.split(",") if name.strip()
    )
    threshold = timedelta(hours=args.threshold_hours)
    now = datetime.now(tz=timezone.utc)

    try:
        freshness = query_freshness(Path(args.db))
    except FileNotFoundError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2
    except sqlite3.Error as exc:
        sys.stderr.write(f"ERROR: database read failed: {exc}\n")
        return 2

    records = classify(freshness, operational, threshold, now)
    exit_code, failures = verdict(records)

    if args.json:
        sys.stdout.write(render_json(records, threshold, now, failures, exit_code))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_text(records, threshold, now, failures))
        sys.stdout.write("\n")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

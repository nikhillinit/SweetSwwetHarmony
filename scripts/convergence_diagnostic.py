#!/usr/bin/env python3
"""
Convergence diagnostic report.

Reports 11 sections:
1. Multi-source signal overlap (domain: keys only)
2. Promoted entities with >=2 sources (raw + eligible KPI)
3. news_api key distribution (run-scoped)
4. Publisher leakage count
5. api_calls per collector (explicit latest-run CTE)
6. SEC Edgar sic_matched distribution
7. Per-collector domain key counts (non-HN)
8. All-prefix multi-source overlap (all key types, not just domain:)
9. Key prefix distribution (signal counts and unique keys per prefix per collector)
10. Per-collector domain-key yield (domain vs name_loc vs total)
11. Multi-evidence-family convergence gate (Phase 0 MVP)

Usage:
    python scripts/convergence_diagnostic.py --db signals.db
    python scripts/convergence_diagnostic.py --db signals.db --latest-run
    python scripts/convergence_diagnostic.py --db signals.db --run-id abc123
    python scripts/convergence_diagnostic.py --db signals.db --json --out report.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from utils.company_name_extractor import is_blocked_domain
from utils.canonical_keys import NEWS_PUBLISHER_DOMAINS

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_latest_run_id(conn: sqlite3.Connection) -> str | None:
    """Get the latest run_id from pipeline_runs."""
    row = conn.execute(
        "SELECT run_id FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def _build_scope_filter(
    run_id: str | None = None,
    since_days: int | None = None,
    base_conditions: list[str] | None = None,
) -> tuple[str, list]:
    """Build a WHERE clause from scope + base conditions.

    Returns (where_clause, params) suitable for f"WHERE {where_clause}".
    Scope priority: run_id > since_days.
    """
    parts: list[str] = []
    params: list = []

    if base_conditions:
        parts.append("(" + " AND ".join(base_conditions) + ")")

    if run_id is not None:
        parts.append("""(rowid IN (
                SELECT s.rowid FROM signals s
                JOIN pipeline_runs pr ON pr.run_id = ?
                WHERE s.created_at >= pr.started_at
                  AND (pr.completed_at IS NULL OR s.created_at <= pr.completed_at)
            ))""")
        params.append(run_id)
    elif since_days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
        parts.append("(created_at >= ?)")
        params.append(cutoff)

    if not parts:
        return "1=1", []

    return " AND ".join(parts), params


def _determine_scope_mode(
    run_id: str | None, since_days: int | None
) -> str:
    """Return the active scope mode string."""
    if run_id:
        return "run_id"
    elif since_days is not None:
        return "since_days"
    return "all_time"


def _format_scope_description(
    run_id: str | None,
    since_days: int | None,
    resolved_from_latest: bool,
) -> str:
    """Human-readable scope description."""
    if run_id and resolved_from_latest:
        return f"Scoped to latest pipeline run: {run_id}"
    elif run_id:
        return f"Scoped to pipeline run: {run_id}"
    elif since_days is not None:
        return f"Scoped to last {since_days} days"
    return "All-time (section 5 defaults to latest run)"


def _section_1_multi_source_overlap(conn: sqlite3.Connection) -> list[dict]:
    """Multi-source signal overlap (informational)."""
    rows = conn.execute("""
        SELECT canonical_key, GROUP_CONCAT(DISTINCT source_api) as sources,
               COUNT(DISTINCT source_api) as n
        FROM signals WHERE canonical_key LIKE 'domain:%'
        GROUP BY canonical_key HAVING n >= 2
        ORDER BY n DESC
    """).fetchall()
    return [
        {"canonical_key": r[0], "sources": r[1], "source_count": r[2]}
        for r in rows
    ]


def _section_2_promoted_multi_source(conn: sqlite3.Connection) -> dict:
    """Promoted entities with >=2 sources (raw + eligible KPI)."""
    # Check if tables exist
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if "company_files" not in tables:
        return {"promoted_multi_source_raw": 0, "promoted_multi_source_eligible": 0, "details": []}

    # RAW count
    raw_rows = conn.execute("""
        SELECT cf.canonical_key, COUNT(DISTINCT s.source_api) as src_count
        FROM company_files cf
        JOIN signals s ON cf.canonical_key = s.canonical_key
        WHERE cf.status = 'promoted'
        GROUP BY cf.canonical_key HAVING src_count >= 2
    """).fetchall()

    # ELIGIBLE count (exclude rejected signals)
    has_signal_processing = "signal_processing" in tables
    if has_signal_processing:
        eligible_rows = conn.execute("""
            SELECT cf.canonical_key, COUNT(DISTINCT s.source_api) as src_count
            FROM company_files cf
            JOIN signals s ON cf.canonical_key = s.canonical_key
            LEFT JOIN signal_processing sp ON sp.signal_id = s.id
            WHERE cf.status = 'promoted'
              AND (sp.status IS NULL OR sp.status != 'rejected')
            GROUP BY cf.canonical_key HAVING src_count >= 2
        """).fetchall()
    else:
        eligible_rows = raw_rows  # No processing table, all are eligible

    return {
        "promoted_multi_source_raw": len(raw_rows),
        "promoted_multi_source_eligible": len(eligible_rows),
        "details": [{"canonical_key": r[0], "source_count": r[1]} for r in raw_rows],
    }


def _section_3_news_api_key_distribution(
    conn: sqlite3.Connection, run_id: str | None, since_days: int | None
) -> list[dict]:
    """news_api key distribution (scoped)."""
    where, params = _build_scope_filter(run_id, since_days, ["source_api = 'news_api'"])

    rows = conn.execute(f"""
        SELECT
            CASE
                WHEN canonical_key LIKE 'domain:%' THEN 'domain'
                WHEN canonical_key LIKE 'name_loc:%' THEN 'name_loc'
                WHEN canonical_key LIKE 'hash:%' THEN 'hash'
                ELSE 'other'
            END as key_type,
            COUNT(*) as count
        FROM signals
        WHERE {where}
        GROUP BY key_type
        ORDER BY count DESC
    """, params).fetchall()

    return [{"key_type": r[0], "count": r[1]} for r in rows]


def _section_4_publisher_leakage(
    conn: sqlite3.Connection, run_id: str | None, since_days: int | None
) -> dict:
    """Publisher leakage count (first-class line item)."""
    where, params = _build_scope_filter(run_id, since_days, ["canonical_key LIKE 'domain:%'"])

    rows = conn.execute(
        f"SELECT canonical_key FROM signals WHERE {where}",
        params,
    ).fetchall()

    leaked = []
    for (key,) in rows:
        host = key[len("domain:"):]
        if is_blocked_domain(host):
            leaked.append(key)

    return {
        "publisher_domain_keys_total": len(leaked),
        "top_leaked": list(set(leaked))[:10],
    }


def _section_5_api_calls(conn: sqlite3.Connection, run_id: str | None) -> list[dict]:
    """api_calls per collector (explicit latest-run CTE)."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if "collector_metrics" not in tables or "pipeline_runs" not in tables:
        return []

    if run_id:
        rows = conn.execute("""
            SELECT collector_name, api_calls, rate_limit_hits, signals_found, status
            FROM collector_metrics
            WHERE run_id = ?
            ORDER BY collector_name
        """, (run_id,)).fetchall()
    else:
        rows = conn.execute("""
            WITH latest AS (
                SELECT run_id FROM pipeline_runs ORDER BY started_at DESC LIMIT 1
            )
            SELECT cm.collector_name, cm.api_calls, cm.rate_limit_hits,
                   cm.signals_found, cm.status
            FROM collector_metrics cm
            JOIN latest ON cm.run_id = latest.run_id
            ORDER BY cm.collector_name
        """).fetchall()

    return [
        {
            "collector_name": r[0],
            "api_calls": r[1],
            "rate_limit_hits": r[2],
            "signals_found": r[3],
            "status": r[4],
        }
        for r in rows
    ]


def _section_6_sic_matched(conn: sqlite3.Connection) -> list[dict]:
    """SEC Edgar sic_matched distribution."""
    rows = conn.execute("""
        SELECT json_extract(raw_data, '$.sic_matched') as sic_matched, COUNT(*)
        FROM signals WHERE source_api = 'sec_edgar'
        GROUP BY sic_matched
    """).fetchall()
    return [{"sic_matched": r[0], "count": r[1]} for r in rows]


def _section_7_domain_key_counts(conn: sqlite3.Connection) -> list[dict]:
    """Per-collector domain key counts (non-HN)."""
    rows = conn.execute("""
        SELECT source_api,
            SUM(CASE WHEN canonical_key LIKE 'domain:%' THEN 1 ELSE 0 END) as domain_keys,
            COUNT(*) as total
        FROM signals WHERE source_api != 'hacker_news'
        GROUP BY source_api
        ORDER BY domain_keys DESC
    """).fetchall()
    return [
        {"source_api": r[0], "domain_keys": r[1], "total": r[2]}
        for r in rows
    ]


def _section_8_all_prefix_overlap(conn: sqlite3.Connection) -> list[dict]:
    """Multi-source signal overlap across ALL key prefixes (not just domain:)."""
    rows = conn.execute("""
        SELECT canonical_key, GROUP_CONCAT(DISTINCT source_api) as sources,
               COUNT(DISTINCT source_api) as n
        FROM signals
        GROUP BY canonical_key HAVING n >= 2
        ORDER BY n DESC
    """).fetchall()
    return [
        {"canonical_key": r[0], "sources": r[1], "source_count": r[2]}
        for r in rows
    ]


def _section_9_key_prefix_distribution(conn: sqlite3.Connection) -> list[dict]:
    """Key prefix distribution: signal counts and unique keys per prefix per collector."""
    rows = conn.execute("""
        SELECT
            source_api,
            CASE
                WHEN canonical_key LIKE 'domain:%' THEN 'domain'
                WHEN canonical_key LIKE 'name_loc:%' THEN 'name_loc'
                ELSE 'other'
            END as prefix,
            COUNT(*) as signal_count,
            COUNT(DISTINCT canonical_key) as unique_keys
        FROM signals
        GROUP BY source_api, prefix
        ORDER BY source_api, signal_count DESC
    """).fetchall()
    return [
        {
            "source_api": r[0],
            "prefix": r[1],
            "signal_count": r[2],
            "unique_keys": r[3],
        }
        for r in rows
    ]


def _section_10_per_collector_domain_yield(conn: sqlite3.Connection) -> list[dict]:
    """Per-collector domain-key yield: domain vs name_loc vs total."""
    rows = conn.execute("""
        SELECT
            source_api,
            SUM(CASE WHEN canonical_key LIKE 'domain:%' THEN 1 ELSE 0 END) as domain_key_count,
            SUM(CASE WHEN canonical_key LIKE 'name_loc:%' THEN 1 ELSE 0 END) as name_loc_count,
            COUNT(*) as total
        FROM signals
        GROUP BY source_api
        ORDER BY total DESC
    """).fetchall()
    return [
        {
            "source_api": r[0],
            "domain_key_count": r[1],
            "name_loc_count": r[2],
            "total": r[3],
            "domain_yield_pct": round(r[1] / r[3] * 100, 1) if r[3] > 0 else 0.0,
        }
        for r in rows
    ]


def _section_11_multi_family_convergence(conn: sqlite3.Connection, since_days: int = 30) -> dict:
    """Multi-evidence-family entity convergence (LOB v7 Phase 0 Gate).

    Gate criteria (redesigned):
    - >=10 entities with 2+ evidence families (30d)
    - >=3 distinct evidence families collectively across all collectors

    Correctness:
    - Uses detected_at (not created_at) for temporal windowing
    - Filters out NULL/empty canonical_key
    - Excludes evidence_family='unknown'
    - Schema preflight: fails fast if required columns are missing
    """
    # A2: Schema compatibility guard — fail fast if columns are missing
    required_columns = {"evidence_family", "canonical_key", "detected_at", "source_api"}
    col_info = conn.execute("PRAGMA table_info(signals)").fetchall()
    existing_columns = {row[1] for row in col_info}
    missing = required_columns - existing_columns
    if missing:
        raise RuntimeError(
            f"Missing columns in signals table: {sorted(missing)}. "
            "Run schema migrations to update."
        )

    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%d")

    rows = conn.execute("""
        SELECT canonical_key,
               COUNT(DISTINCT evidence_family) AS family_count,
               GROUP_CONCAT(DISTINCT evidence_family) AS families,
               COUNT(DISTINCT source_api) AS source_count
        FROM signals
        WHERE evidence_family IS NOT NULL
          AND evidence_family != 'unknown'
          AND canonical_key IS NOT NULL
          AND canonical_key != ''
          AND detected_at >= ?
        GROUP BY canonical_key
        HAVING family_count >= 2
        ORDER BY family_count DESC
    """, (cutoff,)).fetchall()

    entity_count = len(rows)

    # A1: Collective family diversity + active collector count
    diversity_row = conn.execute("""
        SELECT COUNT(DISTINCT evidence_family),
               COUNT(DISTINCT source_api)
        FROM signals
        WHERE evidence_family IS NOT NULL
          AND evidence_family != 'unknown'
          AND canonical_key IS NOT NULL
          AND canonical_key != ''
          AND detected_at >= ?
    """, (cutoff,)).fetchone()
    total_distinct_families, active_collectors = diversity_row

    # Legacy: per-collector family counts (kept for transitional observability)
    collector_families = conn.execute("""
        SELECT source_api, COUNT(DISTINCT evidence_family) AS families
        FROM signals
        WHERE evidence_family IS NOT NULL
          AND evidence_family != 'unknown'
          AND canonical_key IS NOT NULL
          AND canonical_key != ''
          AND detected_at >= ?
        GROUP BY source_api
        HAVING families >= 2
    """, (cutoff,)).fetchall()
    collectors_with_2plus_families = len(collector_families)
    families_per_collector = {r[0]: r[1] for r in collector_families}

    # Redesigned gate: collective family diversity instead of per-collector
    mvp_pass = entity_count >= 10 and total_distinct_families >= 3

    # Forward-looking observability warning
    collector_diversity_warning = None
    if active_collectors < 2:
        collector_diversity_warning = (
            f"Only {active_collectors} active collector(s) in window — "
            "diversity depends on single source"
        )

    return {
        "window_days": since_days,
        "cutoff_date": cutoff,
        "entities_with_2plus_families": entity_count,
        "entities": [{"key": r[0], "families": r[2], "family_count": r[1]} for r in rows[:20]],
        "total_distinct_families": total_distinct_families,
        "active_collectors": active_collectors,
        "collector_diversity_warning": collector_diversity_warning,
        "collectors_with_2plus_families": collectors_with_2plus_families,
        "families_per_collector": families_per_collector,
        "mvp_gate_pass": mvp_pass,
        "verdict": "PASS" if mvp_pass else "FAIL",
    }


def run_diagnostic(
    db_path: str,
    run_id: str | None = None,
    latest_run: bool = False,
    since_days: int | None = None,
    json_output: bool = False,
    output_path: str | None = None,
) -> dict:
    """Run convergence diagnostic and return report dict."""
    conn = sqlite3.connect(db_path)

    # Resolve run_id
    resolved_from_latest = False
    if latest_run and not run_id:
        run_id = _get_latest_run_id(conn)
        if run_id:
            resolved_from_latest = True
            print(f"Using latest run_id: {run_id}")
        else:
            logger.warning("No pipeline_runs found — running without run scope.")

    scope_mode = _determine_scope_mode(run_id, since_days)
    scope_description = _format_scope_description(run_id, since_days, resolved_from_latest)

    if scope_mode == "run_id":
        applies_to = [3, 4, 5]
    elif scope_mode == "since_days":
        applies_to = [3, 4]
    else:
        applies_to = []

    active_scope = {
        "mode": scope_mode,
        "run_id": run_id,
        "since_days": since_days,
        "resolved_from_latest_run": resolved_from_latest,
        "applies_to_sections": applies_to,
        "section_5_default": None if run_id else "latest_run",
        "description": scope_description,
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": db_path,
        "scoped_run_id": run_id,
        "active_scope": active_scope,
        "sections": {},
    }

    # Section 1
    s1 = _section_1_multi_source_overlap(conn)
    report["sections"]["multi_source_overlap"] = s1

    # Section 2
    s2 = _section_2_promoted_multi_source(conn)
    report["sections"]["promoted_multi_source"] = s2

    # Section 3
    s3 = _section_3_news_api_key_distribution(conn, run_id, since_days)
    report["sections"]["news_api_key_distribution"] = s3

    # Section 4
    s4 = _section_4_publisher_leakage(conn, run_id, since_days)
    report["sections"]["publisher_leakage"] = s4

    # Section 5
    s5 = _section_5_api_calls(conn, run_id)
    report["sections"]["api_calls_per_collector"] = s5

    # Section 6
    s6 = _section_6_sic_matched(conn)
    report["sections"]["sic_matched_distribution"] = s6

    # Section 7
    s7 = _section_7_domain_key_counts(conn)
    report["sections"]["domain_key_counts"] = s7

    # Section 8
    s8 = _section_8_all_prefix_overlap(conn)
    report["sections"]["all_prefix_overlap"] = s8

    # Section 9
    s9 = _section_9_key_prefix_distribution(conn)
    report["sections"]["key_prefix_distribution"] = s9

    # Section 10
    s10 = _section_10_per_collector_domain_yield(conn)
    report["sections"]["per_collector_domain_yield"] = s10

    # Section 11
    s11 = _section_11_multi_family_convergence(conn)
    report["sections"]["multi_family_convergence"] = s11

    conn.close()

    # Print human-readable report
    print("\n" + "=" * 60)
    print("CONVERGENCE DIAGNOSTIC REPORT")
    print("=" * 60)

    print(f"\nDB: {db_path}")
    print(f"Scope: {scope_description}")
    if scope_mode == "since_days":
        print("  Note: Section 5 ignores --since (uses latest run).")

    print(f"\n--- 1. Multi-Source Signal Overlap ---")
    if s1:
        for item in s1:
            print(f"  {item['canonical_key']}: {item['sources']} ({item['source_count']} sources)")
    else:
        print("  No multi-source overlap found.")

    print(f"\n--- 2. Promoted Multi-Source KPI ---")
    print(f"  Raw (all signals):      {s2['promoted_multi_source_raw']}")
    print(f"  Eligible (non-rejected): {s2['promoted_multi_source_eligible']}")

    print(f"\n--- 3. news_api Key Distribution ---")
    if s3:
        for item in s3:
            print(f"  {item['key_type']}: {item['count']}")
    else:
        print("  No news_api signals found.")

    print(f"\n--- 4. Publisher Leakage ---")
    print(f"  Publisher domain keys total: {s4['publisher_domain_keys_total']}")
    if s4["top_leaked"]:
        for key in s4["top_leaked"][:5]:
            print(f"    {key}")

    print(f"\n--- 5. API Calls per Collector ---")
    if s5:
        for item in s5:
            print(
                f"  {item['collector_name']}: "
                f"api_calls={item['api_calls']}, "
                f"rate_limit_hits={item['rate_limit_hits']}, "
                f"signals={item['signals_found']}, "
                f"status={item['status']}"
            )
    else:
        print("  No collector metrics found.")

    print(f"\n--- 6. SEC Edgar SIC Distribution ---")
    if s6:
        for item in s6:
            print(f"  sic_matched={item['sic_matched']}: {item['count']}")
    else:
        print("  No SEC Edgar signals found.")

    print(f"\n--- 7. Domain Key Counts (non-HN) ---")
    if s7:
        for item in s7:
            print(
                f"  {item['source_api']}: "
                f"domain_keys={item['domain_keys']}/{item['total']}"
            )
    else:
        print("  No non-HN signals found.")

    print(f"\n--- 8. All-Prefix Multi-Source Overlap ---")
    if s8:
        for item in s8:
            print(f"  {item['canonical_key']}: {item['sources']} ({item['source_count']} sources)")
    else:
        print("  No multi-source overlap found across any prefix.")

    print(f"\n--- 9. Key Prefix Distribution ---")
    if s9:
        # Group by source_api for readability
        current_api = None
        for item in s9:
            if item['source_api'] != current_api:
                current_api = item['source_api']
                print(f"  [{current_api}]")
            pct = (
                round(item['signal_count'] / sum(
                    i['signal_count'] for i in s9 if i['source_api'] == current_api
                ) * 100, 1)
            )
            print(
                f"    {item['prefix']}: "
                f"{item['signal_count']} signals, "
                f"{item['unique_keys']} unique keys "
                f"({pct}%)"
            )
    else:
        print("  No signals found.")

    print(f"\n--- 10. Per-Collector Domain-Key Yield ---")
    if s10:
        for item in s10:
            print(
                f"  {item['source_api']}: "
                f"domain={item['domain_key_count']}, "
                f"name_loc={item['name_loc_count']}, "
                f"total={item['total']}, "
                f"domain_yield={item['domain_yield_pct']}%"
            )
    else:
        print("  No signals found.")

    print(f"\n--- 11. Multi-Family Convergence Gate (30d) ---")
    print(
        f"  Entities with 2+ families: {s11['entities_with_2plus_families']} "
        f"(threshold >= 10)"
    )
    print(
        f"  Total distinct families: {s11['total_distinct_families']} "
        f"(threshold >= 3)"
    )
    print(
        f"  Active collectors: {s11['active_collectors']}"
    )
    if s11.get("collector_diversity_warning"):
        print(f"  WARNING: {s11['collector_diversity_warning']}")
    print(
        f"  Collectors with 2+ families: {s11['collectors_with_2plus_families']} "
        f"(legacy, informational)"
    )
    if s11.get("families_per_collector"):
        for coll, fam_count in sorted(s11["families_per_collector"].items()):
            print(f"    {coll}: {fam_count} families")
    print(f"  Verdict: {s11['verdict']}")

    print("\n" + "=" * 60)

    # JSON output
    if json_output and output_path:
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nJSON report saved to: {output_path}")

    return report


def main():
    parser = argparse.ArgumentParser(description="Convergence diagnostic report")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--run-id", help="Scope sections 3-5 to specific run_id")
    parser.add_argument("--latest-run", action="store_true",
                        help="Auto-detect latest pipeline_runs.run_id")
    parser.add_argument("--since", type=int, help="Scope to signals within N days")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--out", help="Output file path for JSON report")
    args = parser.parse_args()

    if args.since is not None and args.since < 1:
        parser.error("--since must be >= 1")

    run_diagnostic(
        db_path=args.db,
        run_id=args.run_id,
        latest_run=args.latest_run,
        since_days=args.since,
        json_output=args.json,
        output_path=args.out,
    )


if __name__ == "__main__":
    main()

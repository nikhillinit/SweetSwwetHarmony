"""Convergence KPI computation for v6.6.2 canary runbook.

Measures multi-source convergence: how many canonical_key_v2 values
have signals from 2+ distinct evidence families.

Schema guard: hard-fails if schema version < 43.
Importable by CLI and tests.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Get current schema version from schema_migrations table."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return row[0] if row and row[0] else 0
    except sqlite3.OperationalError:
        return 0


async def run(
    db_path: str,
    days: int = 30,
    exclude_unlinked_buzz: bool = True,
    baseline_unknown_rate: Optional[float] = None,
    baseline_kpi_report: Optional[str] = None,
    unknown_delta_max_pp: float = 10.0,
    unlinked_delta_max_pp: float = 10.0,
) -> Dict[str, Any]:
    """Compute convergence KPI metrics.

    Returns a report dict. Hard-fails if schema < v43.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        # Schema guard
        version = _get_schema_version(conn)
        if version < 43:
            return {
                "ok": False,
                "error": f"Schema version {version} < 43. Run migrations first.",
                "schema_version": version,
            }

        # Total signals in window
        total_sql = (
            "SELECT COUNT(*) FROM signals "
            "WHERE detected_at >= datetime('now', ?)"
        )
        total = conn.execute(total_sql, (f"-{days} days",)).fetchone()[0]

        # Unknown family rate
        unknown_sql = (
            "SELECT COUNT(*) FROM signals "
            "WHERE detected_at >= datetime('now', ?) "
            "AND (evidence_family IS NULL OR evidence_family = 'unknown')"
        )
        unknown_count = conn.execute(unknown_sql, (f"-{days} days",)).fetchone()[0]
        unknown_rate = (unknown_count / total * 100) if total > 0 else 0.0

        # Unlinked buzz rate
        unlinked_sql = (
            "SELECT COUNT(*) FROM signals "
            "WHERE detected_at >= datetime('now', ?) "
            "AND canonical_key_v2 LIKE 'name_loc:unlinked_buzz_%'"
        )
        unlinked_count = conn.execute(unlinked_sql, (f"-{days} days",)).fetchone()[0]
        unlinked_rate = (unlinked_count / total * 100) if total > 0 else 0.0

        # Core KPI: keys with 2+ distinct evidence families
        exclude_clause = ""
        if exclude_unlinked_buzz:
            exclude_clause = "AND canonical_key_v2 NOT LIKE 'name_loc:unlinked_buzz_%'"

        convergence_sql = f"""
            SELECT canonical_key_v2, COUNT(DISTINCT evidence_family) as families
            FROM signals
            WHERE canonical_key_v2 IS NOT NULL
              AND evidence_family IS NOT NULL AND evidence_family <> 'unknown'
              AND detected_at >= datetime('now', ?)
              {exclude_clause}
            GROUP BY canonical_key_v2
            HAVING families >= 2
        """
        converged_keys = conn.execute(
            convergence_sql, (f"-{days} days",)
        ).fetchall()
        keys_2plus_families = len(converged_keys)

        # Keys with 2+ source APIs
        source_api_sql = f"""
            SELECT canonical_key_v2, COUNT(DISTINCT source_api) as apis
            FROM signals
            WHERE canonical_key_v2 IS NOT NULL
              AND detected_at >= datetime('now', ?)
              {exclude_clause}
            GROUP BY canonical_key_v2
            HAVING apis >= 2
        """
        converged_apis = conn.execute(
            source_api_sql, (f"-{days} days",)
        ).fetchall()
        keys_2plus_apis = len(converged_apis)

        # Per-source breakdown
        source_sql = (
            "SELECT source_api, COUNT(*) as cnt, "
            "SUM(CASE WHEN evidence_family IS NULL OR evidence_family = 'unknown' THEN 1 ELSE 0 END) as unknown_cnt "
            "FROM signals "
            "WHERE detected_at >= datetime('now', ?) "
            "GROUP BY source_api ORDER BY cnt DESC"
        )
        source_breakdown = []
        for row in conn.execute(source_sql, (f"-{days} days",)).fetchall():
            source_breakdown.append({
                "source_api": row[0],
                "count": row[1],
                "unknown_family_count": row[2],
            })

        return {
            "ok": True,
            "schema_version": version,
            "days": days,
            "total_signals": total,
            "keys_with_2plus_families": keys_2plus_families,
            "keys_with_2plus_source_apis": keys_2plus_apis,
            "unknown_family_rate": round(unknown_rate, 2),
            "unlinked_buzz_rate": round(unlinked_rate, 2),
            "per_source_breakdown": source_breakdown,
            "exclude_unlinked_buzz": exclude_unlinked_buzz,
        }

    finally:
        conn.close()

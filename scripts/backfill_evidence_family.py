"""Backfill evidence_family column for existing signals.

Chunked SELECT→UPDATE loop. Importable by CLI and tests.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def run(
    db_path: str,
    dry_run: bool = True,
    chunk_size: int = 1000,
    rewrite_unknown: bool = False,
    source_api: Optional[str] = None,
    signal_type: Optional[str] = None,
    baseline_unknown_rate: Optional[float] = None,
    unknown_delta_max_pp: float = 10.0,
) -> Dict[str, Any]:
    """Backfill evidence_family for signals where it is NULL (or 'unknown' if rewrite_unknown).

    Returns a report dict with metrics.
    """
    from verification.evidence_families import get_family

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        # Build WHERE clause
        conditions = []
        params: list[Any] = []

        if rewrite_unknown:
            conditions.append("(evidence_family IS NULL OR evidence_family = 'unknown')")
        else:
            conditions.append("evidence_family IS NULL")

        if source_api:
            conditions.append("source_api = ?")
            params.append(source_api)
        if signal_type:
            conditions.append("signal_type = ?")
            params.append(signal_type)

        where = " AND ".join(conditions)

        # Count total eligible rows
        count_sql = f"SELECT COUNT(*) FROM signals WHERE {where}"
        total = conn.execute(count_sql, params).fetchone()[0]

        rows_scanned = 0
        rows_updated = 0
        unknown_count = 0
        unknown_pairs: dict[str, int] = {}

        # Chunked processing
        offset = 0
        while True:
            select_sql = (
                f"SELECT id, signal_type, source_api FROM signals "
                f"WHERE {where} ORDER BY id LIMIT ? OFFSET ?"
            )
            chunk_params = params + [chunk_size, offset]
            rows = conn.execute(select_sql, chunk_params).fetchall()
            if not rows:
                break

            updates: list[tuple[str, int]] = []
            for row_id, st, sa in rows:
                rows_scanned += 1
                family = get_family(st, sa)
                if family == "unknown":
                    unknown_count += 1
                    pair_key = f"{st}:{sa}"
                    unknown_pairs[pair_key] = unknown_pairs.get(pair_key, 0) + 1
                updates.append((family, row_id))

            if not dry_run and updates:
                conn.execute("BEGIN")
                try:
                    conn.executemany(
                        "UPDATE signals SET evidence_family = ? WHERE id = ?",
                        updates,
                    )
                    conn.commit()
                    rows_updated += len(updates)
                except Exception:
                    conn.rollback()
                    raise
            elif dry_run:
                rows_updated += len(updates)  # would-be updates

            offset += chunk_size

        # Compute unknown rate
        unknown_rate = (unknown_count / rows_scanned * 100) if rows_scanned > 0 else 0.0

        # Delta gate check
        delta_exceeded = False
        if baseline_unknown_rate is not None and rows_scanned > 0:
            delta = unknown_rate - baseline_unknown_rate
            if delta > unknown_delta_max_pp:
                delta_exceeded = True

        # Sort top unknown pairs
        top_unknown = sorted(unknown_pairs.items(), key=lambda x: -x[1])[:20]

        return {
            "rows_scanned": rows_scanned,
            "rows_total_eligible": total,
            "rows_updated": rows_updated,
            "unknown_count": unknown_count,
            "unknown_rate": round(unknown_rate, 2),
            "top_unknown_pairs": [{"pair": k, "count": v} for k, v in top_unknown],
            "dry_run": dry_run,
            "delta_exceeded": delta_exceeded,
        }

    finally:
        conn.close()

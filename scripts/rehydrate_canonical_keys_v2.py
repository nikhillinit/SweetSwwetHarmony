"""Rehydrate canonical_key_v2 column for existing signals.

Chunked SELECT→UPDATE loop with fan-in gate and audit sampling.
Importable by CLI and tests.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def run(
    db_path: str,
    dry_run: bool = True,
    chunk_size: int = 1000,
    sources: str = "all",
    max_fanin: int = 10,
    audit_sample: int = 100,
    audit_sample_out: Optional[str] = None,
    max_collision_rate: Optional[float] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Rehydrate canonical_key_v2 for signals where it is NULL.

    Returns a report dict with metrics.
    """
    from utils.canonical_key_v2 import build_canonical_key_v2

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        # Build WHERE clause
        conditions = ["canonical_key_v2 IS NULL"]
        params: list[Any] = []

        if sources != "all":
            source_list = [s.strip() for s in sources.split(",")]
            placeholders = ",".join("?" for _ in source_list)
            conditions.append(f"source_api IN ({placeholders})")
            params.extend(source_list)

        where = " AND ".join(conditions)

        # Count total eligible rows
        count_sql = f"SELECT COUNT(*) FROM signals WHERE {where}"
        total = conn.execute(count_sql, params).fetchone()[0]

        rows_scanned = 0
        rows_updated = 0
        null_v2_count = 0
        key_type_counts: dict[str, int] = {}
        audit_samples: List[Dict[str, Any]] = []

        # Apply limit
        effective_limit = limit if limit else total

        # Chunked processing
        offset = 0
        while rows_scanned < effective_limit:
            remaining = effective_limit - rows_scanned
            this_chunk = min(chunk_size, remaining)

            select_sql = (
                f"SELECT id, signal_type, source_api, canonical_key, raw_data "
                f"FROM signals WHERE {where} ORDER BY id LIMIT ? OFFSET ?"
            )
            chunk_params = params + [this_chunk, offset]
            rows = conn.execute(select_sql, chunk_params).fetchall()
            if not rows:
                break

            updates: list[tuple[Optional[str], int]] = []
            for row_id, st, sa, ck, raw_data_str in rows:
                rows_scanned += 1

                v2_key, key_type, reasons = build_canonical_key_v2(
                    raw_data=raw_data_str,
                    source_api=sa,
                    signal_type=st,
                    canonical_key=ck,
                )

                if v2_key is None:
                    null_v2_count += 1
                else:
                    kt = key_type or "unknown"
                    key_type_counts[kt] = key_type_counts.get(kt, 0) + 1

                updates.append((v2_key, row_id))

                # Audit sampling
                if len(audit_samples) < audit_sample:
                    audit_samples.append({
                        "id": row_id,
                        "canonical_key": ck,
                        "canonical_key_v2": v2_key,
                        "key_type": key_type,
                        "reasons": reasons,
                        "signal_type": st,
                        "source_api": sa,
                    })

            if not dry_run and updates:
                conn.execute("BEGIN")
                try:
                    conn.executemany(
                        "UPDATE signals SET canonical_key_v2 = ? WHERE id = ?",
                        updates,
                    )
                    conn.commit()
                    rows_updated += len([u for u in updates if u[0] is not None])
                except Exception:
                    conn.rollback()
                    raise
            elif dry_run:
                rows_updated += len([u for u in updates if u[0] is not None])

            offset += this_chunk

        # Fan-in check (only after commit)
        fanin_violations: List[Dict[str, Any]] = []
        if not dry_run:
            fanin_sql = (
                "SELECT canonical_key_v2, COUNT(*) as cnt "
                "FROM signals WHERE canonical_key_v2 IS NOT NULL "
                "GROUP BY canonical_key_v2 HAVING cnt > ? "
                "ORDER BY cnt DESC LIMIT 20"
            )
            for row in conn.execute(fanin_sql, (max_fanin,)).fetchall():
                fanin_violations.append({"key": row[0], "count": row[1]})

        # Write audit sample
        if audit_sample_out and audit_samples:
            with open(audit_sample_out, "w", encoding="utf-8") as f:
                json.dump(audit_samples, f, indent=2, ensure_ascii=False)

        # Compute rates
        null_v2_rate = (null_v2_count / rows_scanned * 100) if rows_scanned > 0 else 0.0
        unlinked_count = key_type_counts.get("unlinked_buzz", 0)
        unlinked_rate = (unlinked_count / rows_scanned * 100) if rows_scanned > 0 else 0.0

        return {
            "rows_scanned": rows_scanned,
            "rows_total_eligible": total,
            "rows_updated": rows_updated,
            "null_v2_count": null_v2_count,
            "null_v2_rate": round(null_v2_rate, 2),
            "key_type_counts": key_type_counts,
            "unlinked_rate": round(unlinked_rate, 2),
            "fanin_violations": fanin_violations,
            "audit_sample_count": len(audit_samples),
            "dry_run": dry_run,
        }

    finally:
        conn.close()

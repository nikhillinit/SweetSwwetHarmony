"""Rehydrate canonical_key_v2 column for existing signals.

Chunked SELECT→UPDATE loop with fan-in gate and audit sampling.
Importable by CLI and tests.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional

from utils.db_tool_errors import DBToolError

logger = logging.getLogger(__name__)


class RehydrateCanonicalKeysV2Error(DBToolError):
    """canonical_key_v2 rehydration failure carrying data-path evidence."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        rows_scanned: int = 0,
        rows_updated_attempted: int = 0,
        chunk_size: int | None = None,
        sources: str | None = None,
        limit: int | None = None,
        last_row_id: int | None = None,
        dry_run: bool | None = None,
    ) -> None:
        super().__init__(
            message,
            partial_evidence={
                "phase": phase,
                "rows_scanned": rows_scanned,
                "rows_updated_attempted": rows_updated_attempted,
                "chunk_size": chunk_size,
                "sources": sources,
                "limit": limit,
                "last_row_id": last_row_id,
                "dry_run": dry_run,
            },
        )


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

    rows_scanned = 0
    rows_updated = 0
    last_row_id: int | None = None
    phase = "connect"
    transaction_started = False
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception as exc:
        raise RehydrateCanonicalKeysV2Error(
            f"canonical_key_v2 rehydration failed: {exc}",
            phase=phase,
            chunk_size=chunk_size,
            sources=sources,
            limit=limit,
            dry_run=dry_run,
        ) from exc

    try:
        if not dry_run:
            phase = "begin"
            conn.execute("BEGIN IMMEDIATE")
            transaction_started = True

        # Build WHERE clause
        phase = "build_filter"
        conditions = ["canonical_key_v2 IS NULL"]
        params: list[Any] = []

        if sources != "all":
            source_list = [s.strip() for s in sources.split(",")]
            placeholders = ",".join("?" for _ in source_list)
            conditions.append(f"source_api IN ({placeholders})")
            params.extend(source_list)

        where = " AND ".join(conditions)

        # Count total eligible rows
        phase = "count"
        count_sql = f"SELECT COUNT(*) FROM signals WHERE {where}"
        total = conn.execute(count_sql, params).fetchone()[0]

        null_v2_count = 0
        key_type_counts: dict[str, int] = {}
        audit_samples: List[Dict[str, Any]] = []

        # Apply limit
        effective_limit = min(limit, total) if limit is not None else total

        # Snapshot eligible IDs up front. The old LIMIT/OFFSET scan walked a
        # shrinking "canonical_key_v2 IS NULL" set in commit mode and could skip
        # rows after each chunk wrote out of the predicate.
        phase = "select_eligible_ids"
        id_sql = f"SELECT id FROM signals WHERE {where} ORDER BY id"
        id_params = list(params)
        if effective_limit is not None:
            id_sql += " LIMIT ?"
            id_params.append(effective_limit)
        eligible_ids = [row[0] for row in conn.execute(id_sql, id_params).fetchall()]

        # Chunked processing
        position = 0
        while position < len(eligible_ids):
            chunk_ids = eligible_ids[position: position + chunk_size]
            placeholders = ",".join("?" for _ in chunk_ids)

            select_sql = (
                f"SELECT id, signal_type, source_api, canonical_key, raw_data "
                f"FROM signals WHERE id IN ({placeholders}) ORDER BY id"
            )
            phase = "select_chunk"
            rows = conn.execute(select_sql, chunk_ids).fetchall()
            if not rows:
                break

            updates: list[tuple[Optional[str], int]] = []
            for row_id, st, sa, ck, raw_data_str in rows:
                phase = "process_row"
                rows_scanned += 1
                last_row_id = row_id

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
                phase = "apply_chunk"
                conn.executemany(
                    "UPDATE signals SET canonical_key_v2 = ? WHERE id = ?",
                    updates,
                )
                rows_updated += len([u for u in updates if u[0] is not None])
            elif dry_run:
                rows_updated += len([u for u in updates if u[0] is not None])

            position += len(chunk_ids)

        # Validate tentative writes before committing the invocation.
        fanin_violations: List[Dict[str, Any]] = []
        if not dry_run:
            phase = "fanin_check"
            fanin_sql = (
                "SELECT canonical_key_v2, COUNT(*) as cnt "
                "FROM signals WHERE canonical_key_v2 IS NOT NULL "
                "GROUP BY canonical_key_v2 HAVING cnt > ? "
                "ORDER BY cnt DESC LIMIT 20"
            )
            for row in conn.execute(fanin_sql, (max_fanin,)).fetchall():
                fanin_violations.append({"key": row[0], "count": row[1]})

            if fanin_violations and transaction_started:
                conn.rollback()
                transaction_started = False

        # Write audit sample
        phase = "audit_sample"
        if audit_sample_out and audit_samples:
            with open(audit_sample_out, "w", encoding="utf-8") as f:
                json.dump(audit_samples, f, indent=2, ensure_ascii=False)

        if transaction_started:
            phase = "commit"
            conn.commit()
            transaction_started = False

        # Compute rates
        phase = "summarize"
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

    except RehydrateCanonicalKeysV2Error:
        if transaction_started:
            conn.rollback()
        raise
    except Exception as exc:
        if transaction_started:
            conn.rollback()
        raise RehydrateCanonicalKeysV2Error(
            f"canonical_key_v2 rehydration failed: {exc}",
            phase=phase,
            rows_scanned=rows_scanned,
            rows_updated_attempted=rows_updated,
            chunk_size=chunk_size,
            sources=sources,
            limit=limit,
            last_row_id=last_row_id,
            dry_run=dry_run,
        ) from exc
    finally:
        conn.close()

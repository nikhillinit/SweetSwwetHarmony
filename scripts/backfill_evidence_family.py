"""Backfill evidence_family column for existing signals.

Chunked SELECT→UPDATE loop. Importable by CLI and tests.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, Optional

from utils.db_tool_errors import DBToolError

logger = logging.getLogger(__name__)


class BackfillEvidenceFamilyError(DBToolError):
    """evidence_family backfill failure carrying data-path evidence."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        rows_scanned: int = 0,
        rows_updated_attempted: int = 0,
        chunk_size: int | None = None,
        rewrite_unknown: bool | None = None,
        source_api: str | None = None,
        signal_type: str | None = None,
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
                "rewrite_unknown": rewrite_unknown,
                "source_api": source_api,
                "signal_type": signal_type,
                "last_row_id": last_row_id,
                "dry_run": dry_run,
            },
        )


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
        raise BackfillEvidenceFamilyError(
            f"evidence_family backfill failed: {exc}",
            phase=phase,
            chunk_size=chunk_size,
            rewrite_unknown=rewrite_unknown,
            source_api=source_api,
            signal_type=signal_type,
            dry_run=dry_run,
        ) from exc

    try:
        if not dry_run:
            phase = "begin"
            conn.execute("BEGIN IMMEDIATE")
            transaction_started = True

        # Build WHERE clause
        phase = "build_filter"
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
        phase = "count"
        count_sql = f"SELECT COUNT(*) FROM signals WHERE {where}"
        total = conn.execute(count_sql, params).fetchone()[0]

        unknown_count = 0
        unknown_pairs: dict[str, int] = {}

        # Snapshot eligible IDs up front. The old LIMIT/OFFSET scan walked a
        # shrinking evidence_family predicate in commit mode and could skip
        # rows after each chunk wrote out of the predicate.
        phase = "select_eligible_ids"
        id_sql = f"SELECT id FROM signals WHERE {where} ORDER BY id"
        eligible_ids = [row[0] for row in conn.execute(id_sql, params).fetchall()]

        # Chunked processing
        position = 0
        while position < len(eligible_ids):
            chunk_ids = eligible_ids[position: position + chunk_size]
            placeholders = ",".join("?" for _ in chunk_ids)
            select_sql = (
                f"SELECT id, signal_type, source_api FROM signals "
                f"WHERE id IN ({placeholders}) ORDER BY id"
            )
            phase = "select_chunk"
            rows = conn.execute(select_sql, chunk_ids).fetchall()
            if not rows:
                break

            updates: list[tuple[str, int]] = []
            for row_id, st, sa in rows:
                phase = "process_row"
                rows_scanned += 1
                last_row_id = row_id
                family = get_family(st, sa)
                if family == "unknown":
                    unknown_count += 1
                    pair_key = f"{st}:{sa}"
                    unknown_pairs[pair_key] = unknown_pairs.get(pair_key, 0) + 1
                updates.append((family, row_id))

            if not dry_run and updates:
                phase = "apply_chunk"
                conn.executemany(
                    "UPDATE signals SET evidence_family = ? WHERE id = ?",
                    updates,
                )
                rows_updated += len(updates)
            elif dry_run:
                rows_updated += len(updates)  # would-be updates

            position += len(chunk_ids)

        # Compute unknown rate
        phase = "unknown_rate"
        unknown_rate = (unknown_count / rows_scanned * 100) if rows_scanned > 0 else 0.0

        # Delta gate check
        phase = "delta_gate"
        delta_exceeded = False
        if baseline_unknown_rate is not None and rows_scanned > 0:
            delta = unknown_rate - baseline_unknown_rate
            if delta > unknown_delta_max_pp:
                delta_exceeded = True

        if delta_exceeded and transaction_started:
            conn.rollback()
            transaction_started = False

        if transaction_started:
            phase = "commit"
            conn.commit()
            transaction_started = False

        # Sort top unknown pairs
        phase = "summarize"
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

    except BackfillEvidenceFamilyError:
        if transaction_started:
            conn.rollback()
        raise
    except Exception as exc:
        if transaction_started:
            conn.rollback()
        raise BackfillEvidenceFamilyError(
            f"evidence_family backfill failed: {exc}",
            phase=phase,
            rows_scanned=rows_scanned,
            rows_updated_attempted=rows_updated,
            chunk_size=chunk_size,
            rewrite_unknown=rewrite_unknown,
            source_api=source_api,
            signal_type=signal_type,
            last_row_id=last_row_id,
            dry_run=dry_run,
        ) from exc
    finally:
        conn.close()

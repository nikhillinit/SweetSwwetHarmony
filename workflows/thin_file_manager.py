"""Thin file manager — promotion rules + company file lifecycle (Task 9).

Provides:
- upsert_company_file: Create/update company_files rows on each signal
- check_and_promote_atomic: Evaluate promotion criteria + create ReviewItem
- run_promotion_sweep: Paginated sweep for thin files eligible for promotion
- archive_stale_files: Mark thin files with no new evidence as archived

Promotion criteria (Phase 1a + Phase 3):
  1. Multi-source: len(source_apis) >= 2
  2. Trusted source: SEC, Companies House, Crunchbase
  3. Manual override via metadata.manual_promotion flag
  4. Exemplar similarity: metadata.exemplar_similarity >= threshold (Phase 3)

All queries use explicit-column SELECTs + tuple unpacking (no row_factory).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

# Sources considered "trusted" for single-source promotion
TRUSTED_SOURCES = {"sec_edgar", "companies_house", "crunchbase", "hacker_news"}

# Exemplar similarity threshold for promotion (Phase 3)
EXEMPLAR_PROMOTION_THRESHOLD = float(os.environ.get("EXEMPLAR_PROMOTION_THRESHOLD", "0.75"))


def _parse_source_apis(source_apis_str: Optional[str]) -> List[str]:
    """Parse source_apis JSON string with application-level validation.

    Returns a list of non-empty strings, or [] on any failure.
    """
    if not source_apis_str:
        return []
    try:
        sources = json.loads(source_apis_str)
        if not isinstance(sources, list):
            return []
        return [s for s in sources if isinstance(s, str) and s]
    except (json.JSONDecodeError, TypeError):
        return []


def _meets_promotion_criteria(
    source_apis: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Check if a company file meets promotion criteria.

    Rules (OR logic): (multi_source OR trusted) OR manual OR exemplar_match
      1. Multi-source: 2+ distinct source APIs
      2. Trusted source: any source in TRUSTED_SOURCES
      3. Manual: metadata.manual_promotion is True
      4. Exemplar match: metadata.exemplar_similarity >= threshold (Phase 3)
    """
    # Rule 3: Manual override
    if metadata and metadata.get("manual_promotion"):
        return True

    # Rule 1: Multi-source verification
    if len(source_apis) >= 2:
        return True

    # Rule 2: Trusted source
    if any(s in TRUSTED_SOURCES for s in source_apis):
        return True

    # Rule 4: Exemplar similarity (Phase 3)
    if metadata:
        exemplar_sim = metadata.get("exemplar_similarity")
        if exemplar_sim is not None and exemplar_sim >= EXEMPLAR_PROMOTION_THRESHOLD:
            return True

    return False


async def upsert_company_file(
    store: SignalStore,
    company_id: str,
    company_name: Optional[str],
    canonical_key: str,
    source_api: str,
    tx: Optional[aiosqlite.Connection] = None,
) -> str:
    """Create or update a company_files row for a signal.

    Branches:
    - Existing + archived: reactivate (status='thin', clear archived_at)
    - Existing + thin/promoted: append source_api if new, bump last_seen_at
    - New: INSERT with status='thin'

    Args:
        store: Initialized SignalStore
        company_id: Company identifier
        company_name: Display name (nullable)
        canonical_key: Representative canonical key
        source_api: Source API string (e.g. 'github', 'sec_edgar')
        tx: Optional transaction connection

    Returns:
        'created', 'updated', or 'reactivated'
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    async def _do(conn: aiosqlite.Connection) -> str:
        cursor = await conn.execute(
            """SELECT company_id, status, source_apis, archived_at
               FROM company_files
               WHERE company_id = ?""",
            (company_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            # New company file
            await conn.execute(
                """INSERT INTO company_files
                   (company_id, company_name, canonical_key, status,
                    source_apis, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, 'thin', ?, ?, ?)""",
                (
                    company_id,
                    company_name or canonical_key,
                    canonical_key,
                    json.dumps([source_api]),
                    now_iso,
                    now_iso,
                ),
            )
            return "created"

        existing_status = row[1]
        existing_sources = _parse_source_apis(row[2])

        if existing_status == "archived":
            # Reactivate: thin, clear archived_at, append source
            if source_api not in existing_sources:
                existing_sources.append(source_api)
            await conn.execute(
                """UPDATE company_files
                   SET status = 'thin',
                       archived_at = NULL,
                       source_apis = ?,
                       last_seen_at = ?,
                       company_name = COALESCE(?, company_name)
                   WHERE company_id = ?""",
                (
                    json.dumps(sorted(existing_sources)),
                    now_iso,
                    company_name,
                    company_id,
                ),
            )
            # Audit reactivation
            await conn.execute(
                """INSERT INTO audit_log
                   (action_type, entity_type, entity_id, actor, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    "reactivate",
                    "company_file",
                    company_id,
                    "pipeline",
                    json.dumps({"source_api": source_api}),
                    now_iso,
                ),
            )
            return "reactivated"

        # Existing thin or promoted — append source if new, bump last_seen_at
        changed = False
        if source_api not in existing_sources:
            existing_sources.append(source_api)
            changed = True

        await conn.execute(
            """UPDATE company_files
               SET source_apis = ?,
                   last_seen_at = ?,
                   company_name = COALESCE(?, company_name)
               WHERE company_id = ?""",
            (
                json.dumps(sorted(existing_sources)) if changed else row[2],
                now_iso,
                company_name,
                company_id,
            ),
        )
        return "updated"

    if tx is not None:
        return await _do(tx)
    else:
        async with store.transaction_immediate() as conn:
            return await _do(conn)


async def check_and_promote_atomic(
    store: SignalStore,
    company_id: str,
) -> Optional[int]:
    """Atomically check promotion criteria and create ReviewItem if met.

    Single transaction_immediate(). Returns review_id if promoted, None otherwise.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    async with store.transaction_immediate() as tx:
        # Fetch company file
        cursor = await tx.execute(
            """SELECT company_id, status, source_apis, metadata
               FROM company_files
               WHERE company_id = ? AND status = 'thin'""",
            (company_id,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        source_apis = _parse_source_apis(row[2])
        metadata = None
        if row[3]:
            try:
                metadata = json.loads(row[3])
            except (json.JSONDecodeError, TypeError):
                metadata = None

        if not _meets_promotion_criteria(source_apis, metadata):
            return None

        # Gather evidence signals
        cursor = await tx.execute(
            """SELECT id FROM signals
               WHERE company_id = ?
               ORDER BY created_at DESC
               LIMIT 100""",
            (company_id,),
        )
        signal_ids = [r[0] for r in await cursor.fetchall()]

        if not signal_ids:
            return None

        # Create review item with ON CONFLICT DO NOTHING
        evidence_bundle = json.dumps({
            "signal_ids": sorted(signal_ids),
            "schema_version": 1,
        })

        cursor = await tx.execute(
            """INSERT INTO review_items
               (company_id, status, evidence_bundle, created_at, updated_at)
               VALUES (?, 'pending', ?, ?, ?)
               ON CONFLICT(company_id)
               WHERE status IN ('pending', 'approved', 'publish_queued')
               DO NOTHING""",
            (company_id, evidence_bundle, now_iso, now_iso),
        )

        if cursor.rowcount == 0:
            # Active review already exists
            return None

        review_id = cursor.lastrowid

        # Update company_files status to promoted
        await tx.execute(
            """UPDATE company_files
               SET status = 'promoted', promoted_at = ?
               WHERE company_id = ?""",
            (now_iso, company_id),
        )

        # Audit log
        await tx.execute(
            """INSERT INTO audit_log
               (action_type, entity_type, entity_id, actor, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "promote",
                "company_file",
                company_id,
                "pipeline",
                json.dumps({
                    "review_id": review_id,
                    "source_count": len(source_apis),
                    "signal_count": len(signal_ids),
                }),
                now_iso,
            ),
        )

        logger.info(
            f"Promoted {company_id}: {len(source_apis)} sources, "
            f"{len(signal_ids)} signals -> review {review_id}"
        )
        return review_id


async def _create_repromotion_review(
    store: SignalStore,
    company_id: str,
) -> Optional[int]:
    """Create a new review item for a promoted file with new evidence.

    Unlike check_and_promote_atomic, this does NOT change company_file status
    (it's already 'promoted'). It only creates a new pending review.

    Returns review_id if created, None if active review already exists.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    async with store.transaction_immediate() as tx:
        # Gather evidence signals
        cursor = await tx.execute(
            """SELECT id FROM signals
               WHERE company_id = ?
               ORDER BY created_at DESC
               LIMIT 100""",
            (company_id,),
        )
        signal_ids = [r[0] for r in await cursor.fetchall()]

        if not signal_ids:
            return None

        evidence_bundle = json.dumps({
            "signal_ids": sorted(signal_ids),
            "schema_version": 1,
        })

        cursor = await tx.execute(
            """INSERT INTO review_items
               (company_id, status, evidence_bundle, created_at, updated_at)
               VALUES (?, 'pending', ?, ?, ?)
               ON CONFLICT(company_id)
               WHERE status IN ('pending', 'approved', 'publish_queued')
               DO NOTHING""",
            (company_id, evidence_bundle, now_iso, now_iso),
        )

        if cursor.rowcount == 0:
            return None

        review_id = cursor.lastrowid

        # Audit log
        await tx.execute(
            """INSERT INTO audit_log
               (action_type, entity_type, entity_id, actor, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "repromote",
                "company_file",
                company_id,
                "pipeline",
                json.dumps({
                    "review_id": review_id,
                    "signal_count": len(signal_ids),
                }),
                now_iso,
            ),
        )

        logger.info(
            f"Re-promoted {company_id}: {len(signal_ids)} signals "
            f"-> review {review_id}"
        )
        return review_id


async def run_promotion_sweep(
    store: SignalStore,
    last_seen_cursor: Optional[str] = None,
    company_id_cursor: Optional[str] = None,
    limit: int = 100,
) -> Tuple[int, Optional[str], Optional[str]]:
    """Paginated sweep for company files eligible for promotion.

    Processes both:
    1. Thin files eligible for first promotion
    2. Promoted files with no active review + new evidence (re-promotion)

    Uses composite cursor (last_seen_at, company_id) to avoid skipped rows.

    Args:
        store: Initialized SignalStore
        last_seen_cursor: Previous page's last_seen_at (or None for first page)
        company_id_cursor: Previous page's company_id (or None for first page)
        limit: Max candidates per page

    Returns:
        (promoted_count, new_last_seen_cursor, new_company_id_cursor)
    """
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    cursor_vals = (last_seen_cursor or "", company_id_cursor or "")

    # Query 1: Thin files eligible for first promotion
    cursor = await db.execute(
        """SELECT company_id, last_seen_at
           FROM company_files
           WHERE status = 'thin'
           AND (last_seen_at, company_id) > (?, ?)
           ORDER BY last_seen_at ASC, company_id ASC
           LIMIT ?""",
        (*cursor_vals, limit),
    )
    thin_candidates = [(row[0], row[1], "thin") for row in await cursor.fetchall()]

    # Query 2: Re-promotion candidates (promoted with no active review + new evidence)
    cursor = await db.execute(
        """SELECT cf.company_id, cf.last_seen_at
           FROM company_files cf
           WHERE cf.status = 'promoted'
             AND NOT EXISTS (
                 SELECT 1 FROM review_items ri
                 WHERE ri.company_id = cf.company_id
                 AND ri.status IN ('pending', 'approved', 'publish_queued'))
             AND cf.last_seen_at > COALESCE(
                 (SELECT MAX(decided_at) FROM review_items
                  WHERE company_id = cf.company_id),
                 '1970-01-01')
             AND (cf.last_seen_at, cf.company_id) > (?, ?)
           ORDER BY cf.last_seen_at ASC, cf.company_id ASC
           LIMIT ?""",
        (*cursor_vals, limit),
    )
    repro_candidates = [(row[0], row[1], "repro") for row in await cursor.fetchall()]

    # Combine, sort by (last_seen_at, company_id), deduplicate, take up to limit
    all_candidates = thin_candidates + repro_candidates
    seen: set = set()
    unique_candidates = []
    for company_id, last_seen, ctype in sorted(all_candidates, key=lambda x: (x[1], x[0])):
        if company_id not in seen:
            seen.add(company_id)
            unique_candidates.append((company_id, last_seen, ctype))
    unique_candidates = unique_candidates[:limit]

    # Process each candidate
    promoted_count = 0
    new_last_seen = None
    new_company_id = None

    for company_id, last_seen, ctype in unique_candidates:
        if ctype == "thin":
            review_id = await check_and_promote_atomic(store, company_id)
        else:
            review_id = await _create_repromotion_review(store, company_id)
        if review_id is not None:
            promoted_count += 1
        new_last_seen = last_seen
        new_company_id = company_id

    return promoted_count, new_last_seen, new_company_id


async def archive_stale_files(
    store: SignalStore,
    days: int = 60,
) -> Dict[str, Any]:
    """Mark thin files with no new evidence as archived.

    Args:
        store: Initialized SignalStore
        days: Days since last_seen_at to consider stale

    Returns:
        Report dict with archived_count.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()
    now_iso = now.isoformat()

    async with store.transaction_immediate() as tx:
        # Find stale thin files
        cursor = await tx.execute(
            """SELECT company_id FROM company_files
               WHERE status = 'thin'
               AND last_seen_at < ?""",
            (cutoff,),
        )
        stale = await cursor.fetchall()
        stale_ids = [row[0] for row in stale]

        if not stale_ids:
            return {"archived_count": 0, "cutoff": cutoff}

        # Archive them
        placeholders = ",".join("?" for _ in stale_ids)
        await tx.execute(
            f"""UPDATE company_files
                SET status = 'archived', archived_at = ?
                WHERE company_id IN ({placeholders})""",
            (now_iso, *stale_ids),
        )

        # Single audit log entry
        await tx.execute(
            """INSERT INTO audit_log
               (action_type, entity_type, entity_id, actor, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "archive_stale",
                "company_file",
                "batch",
                "pipeline",
                json.dumps({
                    "archived_count": len(stale_ids),
                    "cutoff_days": days,
                    "cutoff": cutoff,
                    "company_ids": stale_ids[:100],
                }),
                now_iso,
            ),
        )

    logger.info(f"Archived {len(stale_ids)} stale thin files (>{days} days)")
    return {"archived_count": len(stale_ids), "cutoff": cutoff}

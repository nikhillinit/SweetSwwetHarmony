"""v28 backfill: Populate company_id on existing signals.

Reads canonical_key from each signal, resolves to a company_id via:
1. entity_aliases lookup → resolve_entity_root (handles transitive merges)
2. If not found: generate via entity_id_for_seed(canonical_key) + register binding

Modes:
- dry_run=True: Report mapping without modifying DB
- dry_run=False: UPDATE signals SET company_id, register new bindings

Post-backfill validator: validate_company_ids() asserts no NULLs remain.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, TYPE_CHECKING

from storage.entity_identity_store import EntityIdentityStore, StrongKeyBinding

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


async def validate_company_ids(store: SignalStore) -> Dict[str, Any]:
    """Validate that all signals have non-NULL company_id.

    This is the unified validator used by both the backfill script (Task 2)
    and the migration gate (Task 3).

    Returns:
        Dict with valid (bool), null_count, total_signals.
    """
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    cursor = await db.execute("SELECT COUNT(*) FROM signals")
    total = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) FROM signals WHERE company_id IS NULL")
    null_count = (await cursor.fetchone())[0]

    return {
        "valid": null_count == 0,
        "null_count": null_count,
        "total_signals": total,
    }


async def backfill_company_ids(
    store: SignalStore,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Backfill company_id on all signals with NULL company_id.

    Args:
        store: Initialized SignalStore
        dry_run: If True, report mappings without modifying DB

    Returns:
        Report dict with mode, counts, mappings, and metrics.
    """
    db = store._db
    if not db:
        raise RuntimeError("Database not initialized")

    identity_store = EntityIdentityStore(store)

    # Count NULLs before
    cursor = await db.execute("SELECT COUNT(*) FROM signals WHERE company_id IS NULL")
    null_count_before = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) FROM signals")
    total_signals = (await cursor.fetchone())[0]

    if null_count_before == 0:
        logger.info("No signals with NULL company_id — nothing to backfill")
        return {
            "mode": "dry_run" if dry_run else "apply",
            "total_signals": total_signals,
            "null_count_before": 0,
            "null_count_after": 0,
            "mappings": [],
            "newly_generated": 0,
            "merge_resolved": 0,
        }

    # Fetch signals with NULL company_id
    cursor = await db.execute(
        "SELECT id, canonical_key FROM signals WHERE company_id IS NULL"
    )
    signals = await cursor.fetchall()

    # Group by canonical_key to avoid redundant lookups
    key_to_signal_ids: Dict[str, List[int]] = {}
    for signal_id, canonical_key in signals:
        key_to_signal_ids.setdefault(canonical_key, []).append(signal_id)

    # Resolve each unique canonical_key to a company_id
    key_to_company_id: Dict[str, str] = {}
    newly_generated = 0
    merge_resolved = 0

    for canonical_key in key_to_signal_ids:
        # Step 1: Check entity_aliases for existing binding
        strong_map = await identity_store.lookup_strong_keys([canonical_key])

        if canonical_key in strong_map:
            # Found — resolve to current root (handles transitive merges)
            company_id = strong_map[canonical_key]
            # lookup_strong_keys already resolves roots
            merge_resolved += 1
        else:
            # Not found — generate new entity_id and register
            company_id = EntityIdentityStore.entity_id_for_seed(canonical_key)
            newly_generated += 1

        key_to_company_id[canonical_key] = company_id

    # Build mappings
    mappings = []
    for signal_id, canonical_key in signals:
        mappings.append({
            "signal_id": signal_id,
            "canonical_key": canonical_key,
            "company_id": key_to_company_id[canonical_key],
        })

    if dry_run:
        logger.info(
            f"Dry-run: {len(mappings)} signals would be updated "
            f"({newly_generated} new, {merge_resolved} resolved)"
        )
        return {
            "mode": "dry_run",
            "total_signals": total_signals,
            "null_count_before": null_count_before,
            "mappings": mappings,
            "newly_generated": newly_generated,
            "merge_resolved": merge_resolved,
        }

    # Apply mode: UPDATE signals + register new bindings
    async with store.transaction_immediate() as tx:
        # Register new strong key bindings for newly generated IDs
        new_bindings = []
        for canonical_key, company_id in key_to_company_id.items():
            # Check if already registered (skip merge-resolved ones that already exist)
            cursor = await tx.execute(
                "SELECT 1 FROM entity_aliases WHERE strong_key = ?",
                (canonical_key,)
            )
            if not await cursor.fetchone():
                new_bindings.append(StrongKeyBinding(
                    strong_key=canonical_key,
                    entity_id=company_id,
                    source_signal_id=None,
                    source_key="backfill",
                ))

        if new_bindings:
            await identity_store.upsert_strong_key_bindings(new_bindings, tx)

        # UPDATE signals SET company_id
        for mapping in mappings:
            await tx.execute(
                "UPDATE signals SET company_id = ? WHERE id = ?",
                (mapping["company_id"], mapping["signal_id"])
            )

    # Verify post-backfill
    cursor = await db.execute("SELECT COUNT(*) FROM signals WHERE company_id IS NULL")
    null_count_after = (await cursor.fetchone())[0]

    logger.info(
        f"Backfill complete: {len(mappings)} signals updated, "
        f"{null_count_after} NULLs remaining, "
        f"{newly_generated} new IDs, {merge_resolved} merge-resolved"
    )

    return {
        "mode": "apply",
        "total_signals": total_signals,
        "null_count_before": null_count_before,
        "null_count_after": null_count_after,
        "mappings": mappings,
        "newly_generated": newly_generated,
        "merge_resolved": merge_resolved,
    }

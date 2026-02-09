#!/usr/bin/env python3
"""Build exemplar library from TP-labeled signals.

Reads TP-labeled signals from signal_quality_metrics, vectorizes using the
existing case-law TF-IDF vectorizer, and populates the thesis_exemplars table.

Safety invariants:
  - Only TP labels are included (FP and UNSURE excluded).
  - Reuses the case-law vectorizer (transform only, no refit).
  - Dry-run is safe: prints stats without writing.
  - Old-version exemplars are pruned after successful build.

Usage:
    python scripts/build_exemplar_library.py --db signals.db --version v1.0.0
    python scripts/build_exemplar_library.py --db signals.db --version v1.0.0 --dry-run

Phase 3 — case-law + exemplars.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import pickle
import sys
from pathlib import Path
from typing import Any, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.corpus_text_builder import build_corpus_text
from intelligence.vectorizer_config import VECTORIZER_DIR

logger = logging.getLogger(__name__)

# Category inference from thesis_classifications (fallback: "general")
DEFAULT_CATEGORY = "general"


async def _fetch_tp_signals(store) -> list[dict]:
    """Fetch TP-labeled signals joined with signals + optional thesis classification."""
    cursor = await store._db.execute("""
        SELECT
            s.id AS signal_id,
            s.canonical_key,
            s.company_id,
            s.company_name,
            s.raw_data,
            s.source_api,
            s.confidence,
            s.created_at AS signal_created_at,
            sqm.human_label,
            sqm.notes AS label_reason,
            tc.category AS thesis_category
        FROM signal_quality_metrics sqm
        JOIN signals s ON s.id = sqm.signal_id
        LEFT JOIN thesis_classifications tc ON tc.signal_id = s.id
        WHERE sqm.human_label = 'TP'
        ORDER BY s.id
    """)
    rows = await cursor.fetchall()
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


async def _fetch_schema_rows(store, company_ids: list[str]) -> dict[str, dict]:
    """Fetch functional schemas for given company_ids."""
    if not company_ids:
        return {}
    placeholders = ",".join("?" for _ in company_ids)
    try:
        cursor = await store._db.execute(f"""
            SELECT company_id, problem_solved_text, customer_text, customer_archetype
            FROM functional_schemas
            WHERE company_id IN ({placeholders}) AND is_active = 1
        """, company_ids)
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return {row[0]: dict(zip(columns, row)) for row in rows}
    except Exception:
        return {}


def _make_exemplar_key(signal: dict) -> str:
    """Generate a stable exemplar_key from signal data."""
    # Use canonical_key if available, else derive from company_name
    base = signal.get("canonical_key") or signal.get("company_name") or str(signal["signal_id"])
    return f"auto_{hashlib.sha256(base.encode()).hexdigest()[:12]}"


def _infer_category(signal: dict) -> str:
    """Infer category from thesis_classifications or default."""
    return signal.get("thesis_category") or DEFAULT_CATEGORY


def _make_description(signal: dict) -> str:
    """Generate a human-readable description for the exemplar."""
    name = signal.get("company_name") or "Unknown"
    reason = signal.get("label_reason") or ""
    source = signal.get("source_api") or ""
    parts = [f"TP exemplar: {name}"]
    if reason:
        parts.append(f"({reason})")
    if source:
        parts.append(f"[{source}]")
    return " ".join(parts)


async def build_exemplar_library(
    store,
    version: str,
    dry_run: bool = False,
    vectorizer_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Build exemplar library from TP-labeled signals.

    Uses the existing case-law vectorizer (transform only, no refit)
    to ensure consistent vector space.

    Returns dict with build stats.
    """
    vdir = vectorizer_dir or VECTORIZER_DIR

    # Load existing vectorizer
    vectorizer_path = os.path.join(vdir, f"case_law_{version}.joblib")
    if not os.path.exists(vectorizer_path):
        logger.error("Vectorizer not found at %s — run build_case_law_corpus.py first", vectorizer_path)
        return {"error": f"Vectorizer not found: {vectorizer_path}", "exemplar_count": 0}

    import joblib
    vectorizer = joblib.load(vectorizer_path)

    # Fetch TP signals
    signals = await _fetch_tp_signals(store)
    if not signals:
        logger.warning("No TP-labeled signals found — empty exemplar library")
        return {"exemplar_count": 0, "version": version}

    # Fetch schema rows for enrichment
    company_ids = [s["company_id"] for s in signals if s.get("company_id")]
    schemas = await _fetch_schema_rows(store, company_ids)

    # Build corpus text + metadata per signal
    exemplars = []
    for sig in signals:
        schema_row = schemas.get(sig.get("company_id"))
        text = build_corpus_text(
            sig["company_name"] or "",
            sig["raw_data"] or "{}",
            schema_row,
        )
        if not text.strip():
            logger.warning("Empty corpus text for signal %s — skipping", sig["signal_id"])
            continue
        exemplars.append({
            "signal": sig,
            "corpus_text": text,
            "exemplar_key": _make_exemplar_key(sig),
            "category": _infer_category(sig),
            "description": _make_description(sig),
        })

    categories = {}
    for e in exemplars:
        cat = e["category"]
        categories[cat] = categories.get(cat, 0) + 1

    result = {
        "exemplar_count": len(exemplars),
        "categories": categories,
        "version": version,
    }

    if dry_run:
        logger.info(
            "DRY RUN: %d exemplars, categories=%s",
            len(exemplars), categories,
        )
        return result

    # Transform texts using existing vectorizer (no refit)
    texts = [e["corpus_text"] for e in exemplars]
    tfidf_matrix = vectorizer.transform(texts)

    # Insert exemplar rows
    for i, e in enumerate(exemplars):
        sig = e["signal"]
        vec_blob = pickle.dumps(tfidf_matrix[i])

        await store._db.execute(
            """INSERT OR REPLACE INTO thesis_exemplars
            (exemplar_key, canonical_key, company_name, human_label, category,
             description, corpus_text, tfidf_vector, vectorizer_version, source,
             is_active)
            VALUES (?, ?, ?, 'TP', ?, ?, ?, ?, ?, 'auto', 1)""",
            (
                e["exemplar_key"],
                sig.get("canonical_key"),
                sig.get("company_name"),
                e["category"],
                e["description"],
                e["corpus_text"],
                vec_blob,
                version,
            ),
        )
    await store._db.commit()

    # Prune old versions
    pruned = await _prune_old_versions(store, version)
    if pruned:
        logger.info("Pruned %d exemplars from old vectorizer versions", pruned)

    result["pruned"] = pruned
    return result


async def _prune_old_versions(store, current_version: str) -> int:
    """Delete exemplars from superseded vectorizer versions."""
    cursor = await store._db.execute(
        "DELETE FROM thesis_exemplars WHERE vectorizer_version != ? AND source = 'auto'",
        (current_version,),
    )
    await store._db.commit()
    return cursor.rowcount


async def main():
    from storage.signal_store import SignalStore

    parser = argparse.ArgumentParser(description="Build exemplar library from TP labels")
    parser.add_argument("--db", default="signals.db", help="Database path")
    parser.add_argument("--version", default="v1.0.0", help="Vectorizer version (must match case-law)")
    parser.add_argument("--dry-run", action="store_true", help="Print stats without writing")
    parser.add_argument("--vectorizer-dir", default=None, help="Vectorizer directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    store = SignalStore(db_path=args.db)
    await store.initialize()

    try:
        result = await build_exemplar_library(
            store,
            version=args.version,
            dry_run=args.dry_run,
            vectorizer_dir=args.vectorizer_dir,
        )
        if result.get("error"):
            print(f"\nERROR: {result['error']}")
            sys.exit(1)
        print(f"\nExemplar library build {'(DRY RUN) ' if args.dry_run else ''}complete:")
        print(f"  Exemplars: {result['exemplar_count']}")
        print(f"  Categories: {result.get('categories', {})}")
        if result.get("pruned"):
            print(f"  Pruned: {result['pruned']} old-version rows")
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())

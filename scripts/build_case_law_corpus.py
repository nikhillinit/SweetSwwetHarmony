#!/usr/bin/env python3
"""Build case-law corpus from labeled signals.

Reads labeled signals (TP + FP) from signal_quality_metrics, trains a TF-IDF
vectorizer, and stores precomputed vectors in the precedents table.

Safety invariants:
  - Only TP and FP labels are included (UNSURE excluded).
  - Dry-run is safe: prints stats without writing.
  - Old-version precedents are pruned after successful build.
  - Calibrate mode is read-only.

Usage:
    python scripts/build_case_law_corpus.py --db signals.db --version v1.0.0
    python scripts/build_case_law_corpus.py --db signals.db --version v1.0.0 --dry-run
    python scripts/build_case_law_corpus.py --db signals.db --version v1.0.0 --calibrate

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.corpus_text_builder import build_corpus_text
from utils.db_path_helper import resolve_db_path_env
from intelligence.vectorizer_config import (
    VECTORIZER_DIR,
    VectorizerMetadata,
    save_metadata,
    check_retrain_needed,
)

logger = logging.getLogger(__name__)


async def _fetch_labeled_signals(store) -> list[dict]:
    """Fetch labeled signals (TP + FP only) joined with signals table."""
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
            sqm.notes AS label_reason
        FROM signal_quality_metrics sqm
        JOIN signals s ON s.id = sqm.signal_id
        WHERE sqm.human_label IN ('TP', 'FP')
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
        # functional_schemas may not exist in test DBs
        return {}


async def build_corpus(
    store,
    version: str,
    dry_run: bool = False,
    vectorizer_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Build case-law corpus from labeled signals.

    Returns dict with build stats (corpus_size, label_counts, etc.).
    """
    vdir = vectorizer_dir or VECTORIZER_DIR

    # Fetch labeled signals
    signals = await _fetch_labeled_signals(store)
    if not signals:
        logger.warning("No labeled signals found — empty corpus")
        return {"corpus_size": 0, "label_counts": {}, "version": version}

    # Fetch schema rows for enrichment
    company_ids = [s["company_id"] for s in signals if s.get("company_id")]
    schemas = await _fetch_schema_rows(store, company_ids)

    # Build corpus text for each signal
    texts = []
    for sig in signals:
        schema_row = schemas.get(sig.get("company_id"))
        text = build_corpus_text(
            sig["company_name"] or "",
            sig["raw_data"] or "{}",
            schema_row,
        )
        texts.append(text)

    label_counts = {}
    for sig in signals:
        lbl = sig["human_label"]
        label_counts[lbl] = label_counts.get(lbl, 0) + 1

    # Train TF-IDF vectorizer
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(
        max_features=3000,
        ngram_range=(1, 2),
        min_df=1,
        sublinear_tf=True,
        strip_accents="unicode",
    )
    tfidf_matrix = vectorizer.fit_transform(texts)
    vocab_size = len(vectorizer.vocabulary_)

    result = {
        "corpus_size": len(signals),
        "label_counts": label_counts,
        "vocab_size": vocab_size,
        "version": version,
    }

    if dry_run:
        logger.info(
            "DRY RUN: corpus=%d signals, vocab=%d, labels=%s",
            len(signals), vocab_size, label_counts,
        )
        return result

    # Save vectorizer
    os.makedirs(vdir, exist_ok=True)
    vectorizer_path = os.path.join(vdir, f"case_law_{version}.joblib")
    import joblib
    joblib.dump(vectorizer, vectorizer_path)

    # Compute hash
    vec_bytes = pickle.dumps(vectorizer)
    vec_hash = hashlib.sha256(vec_bytes).hexdigest()

    # Save metadata
    meta = VectorizerMetadata(
        version=version,
        trained_at=datetime.now(timezone.utc).isoformat(),
        corpus_size=len(signals),
        corpus_labels=label_counts,
        vocab_size=vocab_size,
        vectorizer_hash=vec_hash,
    )
    meta_path = os.path.join(vdir, f"case_law_{version}_meta.json")
    save_metadata(meta, meta_path)

    # Insert precedent rows
    for i, sig in enumerate(signals):
        vec_blob = pickle.dumps(tfidf_matrix[i])
        text_hash = hashlib.sha256(texts[i].encode()).hexdigest()

        await store._db.execute(
            """INSERT OR REPLACE INTO precedents
            (signal_id, canonical_key, company_id, human_label, corpus_text,
             tfidf_vector, similarity_text_hash, signal_created_at,
             vectorizer_version, label_reason, source_api, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sig["signal_id"],
                sig["canonical_key"],
                sig.get("company_id"),
                sig["human_label"],
                texts[i],
                vec_blob,
                text_hash,
                sig.get("signal_created_at"),
                version,
                sig.get("label_reason"),
                sig.get("source_api"),
                sig.get("confidence"),
            ),
        )
    await store._db.commit()

    # Prune old versions
    pruned = await _prune_old_versions(store, version)
    if pruned:
        logger.info("Pruned %d precedents from old vectorizer versions", pruned)

    result["pruned"] = pruned
    return result


async def _prune_old_versions(store, current_version: str) -> int:
    """Delete precedents from superseded vectorizer versions."""
    cursor = await store._db.execute(
        "DELETE FROM precedents WHERE vectorizer_version != ?",
        (current_version,),
    )
    await store._db.commit()
    return cursor.rowcount


async def calibrate_corpus(store) -> dict[str, dict]:
    """Print TF-IDF cosine similarity score distributions.

    Computes pairwise similarities between all labeled signals,
    partitioned into TP-vs-TP, FP-vs-FP, TP-vs-FP.

    Returns dict with distribution stats per partition.
    Does NOT write to DB.
    """
    signals = await _fetch_labeled_signals(store)
    if len(signals) < 2:
        logger.warning("Need at least 2 labeled signals for calibration")
        return {
            "tp_vs_tp": {"count": 0, "min": 0, "p25": 0, "p50": 0, "p75": 0, "max": 0},
            "fp_vs_fp": {"count": 0, "min": 0, "p25": 0, "p50": 0, "p75": 0, "max": 0},
            "tp_vs_fp": {"count": 0, "min": 0, "p25": 0, "p50": 0, "p75": 0, "max": 0},
        }

    company_ids = [s["company_id"] for s in signals if s.get("company_id")]
    schemas = await _fetch_schema_rows(store, company_ids)

    texts = []
    labels = []
    for sig in signals:
        schema_row = schemas.get(sig.get("company_id"))
        text = build_corpus_text(sig["company_name"] or "", sig["raw_data"] or "{}", schema_row)
        texts.append(text)
        labels.append(sig["human_label"])

    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    vectorizer = TfidfVectorizer(
        max_features=3000, ngram_range=(1, 2), min_df=1,
        sublinear_tf=True, strip_accents="unicode",
    )
    tfidf_matrix = vectorizer.fit_transform(texts)
    sim_matrix = cosine_similarity(tfidf_matrix)

    # Partition pairs
    tp_vs_tp, fp_vs_fp, tp_vs_fp = [], [], []
    n = len(labels)
    for i in range(n):
        for j in range(i + 1, n):
            score = sim_matrix[i, j]
            if labels[i] == "TP" and labels[j] == "TP":
                tp_vs_tp.append(score)
            elif labels[i] == "FP" and labels[j] == "FP":
                fp_vs_fp.append(score)
            else:
                tp_vs_fp.append(score)

    def _stats(scores):
        if not scores:
            return {"count": 0, "min": 0, "p25": 0, "p50": 0, "p75": 0, "max": 0}
        arr = np.array(scores)
        return {
            "count": len(arr),
            "min": float(np.min(arr)),
            "p25": float(np.percentile(arr, 25)),
            "p50": float(np.percentile(arr, 50)),
            "p75": float(np.percentile(arr, 75)),
            "max": float(np.max(arr)),
        }

    return {
        "tp_vs_tp": _stats(tp_vs_tp),
        "fp_vs_fp": _stats(fp_vs_fp),
        "tp_vs_fp": _stats(tp_vs_fp),
    }


async def main():
    from storage.signal_store import SignalStore

    parser = argparse.ArgumentParser(description="Build case-law corpus from labeled signals")
    parser.add_argument("--db", default=resolve_db_path_env(), help="Database path")
    parser.add_argument("--version", default="v1.0.0", help="Vectorizer version")
    parser.add_argument("--dry-run", action="store_true", help="Print stats without writing")
    parser.add_argument("--calibrate", action="store_true", help="Print similarity distributions")
    parser.add_argument("--check-only", action="store_true", help="Check if retrain is needed (no write)")
    parser.add_argument("--vectorizer-dir", default=None, help="Vectorizer output directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    store = SignalStore(db_path=args.db)
    await store.initialize()

    try:
        if args.check_only:
            vdir = args.vectorizer_dir or VECTORIZER_DIR
            needs_retrain, reason = check_retrain_needed(args.db, vdir)
            if needs_retrain:
                print(f"Retrain needed: {reason}")
                sys.exit(0)
            else:
                print(f"No retrain needed: {reason}")
                sys.exit(0)
        elif args.calibrate:
            result = await calibrate_corpus(store)
            print(f"\nSimilarity distributions:")
            for partition, stats in result.items():
                print(
                    f"  {partition} ({stats['count']} pairs): "
                    f"min={stats['min']:.2f} p25={stats['p25']:.2f} "
                    f"p50={stats['p50']:.2f} p75={stats['p75']:.2f} max={stats['max']:.2f}"
                )
            suggested = result["tp_vs_tp"].get("p75", 0.75)
            print(f"\nSuggested veto threshold: {suggested:.2f} (75th pctl TP-vs-TP)")
        else:
            result = await build_corpus(
                store,
                version=args.version,
                dry_run=args.dry_run,
                vectorizer_dir=args.vectorizer_dir,
            )
            print(f"\nCorpus build {'(DRY RUN) ' if args.dry_run else ''}complete:")
            print(f"  Signals: {result['corpus_size']}")
            print(f"  Labels: {result.get('label_counts', {})}")
            print(f"  Vocab: {result.get('vocab_size', 'N/A')}")
            if result.get("pruned"):
                print(f"  Pruned: {result['pruned']} old-version rows")
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""Build case-law corpus from labeled signals.

Reads labeled signals (TP + FP) from signal_quality_metrics, trains a TF-IDF
vectorizer, and stores precomputed vectors in the precedents table.

Safety invariants:
  - Only TP and FP labels are included (UNSURE excluded).
  - Bare invocation and dry-run are safe: print stats without writing.
  - Commit mode is explicit: use --commit for vectorizer artifacts and DB rows.
  - Old-version precedents are pruned after successful build.
  - Calibrate mode is read-only.

Usage:
    python scripts/build_case_law_corpus.py --db signals.db --version v1.0.0 --commit
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
from urllib.parse import quote

import numpy as np

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.corpus_text_builder import build_corpus_text
from utils.db_ops_ledger import append_db_ops_ledger
from utils.db_path_helper import resolve_db_path_env
from utils.db_tool_errors import DBToolError
from utils.db_tool_lock import DBToolLock
from utils.db_tool_preflight import read_sqlite_data_version
from utils.report_envelope import create_report, write_report
from intelligence.vectorizer_config import (
    VECTORIZER_DIR,
    VectorizerMetadata,
    save_metadata,
    check_retrain_needed,
)

logger = logging.getLogger(__name__)

TOOL_NAME = "build_case_law_corpus"
ACTION = "build_case_law_corpus"
LOCK_TIMEOUT_SECONDS = 5


class BuildCaseLawCorpusError(DBToolError):
    """Case-law corpus failure with staged-artifact evidence."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        preflight_data_version: int | None = None,
        version: str,
        corpus_size: int = 0,
        label_counts: dict[str, int] | None = None,
        vectorizer_path: str | None = None,
        metadata_path: str | None = None,
        staged_vectorizer_path: str | None = None,
        staged_metadata_path: str | None = None,
        artifact_finalization_status: dict[str, str] | None = None,
        artifact_cleanup: dict[str, Any] | None = None,
        pruned_attempted: int = 0,
        artifacts_finalized: bool = False,
        db_transaction: str = "rolled_back",
        cause: str | None = None,
    ) -> None:
        full_message = message if cause is None else f"{message}: {cause}"
        super().__init__(
            full_message,
            partial_evidence={
                "phase": phase,
                "preflight_data_version": preflight_data_version,
                "version": version,
                "corpus_size": corpus_size,
                "label_counts": label_counts or {},
                "vectorizer_path": vectorizer_path,
                "metadata_path": metadata_path,
                "staged_vectorizer_path": staged_vectorizer_path,
                "staged_metadata_path": staged_metadata_path,
                "artifact_finalization_status": artifact_finalization_status or {},
                "artifact_cleanup": artifact_cleanup or {},
                "pruned_attempted": pruned_attempted,
                "db_transaction": db_transaction,
                "artifacts_finalized": artifacts_finalized,
                "cause": cause,
            },
        )


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


def _staged_path(final_path: Path) -> Path:
    return final_path.with_name(f".{final_path.name}.{os.getpid()}.tmp")


def _cleanup_artifacts(
    *,
    staged_paths: list[Path],
    finalized_paths: list[tuple[Path, bool]],
    artifact_finalization_status: dict[str, str],
) -> dict[str, Any]:
    cleanup: dict[str, Any] = {"staged": {}, "finalized": {}}
    for path in staged_paths:
        key = str(path)
        if not path.exists():
            cleanup["staged"][key] = "absent"
            continue
        try:
            path.unlink()
            cleanup["staged"][key] = "removed"
        except OSError as exc:
            cleanup["staged"][key] = f"remove_failed: {exc}"

    for path, existed_before in finalized_paths:
        key = str(path)
        status = artifact_finalization_status.get(key, "not_attempted")
        if status != "replaced":
            cleanup["finalized"][key] = status
        elif existed_before:
            cleanup["finalized"][key] = "left_replaced_existing_path"
        elif not path.exists():
            cleanup["finalized"][key] = "absent"
        else:
            try:
                path.unlink()
                cleanup["finalized"][key] = "removed_new_finalized_path"
            except OSError as exc:
                cleanup["finalized"][key] = f"remove_failed: {exc}"
    return cleanup


def _replace_artifact(staged_path: Path, final_path: Path, status: dict[str, str]) -> None:
    key = str(final_path)
    status[key] = "replace_attempted"
    os.replace(staged_path, final_path)
    status[key] = "replaced"


async def build_corpus(
    store,
    version: str,
    dry_run: bool = False,
    vectorizer_dir: Optional[str] = None,
    preflight_data_version: int | None = None,
) -> dict[str, Any]:
    """Build case-law corpus from labeled signals.

    Returns dict with build stats (corpus_size, label_counts, etc.).
    """
    vdir = vectorizer_dir or VECTORIZER_DIR

    # Fetch labeled signals
    signals = await _fetch_labeled_signals(store)
    if not signals:
        logger.warning("No labeled signals found — empty corpus")
        return {
            "corpus_size": 0,
            "label_counts": {},
            "version": version,
            "preflight_data_version": preflight_data_version,
            "dry_run": dry_run,
            "pruned": 0,
            "artifacts_finalized": False,
        }

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
        "preflight_data_version": preflight_data_version,
        "dry_run": dry_run,
    }

    if dry_run:
        logger.info(
            "DRY RUN: corpus=%d signals, vocab=%d, labels=%s",
            len(signals), vocab_size, label_counts,
        )
        return result

    vdir_path = Path(vdir)
    vectorizer_path = vdir_path / f"case_law_{version}.joblib"
    meta_path = vdir_path / f"case_law_{version}_meta.json"
    staged_vectorizer_path = _staged_path(vectorizer_path)
    staged_meta_path = _staged_path(meta_path)
    artifact_finalization_status = {
        str(vectorizer_path): "not_attempted",
        str(meta_path): "not_attempted",
    }
    phase = "create_vectorizer_dir"
    pruned = 0

    try:
        os.makedirs(vdir_path, exist_ok=True)
        existing_targets = [
            str(path)
            for path in (vectorizer_path, meta_path)
            if path.exists()
        ]
        if existing_targets:
            for path in existing_targets:
                artifact_finalization_status[path] = "preexisting"
            raise BuildCaseLawCorpusError(
                "Target corpus artifact already exists; use a new --version or remove stale artifacts",
                phase="preflight_artifacts",
                preflight_data_version=preflight_data_version,
                version=version,
                corpus_size=len(signals),
                label_counts=label_counts,
                vectorizer_path=str(vectorizer_path),
                metadata_path=str(meta_path),
                staged_vectorizer_path=str(staged_vectorizer_path),
                staged_metadata_path=str(staged_meta_path),
                artifact_finalization_status=artifact_finalization_status,
                db_transaction="not_started",
                cause=", ".join(existing_targets),
            )

        phase = "save_vectorizer"
        import joblib
        joblib.dump(vectorizer, staged_vectorizer_path)

        vec_bytes = pickle.dumps(vectorizer)
        vec_hash = hashlib.sha256(vec_bytes).hexdigest()

        phase = "save_metadata"
        meta = VectorizerMetadata(
            version=version,
            trained_at=datetime.now(timezone.utc).isoformat(),
            corpus_size=len(signals),
            corpus_labels=label_counts,
            vocab_size=vocab_size,
            vectorizer_hash=vec_hash,
        )
        save_metadata(meta, str(staged_meta_path))

        async with store.transaction_immediate() as tx:
            phase = "insert_precedents"
            for i, sig in enumerate(signals):
                vec_blob = pickle.dumps(tfidf_matrix[i])
                text_hash = hashlib.sha256(texts[i].encode()).hexdigest()

                await tx.execute(
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

            phase = "prune_old_versions"
            pruned = await _prune_old_versions(store, version, tx=tx)

            phase = "finalize_artifacts"
            _replace_artifact(staged_vectorizer_path, vectorizer_path, artifact_finalization_status)
            _replace_artifact(staged_meta_path, meta_path, artifact_finalization_status)
            phase = "commit_transaction"

        if pruned:
            logger.info("Pruned %d precedents from old vectorizer versions", pruned)

        result.update(
            {
                "pruned": pruned,
                "vectorizer_path": str(vectorizer_path),
                "metadata_path": str(meta_path),
                "staged_vectorizer_path": str(staged_vectorizer_path),
                "staged_metadata_path": str(staged_meta_path),
                "artifact_finalization_status": artifact_finalization_status,
                "artifacts_finalized": True,
                "artifact_cleanup": {},
                "db_transaction": "committed",
            }
        )
        return result
    except DBToolError:
        raise
    except Exception as exc:
        cleanup = _cleanup_artifacts(
            staged_paths=[staged_vectorizer_path, staged_meta_path],
            finalized_paths=[(vectorizer_path, False), (meta_path, False)],
            artifact_finalization_status=artifact_finalization_status,
        )
        artifacts_finalized = all(
            status == "replaced" for status in artifact_finalization_status.values()
        )
        raise BuildCaseLawCorpusError(
            "Failed to build case-law corpus",
            phase=phase,
            preflight_data_version=preflight_data_version,
            version=version,
            corpus_size=len(signals),
            label_counts=label_counts,
            vectorizer_path=str(vectorizer_path),
            metadata_path=str(meta_path),
            staged_vectorizer_path=str(staged_vectorizer_path),
            staged_metadata_path=str(staged_meta_path),
            artifact_finalization_status=artifact_finalization_status,
            artifact_cleanup=cleanup,
            pruned_attempted=pruned,
            artifacts_finalized=artifacts_finalized,
            cause=str(exc),
        ) from exc


async def _prune_old_versions(store, current_version: str, tx=None) -> int:
    """Delete precedents from superseded vectorizer versions."""
    conn = tx or store._db
    cursor = await conn.execute(
        "DELETE FROM precedents WHERE vectorizer_version != ?",
        (current_version,),
    )
    if tx is None:
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


def _write_report_if_requested(
    *,
    report_path: str | None,
    ok: bool,
    db_path: str,
    started_at: datetime,
    metrics: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> None:
    if not report_path:
        return
    report = create_report(
        command=ACTION,
        ok=ok,
        db_path=db_path,
        started_at=started_at,
        metrics=metrics,
        errors=errors,
    )
    write_report(report, report_path)


def _append_ledger(*, db_path: str, status: str, details: dict[str, Any]) -> None:
    append_db_ops_ledger(
        tool_name=TOOL_NAME,
        db_path=db_path,
        action=ACTION,
        status=status,
        details=details,
    )


class _ReadOnlyStore:
    """Minimal read-only store facade for non-mutating CLI modes."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db = None

    async def initialize(self) -> None:
        import aiosqlite

        resolved = Path(self.db_path).resolve().as_posix()
        uri = f"file:{quote(resolved, safe='/:')}?mode=ro"
        self._db = await aiosqlite.connect(uri, uri=True)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()


async def main() -> int:
    from storage.signal_store import SignalStore

    parser = argparse.ArgumentParser(description="Build case-law corpus from labeled signals")
    parser.add_argument("--db", default=None, help="Database path")
    parser.add_argument("--version", default="v1.0.0", help="Vectorizer version")
    parser.add_argument("--dry-run", action="store_true", help="Print stats without writing")
    parser.add_argument("--commit", action="store_true", help="Write vectorizer artifacts and DB rows")
    parser.add_argument("--calibrate", action="store_true", help="Print similarity distributions")
    parser.add_argument("--check-only", action="store_true", help="Check if retrain is needed (no write)")
    parser.add_argument("--vectorizer-dir", default=None, help="Vectorizer output directory")
    parser.add_argument("--report", default="", help="Optional path to write a JSON report envelope")
    args = parser.parse_args()
    args.db = resolve_db_path_env(args.db)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    started_at = datetime.now(timezone.utc)
    report_path = args.report or None
    preflight_data_version: int | None = None
    lock: DBToolLock | None = None
    store: Any | None = None
    read_only_flag_count = sum(1 for flag in (args.dry_run, args.calibrate, args.check_only) if flag)

    if args.commit and read_only_flag_count:
        error = "--commit cannot be combined with --dry-run, --calibrate, or --check-only"
        _write_report_if_requested(
            report_path=report_path,
            ok=False,
            db_path=args.db,
            started_at=started_at,
            errors=[error],
        )
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if read_only_flag_count > 1:
        error = "Choose only one read-only mode: --dry-run, --calibrate, or --check-only"
        _write_report_if_requested(
            report_path=report_path,
            ok=False,
            db_path=args.db,
            started_at=started_at,
            errors=[error],
        )
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    try:
        preflight_data_version = read_sqlite_data_version(args.db)

        if args.check_only:
            vdir = args.vectorizer_dir or VECTORIZER_DIR
            needs_retrain, reason = check_retrain_needed(args.db, vdir)
            metrics = {
                "dry_run": True,
                "preflight_data_version": preflight_data_version,
                "needs_retrain": needs_retrain,
                "reason": reason,
            }
            _write_report_if_requested(
                report_path=report_path,
                ok=True,
                db_path=args.db,
                started_at=started_at,
                metrics=metrics,
            )
            if needs_retrain:
                print(f"Retrain needed: {reason}")
            else:
                print(f"No retrain needed: {reason}")
            return 0

        if args.commit:
            lock = DBToolLock(args.db, tool_name=TOOL_NAME)
            if not lock.acquire(timeout_seconds=LOCK_TIMEOUT_SECONDS):
                holder = lock.get_holder_info()
                error = f"Could not acquire DB tool lock. Holder: {holder}"
                details = {
                    "holder": holder,
                    "commit": True,
                    "preflight_data_version": preflight_data_version,
                    "version": args.version,
                }
                _append_ledger(db_path=args.db, status="lock_blocked", details=details)
                _write_report_if_requested(
                    report_path=report_path,
                    ok=False,
                    db_path=args.db,
                    started_at=started_at,
                    metrics=details,
                    errors=[error],
                )
                print(f"ERROR: {error}", file=sys.stderr)
                return 2

        store = SignalStore(db_path=args.db) if args.commit else _ReadOnlyStore(args.db)
        await store.initialize()

        if args.calibrate:
            result = await calibrate_corpus(store)
            metrics = {
                "dry_run": True,
                "preflight_data_version": preflight_data_version,
                "version": args.version,
                "calibration": result,
            }
            _write_report_if_requested(
                report_path=report_path,
                ok=True,
                db_path=args.db,
                started_at=started_at,
                metrics=metrics,
            )
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
            dry_run = not args.commit
            result = await build_corpus(
                store,
                version=args.version,
                dry_run=dry_run,
                vectorizer_dir=args.vectorizer_dir,
                preflight_data_version=preflight_data_version,
            )
            _write_report_if_requested(
                report_path=report_path,
                ok=True,
                db_path=args.db,
                started_at=started_at,
                metrics=result,
            )
            if args.commit:
                _append_ledger(db_path=args.db, status="success", details=result)
            print(f"\nCorpus build {'(DRY RUN) ' if dry_run else ''}complete:")
            print(f"  Signals: {result['corpus_size']}")
            print(f"  Labels: {result.get('label_counts', {})}")
            print(f"  Vocab: {result.get('vocab_size', 'N/A')}")
            if dry_run:
                print("  Write gate: rerun with --commit to persist artifacts and DB rows")
            if result.get("pruned"):
                print(f"  Pruned: {result['pruned']} old-version rows")
        return 0
    except DBToolError as exc:
        details = {**exc.partial_evidence, "error": str(exc)}
        if args.commit:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            ok=False,
            db_path=args.db,
            started_at=started_at,
            metrics=exc.partial_evidence,
            errors=[str(exc)],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        details = {
            "error": str(exc),
            "preflight_data_version": preflight_data_version,
            "version": args.version,
        }
        if args.commit:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            ok=False,
            db_path=args.db,
            started_at=started_at,
            metrics={
                "preflight_data_version": preflight_data_version,
                "version": args.version,
            },
            errors=[str(exc)],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            await store.close()
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

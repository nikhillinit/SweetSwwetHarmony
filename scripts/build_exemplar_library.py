#!/usr/bin/env python3
"""Build exemplar library from TP-labeled signals.

Reads TP-labeled signals from signal_quality_metrics, vectorizes using the
existing case-law TF-IDF vectorizer, and populates the thesis_exemplars table.

Safety invariants:
  - Only TP labels are included (FP and UNSURE excluded).
  - Reuses the case-law vectorizer (transform only, no refit).
  - Bare invocation and dry-run are safe: print stats without writing.
  - Commit mode is explicit: use --commit for DB rows.
  - Old-version exemplars are pruned after successful build.

Usage:
    python scripts/build_exemplar_library.py --db signals.db --version v1.0.0 --commit
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.corpus_text_builder import build_corpus_text
from utils.db_ops_ledger import append_db_ops_ledger
from utils.db_path_helper import resolve_db_path_env
from utils.db_tool_errors import DBToolError
from utils.db_tool_lock import DBToolLock
from utils.db_tool_preflight import read_sqlite_data_version
from utils.report_envelope import create_report, write_report
from intelligence.vectorizer_config import VECTORIZER_DIR

logger = logging.getLogger(__name__)

# Category inference from thesis_classifications (fallback: "general")
DEFAULT_CATEGORY = "general"
TOOL_NAME = "build_exemplar_library"
ACTION = "build_exemplar_library"
LOCK_TIMEOUT_SECONDS = 5


class BuildExemplarLibraryError(DBToolError):
    """Exemplar build failure with vectorizer and DB evidence."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        preflight_data_version: int | None = None,
        version: str,
        vectorizer_path: str | None = None,
        exemplar_count: int = 0,
        categories: dict[str, int] | None = None,
        skipped_empty_text: int = 0,
        pruned_attempted: int = 0,
        db_transaction: str = "not_started",
    ) -> None:
        super().__init__(
            message,
            partial_evidence={
                "phase": phase,
                "preflight_data_version": preflight_data_version,
                "version": version,
                "vectorizer_path": vectorizer_path,
                "exemplar_count": exemplar_count,
                "categories": categories or {},
                "skipped_empty_text": skipped_empty_text,
                "pruned_attempted": pruned_attempted,
                "db_transaction": db_transaction,
            },
        )


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
    preflight_data_version: int | None = None,
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
        return {
            "error": f"Vectorizer not found: {vectorizer_path}",
            "exemplar_count": 0,
            "version": version,
            "vectorizer_path": vectorizer_path,
            "preflight_data_version": preflight_data_version,
            "dry_run": dry_run,
            "db_transaction": "not_started",
        }

    import joblib
    try:
        vectorizer = joblib.load(vectorizer_path)
    except Exception as exc:
        raise BuildExemplarLibraryError(
            "Failed to load exemplar vectorizer",
            phase="load_vectorizer",
            preflight_data_version=preflight_data_version,
            version=version,
            vectorizer_path=vectorizer_path,
            db_transaction="not_started",
        ) from exc

    # Fetch TP signals
    signals = await _fetch_tp_signals(store)
    if not signals:
        logger.warning("No TP-labeled signals found — empty exemplar library")
        return {
            "exemplar_count": 0,
            "version": version,
            "vectorizer_path": vectorizer_path,
            "preflight_data_version": preflight_data_version,
            "dry_run": dry_run,
            "categories": {},
            "skipped_empty_text": 0,
            "pruned": 0,
            "db_transaction": "not_started",
        }

    # Fetch schema rows for enrichment
    company_ids = [s["company_id"] for s in signals if s.get("company_id")]
    schemas = await _fetch_schema_rows(store, company_ids)

    # Build corpus text + metadata per signal
    exemplars = []
    skipped_empty_text = 0
    for sig in signals:
        schema_row = schemas.get(sig.get("company_id"))
        text = build_corpus_text(
            sig["company_name"] or "",
            sig["raw_data"] or "{}",
            schema_row,
        )
        if not text.strip():
            logger.warning("Empty corpus text for signal %s — skipping", sig["signal_id"])
            skipped_empty_text += 1
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
        "vectorizer_path": vectorizer_path,
        "preflight_data_version": preflight_data_version,
        "dry_run": dry_run,
        "skipped_empty_text": skipped_empty_text,
    }

    if dry_run:
        logger.info(
            "DRY RUN: %d exemplars, categories=%s",
            len(exemplars), categories,
        )
        return result

    phase = "vectorize_exemplars"
    pruned = 0
    try:
        texts = [e["corpus_text"] for e in exemplars]
        tfidf_matrix = vectorizer.transform(texts)

        async def _write_rows(conn) -> None:
            for i, e in enumerate(exemplars):
                sig = e["signal"]
                vec_blob = pickle.dumps(tfidf_matrix[i])

                await conn.execute(
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

        phase = "write_exemplars"
        if hasattr(store, "transaction_immediate"):
            async with store.transaction_immediate() as tx:
                await _write_rows(tx)
                phase = "prune_old_versions"
                pruned = await _prune_old_versions(store, version, tx=tx)
            db_transaction = "committed"
        else:
            await _write_rows(store._db)
            await store._db.commit()
            phase = "prune_old_versions"
            pruned = await _prune_old_versions(store, version)
            db_transaction = "committed"

        if pruned:
            logger.info("Pruned %d exemplars from old vectorizer versions", pruned)

        result["pruned"] = pruned
        result["db_transaction"] = db_transaction
        return result
    except DBToolError:
        raise
    except Exception as exc:
        raise BuildExemplarLibraryError(
            "Failed to build exemplar library",
            phase=phase,
            preflight_data_version=preflight_data_version,
            version=version,
            vectorizer_path=vectorizer_path,
            exemplar_count=len(exemplars),
            categories=categories,
            skipped_empty_text=skipped_empty_text,
            pruned_attempted=pruned,
            db_transaction="rolled_back" if hasattr(store, "transaction_immediate") else "unknown",
        ) from exc


async def _prune_old_versions(store, current_version: str, tx=None) -> int:
    """Delete exemplars from superseded vectorizer versions."""
    conn = tx or store._db
    cursor = await conn.execute(
        "DELETE FROM thesis_exemplars WHERE vectorizer_version != ? AND source = 'auto'",
        (current_version,),
    )
    if tx is None:
        await store._db.commit()
    return cursor.rowcount


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

    parser = argparse.ArgumentParser(description="Build exemplar library from TP labels")
    parser.add_argument("--db", default=resolve_db_path_env(), help="Database path")
    parser.add_argument("--version", default="v1.0.0", help="Vectorizer version (must match case-law)")
    parser.add_argument("--dry-run", action="store_true", help="Print stats without writing")
    parser.add_argument("--commit", action="store_true", help="Write exemplar rows to the database")
    parser.add_argument("--vectorizer-dir", default=None, help="Vectorizer directory")
    parser.add_argument("--report", default="", help="Optional path to write a JSON report envelope")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    started_at = datetime.now(timezone.utc)
    report_path = args.report or None
    preflight_data_version: int | None = None
    lock: DBToolLock | None = None
    store: Any | None = None

    if args.commit and args.dry_run:
        error = "--commit cannot be combined with --dry-run"
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

        dry_run = not args.commit
        result = await build_exemplar_library(
            store,
            version=args.version,
            dry_run=dry_run,
            vectorizer_dir=args.vectorizer_dir,
            preflight_data_version=preflight_data_version,
        )
        if result.get("error"):
            exc = BuildExemplarLibraryError(
                result["error"],
                phase="load_vectorizer",
                preflight_data_version=preflight_data_version,
                version=args.version,
                vectorizer_path=result.get("vectorizer_path"),
                exemplar_count=0,
                db_transaction="not_started",
            )
            raise exc
        _write_report_if_requested(
            report_path=report_path,
            ok=True,
            db_path=args.db,
            started_at=started_at,
            metrics=result,
        )
        if args.commit:
            _append_ledger(db_path=args.db, status="success", details=result)
        print(f"\nExemplar library build {'(DRY RUN) ' if dry_run else ''}complete:")
        print(f"  Exemplars: {result['exemplar_count']}")
        print(f"  Categories: {result.get('categories', {})}")
        if dry_run:
            print("  Write gate: rerun with --commit to persist exemplar rows")
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

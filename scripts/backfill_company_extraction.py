"""Backfill company name extraction for news/RSS signals.

Default policy is precision-first (`COMPANY_NAME_WRITE_POLICY`):
- Canonical writes are fill-empty-only (never overwrite existing company_name)
- Bulk writes use structured extraction only (regex + url_derived)
- NER can be inspected in dry-run candidate mode but never committed

Follows backfill_evidence_keys.py pattern (sync sqlite3, chunked).

Usage:
    python scripts/backfill_company_extraction.py --db signals.db --preflight
    python scripts/backfill_company_extraction.py --db signals.db --dry-run
    python scripts/backfill_company_extraction.py --db signals.db --commit
    python scripts/backfill_company_extraction.py --db signals.db --dry-run --include-ner-candidates
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_ops_ledger import append_db_ops_ledger
from utils.db_tool_errors import DBToolError
from utils.db_tool_lock import DBToolLock
from utils.db_tool_preflight import read_sqlite_data_version
from utils.report_envelope import create_report, write_report

logger = logging.getLogger(__name__)
LOCK_TIMEOUT_SECONDS = 5

# Source APIs to re-extract
_TARGET_SOURCES = ("news_api", "rss_feeds")
_ACTOR_ID = "system:bulk_company_name_backfill_v1"
_SOURCE_VERSION = "bulk_company_name_backfill_v1"


class BackfillCompanyExtractionError(DBToolError):
    """Company extraction backfill failure carrying partial progress evidence."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        scanned: int = 0,
        updated_attempted: int = 0,
        errors: int = 0,
        skipped_non_empty: int = 0,
        allowlist_count: int | None = None,
        policy_id: str | None = None,
        policy_version: str | None = None,
        include_ner_candidates: bool | None = None,
        dry_run: bool | None = None,
        preflight_data_version: int | None = None,
    ) -> None:
        super().__init__(
            message,
            partial_evidence={
                "phase": phase,
                "scanned": scanned,
                "updated_attempted": updated_attempted,
                "errors": errors,
                "skipped_non_empty": skipped_non_empty,
                "allowlist_count": allowlist_count,
                "policy_id": policy_id,
                "policy_version": policy_version,
                "include_ner_candidates": include_ner_candidates,
                "dry_run": dry_run,
                "preflight_data_version": preflight_data_version,
            },
        )


def _parse_raw_data(raw_data_str: str) -> Optional[Dict[str, Any]]:
    """Parse raw_data JSON string, returning None on failure."""
    try:
        return json.loads(raw_data_str)
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_allowlist_ids(raw_ids: Optional[str]) -> Optional[Set[int]]:
    """Parse comma-separated signal IDs, returning a set or None."""
    if raw_ids is None:
        return None

    text = raw_ids.strip()
    if not text:
        return None

    ids: Set[int] = set()
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if not token.isdigit():
            raise ValueError(f"Invalid signal id '{token}' in --allowlist-ids")
        value = int(token)
        if value <= 0:
            raise ValueError(f"Signal id must be > 0, got {value}")
        ids.add(value)

    if not ids:
        raise ValueError("No valid IDs provided in --allowlist-ids")
    return ids


def _rebuild_canonical_key(
    company_name: Optional[str],
    promoted_domain: Optional[str],
) -> Optional[str]:
    """Rebuild canonical_key from extraction result.

    Priority: domain > name_loc > None (leave unchanged).
    """
    from utils.canonical_keys import build_canonical_key

    if promoted_domain:
        return build_canonical_key(domain_or_website=promoted_domain)
    if company_name:
        return build_canonical_key(fallback_company_name=company_name)
    return None


def _is_empty_name(name: Optional[str]) -> bool:
    """True when canonical company_name is unset/blank."""
    return name is None or not str(name).strip()


def preflight(db_path: str) -> Dict[str, Any]:
    """Show summary statistics for news/RSS signals before backfill."""
    preflight_data_version = read_sqlite_data_version(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE source_api IN (?, ?)",
            _TARGET_SOURCES,
        ).fetchone()[0]

        hash_keys = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE source_api IN (?, ?) "
            "AND canonical_key LIKE 'rss_%'",
            _TARGET_SOURCES,
        ).fetchone()[0]

        name_loc_keys = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE source_api IN (?, ?) "
            "AND canonical_key LIKE 'name_loc:%'",
            _TARGET_SOURCES,
        ).fetchone()[0]

        domain_keys = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE source_api IN (?, ?) "
            "AND canonical_key LIKE 'domain:%'",
            _TARGET_SOURCES,
        ).fetchone()[0]

        no_company = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE source_api IN (?, ?) "
            "AND (company_name IS NULL OR company_name = '')",
            _TARGET_SOURCES,
        ).fetchone()[0]

        return {
            "total_news_rss_signals": total,
            "hash_canonical_keys": hash_keys,
            "name_loc_keys": name_loc_keys,
            "domain_keys": domain_keys,
            "missing_company_name": no_company,
            "preflight_data_version": preflight_data_version,
        }
    finally:
        conn.close()


def run(
    db_path: str,
    dry_run: bool = True,
    chunk_size: int = 100,
    allowlist_ids: Optional[Set[int]] = None,
    include_ner_candidates: bool = False,
    policy_path: Optional[str] = None,
    preflight_data_version: int | None = None,
) -> Dict[str, Any]:
    """Re-extract company names for news/RSS signals.

    1. SELECT all news/RSS signals (or allowlisted IDs if provided)
    2. Skip non-empty company_name rows (fill-empty-only policy)
    3. Re-extract company name from raw_data title + description
    4. Rebuild canonical_key if extraction improved
    4. UPDATE company_name, canonical_key, and raw_data (with backfill flag)

    Returns: {total, scanned, updated, unchanged, errors, dry_run, diffs}
    """
    from utils.company_name_extractor import extract_company_info, warmup_ner
    from utils.company_name_policy import (
        Candidate,
        CanonicalState,
        decide_write_canonical_auto,
        load_company_name_policy,
        normalize_company_name,
    )

    policy = load_company_name_policy(policy_path)
    policy_id = str(policy.get("policy_id", "COMPANY_NAME_WRITE_POLICY"))
    policy_version = str(policy.get("policy_version", "1"))
    candidate_source_version = f"{_SOURCE_VERSION}@p{policy_version}"
    canonical_auto_rules = policy.get("auto_write_rules", {}).get("canonical", {})
    fill_only_when_empty = canonical_auto_rules.get("fill_only_when_empty", True)

    # Default to structured extraction only for safe bulk writes.
    extraction_mode = "ner_active" if include_ner_candidates else "url_promote"
    if include_ner_candidates:
        warmup_ner()

    phase = "preflight_data_version"
    scanned = 0
    updated = 0
    updated_attempted = 0
    errors = 0
    skipped_non_empty = 0
    transaction_started = False
    conn: sqlite3.Connection | None = None
    try:
        if preflight_data_version is None:
            preflight_data_version = read_sqlite_data_version(db_path)

        phase = "connect"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        if not dry_run:
            phase = "begin"
            conn.execute("BEGIN IMMEDIATE")
            transaction_started = True

        where_clause = "source_api IN (?, ?)"
        where_params: List[Any] = [*_TARGET_SOURCES]
        if allowlist_ids:
            placeholders = ", ".join("?" for _ in allowlist_ids)
            where_clause += f" AND id IN ({placeholders})"
            where_params.extend(sorted(allowlist_ids))

        phase = "count"
        total = conn.execute(
            f"SELECT COUNT(*) FROM signals WHERE {where_clause}",
            tuple(where_params),
        ).fetchone()[0]

        unchanged = 0
        ner_candidates_seen = 0
        ner_writes_blocked = 0
        policy_denied = 0
        policy_denied_reasons: Counter[str] = Counter()
        diffs: List[Dict[str, Any]] = []

        offset = 0
        while True:
            phase = "select_chunk"
            rows = conn.execute(
                "SELECT id, source_api, company_name, canonical_key, raw_data "
                f"FROM signals WHERE {where_clause} "
                "ORDER BY id LIMIT ? OFFSET ?",
                tuple(where_params + [chunk_size, offset]),
            ).fetchall()
            if not rows:
                break

            batch_updates: List[tuple] = []

            for row_id, source_api, old_name, old_key, raw_data_str in rows:
                phase = "process_row"
                scanned += 1
                raw = _parse_raw_data(raw_data_str)
                if raw is None:
                    errors += 1
                    continue

                title = raw.get("title", "")
                description = raw.get("description", "")

                # Policy guard: fill-only-when-empty for automated writes.
                if fill_only_when_empty and not _is_empty_name(old_name):
                    skipped_non_empty += 1
                    unchanged += 1
                    continue

                # Re-extract with improved pipeline
                try:
                    result = extract_company_info(
                        title=title,
                        description=description,
                        url=raw.get("url", ""),
                        mode=extraction_mode,
                    )
                except Exception as e:
                    logger.warning("Extraction error for signal %d: %s", row_id, e)
                    errors += 1
                    continue

                new_name = result.company_name
                new_key = _rebuild_canonical_key(
                    new_name, result.promoted_domain
                )

                # Only update if extraction actually improved
                if not new_name:
                    unchanged += 1
                    continue
                if normalize_company_name(new_name, policy) == normalize_company_name(old_name or "", policy):
                    unchanged += 1
                    continue
                if new_name == old_name and (new_key is None or new_key == old_key):
                    unchanged += 1
                    continue

                # Use old key if rebuild returned None
                final_key = new_key if new_key else old_key

                method = result.company_name_method or "unknown"
                if method == "ner":
                    ner_candidates_seen += 1

                decision = decide_write_canonical_auto(
                    actor_id=_ACTOR_ID,
                    existing=CanonicalState(
                        name=old_name,
                        normalized=normalize_company_name(old_name or "", policy),
                        source=None,
                        locked=False,
                    ),
                    candidate=Candidate(
                        name=new_name,
                        source=method,
                        source_version=candidate_source_version,
                        confidence=None,
                        evidence={
                            "title": title,
                            "description": description,
                            "signal_id": row_id,
                        },
                    ),
                    policy=policy,
                )
                blocked_by_policy = not decision.allowed
                if blocked_by_policy:
                    policy_denied += 1
                    policy_denied_reasons[decision.reason] += 1
                    if method == "ner" and not dry_run:
                        ner_writes_blocked += 1
                    if not dry_run:
                        unchanged += 1
                        continue

                # Flag backfill in raw_data
                raw["_backfill_extraction"] = True
                raw["_backfill_policy_id"] = policy_id
                raw["_backfill_policy_version"] = policy_version
                raw["_backfill_company_name_source"] = method
                raw["_backfill_company_name_source_version"] = candidate_source_version
                if old_name and old_name != new_name:
                    raw["_backfill_old_company_name"] = old_name
                if old_key != final_key:
                    raw["_backfill_old_canonical_key"] = old_key

                diff = {
                    "id": row_id,
                    "old_name": old_name,
                    "new_name": new_name,
                    "old_key": old_key,
                    "new_key": final_key,
                    "method": method,
                    "blocked_by_policy": blocked_by_policy,
                    "policy_reason": decision.reason,
                }
                diffs.append(diff)

                if decision.allowed and decision.action == "write_canonical":
                    batch_updates.append((
                        new_name,
                        final_key,
                        json.dumps(raw, ensure_ascii=False),
                        row_id,
                    ))

            # Apply batch
            if batch_updates and not dry_run:
                try:
                    phase = "apply_chunk"
                    updated_attempted = updated + len(batch_updates)
                    conn.executemany(
                        "UPDATE signals SET company_name = ?, canonical_key = ?, "
                        "raw_data = ? WHERE id = ?",
                        batch_updates,
                    )
                except Exception:
                    raise

            updated += len(batch_updates)
            offset += chunk_size

        if transaction_started:
            phase = "commit"
            conn.commit()
            transaction_started = False

        return {
            "total": total,
            "scanned": scanned,
            "updated": updated,
            "unchanged": unchanged,
            "errors": errors,
            "dry_run": dry_run,
            "allowlist_count": len(allowlist_ids) if allowlist_ids else None,
            "policy_id": policy_id,
            "policy_version": policy_version,
            "mode": extraction_mode,
            "include_ner_candidates": include_ner_candidates,
            "skipped_non_empty": skipped_non_empty,
            "ner_candidates_seen": ner_candidates_seen,
            "ner_writes_blocked": ner_writes_blocked,
            "policy_denied": policy_denied,
            "policy_denied_reasons": dict(policy_denied_reasons),
            "preflight_data_version": preflight_data_version,
            "diffs": diffs if dry_run else [],
        }

    except BackfillCompanyExtractionError:
        if transaction_started and conn is not None:
            conn.rollback()
        raise
    except Exception as exc:
        if transaction_started and conn is not None:
            conn.rollback()
        raise BackfillCompanyExtractionError(
            f"company extraction backfill failed: {exc}",
            phase=phase,
            scanned=scanned,
            updated_attempted=updated_attempted,
            errors=errors,
            skipped_non_empty=skipped_non_empty,
            allowlist_count=len(allowlist_ids) if allowlist_ids else None,
            policy_id=policy_id,
            policy_version=policy_version,
            include_ner_candidates=include_ner_candidates,
            dry_run=dry_run,
            preflight_data_version=preflight_data_version,
        ) from exc
    finally:
        if conn is not None:
            conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill company name extraction for news/RSS signals"
    )
    parser.add_argument("--db", required=True, help="Path to signals.db")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Preview without modifying"
    )
    mode.add_argument(
        "--commit", action="store_true",
        help="Actually apply changes"
    )
    parser.add_argument(
        "--preflight", action="store_true",
        help="Show summary statistics only"
    )
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument(
        "--allowlist-ids",
        default="",
        help="Comma-separated signal IDs to process (e.g. 41,95,210)",
    )
    parser.add_argument(
        "--include-ner-candidates",
        action="store_true",
        help="Include NER candidates in dry-run output (never committed by policy)",
    )
    parser.add_argument(
        "--policy-path",
        default="",
        help="Optional path to company_name_policy.yaml (default: config/company_name_policy.yaml)",
    )
    parser.add_argument("--report", default="", help="Optional path to write a JSON report envelope")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    tool_name = "backfill_company_extraction"
    action = "backfill_company_extraction"
    started_at = datetime.now(timezone.utc)
    report_path = args.report or None
    preflight_data_version: int | None = None
    lock: DBToolLock | None = None

    if not os.path.exists(args.db):
        print(f"ERROR: Database not found: {args.db}")
        sys.exit(1)

    try:
        preflight_data_version = read_sqlite_data_version(args.db)
    except Exception as exc:
        report = create_report(
            command=action,
            ok=False,
            db_path=args.db,
            started_at=started_at,
            errors=[str(exc)],
        )
        if report_path:
            write_report(report, report_path)
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.preflight:
        report = preflight(args.db)
        print(json.dumps(report, indent=2))
        sys.exit(0)

    try:
        allowlist_ids = _parse_allowlist_ids(args.allowlist_ids)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(2)

    if args.commit and args.include_ner_candidates:
        append_db_ops_ledger(
            tool_name=tool_name,
            db_path=args.db,
            action=action,
            status="refused",
            details={
                "reason": "include_ner_candidates_commit",
                "preflight_data_version": preflight_data_version,
            },
        )
        print("ERROR: --include-ner-candidates is dry-run only under COMPANY_NAME_WRITE_POLICY")
        sys.exit(2)

    dry_run = not args.commit
    if args.commit:
        lock = DBToolLock(args.db, tool_name=tool_name)
        if not lock.acquire(timeout_seconds=LOCK_TIMEOUT_SECONDS):
            holder = lock.get_holder_info()
            error = f"Could not acquire DB tool lock. Holder: {holder}"
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=args.db,
                action=action,
                status="lock_blocked",
                details={
                    "holder": holder,
                    "commit": True,
                    "preflight_data_version": preflight_data_version,
                },
            )
            report = create_report(
                command=action,
                ok=False,
                db_path=args.db,
                started_at=started_at,
                metrics={
                    "holder": holder,
                    "preflight_data_version": preflight_data_version,
                },
                errors=[error],
            )
            if report_path:
                write_report(report, report_path)
            print(f"ERROR: {error}", file=sys.stderr)
            sys.exit(2)

    try:
        report = run(
            args.db,
            dry_run=dry_run,
            chunk_size=args.chunk_size,
            allowlist_ids=allowlist_ids,
            include_ner_candidates=args.include_ner_candidates,
            policy_path=args.policy_path or None,
            preflight_data_version=preflight_data_version,
        )
        envelope = create_report(
            command=action,
            ok=True,
            db_path=args.db,
            started_at=started_at,
            metrics={k: v for k, v in report.items() if k != "diffs"},
        )
        if report_path:
            write_report(envelope, report_path)
        if args.commit:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=args.db,
                action=action,
                status="success",
                details={k: v for k, v in report.items() if k != "diffs"},
            )
    except DBToolError as exc:
        details = {**exc.partial_evidence, "error": str(exc)}
        if args.commit:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=args.db,
                action=action,
                status="error",
                details=details,
            )
        envelope = create_report(
            command=action,
            ok=False,
            db_path=args.db,
            started_at=started_at,
            metrics=exc.partial_evidence,
            errors=[str(exc)],
        )
        if report_path:
            write_report(envelope, report_path)
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        details = {"error": str(exc), "preflight_data_version": preflight_data_version}
        if args.commit:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=args.db,
                action=action,
                status="error",
                details=details,
            )
        envelope = create_report(
            command=action,
            ok=False,
            db_path=args.db,
            started_at=started_at,
            metrics={"preflight_data_version": preflight_data_version},
            errors=[str(exc)],
        )
        if report_path:
            write_report(envelope, report_path)
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    finally:
        if lock is not None:
            lock.release()

    if allowlist_ids:
        ids_csv = ",".join(str(i) for i in sorted(allowlist_ids))
        print(f"\n=== ALLOWLIST MODE: {len(allowlist_ids)} IDs ===")
        print(f"IDs: {ids_csv}")
    if args.include_ner_candidates:
        print("\n=== NER CANDIDATE MODE (dry-run only) ===")

    if dry_run and report["diffs"]:
        blocked = sum(1 for d in report["diffs"] if d.get("blocked_by_policy"))
        writable = len(report["diffs"]) - blocked
        print(
            f"\n=== DRY RUN: {writable} signals would be updated "
            f"({blocked} blocked by policy) ===\n"
        )
        for d in report["diffs"]:
            policy_suffix = ""
            if d.get("blocked_by_policy"):
                policy_suffix = f", blocked={d.get('policy_reason')}"
            print(
                f"  Signal {d['id']}: {d['old_name']!r} -> {d['new_name']!r} "
                f"(method={d['method']}, key: {d['old_key']} -> {d['new_key']}{policy_suffix})"
            )

    summary = {k: v for k, v in report.items() if k != "diffs"}
    print(f"\n{json.dumps(summary, indent=2)}")
    sys.exit(0)


if __name__ == "__main__":
    main()

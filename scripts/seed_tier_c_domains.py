#!/usr/bin/env python3
"""
Seed Tier C consumer startup domains into company_files.

Reads a list of domains (one per line) and idempotently upserts them into
company_files with status='thin' and metadata.manual_seed=true.

Uses BEGIN IMMEDIATE transaction for safety.  Dry-run by default.

Usage:
    # Dry-run (report only, no writes)
    python scripts/seed_tier_c_domains.py --db signals.db --domains datasets/tier_c_domains.txt

    # Commit
    python scripts/seed_tier_c_domains.py --db signals.db --domains datasets/tier_c_domains.txt --commit

    # Commit with status override
    python scripts/seed_tier_c_domains.py --db signals.db --domains datasets/tier_c_domains.txt --commit --status thin
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, ".")

# Windows console encoding safety (cp1252/cp437)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")

from utils.canonical_keys import normalize_domain, derive_company_id
from utils.company_name_extractor import is_blocked_domain
from utils.db_ops_ledger import append_db_ops_ledger
from utils.db_tool_errors import DBToolError
from utils.db_tool_lock import DBToolLock
from utils.db_tool_preflight import read_sqlite_data_version
from utils.report_envelope import create_report, write_report

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

VALID_STATUSES = ("thin", "promoted", "archived")
TOOL_NAME = "seed_tier_c_domains"
ACTION = "seed_tier_c_domains"
LOCK_TIMEOUT_SECONDS = 5

# Infrastructure/hosting platforms — deployment targets, not companies
INFRA_DOMAIN_SUFFIXES: set[str] = {
    "vercel.app",
    "netlify.app",
    "herokuapp.com",
    "fly.dev",
    "railway.app",
    "render.com",
    "pages.dev",
    "web.app",
    "firebaseapp.com",
}

# Social/community platforms not already caught by is_blocked_domain()
SOCIAL_PLATFORM_SUFFIXES: set[str] = {
    "ycombinator.com",
    "notion.site",
    "notion.so",
}


class SeedTierCDomainsError(DBToolError):
    """Tier-C seed failure carrying partial progress evidence."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        preflight_data_version: int | None,
        commit_requested: bool,
        transaction_started: bool = False,
        raw_domain_count: int = 0,
        filtered_domain_count: int = 0,
        publisher_blocked: int = 0,
        infra_blocked: int = 0,
        social_blocked: int = 0,
        rows_inserted_attempted: int = 0,
        rows_updated_attempted: int = 0,
        rows_skipped: int = 0,
        target_domain_sample: list[str] | None = None,
    ) -> None:
        super().__init__(
            message,
            partial_evidence={
                "phase": phase,
                "preflight_data_version": preflight_data_version,
                "commit_requested": commit_requested,
                "dry_run": not commit_requested,
                "transaction_started": transaction_started,
                "raw_domain_count": raw_domain_count,
                "filtered_domain_count": filtered_domain_count,
                "publisher_blocked": publisher_blocked,
                "infra_blocked": infra_blocked,
                "social_blocked": social_blocked,
                "rows_inserted_attempted": rows_inserted_attempted,
                "rows_updated_attempted": rows_updated_attempted,
                "rows_skipped": rows_skipped,
                "target_domain_sample": list(target_domain_sample or []),
            },
        )


def _build_metrics(
    *,
    inserted: int,
    updated: int,
    skipped: int,
    raw_domain_count: int,
    filtered_domain_count: int,
    publisher_blocked: int,
    infra_blocked: int,
    social_blocked: int,
    commit_requested: bool,
    preflight_data_version: int | None,
    phase: str,
    transaction_started: bool,
    target_domains: list[str],
    rows_inserted_attempted: int | None = None,
    rows_updated_attempted: int | None = None,
) -> dict[str, Any]:
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "total": filtered_domain_count,
        "publisher_blocked": publisher_blocked,
        "infra_blocked": infra_blocked,
        "social_blocked": social_blocked,
        "preflight_data_version": preflight_data_version,
        "commit_requested": commit_requested,
        "dry_run": not commit_requested,
        "phase": phase,
        "transaction_started": transaction_started,
        "raw_domain_count": raw_domain_count,
        "filtered_domain_count": filtered_domain_count,
        "rows_inserted_attempted": (
            inserted if rows_inserted_attempted is None else rows_inserted_attempted
        ),
        "rows_updated_attempted": (
            updated if rows_updated_attempted is None else rows_updated_attempted
        ),
        "rows_skipped": skipped,
        "target_domain_sample": target_domains[:10],
    }


def _write_report_if_requested(
    *,
    report_path: str | None,
    ok: bool,
    started_at: datetime,
    db_path: str,
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
        metrics=metrics or {},
        errors=errors or [],
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


def _is_infra_domain(domain: str) -> bool:
    """Check if domain is an infrastructure/hosting platform (suffix-aware)."""
    dl = domain.lower()
    for suffix in INFRA_DOMAIN_SUFFIXES:
        if dl == suffix or dl.endswith("." + suffix):
            return True
    return False


def _is_social_platform(domain: str) -> bool:
    """Check if domain is a social/community platform (suffix-aware)."""
    dl = domain.lower()
    for suffix in SOCIAL_PLATFORM_SUFFIXES:
        if dl == suffix or dl.endswith("." + suffix):
            return True
    return False


def _is_filtered(domain: str) -> bool:
    """Apply all three filter layers to a single domain."""
    return is_blocked_domain(domain) or _is_infra_domain(domain) or _is_social_platform(domain)


def _load_domains(path: str) -> list[str]:
    """Load domains from a text file (one per line), skip blanks and comments."""
    domains: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            normalized = normalize_domain(line)
            if normalized:
                domains.append(normalized)
            else:
                truncated = line[:80] + ("..." if len(line) > 80 else "")
                logger.warning(
                    "Skipping invalid domain at line %d: %s", lineno, truncated
                )
    return list(dict.fromkeys(domains))  # dedupe, preserve order


def _merge_metadata(existing_json: str | None, new_meta: dict) -> str:
    """Merge new metadata keys into existing JSON metadata."""
    try:
        existing = json.loads(existing_json or "{}")
    except (json.JSONDecodeError, TypeError):
        existing = {}
    existing.update(new_meta)
    return json.dumps(existing)


def seed_tier_c(
    db_path: str,
    domains_path: str,
    commit: bool = False,
    status: str = "thin",
    allow_blocked: bool = False,
    preflight_data_version: int | None = None,
) -> dict:
    """Upsert Tier C domains into company_files.

    Args:
        db_path: Path to SQLite database.
        domains_path: Path to text file with one domain per line.
        commit: If False, dry-run only (report counts, no writes).
        status: company_files status for new rows (default: thin).
        allow_blocked: If True, skip domain filtering (allow all domains).

    Returns:
        Dict with counts: inserted, updated, skipped, total,
        publisher_blocked, infra_blocked, social_blocked.
    """
    if status not in VALID_STATUSES:
        raise SeedTierCDomainsError(
            f"Invalid status '{status}', must be one of {VALID_STATUSES}",
            phase="validate_status",
            preflight_data_version=preflight_data_version,
            commit_requested=commit,
        )

    try:
        if preflight_data_version is None:
            preflight_data_version = read_sqlite_data_version(db_path)
    except Exception as exc:
        raise SeedTierCDomainsError(
            f"Tier C seeding failed during preflight_data_version: {exc}",
            phase="preflight_data_version",
            preflight_data_version=None,
            commit_requested=commit,
        ) from exc

    try:
        raw_domains = _load_domains(domains_path)
    except Exception as exc:
        raise SeedTierCDomainsError(
            f"Tier C seeding failed during load_domains: {exc}",
            phase="load_domains",
            preflight_data_version=preflight_data_version,
            commit_requested=commit,
        ) from exc
    if not raw_domains:
        print("No valid domains found in input file.")
        return _build_metrics(
            inserted=0,
            updated=0,
            skipped=0,
            raw_domain_count=0,
            filtered_domain_count=0,
            publisher_blocked=0,
            infra_blocked=0,
            social_blocked=0,
            commit_requested=commit,
            preflight_data_version=preflight_data_version,
            phase="load_domains",
            transaction_started=False,
            target_domains=[],
            rows_inserted_attempted=0,
            rows_updated_attempted=0,
        )

    # Three-layer domain filtering (unless --allow-blocked)
    publisher_blocked = 0
    infra_blocked = 0
    social_blocked = 0
    domains: list[str] = []

    for domain in raw_domains:
        if not allow_blocked:
            if is_blocked_domain(domain):
                publisher_blocked += 1
                continue
            if _is_infra_domain(domain):
                infra_blocked += 1
                continue
            if _is_social_platform(domain):
                social_blocked += 1
                continue
        domains.append(domain)

    total_filtered = publisher_blocked + infra_blocked + social_blocked
    if total_filtered > 0:
        print(
            f"\nFiltering: {len(raw_domains)} total -> {publisher_blocked} publisher-blocked"
            f" -> {infra_blocked} infra-blocked -> {social_blocked} social-blocked"
            f" -> {len(domains)} final"
        )

    if not domains:
        print("No domains remaining after filtering.")
        return _build_metrics(
            inserted=0,
            updated=0,
            skipped=0,
            raw_domain_count=len(raw_domains),
            filtered_domain_count=0,
            publisher_blocked=publisher_blocked,
            infra_blocked=infra_blocked,
            social_blocked=social_blocked,
            commit_requested=commit,
            preflight_data_version=preflight_data_version,
            phase="filter_domains",
            transaction_started=False,
            target_domains=[],
            rows_inserted_attempted=0,
            rows_updated_attempted=0,
        )

    now = datetime.now(timezone.utc).isoformat()
    new_meta = {"manual_seed": True, "seed_source": "tier_c", "seeded_at": now}

    inserted = 0
    updated = 0
    skipped = 0
    rows_inserted_attempted = 0
    rows_updated_attempted = 0
    phase = "connect"
    transaction_started = False
    conn: sqlite3.Connection | None = None

    try:
        conn = sqlite3.connect(db_path)

        if not commit:
            # Dry-run: check which domains already exist without taking the write lock.
            phase = "dry_run_select"
            for domain in domains:
                canonical_key = f"domain:{domain}"
                row = conn.execute(
                    "SELECT company_id, metadata FROM company_files WHERE canonical_key = ?",
                    (canonical_key,),
                ).fetchone()
                if row is None:
                    inserted += 1
                else:
                    existing_meta = row[1]
                    try:
                        meta = json.loads(existing_meta or "{}")
                    except (json.JSONDecodeError, TypeError):
                        meta = {}
                    if meta.get("manual_seed"):
                        skipped += 1
                    else:
                        updated += 1

            print(f"\n[DRY-RUN] Tier C seeding report:")
            print(f"  Input domains:  {len(raw_domains)}")
            if total_filtered:
                print(f"  Filtered out:   {total_filtered} (publisher={publisher_blocked}, infra={infra_blocked}, social={social_blocked})")
            print(f"  After filtering: {len(domains)}")
            print(f"  Would insert:   {inserted}")
            print(f"  Would update:   {updated} (add manual_seed metadata)")
            print(f"  Already seeded: {skipped}")
            print(f"  Preflight data_version: {preflight_data_version}")
            return _build_metrics(
                inserted=inserted,
                updated=updated,
                skipped=skipped,
                raw_domain_count=len(raw_domains),
                filtered_domain_count=len(domains),
                publisher_blocked=publisher_blocked,
                infra_blocked=infra_blocked,
                social_blocked=social_blocked,
                commit_requested=False,
                preflight_data_version=preflight_data_version,
                phase="dry_run",
                transaction_started=False,
                target_domains=domains,
                rows_inserted_attempted=0,
                rows_updated_attempted=0,
            )

        # Commit mode: BEGIN IMMEDIATE for write safety.
        phase = "begin_transaction"
        conn.execute("BEGIN IMMEDIATE")
        transaction_started = True

        for domain in domains:
            canonical_key = f"domain:{domain}"
            company_id = derive_company_id(canonical_key)

            phase = "select_existing"
            row = conn.execute(
                "SELECT company_id, metadata, source_apis FROM company_files WHERE canonical_key = ?",
                (canonical_key,),
            ).fetchone()

            if row is None:
                # Insert new row
                merged_meta = json.dumps(new_meta)
                source_apis = json.dumps(["manual_seed"])
                phase = "insert_row"
                rows_inserted_attempted += 1
                conn.execute(
                    """INSERT INTO company_files
                       (company_id, company_name, canonical_key, status,
                        source_apis, first_seen_at, last_seen_at, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (company_id, domain, canonical_key, status,
                     source_apis, now, now, merged_meta),
                )
                inserted += 1
            else:
                existing_id, existing_meta_str, existing_apis_str = row
                try:
                    meta = json.loads(existing_meta_str or "{}")
                except (json.JSONDecodeError, TypeError):
                    meta = {}

                if meta.get("manual_seed"):
                    # Already seeded, just touch last_seen_at
                    phase = "touch_seeded_row"
                    rows_updated_attempted += 1
                    conn.execute(
                        "UPDATE company_files SET last_seen_at = ? WHERE canonical_key = ?",
                        (now, canonical_key),
                    )
                    skipped += 1
                else:
                    # Existing row without manual_seed: merge metadata + add source
                    merged_meta = _merge_metadata(existing_meta_str, new_meta)

                    # Add manual_seed to source_apis if not present
                    try:
                        apis = json.loads(existing_apis_str or "[]")
                    except (json.JSONDecodeError, TypeError):
                        apis = []
                    if "manual_seed" not in apis:
                        apis.append("manual_seed")

                    phase = "update_existing_row"
                    rows_updated_attempted += 1
                    conn.execute(
                        """UPDATE company_files
                           SET metadata = ?, source_apis = ?, last_seen_at = ?
                           WHERE canonical_key = ?""",
                        (merged_meta, json.dumps(apis), now, canonical_key),
                    )
                    updated += 1

        phase = "commit_transaction"
        conn.execute("COMMIT")
        transaction_started = False
    except Exception as exc:
        if transaction_started and conn is not None:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                logger.exception("Failed to roll back Tier C seeding transaction")
        raise SeedTierCDomainsError(
            f"Tier C seeding failed during {phase}: {exc}",
            phase=phase,
            preflight_data_version=preflight_data_version,
            commit_requested=commit,
            transaction_started=transaction_started,
            raw_domain_count=len(raw_domains),
            filtered_domain_count=len(domains),
            publisher_blocked=publisher_blocked,
            infra_blocked=infra_blocked,
            social_blocked=social_blocked,
            rows_inserted_attempted=rows_inserted_attempted,
            rows_updated_attempted=rows_updated_attempted,
            rows_skipped=skipped,
            target_domain_sample=domains[:10],
        ) from exc
    finally:
        if conn is not None:
            conn.close()

    print(f"\n[COMMITTED] Tier C seeding results:")
    print(f"  Input domains:  {len(raw_domains)}")
    if total_filtered:
        print(f"  Filtered out:   {total_filtered} (publisher={publisher_blocked}, infra={infra_blocked}, social={social_blocked})")
    print(f"  After filtering: {len(domains)}")
    print(f"  Inserted:       {inserted}")
    print(f"  Updated:        {updated} (added manual_seed metadata)")
    print(f"  Already seeded: {skipped}")
    print(f"  Preflight data_version: {preflight_data_version}")
    return _build_metrics(
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        raw_domain_count=len(raw_domains),
        filtered_domain_count=len(domains),
        publisher_blocked=publisher_blocked,
        infra_blocked=infra_blocked,
        social_blocked=social_blocked,
        commit_requested=True,
        preflight_data_version=preflight_data_version,
        phase="committed",
        transaction_started=False,
        target_domains=domains,
        rows_inserted_attempted=rows_inserted_attempted,
        rows_updated_attempted=rows_updated_attempted,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Tier C consumer startup domains into company_files")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--domains", required=True, help="Path to domain list file (one per line)")
    parser.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Actually write to DB (default: dry-run)",
    )
    parser.add_argument(
        "--status",
        choices=["thin", "promoted", "archived"],
        default="thin",
        help="Status for new company_files rows (default: thin)",
    )
    parser.add_argument(
        "--allow-blocked",
        action="store_true",
        default=False,
        help="Bypass domain filtering (allow publisher, infra, and social domains)",
    )
    parser.add_argument("--report", default="", help="Optional path to write a JSON report envelope")
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    report_path = args.report or None
    preflight_data_version: int | None = None
    lock: DBToolLock | None = None

    try:
        preflight_data_version = read_sqlite_data_version(args.db)
    except Exception as exc:
        details = {
            "phase": "preflight_data_version",
            "commit_requested": args.commit,
            "dry_run": not args.commit,
            "preflight_data_version": None,
            "error": str(exc),
        }
        if args.commit:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            ok=False,
            started_at=started_at,
            db_path=args.db,
            metrics=details,
            errors=[str(exc)],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.commit:
        lock = DBToolLock(args.db, tool_name=TOOL_NAME)
        if not lock.acquire(timeout_seconds=LOCK_TIMEOUT_SECONDS):
            holder = lock.get_holder_info()
            error = f"Could not acquire DB tool lock. Holder: {holder}"
            details = {
                "holder": holder,
                "commit_requested": True,
                "dry_run": False,
                "preflight_data_version": preflight_data_version,
            }
            _append_ledger(db_path=args.db, status="lock_blocked", details=details)
            _write_report_if_requested(
                report_path=report_path,
                ok=False,
                started_at=started_at,
                db_path=args.db,
                metrics=details,
                errors=[error],
            )
            print(f"ERROR: {error}", file=sys.stderr)
            return 2

    try:
        metrics = seed_tier_c(
            args.db,
            args.domains,
            commit=args.commit,
            status=args.status,
            allow_blocked=args.allow_blocked,
            preflight_data_version=preflight_data_version,
        )
        _write_report_if_requested(
            report_path=report_path,
            ok=True,
            started_at=started_at,
            db_path=args.db,
            metrics=metrics,
        )
        if args.commit:
            _append_ledger(db_path=args.db, status="success", details=metrics)
        return 0
    except DBToolError as exc:
        details = {**exc.partial_evidence, "error": str(exc)}
        if args.commit:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            ok=False,
            started_at=started_at,
            db_path=args.db,
            metrics=exc.partial_evidence,
            errors=[str(exc)],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        details = {
            "phase": "cli",
            "commit_requested": args.commit,
            "dry_run": not args.commit,
            "preflight_data_version": preflight_data_version,
            "error": str(exc),
        }
        if args.commit:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            ok=False,
            started_at=started_at,
            db_path=args.db,
            metrics=details,
            errors=[str(exc)],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(main())

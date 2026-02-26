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

sys.path.insert(0, ".")

# Windows console encoding safety (cp1252/cp437)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")

from utils.canonical_keys import normalize_domain, derive_company_id
from utils.company_name_extractor import is_blocked_domain

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

VALID_STATUSES = ("thin", "promoted", "archived")

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
        raise ValueError(f"Invalid status '{status}', must be one of {VALID_STATUSES}")

    raw_domains = _load_domains(domains_path)
    if not raw_domains:
        print("No valid domains found in input file.")
        return {
            "inserted": 0, "updated": 0, "skipped": 0, "total": 0,
            "publisher_blocked": 0, "infra_blocked": 0, "social_blocked": 0,
        }

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
        return {
            "inserted": 0, "updated": 0, "skipped": 0, "total": 0,
            "publisher_blocked": publisher_blocked, "infra_blocked": infra_blocked,
            "social_blocked": social_blocked,
        }

    now = datetime.now(timezone.utc).isoformat()
    new_meta = {"manual_seed": True, "seed_source": "tier_c", "seeded_at": now}

    inserted = 0
    updated = 0
    skipped = 0

    conn = sqlite3.connect(db_path)

    if not commit:
        # Dry-run: just check which domains exist
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
        conn.close()

        print(f"\n[DRY-RUN] Tier C seeding report:")
        print(f"  Input domains:  {len(raw_domains)}")
        if total_filtered:
            print(f"  Filtered out:   {total_filtered} (publisher={publisher_blocked}, infra={infra_blocked}, social={social_blocked})")
        print(f"  After filtering: {len(domains)}")
        print(f"  Would insert:   {inserted}")
        print(f"  Would update:   {updated} (add manual_seed metadata)")
        print(f"  Already seeded: {skipped}")
        return {
            "inserted": inserted, "updated": updated, "skipped": skipped, "total": len(domains),
            "publisher_blocked": publisher_blocked, "infra_blocked": infra_blocked,
            "social_blocked": social_blocked,
        }

    # Commit mode: BEGIN IMMEDIATE for write safety
    conn.execute("BEGIN IMMEDIATE")
    try:
        for domain in domains:
            canonical_key = f"domain:{domain}"
            company_id = derive_company_id(canonical_key)

            row = conn.execute(
                "SELECT company_id, metadata, source_apis FROM company_files WHERE canonical_key = ?",
                (canonical_key,),
            ).fetchone()

            if row is None:
                # Insert new row
                merged_meta = json.dumps(new_meta)
                source_apis = json.dumps(["manual_seed"])
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

                    conn.execute(
                        """UPDATE company_files
                           SET metadata = ?, source_apis = ?, last_seen_at = ?
                           WHERE canonical_key = ?""",
                        (merged_meta, json.dumps(apis), now, canonical_key),
                    )
                    updated += 1

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.close()
        raise
    conn.close()

    print(f"\n[COMMITTED] Tier C seeding results:")
    print(f"  Input domains:  {len(raw_domains)}")
    if total_filtered:
        print(f"  Filtered out:   {total_filtered} (publisher={publisher_blocked}, infra={infra_blocked}, social={social_blocked})")
    print(f"  After filtering: {len(domains)}")
    print(f"  Inserted:       {inserted}")
    print(f"  Updated:        {updated} (added manual_seed metadata)")
    print(f"  Already seeded: {skipped}")
    return {
        "inserted": inserted, "updated": updated, "skipped": skipped, "total": len(domains),
        "publisher_blocked": publisher_blocked, "infra_blocked": infra_blocked,
        "social_blocked": social_blocked,
    }


def main():
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
    args = parser.parse_args()

    seed_tier_c(
        args.db, args.domains, commit=args.commit, status=args.status,
        allow_blocked=args.allow_blocked,
    )


if __name__ == "__main__":
    main()

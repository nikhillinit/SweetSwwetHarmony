#!/usr/bin/env python3
"""
Cleanup publisher domain keys leaked into canonical_key columns.

Suffix-aware matching: catches domain:m.reuters.com, domain:www.reuters.com, etc.
Multi-table, single transaction: rewrites canonical_key in signals and company_files.

Usage:
    # Dry-run (default): show what would change
    python scripts/cleanup_publisher_keys.py --db signals.db

    # Apply changes (double-confirmation required)
    python scripts/cleanup_publisher_keys.py --db signals.db --apply --yes-rewrite-keys

    # Idempotent: second run says "0 changes needed"
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from urllib.parse import urlparse

sys.path.insert(0, ".")

from utils.company_name_extractor import is_blocked_domain
from utils.canonical_keys import normalize_domain, NEWS_PUBLISHER_DOMAINS

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _slug(s: str) -> str:
    """Lowercase, keep [a-z0-9], collapse separators to '-'."""
    import re
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _extract_host_from_domain_key(canonical_key: str) -> str | None:
    """Extract the host part from a 'domain:xxx' canonical key."""
    if not canonical_key.startswith("domain:"):
        return None
    return canonical_key[len("domain:"):]


def _is_publisher_domain_key(canonical_key: str) -> bool:
    """Check if a domain: key refers to a publisher (suffix-aware)."""
    host = _extract_host_from_domain_key(canonical_key)
    if not host:
        return False
    return is_blocked_domain(host)


def _compute_new_key(raw_data_json: str | None, canonical_key: str) -> str | None:
    """Compute a replacement canonical key from raw_data company_name.

    Returns new key or None if no company_name available.
    """
    if not raw_data_json:
        return None
    try:
        raw = json.loads(raw_data_json)
    except (json.JSONDecodeError, TypeError):
        return None

    company_name = raw.get("company_name", "")
    if not company_name or not company_name.strip():
        return None

    slug = _slug(company_name)
    if not slug:
        return None

    return f"name_loc:{slug}"


def run_cleanup(db_path: str, apply: bool = False) -> dict:
    """Run publisher key cleanup.

    Returns summary dict with per-table change counts.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Phase 1: Find all affected signals
    rows = conn.execute(
        "SELECT id, canonical_key, raw_data FROM signals WHERE canonical_key LIKE 'domain:%'"
    ).fetchall()

    changes = []  # (signal_id, old_key, new_key)
    manual_review = []  # (signal_id, old_key, reason)

    for row in rows:
        old_key = row["canonical_key"]
        if not _is_publisher_domain_key(old_key):
            continue

        new_key = _compute_new_key(row["raw_data"], old_key)
        if not new_key:
            manual_review.append((row["id"], old_key, "no company_name in raw_data"))
            continue

        changes.append((row["id"], old_key, new_key))

    # Collect unique old_key → new_key mappings for company_files
    key_mapping: dict[str, str] = {}
    for _sid, old_key, new_key in changes:
        if old_key not in key_mapping:
            key_mapping[old_key] = new_key

    # Find affected company_files
    cf_changes = []
    if key_mapping:
        placeholders = ",".join("?" * len(key_mapping))
        cf_rows = conn.execute(
            f"SELECT id, canonical_key FROM company_files WHERE canonical_key IN ({placeholders})",
            list(key_mapping.keys()),
        ).fetchall()
        for cf in cf_rows:
            new_key = key_mapping.get(cf["canonical_key"])
            if new_key:
                cf_changes.append((cf["id"], cf["canonical_key"], new_key))

    # Check for collisions (new key already exists in signals or company_files)
    collision_count = 0
    if key_mapping:
        new_keys = list(set(key_mapping.values()))
        placeholders = ",".join("?" * len(new_keys))
        existing_signal_keys = {
            r[0] for r in conn.execute(
                f"SELECT DISTINCT canonical_key FROM signals WHERE canonical_key IN ({placeholders})",
                new_keys,
            ).fetchall()
        }
        existing_cf_keys = {
            r[0] for r in conn.execute(
                f"SELECT DISTINCT canonical_key FROM company_files WHERE canonical_key IN ({placeholders})",
                new_keys,
            ).fetchall()
        }
        collisions = existing_signal_keys | existing_cf_keys
        # Filter out changes that would collide
        filtered_changes = []
        for sid, old_key, new_key in changes:
            if new_key in collisions:
                collision_count += 1
                manual_review.append((sid, old_key, f"collision with existing {new_key}"))
            else:
                filtered_changes.append((sid, old_key, new_key))
        changes = filtered_changes

        # Also filter cf_changes
        filtered_cf = []
        for cfid, old_key, new_key in cf_changes:
            if new_key not in collisions:
                filtered_cf.append((cfid, old_key, new_key))
        cf_changes = filtered_cf

    summary = {
        "signals_to_rewrite": len(changes),
        "company_files_to_rewrite": len(cf_changes),
        "collisions": collision_count,
        "manual_review": len(manual_review),
    }

    # Report
    print(f"\n=== Publisher Key Cleanup {'(DRY RUN)' if not apply else '(APPLYING)'} ===")
    print(f"DB: {db_path}")
    print(f"Signals to rewrite:      {summary['signals_to_rewrite']}")
    print(f"Company files to rewrite: {summary['company_files_to_rewrite']}")
    print(f"Collisions (skipped):    {summary['collisions']}")
    print(f"Manual review needed:    {summary['manual_review']}")

    if changes:
        print("\nSample changes (first 5):")
        for _sid, old_key, new_key in changes[:5]:
            print(f"  {old_key} → {new_key}")

    if manual_review:
        print("\nManual review items (first 5):")
        for sid, old_key, reason in manual_review[:5]:
            print(f"  signal {sid}: {old_key} — {reason}")

    if not changes and not cf_changes:
        print("\n0 changes needed.")
        conn.close()
        return summary

    if not apply:
        print("\nUse --apply --yes-rewrite-keys to apply changes.")
        conn.close()
        return summary

    # Apply in single transaction
    print("\nApplying changes...")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for sid, old_key, new_key in changes:
            conn.execute(
                "UPDATE signals SET canonical_key = ? WHERE id = ? AND canonical_key = ?",
                (new_key, sid, old_key),
            )

        for cfid, old_key, new_key in cf_changes:
            conn.execute(
                "UPDATE company_files SET canonical_key = ? WHERE id = ? AND canonical_key = ?",
                (new_key, cfid, old_key),
            )

        conn.execute("COMMIT")
        print(f"Done. Rewrote {len(changes)} signals + {len(cf_changes)} company_files.")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    return summary


def main():
    parser = argparse.ArgumentParser(description="Cleanup publisher domain keys")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    parser.add_argument(
        "--yes-rewrite-keys", action="store_true",
        help="Double-confirmation for write operations",
    )
    args = parser.parse_args()

    if args.apply and not args.yes_rewrite_keys:
        print("ERROR: --apply requires --yes-rewrite-keys for confirmation")
        sys.exit(1)

    run_cleanup(args.db, apply=args.apply)


if __name__ == "__main__":
    main()

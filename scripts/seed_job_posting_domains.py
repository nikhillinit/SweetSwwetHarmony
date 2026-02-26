#!/usr/bin/env python3
"""
Seed JOB_POSTING_DOMAINS from signal frequency or company_files.

Queries domain: canonical keys, groups by normalized domain, and outputs
the top N domains after three-layer filtering.

Sources:
  signals       - mine HN signals for domain frequency (original behavior)
  company_files - pull canonical_key domains from company_files table

Usage:
    # Default: top 30 from HN signals in last 90 days
    python scripts/seed_job_posting_domains.py --db signals.db

    # From company_files with manual-seed filter
    python scripts/seed_job_posting_domains.py --db signals.db --source company_files --seed-filter

    # Custom: top 50 from last 180 days
    python scripts/seed_job_posting_domains.py --db signals.db --top 50 --days 180

    # Output formats
    python scripts/seed_job_posting_domains.py --db signals.db --format env   # JOB_POSTING_DOMAINS=...
    python scripts/seed_job_posting_domains.py --db signals.db --format list  # One per line
    python scripts/seed_job_posting_domains.py --db signals.db --format csv   # CSV
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

# Windows console encoding safety (cp1252/cp437)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")

from utils.company_name_extractor import is_blocked_domain
from utils.canonical_keys import normalize_domain

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ============================================================================
# Domain filter lists (suffix-aware)
# ============================================================================

# Infrastructure/hosting platforms -- these are deployment targets, not companies
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

# Social/community platforms frequently seen in HN domains
# NOT already caught by is_blocked_domain()
SOCIAL_PLATFORM_SUFFIXES: set[str] = {
    "ycombinator.com",
    "notion.site",
    "notion.so",
}

# Backward-compat aliases (keep for one release)
INFRA_DENYLIST: set[str] = set(INFRA_DOMAIN_SUFFIXES)
SOCIAL_PLATFORM_DENYLIST: set[str] = set(SOCIAL_PLATFORM_SUFFIXES)


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


# ============================================================================
# Source: signals (original behavior)
# ============================================================================

def _query_signal_domains(
    db_path: str,
    days: int,
) -> dict[str, int]:
    """Query HN signals and return domain -> count mapping."""
    conn = sqlite3.connect(db_path)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT canonical_key FROM signals
        WHERE source_api = 'hacker_news'
          AND canonical_key LIKE 'domain:%'
          AND created_at >= ?
        """,
        (cutoff,),
    ).fetchall()
    conn.close()

    domain_counts: dict[str, int] = {}
    for (key,) in rows:
        raw_domain = key[len("domain:"):]
        normalized = normalize_domain(raw_domain) or raw_domain.lower()
        domain_counts[normalized] = domain_counts.get(normalized, 0) + 1

    return domain_counts


# ============================================================================
# Source: company_files
# ============================================================================

def _query_company_file_domains(
    db_path: str,
    seed_filter: bool = False,
) -> dict[str, int]:
    """Query company_files for domain: canonical keys.

    If seed_filter is True, only returns rows where metadata contains
    manual_seed=true.

    Returns domain -> 1 mapping (no frequency ranking for company_files).
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT canonical_key, metadata FROM company_files WHERE canonical_key LIKE 'domain:%'"
        ).fetchall()
    except sqlite3.OperationalError:
        logger.warning("company_files table not found")
        conn.close()
        return {}
    conn.close()

    domain_counts: dict[str, int] = {}
    for key, metadata_str in rows:
        if seed_filter:
            try:
                meta = json.loads(metadata_str or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            if not meta.get("manual_seed"):
                continue

        raw_domain = key[len("domain:"):]
        normalized = normalize_domain(raw_domain) or raw_domain.lower()
        domain_counts[normalized] = domain_counts.get(normalized, 0) + 1

    return domain_counts


# ============================================================================
# Core logic
# ============================================================================

def seed_domains(
    db_path: str,
    days: int = 90,
    top: int = 30,
    output_format: str = "env",
    source: str = "signals",
    seed_filter: bool = False,
) -> list[str]:
    """Query domains and return filtered, ranked list.

    Args:
        db_path: Path to SQLite database.
        days: Lookback window in days (only used for source=signals).
        top: Maximum number of domains to return.
        output_format: One of env, list, csv.
        source: One of signals, company_files.
        seed_filter: If True and source=company_files, only return manual seeds.

    Returns list of domain strings, highest frequency first.
    """
    if source == "company_files":
        domain_counts = _query_company_file_domains(db_path, seed_filter=seed_filter)
    else:
        domain_counts = _query_signal_domains(db_path, days=days)

    if not domain_counts:
        print("No domain: keys found for the selected source.")
        return []

    # Sort by frequency descending, alphabetical tiebreak
    sorted_domains = sorted(domain_counts.items(), key=lambda x: (-x[1], x[0]))

    # Three-layer filtering with breakdown
    total = len(sorted_domains)
    publisher_blocked = 0
    infra_blocked = 0
    social_blocked = 0
    final = []

    for domain, count in sorted_domains:
        if is_blocked_domain(domain):
            publisher_blocked += 1
            continue
        if _is_infra_domain(domain):
            infra_blocked += 1
            continue
        if _is_social_platform(domain):
            social_blocked += 1
            continue
        final.append((domain, count))

    # Trim to --top N
    final = final[:top]

    # Report filtering breakdown (ASCII-safe for Windows cp1252)
    print(
        f"\nFiltering: {total} total -> {publisher_blocked} publisher/suffix-blocked"
        f" -> {infra_blocked} infra-blocked -> {social_blocked} social-blocked"
        f" -> {len(final)} final"
    )

    domains = [d for d, _c in final]

    if not domains:
        print("No candidates found after filtering.")
        return []

    # Output
    if output_format == "env":
        print(f"\nJOB_POSTING_DOMAINS={','.join(domains)}")
    elif output_format == "list":
        print("\nDomains (one per line):")
        for d in domains:
            print(f"  {d}")
    elif output_format == "csv":
        print("\ndomain,frequency")
        for d, c in final:
            print(f"{d},{c}")

    return domains


def main():
    parser = argparse.ArgumentParser(description="Seed JOB_POSTING_DOMAINS from signals or company_files")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--days", type=int, default=90, help="Lookback window in days (default: 90)")
    parser.add_argument("--top", type=int, default=30, help="Max domains to return (default: 30)")
    parser.add_argument(
        "--format",
        choices=["env", "list", "csv"],
        default="env",
        help="Output format (default: env)",
    )
    parser.add_argument(
        "--source",
        choices=["signals", "company_files"],
        default="signals",
        help="Domain source: signals (HN frequency) or company_files (default: signals)",
    )
    parser.add_argument(
        "--seed-filter",
        action="store_true",
        default=False,
        help="When --source company_files, only return manual_seed=true rows",
    )
    args = parser.parse_args()

    seed_domains(
        args.db,
        days=args.days,
        top=args.top,
        output_format=args.format,
        source=args.source,
        seed_filter=args.seed_filter,
    )


if __name__ == "__main__":
    main()

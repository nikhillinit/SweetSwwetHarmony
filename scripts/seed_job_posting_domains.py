#!/usr/bin/env python3
"""
Seed JOB_POSTING_DOMAINS from HN signal frequency.

Queries domain: canonical keys from hacker_news signals, groups by normalized
domain, and outputs the top N domains after three-layer filtering.

Usage:
    # Default: top 30 from last 90 days
    python scripts/seed_job_posting_domains.py --db signals.db

    # Custom: top 50 from last 180 days
    python scripts/seed_job_posting_domains.py --db signals.db --top 50 --days 180

    # Output formats
    python scripts/seed_job_posting_domains.py --db signals.db --format env   # JOB_POSTING_DOMAINS=...
    python scripts/seed_job_posting_domains.py --db signals.db --format list  # One per line
    python scripts/seed_job_posting_domains.py --db signals.db --format csv   # CSV
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from utils.company_name_extractor import is_blocked_domain
from utils.canonical_keys import normalize_domain

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Infrastructure/hosting platforms — these are deployment targets, not companies
INFRA_DENYLIST: set[str] = {
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
SOCIAL_PLATFORM_DENYLIST: set[str] = {
    "ycombinator.com",
    "notion.site",
    "notion.so",
}


def _is_infra_domain(domain: str) -> bool:
    """Check if domain is an infrastructure/hosting platform (suffix-aware)."""
    dl = domain.lower()
    for suffix in INFRA_DENYLIST:
        if dl == suffix or dl.endswith("." + suffix):
            return True
    return False


def _is_social_platform(domain: str) -> bool:
    """Check if domain is a social/community platform (suffix-aware)."""
    dl = domain.lower()
    for suffix in SOCIAL_PLATFORM_DENYLIST:
        if dl == suffix or dl.endswith("." + suffix):
            return True
    return False


def seed_domains(
    db_path: str,
    days: int = 90,
    top: int = 30,
    output_format: str = "env",
) -> list[str]:
    """Query HN signals and return filtered, ranked domains.

    Returns list of domain strings, highest frequency first.
    """
    conn = sqlite3.connect(db_path)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Get all domain: keys from hacker_news within time window
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

    if not rows:
        print("No HN domain: signals found in the time window.")
        return []

    # Extract domains and normalize before grouping
    domain_counts: dict[str, int] = {}
    for (key,) in rows:
        raw_domain = key[len("domain:"):]
        normalized = normalize_domain(raw_domain) or raw_domain.lower()
        domain_counts[normalized] = domain_counts.get(normalized, 0) + 1

    # Sort by frequency descending
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

    # Report filtering breakdown
    print(
        f"\nFiltering: {total} total → {publisher_blocked} publisher/suffix-blocked"
        f" → {infra_blocked} infra-blocked → {social_blocked} social-blocked"
        f" → {len(final)} final"
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
    parser = argparse.ArgumentParser(description="Seed JOB_POSTING_DOMAINS from HN signals")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--days", type=int, default=90, help="Lookback window in days (default: 90)")
    parser.add_argument("--top", type=int, default=30, help="Max domains to return (default: 30)")
    parser.add_argument(
        "--format",
        choices=["env", "list", "csv"],
        default="env",
        help="Output format (default: env)",
    )
    args = parser.parse_args()

    seed_domains(args.db, args.days, args.top, args.format)


if __name__ == "__main__":
    main()

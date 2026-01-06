#!/usr/bin/env python3
"""
Discovery Engine - Collector CLI

Run signal collectors from command line.

Usage:
    python run_collector.py sec_edgar --dry-run
    python run_collector.py github --max-repos 50
    python run_collector.py sec_edgar --lookback 60 --max 100
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime

# Setup path
sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def run_sec_edgar(args):
    """Run SEC EDGAR Form D collector."""
    from collectors.sec_edgar import SECEdgarCollector

    print(f"\n{'='*60}")
    print("SEC EDGAR Form D Collector")
    print(f"{'='*60}")
    print(f"Lookback: {args.lookback} days")
    print(f"Max filings: {args.max}")
    print(f"Target sectors only: {not args.all_sectors}")
    print(f"Dry run: {args.dry_run}")
    print(f"{'='*60}\n")

    collector = SECEdgarCollector(
        lookback_days=args.lookback,
        max_filings=args.max,
        target_sectors_only=not args.all_sectors,
    )

    result = await collector.run(dry_run=args.dry_run)

    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"New signals: {result.signals_new}")
    print(f"Suppressed: {result.signals_suppressed}")
    print(f"Timestamp: {result.timestamp}")

    if result.error_message:
        print(f"Error: {result.error_message}")

    if args.json:
        print(f"\n{json.dumps(result.to_dict(), indent=2)}")

    return result


async def run_github(args):
    """Run GitHub trending collector."""
    import os
    from collectors.github import GitHubCollector

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN environment variable required")
        print("Get one at: https://github.com/settings/tokens")
        sys.exit(1)

    print(f"\n{'='*60}")
    print("GitHub Trending Collector")
    print(f"{'='*60}")
    print(f"Lookback: {args.lookback} days")
    print(f"Max repos: {args.max}")
    print(f"Dry run: {args.dry_run}")
    print(f"{'='*60}\n")

    collector = GitHubCollector(
        lookback_days=args.lookback,
        max_repos=args.max,
        github_token=token,
    )

    result = await collector.run(dry_run=args.dry_run)

    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"New signals: {result.signals_new}")
    print(f"Suppressed: {result.signals_suppressed}")
    print(f"Timestamp: {result.timestamp}")

    if result.error_message:
        print(f"Error: {result.error_message}")

    if args.json:
        print(f"\n{json.dumps(result.to_dict(), indent=2)}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Discovery Engine Collector CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_collector.py sec_edgar --dry-run
  python run_collector.py sec_edgar --lookback 60 --max 100
  python run_collector.py github --max 50
  python run_collector.py sec_edgar --all-sectors --json
        """,
    )

    subparsers = parser.add_subparsers(dest="collector", help="Collector to run")

    # SEC EDGAR subcommand
    sec_parser = subparsers.add_parser("sec_edgar", help="SEC EDGAR Form D collector")
    sec_parser.add_argument("--lookback", type=int, default=30, help="Days to look back (default: 30)")
    sec_parser.add_argument("--max", type=int, default=50, help="Max filings to process (default: 50)")
    sec_parser.add_argument("--all-sectors", action="store_true", help="Include all sectors, not just target")
    sec_parser.add_argument("--dry-run", action="store_true", default=True, help="Don't persist results (default)")
    sec_parser.add_argument("--live", action="store_true", help="Persist results (opposite of --dry-run)")
    sec_parser.add_argument("--json", action="store_true", help="Output full JSON result")

    # GitHub subcommand
    gh_parser = subparsers.add_parser("github", help="GitHub trending collector")
    gh_parser.add_argument("--lookback", type=int, default=30, help="Days to look back (default: 30)")
    gh_parser.add_argument("--max", type=int, default=50, help="Max repos to process (default: 50)")
    gh_parser.add_argument("--dry-run", action="store_true", default=True, help="Don't persist results (default)")
    gh_parser.add_argument("--live", action="store_true", help="Persist results (opposite of --dry-run)")
    gh_parser.add_argument("--json", action="store_true", help="Output full JSON result")

    # Companies House subcommand
    ch_parser = subparsers.add_parser("companies_house", help="UK Companies House collector")
    ch_parser.add_argument("--lookback", type=int, default=30, help="Days to look back (default: 30)")
    ch_parser.add_argument("--max", type=int, default=50, help="Max companies to process (default: 50)")
    ch_parser.add_argument("--all-sectors", action="store_true", help="Include all sectors, not just target")
    ch_parser.add_argument("--dry-run", action="store_true", default=True, help="Don't persist results (default)")
    ch_parser.add_argument("--live", action="store_true", help="Persist results (opposite of --dry-run)")
    ch_parser.add_argument("--json", action="store_true", help="Output full JSON result")

    # Domain/WHOIS subcommand
    whois_parser = subparsers.add_parser("domain_whois", help="Domain WHOIS/RDAP collector")
    whois_parser.add_argument("--domains", type=str, help="Comma-separated domains to check")
    whois_parser.add_argument("--dry-run", action="store_true", default=True, help="Don't persist results (default)")
    whois_parser.add_argument("--live", action="store_true", help="Persist results (opposite of --dry-run)")
    whois_parser.add_argument("--json", action="store_true", help="Output full JSON result")

    args = parser.parse_args()

    if not args.collector:
        parser.print_help()
        sys.exit(1)

    # Handle --live flag
    if hasattr(args, "live") and args.live:
        args.dry_run = False

    # Run collector
    if args.collector == "sec_edgar":
        asyncio.run(run_sec_edgar(args))
    elif args.collector == "github":
        asyncio.run(run_github(args))
    elif args.collector == "companies_house":
        asyncio.run(run_companies_house(args))
    elif args.collector == "domain_whois":
        asyncio.run(run_domain_whois(args))
    else:
        print(f"Unknown collector: {args.collector}")
        sys.exit(1)


async def run_companies_house(args):
    """Run UK Companies House collector."""
    import os

    api_key = os.environ.get("COMPANIES_HOUSE_API_KEY")
    if not api_key:
        print("ERROR: COMPANIES_HOUSE_API_KEY environment variable required")
        print("Get one at: https://developer.company-information.service.gov.uk/")
        sys.exit(1)

    print(f"\n{'='*60}")
    print("UK Companies House Collector")
    print(f"{'='*60}")
    print(f"Lookback: {args.lookback} days")
    print(f"Max companies: {args.max}")
    print(f"Target sectors only: {not args.all_sectors}")
    print(f"Dry run: {args.dry_run}")
    print(f"{'='*60}\n")

    try:
        from collectors.companies_house import CompaniesHouseCollector
        collector = CompaniesHouseCollector(
            api_key=api_key,
            lookback_days=args.lookback,
            max_companies=args.max,
            target_sectors_only=not args.all_sectors,
        )
        result = await collector.run(dry_run=args.dry_run)

        print(f"\n{'='*60}")
        print("RESULTS")
        print(f"{'='*60}")
        print(f"Status: {result.status.value}")
        print(f"Signals found: {result.signals_found}")
        print(f"New signals: {result.signals_new}")
        print(f"Timestamp: {result.timestamp}")

        if result.error_message:
            print(f"Error: {result.error_message}")
        if args.json:
            print(f"\n{json.dumps(result.to_dict(), indent=2)}")
        return result
    except ImportError:
        print("ERROR: Companies House collector not yet implemented")
        print("File: collectors/companies_house.py")
        sys.exit(1)


async def run_domain_whois(args):
    """Run Domain WHOIS/RDAP collector."""
    domains = args.domains.split(",") if args.domains else []

    if not domains:
        print("ERROR: --domains required (comma-separated list)")
        print("Example: --domains acme.ai,example.io,startup.tech")
        sys.exit(1)

    print(f"\n{'='*60}")
    print("Domain WHOIS/RDAP Collector")
    print(f"{'='*60}")
    print(f"Domains: {', '.join(domains)}")
    print(f"Dry run: {args.dry_run}")
    print(f"{'='*60}\n")

    try:
        from collectors.domain_whois import DomainWhoisCollector
        collector = DomainWhoisCollector(domains=domains)
        result = await collector.run(dry_run=args.dry_run)

        print(f"\n{'='*60}")
        print("RESULTS")
        print(f"{'='*60}")
        print(f"Status: {result.status.value}")
        print(f"Signals found: {result.signals_found}")
        print(f"Timestamp: {result.timestamp}")

        if result.error_message:
            print(f"Error: {result.error_message}")
        if args.json:
            print(f"\n{json.dumps(result.to_dict(), indent=2)}")
        return result
    except ImportError:
        print("ERROR: Domain WHOIS collector not yet implemented")
        print("File: collectors/domain_whois.py")
        sys.exit(1)


if __name__ == "__main__":
    main()

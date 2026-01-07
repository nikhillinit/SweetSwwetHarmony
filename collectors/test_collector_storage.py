"""
Test script to verify collector integration with SignalStore.

This script demonstrates:
1. Creating a SignalStore instance
2. Running collectors with the store
3. Verifying signals are saved
4. Checking deduplication works
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.github import GitHubCollector
from collectors.sec_edgar import SECEdgarCollector
from collectors.companies_house import CompaniesHouseCollector
from collectors.domain_whois import DomainWhoisCollector
from storage.signal_store import signal_store


@pytest_asyncio.fixture
async def clean_test_db():
    """Clean up test database before and after each test"""
    test_db = Path("test_signals.db")
    if test_db.exists():
        test_db.unlink()
    yield
    # Cleanup after test
    if test_db.exists():
        test_db.unlink()


@pytest.mark.asyncio
async def test_github_collector():
    """Test GitHub collector with SignalStore"""
    print("\n" + "=" * 60)
    print("TEST: GitHub Collector with SignalStore")
    print("=" * 60)

    # Check for GitHub token
    if not os.getenv("GITHUB_TOKEN"):
        print("SKIP: GITHUB_TOKEN not set")
        return

    async with signal_store("test_signals.db") as store:
        # First run: collect signals and save
        print("\nRun 1: Collecting and saving signals...")
        collector = GitHubCollector(
            store=store,
            github_token=os.getenv("GITHUB_TOKEN"),
            lookback_days=30,
            max_repos=10,
        )
        result1 = await collector.run(dry_run=False)

        print(f"\nResults:")
        print(f"  Status: {result1.status.value}")
        print(f"  Signals found: {result1.signals_found}")
        print(f"  Signals new: {result1.signals_new}")
        print(f"  Signals suppressed: {result1.signals_suppressed}")

        # Second run: should detect duplicates
        print("\nRun 2: Re-running collector (should detect duplicates)...")
        collector2 = GitHubCollector(
            store=store,
            github_token=os.getenv("GITHUB_TOKEN"),
            lookback_days=30,
            max_repos=10,
        )
        result2 = await collector2.run(dry_run=False)

        print(f"\nResults:")
        print(f"  Status: {result2.status.value}")
        print(f"  Signals found: {result2.signals_found}")
        print(f"  Signals new: {result2.signals_new}")
        print(f"  Signals suppressed: {result2.signals_suppressed}")

        # Verify deduplication worked
        if result2.signals_suppressed > 0:
            print(f"\n✓ Deduplication working: {result2.signals_suppressed} duplicates detected")
        else:
            print("\n⚠ Warning: No duplicates detected on second run")

        # Check database stats
        stats = await store.get_stats()
        print(f"\nDatabase Stats:")
        print(f"  Total signals: {stats['total_signals']}")
        print(f"  By type: {stats['signals_by_type']}")
        print(f"  Processing status: {stats['processing_status']}")


@pytest.mark.asyncio
async def test_sec_edgar_collector():
    """Test SEC EDGAR collector with SignalStore"""
    print("\n" + "=" * 60)
    print("TEST: SEC EDGAR Collector with SignalStore")
    print("=" * 60)

    async with signal_store("test_signals.db") as store:
        # Run collector
        print("\nCollecting Form D filings...")
        collector = SECEdgarCollector(
            store=store,
            lookback_days=30,
            max_filings=10,
            target_sectors_only=True,
        )
        result = await collector.run(dry_run=False)

        print(f"\nResults:")
        print(f"  Status: {result.status.value}")
        print(f"  Signals found: {result.signals_found}")
        print(f"  Signals new: {result.signals_new}")
        print(f"  Signals suppressed: {result.signals_suppressed}")

        if result.error_message:
            print(f"  Error: {result.error_message}")

        # Get pending signals
        pending = await store.get_pending_signals(limit=5)
        print(f"\nSample pending signals: {len(pending)}")
        for sig in pending[:3]:
            print(f"  - {sig.signal_type}: {sig.company_name} ({sig.canonical_key})")


@pytest.mark.asyncio
async def test_companies_house_collector():
    """Test Companies House collector with SignalStore"""
    print("\n" + "=" * 60)
    print("TEST: Companies House Collector with SignalStore")
    print("=" * 60)

    # Check for API key
    if not os.getenv("COMPANIES_HOUSE_API_KEY"):
        print("SKIP: COMPANIES_HOUSE_API_KEY not set")
        return

    async with signal_store("test_signals.db") as store:
        # Run collector
        print("\nCollecting UK incorporations...")
        collector = CompaniesHouseCollector(
            store=store,
            api_key=os.getenv("COMPANIES_HOUSE_API_KEY"),
            lookback_days=90,
            max_companies=10,
            target_sectors_only=True,
        )
        result = await collector.run(dry_run=False)

        print(f"\nResults:")
        print(f"  Status: {result.status.value}")
        print(f"  Signals found: {result.signals_found}")
        print(f"  Signals new: {result.signals_new}")
        print(f"  Signals suppressed: {result.signals_suppressed}")

        if result.error_message:
            print(f"  Error: {result.error_message}")


@pytest.mark.asyncio
async def test_domain_whois_collector():
    """Test Domain WHOIS collector with SignalStore"""
    print("\n" + "=" * 60)
    print("TEST: Domain WHOIS Collector with SignalStore")
    print("=" * 60)

    # Test domains
    test_domains = [
        "anthropic.com",
        "openai.com",
        "example.ai",
    ]

    async with signal_store("test_signals.db") as store:
        # Run collector
        print(f"\nChecking {len(test_domains)} domains...")
        collector = DomainWhoisCollector(
            store=store,
            lookback_days=365,  # More permissive for testing
            max_domains=10,
            tech_tlds_only=False,
        )
        result = await collector.run(domains=test_domains, dry_run=False)

        print(f"\nResults:")
        print(f"  Status: {result.status.value}")
        print(f"  Signals found: {result.signals_found}")
        print(f"  Signals new: {result.signals_new}")
        print(f"  Signals suppressed: {result.signals_suppressed}")

        if result.error_message:
            print(f"  Error: {result.error_message}")


@pytest.mark.asyncio
async def test_dry_run_mode():
    """Test that dry run mode doesn't persist signals"""
    print("\n" + "=" * 60)
    print("TEST: Dry Run Mode")
    print("=" * 60)

    async with signal_store("test_signals.db") as store:
        # Get initial count
        initial_stats = await store.get_stats()
        initial_count = initial_stats['total_signals']
        print(f"\nInitial signal count: {initial_count}")

        # Run in dry run mode
        if os.getenv("GITHUB_TOKEN"):
            print("\nRunning GitHub collector in dry run mode...")
            collector = GitHubCollector(
                store=store,
                github_token=os.getenv("GITHUB_TOKEN"),
                lookback_days=30,
                max_repos=5,
            )
            result = await collector.run(dry_run=True)

            print(f"\nResults:")
            print(f"  Status: {result.status.value}")
            print(f"  Signals found: {result.signals_found}")
            print(f"  Dry run: {result.dry_run}")

            # Check count didn't change
            final_stats = await store.get_stats()
            final_count = final_stats['total_signals']
            print(f"\nFinal signal count: {final_count}")

            if final_count == initial_count:
                print("✓ Dry run mode working: no signals persisted")
            else:
                print("✗ FAIL: Signals were persisted in dry run mode!")
        else:
            print("SKIP: GITHUB_TOKEN not set")


@pytest.mark.asyncio
async def test_final_stats():
    """Show final database stats after all tests"""
    print("\n" + "=" * 60)
    print("FINAL DATABASE STATS")
    print("=" * 60)

    test_db = Path("test_signals.db")
    if not test_db.exists():
        print("No test database found")
        return

    async with signal_store("test_signals.db") as store:
        stats = await store.get_stats()
        print("\nFinal Database Stats:")
        print(f"  Database: {stats['database_path']}")
        print(f"  Total signals: {stats['total_signals']}")
        print(f"  By type: {stats['signals_by_type']}")
        print(f"  Processing status: {stats['processing_status']}")
        print(f"  Active cache entries: {stats['active_suppression_entries']}")


# Optional: Keep the main() function for standalone execution
async def main():
    """Run all tests manually (for standalone execution)"""
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    print("\n" + "=" * 60)
    print("COLLECTOR STORAGE INTEGRATION TESTS")
    print("=" * 60)

    # Clean up test database if it exists
    test_db = Path("test_signals.db")
    if test_db.exists():
        print(f"\nRemoving existing test database: {test_db}")
        test_db.unlink()

    try:
        # Run tests
        await test_sec_edgar_collector()
        await test_github_collector()
        await test_companies_house_collector()
        await test_domain_whois_collector()
        await test_dry_run_mode()
        await test_final_stats()

        print("\n" + "=" * 60)
        print("ALL TESTS COMPLETE")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
    except Exception as e:
        print(f"\n\nTest failed with error: {e}")
        raise


if __name__ == "__main__":
    # For standalone execution with: python test_collector_storage.py
    asyncio.run(main())

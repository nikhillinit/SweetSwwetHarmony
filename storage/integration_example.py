"""
Integration example showing how collectors use SignalStore.

This demonstrates the full workflow:
1. Collector generates signals
2. Check suppression cache (skip if already in Notion)
3. Save signals to storage
4. Get pending signals
5. Verify with VerificationGate
6. Push to Notion or reject
7. Update processing state

Run with:
    python storage/integration_example.py
"""

import asyncio
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add parent directory to path for imports
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.signal_store import signal_store, SuppressionEntry
from utils.canonical_keys import build_canonical_key


# =============================================================================
# MOCK COLLECTOR
# =============================================================================

class MockCollector:
    """Mock collector that generates sample signals."""

    async def collect(self):
        """Generate mock signals."""
        return [
            {
                "signal_type": "github_spike",
                "source_api": "github",
                "company_name": "Acme AI",
                "confidence": 0.85,
                "website": "https://acme.ai",
                "github_org": "acme-ai",
                "raw_data": {
                    "repo": "acme-ai/awesome-ml",
                    "stars": 1500,
                    "recent_stars": 200,
                    "topics": ["ai", "machine-learning"],
                }
            },
            {
                "signal_type": "incorporation",
                "source_api": "companies_house",
                "company_name": "Beta Corp",
                "confidence": 0.95,
                "companies_house_number": "12345678",
                "website": "https://beta.io",
                "raw_data": {
                    "company_number": "12345678",
                    "incorporated_on": "2024-01-15",
                    "directors": ["Jane Doe"],
                }
            },
            {
                "signal_type": "domain_registration",
                "source_api": "whois",
                "company_name": "Gamma Labs",
                "confidence": 0.7,
                "website": "https://gammalabs.ai",
                "raw_data": {
                    "domain": "gammalabs.ai",
                    "registered_on": "2024-02-01",
                    "registrant": "Gamma Labs Inc",
                }
            },
            {
                "signal_type": "github_spike",
                "source_api": "github",
                "company_name": "Existing Company",  # Already in Notion
                "confidence": 0.9,
                "website": "https://existing.com",
                "github_org": "existing-co",
                "raw_data": {
                    "repo": "existing-co/product",
                    "stars": 3000,
                }
            }
        ]


# =============================================================================
# INTEGRATION WORKFLOW
# =============================================================================

async def run_collector_integration():
    """Demonstrate full integration workflow."""

    print("=" * 70)
    print("Signal Storage Integration Example")
    print("=" * 70)

    # Use a test database
    db_path = Path("integration_test.db")

    async with signal_store(db_path) as store:
        print("\n1. SETUP: Initialize suppression cache")
        print("-" * 70)

        # Simulate existing Notion entry
        existing_entries = [
            SuppressionEntry(
                canonical_key="domain:existing.com",
                notion_page_id="notion-existing-123",
                status="Source",
                company_name="Existing Company",
                expires_at=datetime.now(timezone.utc) + timedelta(days=7)
            )
        ]
        await store.update_suppression_cache(existing_entries)
        print("  Added 1 entry to suppression cache: domain:existing.com")

        # =====================================================================
        # STEP 1: Run Collector
        # =====================================================================

        print("\n2. COLLECTOR: Run collector and save signals")
        print("-" * 70)

        collector = MockCollector()
        results = await collector.collect()

        signals_saved = 0
        signals_suppressed = 0
        signals_duplicate = 0

        for result in results:
            # Build canonical key
            canonical_key = build_canonical_key(
                domain_or_website=result.get("website"),
                companies_house_number=result.get("companies_house_number"),
                github_org=result.get("github_org"),
                fallback_company_name=result.get("company_name")
            )

            print(f"\n  Processing: {result['company_name']}")
            print(f"    Signal type: {result['signal_type']}")
            print(f"    Canonical key: {canonical_key}")

            # Check suppression cache first
            suppressed = await store.check_suppression(canonical_key)
            if suppressed:
                print(f"    -> SKIPPED: Already in Notion (status: {suppressed.status})")
                signals_suppressed += 1
                continue

            # Check for duplicates
            if await store.is_duplicate(canonical_key):
                print(f"    -> SKIPPED: Duplicate signal")
                signals_duplicate += 1
                continue

            # Save signal
            signal_id = await store.save_signal(
                signal_type=result["signal_type"],
                source_api=result["source_api"],
                canonical_key=canonical_key,
                company_name=result["company_name"],
                confidence=result["confidence"],
                raw_data=result["raw_data"],
                detected_at=datetime.now(timezone.utc)
            )

            print(f"    -> SAVED: Signal ID {signal_id}")
            signals_saved += 1

        print(f"\n  Summary:")
        print(f"    Saved: {signals_saved}")
        print(f"    Suppressed: {signals_suppressed}")
        print(f"    Duplicates: {signals_duplicate}")

        # =====================================================================
        # STEP 2: Process Pending Signals
        # =====================================================================

        print("\n3. PROCESSING: Get pending signals")
        print("-" * 70)

        pending = await store.get_pending_signals()
        print(f"  Found {len(pending)} pending signals")

        for signal in pending:
            print(f"\n  Signal ID {signal.id}: {signal.company_name}")
            print(f"    Type: {signal.signal_type}")
            print(f"    Confidence: {signal.confidence:.2f}")
            print(f"    Canonical key: {signal.canonical_key}")

            # Simulate verification decision
            if signal.confidence >= 0.8:
                print(f"    Decision: AUTO_PUSH (high confidence)")

                # Simulate pushing to Notion
                notion_page_id = f"notion-{signal.id}-abc"

                await store.mark_pushed(
                    signal.id,
                    notion_page_id,
                    metadata={
                        "status": "Source",
                        "confidence": signal.confidence
                    }
                )

                print(f"    -> Marked as PUSHED (Notion: {notion_page_id})")

                # Add to suppression cache
                await store.update_suppression_cache([
                    SuppressionEntry(
                        canonical_key=signal.canonical_key,
                        notion_page_id=notion_page_id,
                        status="Source",
                        company_name=signal.company_name
                    )
                ])

                print(f"    -> Added to suppression cache")

            elif signal.confidence >= 0.5:
                print(f"    Decision: NEEDS_REVIEW (medium confidence)")

                # For demo, we'll mark as pushed to "Tracking" status
                notion_page_id = f"notion-{signal.id}-xyz"

                await store.mark_pushed(
                    signal.id,
                    notion_page_id,
                    metadata={
                        "status": "Tracking",
                        "confidence": signal.confidence
                    }
                )

                print(f"    -> Marked as PUSHED to Tracking (Notion: {notion_page_id})")

            else:
                print(f"    Decision: REJECT (low confidence)")

                await store.mark_rejected(
                    signal.id,
                    "Low confidence score",
                    metadata={"confidence": signal.confidence}
                )

                print(f"    -> Marked as REJECTED")

        # =====================================================================
        # STEP 3: Show Statistics
        # =====================================================================

        print("\n4. STATISTICS: Database stats")
        print("-" * 70)

        stats = await store.get_stats()

        print(f"  Total signals: {stats['total_signals']}")
        print(f"\n  By type:")
        for signal_type, count in stats['signals_by_type'].items():
            print(f"    {signal_type}: {count}")

        print(f"\n  Processing status:")
        for status, count in stats['processing_status'].items():
            print(f"    {status}: {count}")

        print(f"\n  Suppression cache: {stats['active_suppression_entries']} active entries")

        # =====================================================================
        # STEP 4: Query Examples
        # =====================================================================

        print("\n5. QUERIES: Example queries")
        print("-" * 70)

        # Get all signals for a specific company
        acme_signals = await store.get_signals_for_company("domain:acme.ai")
        print(f"\n  Signals for Acme AI: {len(acme_signals)}")
        for sig in acme_signals:
            print(f"    - {sig.signal_type} ({sig.confidence:.2f}) [{sig.processing_status}]")

        # Get pending by type
        gh_pending = await store.get_pending_signals(signal_type="github_spike")
        print(f"\n  Pending GitHub signals: {len(gh_pending)}")

        # Check suppression for new company
        print(f"\n  Check suppression for 'domain:newco.ai':")
        new_suppressed = await store.check_suppression("domain:newco.ai")
        if new_suppressed:
            print(f"    -> Found: {new_suppressed.company_name} (Notion: {new_suppressed.notion_page_id})")
        else:
            print(f"    -> Not found (OK to process)")

        # Check suppression for Acme (should be there now)
        print(f"\n  Check suppression for 'domain:acme.ai':")
        acme_suppressed = await store.check_suppression("domain:acme.ai")
        if acme_suppressed:
            print(f"    -> Found: {acme_suppressed.company_name} (Notion: {acme_suppressed.notion_page_id})")

    print("\n" + "=" * 70)
    print("Integration example complete!")
    print("=" * 70)
    print(f"\nTest database created at: {db_path}")
    print("You can inspect it with:")
    print(f"  python storage/migrations.py info {db_path}")
    print(f"  python storage/migrations.py validate {db_path}")


if __name__ == "__main__":
    asyncio.run(run_collector_integration())

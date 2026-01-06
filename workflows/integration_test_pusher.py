"""
Integration Test for NotionPusher

Tests the complete signal → verification → Notion pipeline.

Run with: python workflows/integration_test_pusher.py
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from workflows.notion_pusher import NotionPusher
from storage.signal_store import SignalStore
from verification.verification_gate_v2 import VerificationGate


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


class MockNotionConnector:
    """Mock Notion connector for testing"""

    def __init__(self):
        self.pushed_prospects = []
        self.call_count = 0

    async def upsert_prospect(self, payload):
        self.call_count += 1
        self.pushed_prospects.append(payload)

        logger.info(f"[MOCK] Pushing to Notion: {payload.company_name} → {payload.status}")

        return {
            "status": "created",
            "page_id": f"notion-{payload.discovery_id}",
            "reason": "Test push"
        }


async def test_multi_source_aggregation():
    """Test 1: Multi-source signal aggregation"""
    print("\n" + "=" * 70)
    print("TEST 1: Multi-Source Signal Aggregation")
    print("=" * 70)

    # Create temp database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = SignalStore(db_path)
    await store.initialize()

    try:
        # Add multiple signals for same company
        now = datetime.now(timezone.utc)

        await store.save_signal(
            signal_type="incorporation",
            source_api="companies_house",
            canonical_key="domain:multitest.ai",
            company_name="MultiTest Inc",
            confidence=0.9,
            raw_data={
                "company_number": "12345678",
                "website": "multitest.ai",
                "location": "London"
            },
            detected_at=now - timedelta(days=30)
        )

        await store.save_signal(
            signal_type="github_spike",
            source_api="github",
            canonical_key="domain:multitest.ai",
            company_name="MultiTest Inc",
            confidence=0.8,
            raw_data={
                "repo": "multitest/ml",
                "stars": 500,
                "founder_name": "Jane Doe"
            },
            detected_at=now - timedelta(days=7)
        )

        await store.save_signal(
            signal_type="domain_registration",
            source_api="whois",
            canonical_key="domain:multitest.ai",
            company_name="MultiTest Inc",
            confidence=0.7,
            raw_data={
                "domain": "multitest.ai",
                "registered_at": "2025-12-01"
            },
            detected_at=now - timedelta(days=14)
        )

        # Process batch
        mock_notion = MockNotionConnector()
        gate = VerificationGate(strict_mode=False)

        pusher = NotionPusher(
            signal_store=store,
            notion_connector=mock_notion,
            verification_gate=gate,
            dry_run=False
        )

        result = await pusher.process_batch()

        # Verify results
        assert result.total_processed == 1, "Should process 1 prospect"
        assert result.pushed == 1, "Should push 1 prospect"

        payload = mock_notion.pushed_prospects[0]
        assert payload.company_name == "MultiTest Inc"
        assert payload.canonical_key == "domain:multitest.ai"
        assert payload.status == "Source"  # High confidence multi-source
        assert payload.confidence_score >= 0.7
        assert len(payload.signal_types) == 3

        # Check aggregated data
        assert "company_number" in payload.external_refs or "location" in payload.location
        assert "repo" in str(payload.short_description) or payload.founder_name == "Jane Doe"

        print(f"✓ Aggregated {len(payload.signal_types)} signal types")
        print(f"✓ Confidence: {payload.confidence_score:.2%}")
        print(f"✓ Status: {payload.status}")
        print(f"✓ Why Now: {payload.why_now}")

    finally:
        await store.close()
        Path(db_path).unlink(missing_ok=True)

    print("\n✅ TEST 1 PASSED")


async def test_confidence_routing():
    """Test 2: Confidence-based routing"""
    print("\n" + "=" * 70)
    print("TEST 2: Confidence-Based Routing")
    print("=" * 70)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = SignalStore(db_path)
    await store.initialize()

    try:
        now = datetime.now(timezone.utc)

        # High confidence (should → Source)
        await store.save_signal(
            signal_type="incorporation",
            source_api="companies_house",
            canonical_key="domain:highconf.ai",
            company_name="High Confidence Inc",
            confidence=0.95,
            raw_data={"company_number": "11111111"},
            detected_at=now - timedelta(days=30)
        )

        await store.save_signal(
            signal_type="github_spike",
            source_api="github",
            canonical_key="domain:highconf.ai",
            company_name="High Confidence Inc",
            confidence=0.85,
            raw_data={"repo": "highconf/ai", "stars": 1000},
            detected_at=now - timedelta(days=7)
        )

        # Medium confidence (should → Tracking)
        await store.save_signal(
            signal_type="domain_registration",
            source_api="whois",
            canonical_key="domain:medconf.ai",
            company_name="Medium Confidence Corp",
            confidence=0.6,
            raw_data={"domain": "medconf.ai"},
            detected_at=now - timedelta(days=14)
        )

        # Low confidence (should → HOLD)
        await store.save_signal(
            signal_type="social_announcement",
            source_api="twitter",
            canonical_key="domain:lowconf.ai",
            company_name="Low Confidence Startup",
            confidence=0.3,
            raw_data={"tweet": "Launching soon!"},
            detected_at=now - timedelta(days=1)
        )

        # Process batch
        mock_notion = MockNotionConnector()
        gate = VerificationGate(strict_mode=False)

        pusher = NotionPusher(
            signal_store=store,
            notion_connector=mock_notion,
            verification_gate=gate,
            dry_run=False
        )

        result = await pusher.process_batch()

        # Verify routing
        assert result.total_processed == 3
        assert result.pushed == 2  # High and medium
        assert result.held == 1    # Low confidence

        # Check high confidence → Source
        high_conf = next(p for p in mock_notion.pushed_prospects if "High" in p.company_name)
        assert high_conf.status == "Source"
        print(f"✓ High confidence → {high_conf.status} (confidence: {high_conf.confidence_score:.2%})")

        # Check medium confidence → Tracking
        med_conf = next(p for p in mock_notion.pushed_prospects if "Medium" in p.company_name)
        assert med_conf.status == "Tracking"
        print(f"✓ Medium confidence → {med_conf.status} (confidence: {med_conf.confidence_score:.2%})")

        # Check low confidence still pending
        pending = await store.get_pending_signals()
        assert len(pending) == 1
        assert pending[0].company_name == "Low Confidence Startup"
        print(f"✓ Low confidence → HOLD (still pending)")

    finally:
        await store.close()
        Path(db_path).unlink(missing_ok=True)

    print("\n✅ TEST 2 PASSED")


async def test_hard_kill_rejection():
    """Test 3: Hard kill signal rejection"""
    print("\n" + "=" * 70)
    print("TEST 3: Hard Kill Signal Rejection")
    print("=" * 70)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = SignalStore(db_path)
    await store.initialize()

    try:
        now = datetime.now(timezone.utc)

        # Good signal + hard kill signal
        await store.save_signal(
            signal_type="incorporation",
            source_api="companies_house",
            canonical_key="domain:deadco.ai",
            company_name="Dead Company Inc",
            confidence=0.9,
            raw_data={"company_number": "99999999"},
            detected_at=now - timedelta(days=30)
        )

        await store.save_signal(
            signal_type="company_dissolved",
            source_api="companies_house",
            canonical_key="domain:deadco.ai",
            company_name="Dead Company Inc",
            confidence=1.0,
            raw_data={"status": "dissolved"},
            detected_at=now - timedelta(days=5)
        )

        # Process batch
        mock_notion = MockNotionConnector()
        gate = VerificationGate(strict_mode=False)

        pusher = NotionPusher(
            signal_store=store,
            notion_connector=mock_notion,
            verification_gate=gate,
            dry_run=False
        )

        result = await pusher.process_batch()

        # Verify rejection
        assert result.total_processed == 1
        assert result.pushed == 0
        assert result.rejected == 1

        # Should NOT be in Notion
        assert len(mock_notion.pushed_prospects) == 0

        # Should be marked rejected in store
        pending = await store.get_pending_signals()
        assert len(pending) == 0

        print("✓ Hard kill signal → REJECT")
        print("✓ Not pushed to Notion")
        print("✓ Marked as rejected in store")

    finally:
        await store.close()
        Path(db_path).unlink(missing_ok=True)

    print("\n✅ TEST 3 PASSED")


async def test_error_recovery():
    """Test 4: Error recovery and retry"""
    print("\n" + "=" * 70)
    print("TEST 4: Error Recovery and Retry")
    print("=" * 70)

    class FailOnceThenSucceedNotionConnector:
        """Mock connector that fails once then succeeds"""

        def __init__(self):
            self.call_count = 0
            self.pushed_prospects = []

        async def upsert_prospect(self, payload):
            self.call_count += 1

            if self.call_count == 1:
                logger.info("[MOCK] First call fails...")
                raise Exception("Temporary Notion API error")

            logger.info("[MOCK] Retry succeeded")
            self.pushed_prospects.append(payload)
            return {
                "status": "created",
                "page_id": f"notion-{payload.discovery_id}",
                "reason": "Success on retry"
            }

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = SignalStore(db_path)
    await store.initialize()

    try:
        now = datetime.now(timezone.utc)

        await store.save_signal(
            signal_type="incorporation",
            source_api="companies_house",
            canonical_key="domain:retry.ai",
            company_name="Retry Test Inc",
            confidence=0.9,
            raw_data={},
            detected_at=now
        )

        # Process with failing connector
        mock_notion = FailOnceThenSucceedNotionConnector()
        gate = VerificationGate(strict_mode=False)

        pusher = NotionPusher(
            signal_store=store,
            notion_connector=mock_notion,
            verification_gate=gate,
            dry_run=False
        )

        result = await pusher.process_batch()

        # Should succeed after retry
        assert result.total_processed == 1
        assert result.pushed == 1
        assert mock_notion.call_count == 2  # Failed once, succeeded on retry
        assert len(mock_notion.pushed_prospects) == 1

        print("✓ First call failed")
        print("✓ Retry succeeded")
        print(f"✓ Total calls: {mock_notion.call_count}")

    finally:
        await store.close()
        Path(db_path).unlink(missing_ok=True)

    print("\n✅ TEST 4 PASSED")


async def test_batch_limit():
    """Test 5: Batch limit enforcement"""
    print("\n" + "=" * 70)
    print("TEST 5: Batch Limit Enforcement")
    print("=" * 70)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = SignalStore(db_path)
    await store.initialize()

    try:
        now = datetime.now(timezone.utc)

        # Add 5 signals
        for i in range(5):
            await store.save_signal(
                signal_type="incorporation",
                source_api="companies_house",
                canonical_key=f"domain:test{i}.ai",
                company_name=f"Test {i} Inc",
                confidence=0.9,
                raw_data={},
                detected_at=now
            )

        # Process with limit of 2
        mock_notion = MockNotionConnector()
        gate = VerificationGate(strict_mode=False)

        pusher = NotionPusher(
            signal_store=store,
            notion_connector=mock_notion,
            verification_gate=gate,
            dry_run=False
        )

        result = await pusher.process_batch(limit=2)

        # Should only process 2
        assert result.total_processed <= 2
        assert len(mock_notion.pushed_prospects) <= 2

        # Should still have pending signals
        pending = await store.get_pending_signals()
        assert len(pending) >= 3

        print(f"✓ Processed: {result.total_processed} (limit: 2)")
        print(f"✓ Remaining pending: {len(pending)}")

    finally:
        await store.close()
        Path(db_path).unlink(missing_ok=True)

    print("\n✅ TEST 5 PASSED")


async def main():
    """Run all integration tests"""
    print("\n" + "=" * 70)
    print("NOTION PUSHER INTEGRATION TESTS")
    print("=" * 70)

    try:
        await test_multi_source_aggregation()
        await test_confidence_routing()
        await test_hard_kill_rejection()
        await test_error_recovery()
        await test_batch_limit()

        print("\n" + "=" * 70)
        print("ALL TESTS PASSED ✅")
        print("=" * 70)

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        raise

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())

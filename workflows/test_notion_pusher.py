"""
Tests for NotionPusher

Run with: pytest workflows/test_notion_pusher.py -v
"""

import asyncio
import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from workflows.notion_pusher import (
    NotionPusher,
    AggregatedProspect,
    PushResult,
    BatchResult,
)
from storage.signal_store import SignalStore
from connectors.notion_connector_v2 import (
    NotionConnector,
    ProspectPayload,
    InvestmentStage,
)
from verification.verification_gate_v2 import (
    VerificationGate,
    PushDecision,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def temp_db():
    """Create temporary database for testing"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = SignalStore(db_path)
    await store.initialize()

    yield store

    await store.close()
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def mock_notion():
    """Mock NotionConnector for testing"""

    class MockNotionConnector:
        def __init__(self):
            self.pushed_prospects = []

        async def upsert_prospect(self, payload: ProspectPayload):
            self.pushed_prospects.append(payload)
            return {
                "status": "created",
                "page_id": f"notion-{payload.discovery_id}",
                "reason": "Test push"
            }

    return MockNotionConnector()


@pytest.fixture
def verification_gate():
    """Create verification gate"""
    return VerificationGate(
        strict_mode=False,
        auto_push_status="Source",
        needs_review_status="Tracking"
    )


# =============================================================================
# TESTS: SIGNAL AGGREGATION
# =============================================================================

@pytest.mark.asyncio
async def test_group_signals_by_canonical_key(temp_db):
    """Test grouping signals by canonical key"""
    store = temp_db

    # Add signals for same company from different sources
    await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:acme.ai",
        company_name="Acme Inc",
        confidence=0.7,
        raw_data={"repo": "acme/ml", "stars": 100}
    )

    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:acme.ai",
        company_name="Acme Inc",
        confidence=0.9,
        raw_data={"company_number": "12345678"}
    )

    # Add signal for different company
    await store.save_signal(
        signal_type="domain_registration",
        source_api="whois",
        canonical_key="domain:beta.com",
        company_name="Beta Corp",
        confidence=0.6,
        raw_data={"domain": "beta.com"}
    )

    # Get pending signals
    pending = await store.get_pending_signals()
    assert len(pending) == 3

    # Create pusher and group signals
    pusher = NotionPusher(
        signal_store=store,
        notion_connector=None,
        dry_run=True
    )

    prospects = pusher._group_by_canonical_key(pending)

    # Should have 2 unique prospects
    assert len(prospects) == 2

    # Find Acme prospect
    acme = next(p for p in prospects if p.canonical_key == "domain:acme.ai")
    assert acme.signal_count == 2
    assert acme.is_multi_source
    assert len(acme.sources) == 2
    assert set(acme.signal_types) == {"github_spike", "incorporation"}

    # Find Beta prospect
    beta = next(p for p in prospects if p.canonical_key == "domain:beta.com")
    assert beta.signal_count == 1
    assert not beta.is_multi_source


@pytest.mark.asyncio
async def test_aggregated_prospect_metadata(temp_db):
    """Test AggregatedProspect aggregates metadata correctly"""
    store = temp_db

    # Add signals with different detection times
    now = datetime.now(timezone.utc)

    await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:test.ai",
        company_name="Test Inc",
        confidence=0.7,
        raw_data={"stars": 100, "website": "test.ai"},
        detected_at=now - timedelta(days=7)
    )

    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:test.ai",
        company_name="Test Inc",
        confidence=0.9,
        raw_data={"company_number": "12345678", "location": "London"},
        detected_at=now - timedelta(days=2)
    )

    pending = await store.get_pending_signals()

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=None,
        dry_run=True
    )

    prospects = pusher._group_by_canonical_key(pending)
    prospect = prospects[0]

    # Check aggregated metadata
    assert prospect.earliest_detected == now - timedelta(days=7)
    assert prospect.latest_detected == now - timedelta(days=2)

    # Aggregated data should merge from all signals (latest wins)
    assert "stars" in prospect.aggregated_data
    assert "company_number" in prospect.aggregated_data
    assert "website" in prospect.aggregated_data
    assert "location" in prospect.aggregated_data


# =============================================================================
# TESTS: PUSH DECISIONS
# =============================================================================

@pytest.mark.asyncio
async def test_high_confidence_multi_source_auto_push(temp_db, mock_notion, verification_gate):
    """High confidence + multi-source should AUTO_PUSH to 'Source' status"""
    store = temp_db

    # Create high-confidence multi-source signals
    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:highconf.ai",
        company_name="High Confidence Inc",
        confidence=0.95,
        raw_data={"company_number": "12345678"},
        detected_at=datetime.now(timezone.utc) - timedelta(days=30)
    )

    await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:highconf.ai",
        company_name="High Confidence Inc",
        confidence=0.8,
        raw_data={"repo": "highconf/ai", "stars": 500},
        detected_at=datetime.now(timezone.utc) - timedelta(days=7)
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=False
    )

    result = await pusher.process_batch()

    # Check results
    assert result.total_processed == 1
    assert result.pushed == 1
    assert result.rejected == 0
    assert result.held == 0

    # Check Notion payload
    assert len(mock_notion.pushed_prospects) == 1
    payload = mock_notion.pushed_prospects[0]
    assert payload.status == "Source"  # AUTO_PUSH status
    assert payload.confidence_score >= 0.7


@pytest.mark.asyncio
async def test_medium_confidence_needs_review(temp_db, mock_notion, verification_gate):
    """Medium confidence should route to 'Tracking' status"""
    store = temp_db

    # Create medium-confidence signal
    await store.save_signal(
        signal_type="domain_registration",
        source_api="whois",
        canonical_key="domain:medconf.ai",
        company_name="Medium Confidence Corp",
        confidence=0.6,
        raw_data={"domain": "medconf.ai"},
        detected_at=datetime.now(timezone.utc) - timedelta(days=14)
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=False
    )

    result = await pusher.process_batch()

    # Check results
    assert result.total_processed == 1
    assert result.pushed == 1

    # Check Notion payload
    payload = mock_notion.pushed_prospects[0]
    assert payload.status == "Tracking"  # NEEDS_REVIEW status
    assert 0.4 <= payload.confidence_score < 0.7


@pytest.mark.asyncio
async def test_low_confidence_held(temp_db, mock_notion, verification_gate):
    """Low confidence should be HOLD (not pushed)"""
    store = temp_db

    # Create low-confidence signal
    await store.save_signal(
        signal_type="social_announcement",
        source_api="twitter",
        canonical_key="domain:lowconf.ai",
        company_name="Low Confidence Startup",
        confidence=0.3,
        raw_data={"tweet": "Launching soon!"},
        detected_at=datetime.now(timezone.utc) - timedelta(days=1)
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=False
    )

    result = await pusher.process_batch()

    # Check results
    assert result.total_processed == 1
    assert result.pushed == 0
    assert result.held == 1

    # Nothing pushed to Notion
    assert len(mock_notion.pushed_prospects) == 0

    # Signal should remain pending (not rejected)
    pending = await store.get_pending_signals()
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_hard_kill_signal_rejected(temp_db, mock_notion, verification_gate):
    """Hard kill signal should be rejected immediately"""
    store = temp_db

    # Create signal with hard kill
    await store.save_signal(
        signal_type="company_dissolved",
        source_api="companies_house",
        canonical_key="domain:dead.ai",
        company_name="Dead Company",
        confidence=1.0,
        raw_data={"status": "dissolved"},
        detected_at=datetime.now(timezone.utc) - timedelta(days=5)
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=False
    )

    result = await pusher.process_batch()

    # Check results
    assert result.total_processed == 1
    assert result.pushed == 0
    assert result.rejected == 1

    # Nothing pushed to Notion
    assert len(mock_notion.pushed_prospects) == 0

    # Signal should be marked as rejected
    pending = await store.get_pending_signals()
    assert len(pending) == 0


# =============================================================================
# TESTS: ERROR HANDLING
# =============================================================================

@pytest.mark.asyncio
async def test_notion_error_handling(temp_db, verification_gate):
    """Test handling of Notion API errors"""

    class FailingNotionConnector:
        async def upsert_prospect(self, payload):
            raise Exception("Notion API error")

    store = temp_db

    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:error.ai",
        company_name="Error Inc",
        confidence=0.9,
        raw_data={},
        detected_at=datetime.now(timezone.utc)
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=FailingNotionConnector(),
        verification_gate=verification_gate,
        dry_run=False
    )

    result = await pusher.process_batch()

    # Should record error but continue
    assert result.total_processed == 1
    assert result.errors == 1
    assert len(result.error_messages) > 0


@pytest.mark.asyncio
async def test_partial_batch_failure(temp_db, verification_gate):
    """Test that one failure doesn't stop entire batch"""

    class PartiallyFailingNotionConnector:
        def __init__(self):
            self.call_count = 0
            self.pushed_prospects = []

        async def upsert_prospect(self, payload):
            self.call_count += 1
            if self.call_count == 1:
                raise Exception("First call fails")

            self.pushed_prospects.append(payload)
            return {
                "status": "created",
                "page_id": f"notion-{payload.discovery_id}",
                "reason": "Test"
            }

    store = temp_db

    # Add two prospects
    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:first.ai",
        company_name="First Inc",
        confidence=0.9,
        raw_data={},
        detected_at=datetime.now(timezone.utc)
    )

    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:second.ai",
        company_name="Second Inc",
        confidence=0.9,
        raw_data={},
        detected_at=datetime.now(timezone.utc)
    )

    notion = PartiallyFailingNotionConnector()
    pusher = NotionPusher(
        signal_store=store,
        notion_connector=notion,
        verification_gate=verification_gate,
        dry_run=False
    )

    result = await pusher.process_batch()

    # Should process both, one success, one failure
    assert result.total_processed == 2
    assert result.errors == 1
    assert len(notion.pushed_prospects) == 1  # One succeeded


# =============================================================================
# TESTS: DRY RUN
# =============================================================================

@pytest.mark.asyncio
async def test_dry_run_mode(temp_db, mock_notion, verification_gate):
    """Test dry run doesn't push to Notion or update store"""
    store = temp_db

    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:dryrun.ai",
        company_name="Dry Run Inc",
        confidence=0.9,
        raw_data={},
        detected_at=datetime.now(timezone.utc)
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=True
    )

    result = await pusher.process_batch()

    # Should show as "pushed" in stats but not actually push
    assert result.pushed == 1

    # Nothing actually pushed to Notion
    assert len(mock_notion.pushed_prospects) == 0

    # Signal still pending
    pending = await store.get_pending_signals()
    assert len(pending) == 1


# =============================================================================
# TESTS: PROSPECT PAYLOAD BUILDING
# =============================================================================

@pytest.mark.asyncio
async def test_prospect_payload_generation(temp_db, mock_notion, verification_gate):
    """Test ProspectPayload is built correctly from aggregated signals"""
    store = temp_db

    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:test.ai",
        company_name="Test Inc",
        confidence=0.9,
        raw_data={
            "website": "test.ai",
            "description": "AI testing platform",
            "founder_name": "Jane Doe",
            "location": "San Francisco",
            "stage": "Seed"
        },
        detected_at=datetime.now(timezone.utc)
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=False
    )

    await pusher.process_batch()

    # Check payload
    assert len(mock_notion.pushed_prospects) == 1
    payload = mock_notion.pushed_prospects[0]

    assert payload.company_name == "Test Inc"
    assert payload.canonical_key == "domain:test.ai"
    assert payload.website == "test.ai"
    assert payload.short_description == "AI testing platform"
    assert payload.founder_name == "Jane Doe"
    assert payload.location == "San Francisco"
    assert payload.stage == InvestmentStage.SEED
    assert "incorporation" in payload.signal_types


# =============================================================================
# TESTS: BATCH LIMITS
# =============================================================================

@pytest.mark.asyncio
async def test_batch_limit(temp_db, mock_notion, verification_gate):
    """Test batch processing respects limit"""
    store = temp_db

    # Add 5 signals
    for i in range(5):
        await store.save_signal(
            signal_type="incorporation",
            source_api="companies_house",
            canonical_key=f"domain:test{i}.ai",
            company_name=f"Test {i} Inc",
            confidence=0.9,
            raw_data={},
            detected_at=datetime.now(timezone.utc)
        )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=False
    )

    # Process with limit of 2
    result = await pusher.process_batch(limit=2)

    # Should only process 2
    assert result.total_processed <= 2

    # Should still have pending signals
    pending = await store.get_pending_signals()
    assert len(pending) >= 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

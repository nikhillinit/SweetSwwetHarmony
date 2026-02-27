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
    VerificationResult,
    VerificationStatus,
    Signal,
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


class FixedDecisionGate:
    """Deterministic gate for pipeline behavior tests.

    Use this for tests validating pusher behavior (error handling, dry-run,
    payload construction), not scoring policy.
    """

    MEDIUM_CONFIDENCE_THRESHOLD = 0.4

    def __init__(
        self,
        decision: PushDecision,
        confidence: float = 0.9,
        reason: str = "forced gate decision for pipeline test",
    ):
        self.decision = decision
        self.confidence = confidence
        self.reason = reason
        self.auto_push_status = "Source"
        self.needs_review_status = "Tracking"

    def evaluate(self, signals, **kwargs):
        sources = sorted({s.source_api for s in signals})
        verification_status = (
            VerificationStatus.MULTI_SOURCE if len(sources) > 1 else VerificationStatus.SINGLE_SOURCE
        )

        if self.decision == PushDecision.AUTO_PUSH:
            suggested_status = self.auto_push_status
        elif self.decision == PushDecision.NEEDS_REVIEW:
            suggested_status = self.needs_review_status
        else:
            suggested_status = ""

        return VerificationResult(
            decision=self.decision,
            verification_status=verification_status,
            confidence_score=self.confidence,
            confidence_breakdown={"forced_for_test": True, "sources_checked": len(sources)},
            reason=self.reason,
            suggested_status=suggested_status,
            signals_used=[s.id for s in signals],
            sources_checked=sources,
            verification_details=[],
        )


@pytest.fixture
def fixed_gate_factory():
    """Factory for deterministic gate decisions in pipeline tests."""

    def _make(decision: PushDecision, confidence: float = 0.9):
        return FixedDecisionGate(decision=decision, confidence=confidence)

    return _make


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

    prospects = await pusher._group_by_canonical_key(pending)

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

    prospects = await pusher._group_by_canonical_key(pending)
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
async def test_high_confidence_multi_source_tracks_under_current_scoring(temp_db, mock_notion, verification_gate, monkeypatch):
    """Current scoring yields NEEDS_REVIEW (Tracking) for this fixture."""
    monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
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
    assert payload.status == "Tracking"
    assert 0.4 <= payload.confidence_score < 0.7


@pytest.mark.asyncio
async def test_medium_confidence_single_signal_holds_under_current_scoring(temp_db, mock_notion, verification_gate, monkeypatch):
    """Single-signal fixture is HOLD under current weighted scoring."""
    monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
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
    assert result.pushed == 0
    assert result.held == 1
    assert len(mock_notion.pushed_prospects) == 0


@pytest.mark.asyncio
async def test_medium_confidence_multi_signal_routes_tracking(temp_db, mock_notion, verification_gate, monkeypatch):
    """Real-gate medium path should remain reachable with fresh multi-source evidence."""
    monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
    store = temp_db

    now = datetime.now(timezone.utc)
    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:medroute.ai",
        company_name="Medium Route Inc",
        confidence=0.85,
        raw_data={"company_number": "87654321"},
        detected_at=now - timedelta(days=1),
    )
    await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:medroute.ai",
        company_name="Medium Route Inc",
        confidence=0.75,
        raw_data={"repo": "medroute/ai", "stars": 120},
        detected_at=now - timedelta(days=1),
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=False,
    )

    result = await pusher.process_batch()

    assert result.total_processed == 1
    assert result.pushed == 1
    assert result.held == 0
    assert len(mock_notion.pushed_prospects) == 1
    payload = mock_notion.pushed_prospects[0]
    assert payload.status == "Tracking"
    assert 0.4 <= payload.confidence_score < 0.7


def test_gate_reachability_contract(verification_gate):
    """Lock reachability assumptions for current threshold policy.

    - A best-case single signal can reach MEDIUM threshold after recalibration.
    - Strong fresh multi-source evidence can reach HIGH threshold.
    """
    now = datetime.now(timezone.utc)
    single = Signal(
        id="single-1",
        signal_type="hiring_signal",
        source_api="src1",
        confidence=1.0,
        detected_at=now,
        raw_data={},
    )
    single_result = verification_gate.evaluate([single])
    assert single_result.confidence_score >= verification_gate.MEDIUM_CONFIDENCE_THRESHOLD
    assert single_result.confidence_score < verification_gate.HIGH_CONFIDENCE_THRESHOLD

    multi = [
        Signal(
            id="multi-1",
            signal_type="hiring_signal",
            source_api="src1",
            confidence=1.0,
            detected_at=now,
            raw_data={},
        ),
        Signal(
            id="multi-2",
            signal_type="incorporation",
            source_api="src2",
            confidence=1.0,
            detected_at=now,
            raw_data={},
        ),
    ]
    multi_result = verification_gate.evaluate(multi)
    assert multi_result.confidence_score >= verification_gate.HIGH_CONFIDENCE_THRESHOLD


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
async def test_notion_error_handling(temp_db, fixed_gate_factory, monkeypatch):
    """Persistent Notion failures should not mark prospect as pushed."""
    monkeypatch.setenv("DELIVERY_MODE", "auto_publish")

    class FailingNotionConnector:
        def __init__(self):
            self.attempts = 0

        async def upsert_prospect(self, payload):
            self.attempts += 1
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

    notion = FailingNotionConnector()
    pusher = NotionPusher(
        signal_store=store,
        notion_connector=notion,
        verification_gate=fixed_gate_factory(PushDecision.AUTO_PUSH, confidence=0.85),
        dry_run=False
    )

    result = await pusher.process_batch()

    # Current behavior: retries then returns not-pushed without batch error increment.
    assert result.total_processed == 1
    assert notion.attempts == 3
    assert result.errors == 0
    assert result.pushed == 0
    assert result.results[0].pushed is False


@pytest.mark.asyncio
async def test_partial_batch_failure(temp_db, fixed_gate_factory, monkeypatch):
    """Transient failure on first attempt is recovered by retry."""
    monkeypatch.setenv("DELIVERY_MODE", "auto_publish")

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
        verification_gate=fixed_gate_factory(PushDecision.AUTO_PUSH, confidence=0.85),
        dry_run=False
    )

    result = await pusher.process_batch()

    # Current behavior: first attempt fails then retry succeeds, so no final errors.
    assert result.total_processed == 2
    assert result.errors == 0
    assert result.pushed == 2
    assert len(notion.pushed_prospects) == 2
    assert notion.call_count == 3


# =============================================================================
# TESTS: DRY RUN
# =============================================================================

@pytest.mark.asyncio
async def test_dry_run_mode(temp_db, mock_notion, fixed_gate_factory, monkeypatch):
    """Test dry run doesn't push to Notion or update store"""
    monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
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
        verification_gate=fixed_gate_factory(PushDecision.AUTO_PUSH, confidence=0.85),
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
async def test_prospect_payload_generation(temp_db, mock_notion, fixed_gate_factory, monkeypatch):
    """Test ProspectPayload is built correctly from aggregated signals"""
    monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
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
        verification_gate=fixed_gate_factory(PushDecision.AUTO_PUSH, confidence=0.85),
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
async def test_batch_limit(temp_db, mock_notion, verification_gate, monkeypatch):
    """Test batch processing respects limit"""
    monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
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


# =============================================================================
# TESTS: DELIVERY GUARD
# =============================================================================

@pytest.mark.asyncio
async def test_delivery_guard_staging_only_blocks_push(
    temp_db, mock_notion, verification_gate, monkeypatch
):
    """staging_only DELIVERY_MODE blocks Notion writes and aborts batch"""
    monkeypatch.setenv("DELIVERY_MODE", "staging_only")
    store = temp_db

    # Multi-source signals to reach the push path (NEEDS_REVIEW or AUTO_PUSH)
    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:guarded.ai",
        company_name="Guarded Inc",
        confidence=0.95,
        raw_data={"company_number": "12345678"},
        detected_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:guarded.ai",
        company_name="Guarded Inc",
        confidence=0.8,
        raw_data={"repo": "guarded/ai", "stars": 500},
        detected_at=datetime.now(timezone.utc) - timedelta(days=7),
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=False,
    )

    result = await pusher.process_batch()

    # Nothing should be pushed to Notion
    assert len(mock_notion.pushed_prospects) == 0
    # Batch should contain a delivery-policy error message
    assert any("Delivery policy" in msg for msg in result.error_messages)


@pytest.mark.asyncio
async def test_delivery_guard_auto_publish_allows_push(
    temp_db, mock_notion, verification_gate, monkeypatch
):
    """auto_publish DELIVERY_MODE allows Notion writes"""
    monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
    store = temp_db

    # Multi-source signals to reach the push path
    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:allowed.ai",
        company_name="Allowed Inc",
        confidence=0.95,
        raw_data={"company_number": "12345678"},
        detected_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:allowed.ai",
        company_name="Allowed Inc",
        confidence=0.8,
        raw_data={"repo": "allowed/ai", "stars": 500},
        detected_at=datetime.now(timezone.utc) - timedelta(days=7),
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=False,
    )

    result = await pusher.process_batch()

    # Should push successfully
    assert result.pushed == 1
    assert len(mock_notion.pushed_prospects) == 1
    assert not result.error_messages


@pytest.mark.asyncio
async def test_dry_run_skips_regardless_of_delivery_mode(
    temp_db, mock_notion, verification_gate, monkeypatch
):
    """dry_run=True should skip Notion writes even when DELIVERY_MODE=staging_only"""
    monkeypatch.setenv("DELIVERY_MODE", "staging_only")
    store = temp_db

    # Multi-source signals to reach the push path
    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:dryguard.ai",
        company_name="Dry Guard Inc",
        confidence=0.95,
        raw_data={"company_number": "12345678"},
        detected_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:dryguard.ai",
        company_name="Dry Guard Inc",
        confidence=0.8,
        raw_data={"repo": "dryguard/ai", "stars": 500},
        detected_at=datetime.now(timezone.utc) - timedelta(days=7),
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=True,
    )

    result = await pusher.process_batch()

    # dry_run reports as pushed (for stats) but nothing actually hits Notion
    assert result.pushed == 1
    assert len(mock_notion.pushed_prospects) == 0
    # No delivery-policy errors because the guard is never reached
    assert not any("Delivery policy" in msg for msg in result.error_messages)


@pytest.mark.asyncio
async def test_delivery_guard_manual_publish_allows_manual_intent(
    temp_db, mock_notion, verification_gate, monkeypatch
):
    """manual_publish DELIVERY_MODE allows MANUAL_PUSH intent via process_single_prospect"""
    from workflows.delivery_policy import DeliveryIntent

    monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
    store = temp_db

    # Multi-source signals to reach the push path
    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:manual.ai",
        company_name="Manual Inc",
        confidence=0.95,
        raw_data={"company_number": "12345678"},
        detected_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:manual.ai",
        company_name="Manual Inc",
        confidence=0.8,
        raw_data={"repo": "manual/ai", "stars": 500},
        detected_at=datetime.now(timezone.utc) - timedelta(days=7),
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=False,
    )

    result = await pusher.process_single_prospect(
        "domain:manual.ai",
        intent=DeliveryIntent.MANUAL_PUSH,
    )

    # Should succeed -- manual_publish allows MANUAL_PUSH
    assert result.pushed
    assert len(mock_notion.pushed_prospects) == 1


@pytest.mark.asyncio
async def test_delivery_guard_manual_publish_blocks_auto_intent(
    temp_db, mock_notion, verification_gate, monkeypatch
):
    """manual_publish DELIVERY_MODE blocks AUTO_PUSH intent"""
    from workflows.delivery_policy import DeliveryPolicyError

    monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
    store = temp_db

    # Multi-source signals to reach the push path
    await store.save_signal(
        signal_type="incorporation",
        source_api="companies_house",
        canonical_key="domain:blocked.ai",
        company_name="Blocked Inc",
        confidence=0.95,
        raw_data={"company_number": "12345678"},
        detected_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:blocked.ai",
        company_name="Blocked Inc",
        confidence=0.8,
        raw_data={"repo": "blocked/ai", "stars": 500},
        detected_at=datetime.now(timezone.utc) - timedelta(days=7),
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=False,
    )

    # AUTO_PUSH should raise DeliveryPolicyError
    with pytest.raises(DeliveryPolicyError):
        await pusher.process_single_prospect("domain:blocked.ai")

    # Nothing pushed to Notion
    assert len(mock_notion.pushed_prospects) == 0


# =============================================================================
# TESTS: DNS ALIAS RESOLUTION IN GROUPING
# =============================================================================

@pytest.mark.asyncio
async def test_alias_resolution_merges_groups(temp_db, mock_notion, verification_gate):
    """Two signals with different keys merge into one prospect when one is an alias."""
    store = temp_db
    from unittest.mock import AsyncMock, patch

    await store.save_signal(
        signal_type="rss_mention",
        source_api="rss_feeds",
        canonical_key="name_loc:acme-labs",
        company_name="Acme Labs",
        confidence=0.5,
        raw_data={"title": "Acme Labs launches"},
    )
    await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:acmelabs.io",
        company_name="Acme Labs",
        confidence=0.7,
        raw_data={"repo": "acmelabs/app", "stars": 200},
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=True,
    )

    # Mock alias resolution: name_loc:acme-labs → domain:acmelabs.io
    async def mock_resolve(keys, conn):
        result = {k: k for k in keys}
        result["name_loc:acme-labs"] = "domain:acmelabs.io"
        return result

    with patch("utils.dns_alias_resolver.resolve_aliases_batch_async", new=mock_resolve):
        pending = await store.get_pending_signals()
        prospects = await pusher._group_by_canonical_key(pending)

    # Both signals should be grouped under domain:acmelabs.io
    keys = [p.canonical_key for p in prospects]
    assert "domain:acmelabs.io" in keys
    assert "name_loc:acme-labs" not in keys

    merged = [p for p in prospects if p.canonical_key == "domain:acmelabs.io"][0]
    assert len(merged.signals) == 2


@pytest.mark.asyncio
async def test_alias_resolution_no_alias_table(temp_db, mock_notion, verification_gate):
    """Pre-v44 DB (no alias table) groups signals normally by original key."""
    store = temp_db

    await store.save_signal(
        signal_type="rss_mention",
        source_api="rss_feeds",
        canonical_key="name_loc:beta-corp",
        company_name="Beta Corp",
        confidence=0.5,
        raw_data={"title": "Beta Corp news"},
    )
    await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:betacorp.com",
        company_name="Beta Corp",
        confidence=0.7,
        raw_data={"repo": "betacorp/api", "stars": 150},
    )

    pusher = NotionPusher(
        signal_store=store,
        notion_connector=mock_notion,
        verification_gate=verification_gate,
        dry_run=True,
    )

    # No mock — resolve_aliases_batch_async will hit the real DB which has no
    # dns_promotion_aliases table, so it falls through to identity mapping
    pending = await store.get_pending_signals()
    prospects = await pusher._group_by_canonical_key(pending)

    # Without alias table, signals stay in separate groups
    keys = sorted([p.canonical_key for p in prospects])
    assert len(keys) == 2
    assert "domain:betacorp.com" in keys
    assert "name_loc:beta-corp" in keys


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

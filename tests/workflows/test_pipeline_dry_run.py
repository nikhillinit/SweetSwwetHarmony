"""Tests for the dry-run mutation fix in _process_company().

Verifies four scenarios around the else branch (line ~2110) in
workflows/pipeline.py:

1. connector present + dry_run=True  -> logs only, no state mutation
2. connector absent  + dry_run=True  -> logs only, no state mutation
3. connector present + dry_run=False -> normal path (mark_queued + _push_to_notion)
4. connector absent  + dry_run=False -> mark_rejected with "no_connector" reason

Previously the else branch always called mark_pushed() with a dummy page ID
for both dry_run=True and no-connector cases, polluting signal state. The fix
ensures dry_run=True never mutates state and the no-connector path uses
mark_held() to prevent infinite reprocessing while preserving recoverability.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.signal_store import SignalStore, StoredSignal
from storage.claim_fact_store import ClaimFact
from verification.verification_gate_v2 import (
    PushDecision,
    Signal,
    VerificationGate,
    VerificationResult,
    VerificationStatus,
)
from utils.thesis_filter import RoutingDecision, ThesisFilterResult
from workflows.pipeline import DiscoveryPipeline, PipelineConfig, PipelineStats
from utils.signal_consolidator import ConsolidatedSignal


# =============================================================================
# MODULE-LEVEL FIXTURES: pre-migrated DB template (runs migrations once)
# =============================================================================


@pytest.fixture(scope="module")
def template_db_path():
    """Create a pre-migrated template DB once per module.

    Runs all SignalStore migrations a single time, then each test copies
    this file instead of re-running migrations from scratch.
    """
    fd, path = tempfile.mkstemp(suffix="_template.db")
    os.close(fd)

    loop = asyncio.new_event_loop()
    try:

        async def _create():
            store = SignalStore(db_path=path)
            await store.initialize()
            await store.close()

        loop.run_until_complete(_create())
    finally:
        loop.close()

    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def pipeline_db(template_db_path):
    """Copy the template DB for per-test isolation."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    shutil.copy2(template_db_path, path)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# =============================================================================
# HELPERS
# =============================================================================


def _make_test_signal(
    *,
    signal_id: int = 1,
    canonical_key: str = "domain:test-dry-run.com",
    company_name: str = "DryRunTestCo",
    confidence: float = 0.8,
    company_id: str | None = None,
) -> StoredSignal:
    """Build a minimal StoredSignal suitable for _process_company tests."""
    return StoredSignal(
        id=signal_id,
        signal_type="github_trending",
        source_api="github",
        canonical_key=canonical_key,
        company_name=company_name,
        confidence=confidence,
        raw_data={"url": "https://github.com/test/repo", "stars": 500},
        detected_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        created_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        company_id=company_id,
        processing_status="pending",
        notion_page_id=None,
        processed_at=None,
        error_message=None,
    )


def _make_auto_push_verification() -> VerificationResult:
    """Build a VerificationResult with AUTO_PUSH decision.

    This ensures the code reaches the if/else branch under test
    (line 2065: verification.decision in AUTO_PUSH/NEEDS_REVIEW).
    """
    return VerificationResult(
        decision=PushDecision.AUTO_PUSH,
        verification_status=VerificationStatus.SINGLE_SOURCE,
        confidence_score=0.75,
        confidence_breakdown={
            "overall": 0.75,
            "base_score": 0.6,
            "multi_source_boost": 0.0,
            "convergence_boost": 0.15,
        },
        reason="Single strong signal with high confidence",
        suggested_status="Source",
        signals_used=["github_trending"],
        sources_checked=["github"],
        verification_details=[],
    )


def _make_reject_verification(reason: str = "Rejected by verification") -> VerificationResult:
    """Build a VerificationResult with REJECT decision."""
    return VerificationResult(
        decision=PushDecision.REJECT,
        verification_status=VerificationStatus.SINGLE_SOURCE,
        confidence_score=0.2,
        confidence_breakdown={"overall": 0.2},
        reason=reason,
        suggested_status="Rejected",
        signals_used=["github_trending"],
        sources_checked=["github"],
        verification_details=[],
    )


def _make_consolidated_signal() -> ConsolidatedSignal:
    """Build a minimal ConsolidatedSignal for thesis-filter tests."""
    return ConsolidatedSignal(
        canonical_key="domain:test-dry-run.com",
        company_name="DryRunTestCo",
        contributing_signal_ids=[1],
        signal_types=["github_trending"],
        source_apis=["github"],
        aggregated_confidence=0.8,
        earliest_detected_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        latest_detected_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        merged_raw_data={},
        descriptions=["Consumer wellness product with recurring usage"],
    )


def _make_thesis_result(routing: RoutingDecision) -> ThesisFilterResult:
    """Build a deterministic thesis-filter result for dry-run tests."""
    return ThesisFilterResult(
        routing=routing,
        keyword_score=0.85 if routing == RoutingDecision.QUALIFIED else 0.15,
        keyword_category="Consumer Health Tech",
        keyword_matches=["wellness"],
        negative_keywords=["enterprise"] if routing == RoutingDecision.REJECTED else [],
        llm_score=0.9 if routing == RoutingDecision.QUALIFIED else 0.1,
        llm_category="Consumer Health Tech",
        llm_rationale="Deterministic test fixture",
        llm_classification_status="success",
        confidence_adjustment=0.05 if routing == RoutingDecision.QUALIFIED else -0.05,
        thesis_fit=0.8 if routing == RoutingDecision.QUALIFIED else 0.1,
    )


def _build_pipeline(
    *,
    pipeline_db: str,
    with_notion: bool,
    use_claim_facts: bool = False,
) -> DiscoveryPipeline:
    """Build a DiscoveryPipeline with mocked internals.

    The pipeline is constructed with:
    - A mock store (all state-mutation methods are AsyncMock)
    - A mock verification gate returning AUTO_PUSH
    - Notion connector present or absent based on with_notion
    - All optional components disabled to minimize mocking surface
    """
    config = PipelineConfig(
        db_path=pipeline_db,
        notion_api_key="test-key" if with_notion else None,
        notion_database_id="test-db" if with_notion else None,
        # Disable optional components to keep tests focused
        use_gating=False,
        use_entities=False,
        use_asset_store=False,
        use_founder_scoring=False,
        use_velocity_tracking=False,
        use_consolidation=False,
        use_enrichment_boost=False,
        use_thesis_filter=False,
        use_competitor_detection=False,
        use_exit_predictor=False,
        use_investor_matching=False,
        use_phase_g_identity_resolution=False,
        use_claim_facts=use_claim_facts,
        use_shadow_entity_resolution=False,
        use_functional_schema=False,
        use_thin_files=False,
    )

    pipeline = DiscoveryPipeline(config)

    # -- Mock the store so no real DB calls happen --
    mock_store = MagicMock(spec=SignalStore)
    mock_store.check_suppression = AsyncMock(return_value=None)
    mock_store.mark_pushed = AsyncMock()
    mock_store.mark_rejected = AsyncMock()
    mock_store.mark_held = AsyncMock()
    mock_store.mark_queued = AsyncMock()
    mock_store.log_shadow_computation = AsyncMock()
    mock_store.update_signal_status = AsyncMock()
    mock_store.save_thesis_classification = AsyncMock()
    mock_store.save_confidence_ledger = AsyncMock(return_value=101)
    mock_store.store_exit_prediction = AsyncMock()
    mock_store.has_active_schema = AsyncMock(return_value=False)
    mock_store.save_functional_schema = AsyncMock()
    pipeline._store = mock_store

    # -- Mock the verification gate to return AUTO_PUSH --
    mock_gate = MagicMock(spec=VerificationGate)
    mock_gate.evaluate = MagicMock(return_value=_make_auto_push_verification())
    mock_gate.POLICY_VERSION = "test-policy"
    mock_gate.HIGH_CONFIDENCE_THRESHOLD = 0.7
    mock_gate.MEDIUM_CONFIDENCE_THRESHOLD = 0.4
    mock_gate.score_recalibration_factor = 1.0
    mock_gate.strict_mode = False
    pipeline._gate = mock_gate

    # -- Set up Notion connector mock (or None) --
    if with_notion:
        mock_notion = MagicMock()
        pipeline._notion = mock_notion
    else:
        pipeline._notion = None

    # -- Mock _push_to_notion so we never make real HTTP calls --
    pipeline._push_to_notion = AsyncMock(
        return_value={
            "status": "queued",
            "outbox_id": "out-001",
            "idempotency_key": "idem-001",
        }
    )

    # -- Provide a PipelineStats so shadow log writes don't crash --
    pipeline._run_stats = PipelineStats()

    # -- Disable feature-state persistence so tests can target specific writes --
    pipeline._feature_registry = MagicMock()
    pipeline._feature_registry.is_enabled.return_value = False

    # -- Disable notifier (Slack) --
    pipeline._notifier = None

    # -- Mark as initialized so _process_company doesn't complain --
    pipeline._initialized = True

    return pipeline


# =============================================================================
# TEST CASE 1: connector present + dry_run=True
# =============================================================================


class TestDryRunWithConnector:
    """When connector is present and dry_run=True, no state mutation occurs."""

    @pytest.mark.asyncio
    async def test_no_mark_pushed_called(self, pipeline_db):
        """mark_pushed should NOT be called during dry run."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=True)

        pipeline._store.mark_pushed.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mark_rejected_called(self, pipeline_db):
        """mark_rejected should NOT be called during dry run."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=True)

        pipeline._store.mark_rejected.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mark_queued_called(self, pipeline_db):
        """mark_queued should NOT be called during dry run."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=True)

        pipeline._store.mark_queued.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_to_notion_not_called(self, pipeline_db):
        """_push_to_notion should NOT be called during dry run."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=True)

        pipeline._push_to_notion.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_dry_run_notion_status(self, pipeline_db):
        """Result should indicate dry_run notion_status."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [_make_test_signal()]

        result = await pipeline._process_company(signals, dry_run=True)

        assert result["notion_status"] == "dry_run"

    @pytest.mark.asyncio
    async def test_decision_is_auto_push(self, pipeline_db):
        """Verification decision should still be AUTO_PUSH (not suppressed)."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [_make_test_signal()]

        result = await pipeline._process_company(signals, dry_run=True)

        assert result["decision"] == PushDecision.AUTO_PUSH


# =============================================================================
# TEST CASE 2: connector absent + dry_run=True
# =============================================================================


class TestDryRunWithoutConnector:
    """When connector is absent and dry_run=True, no state mutation occurs."""

    @pytest.mark.asyncio
    async def test_no_mark_pushed_called(self, pipeline_db):
        """mark_pushed should NOT be called (no connector, dry run)."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=True)

        pipeline._store.mark_pushed.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mark_rejected_called(self, pipeline_db):
        """mark_rejected should NOT be called (dry run overrides no-connector)."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=True)

        pipeline._store.mark_rejected.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mark_held_called(self, pipeline_db):
        """mark_held should NOT be called (dry run overrides no-connector)."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=True)

        pipeline._store.mark_held.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mark_queued_called(self, pipeline_db):
        """mark_queued should NOT be called (no connector, dry run)."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=True)

        pipeline._store.mark_queued.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_dry_run_notion_status(self, pipeline_db):
        """Result should indicate dry_run notion_status."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal()]

        result = await pipeline._process_company(signals, dry_run=True)

        assert result["notion_status"] == "dry_run"


# =============================================================================
# TEST CASE 3: connector present + dry_run=False
# =============================================================================


class TestNormalPushWithConnector:
    """When connector is present and dry_run=False, normal push path runs."""

    @pytest.mark.asyncio
    async def test_push_to_notion_called(self, pipeline_db):
        """_push_to_notion should be called for live runs with connector."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=False)

        pipeline._push_to_notion.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_queued_called(self, pipeline_db):
        """mark_queued should be called for each signal."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [_make_test_signal(signal_id=1), _make_test_signal(signal_id=2)]

        await pipeline._process_company(signals, dry_run=False)

        assert pipeline._store.mark_queued.call_count == 2

    @pytest.mark.asyncio
    async def test_mark_queued_includes_decision_metadata(self, pipeline_db):
        """mark_queued metadata should contain decision and confidence."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=False)

        call_args = pipeline._store.mark_queued.call_args
        # First positional arg is signal_id
        assert call_args[0][0] == 1
        # metadata keyword arg
        metadata = call_args[1]["metadata"]
        assert metadata["decision"] == "auto_push"
        assert metadata["confidence"] == 0.75
        assert metadata["status"] == "Source"
        assert "outbox_id" in metadata
        assert "idempotency_key" in metadata

    @pytest.mark.asyncio
    async def test_no_mark_pushed_called(self, pipeline_db):
        """mark_pushed should NOT be called (outbox pattern uses mark_queued)."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=False)

        pipeline._store.mark_pushed.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mark_rejected_called(self, pipeline_db):
        """mark_rejected should NOT be called on successful push path."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=False)

        pipeline._store.mark_rejected.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_queued_notion_status(self, pipeline_db):
        """Result notion_status should reflect the push outcome."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [_make_test_signal()]

        result = await pipeline._process_company(signals, dry_run=False)

        assert result["notion_status"] == "queued"


# =============================================================================
# TEST CASE 4: connector absent + dry_run=False
# =============================================================================


class TestNoConnectorLiveRun:
    """When connector is absent and dry_run=False, signals get mark_held."""

    @pytest.mark.asyncio
    async def test_mark_held_called(self, pipeline_db):
        """mark_held should be called for each signal."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal(signal_id=1), _make_test_signal(signal_id=2)]

        await pipeline._process_company(signals, dry_run=False)

        assert pipeline._store.mark_held.call_count == 2

    @pytest.mark.asyncio
    async def test_mark_held_reason_contains_no_connector(self, pipeline_db):
        """Held reason should indicate no_connector."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=False)

        call_args = pipeline._store.mark_held.call_args
        # reason may be positional or keyword depending on the call site
        if len(call_args[0]) > 1:
            reason = call_args[0][1]
        else:
            reason = call_args[1]["reason"]
        assert "no_connector" in reason

    @pytest.mark.asyncio
    async def test_mark_held_metadata_has_no_connector_flag(self, pipeline_db):
        """Held metadata should include no_connector: True."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=False)

        call_args = pipeline._store.mark_held.call_args
        metadata = call_args[1]["metadata"]
        assert metadata["no_connector"] is True
        assert metadata["decision"] == "auto_push"
        assert metadata["confidence"] == 0.75

    @pytest.mark.asyncio
    async def test_no_mark_pushed_called(self, pipeline_db):
        """mark_pushed should NOT be called (the old buggy behavior)."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=False)

        pipeline._store.mark_pushed.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mark_rejected_called(self, pipeline_db):
        """mark_rejected should NOT be called (held is non-terminal, not rejected)."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=False)

        pipeline._store.mark_rejected.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mark_queued_called(self, pipeline_db):
        """mark_queued should NOT be called (no connector to push to)."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=False)

        pipeline._store.mark_queued.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_to_notion_not_called(self, pipeline_db):
        """_push_to_notion should NOT be called without a connector."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal()]

        await pipeline._process_company(signals, dry_run=False)

        pipeline._push_to_notion.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_no_connector_notion_status(self, pipeline_db):
        """Result notion_status should be 'no_connector'."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [_make_test_signal()]

        result = await pipeline._process_company(signals, dry_run=False)

        assert result["notion_status"] == "no_connector"


# =============================================================================
# EDGE CASES
# =============================================================================


class TestDryRunEdgeCases:
    """Edge cases for the dry-run mutation fix."""

    @pytest.mark.asyncio
    async def test_multiple_signals_dry_run_none_mutated(self, pipeline_db):
        """With multiple signals, dry_run=True should not mutate any of them."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)
        signals = [
            _make_test_signal(signal_id=1),
            _make_test_signal(signal_id=2),
            _make_test_signal(signal_id=3),
        ]

        await pipeline._process_company(signals, dry_run=True)

        pipeline._store.mark_pushed.assert_not_called()
        pipeline._store.mark_rejected.assert_not_called()
        pipeline._store.mark_held.assert_not_called()
        pipeline._store.mark_queued.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_signals_no_connector_all_held(self, pipeline_db):
        """With multiple signals and no connector, all should be mark_held."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        signals = [
            _make_test_signal(signal_id=10),
            _make_test_signal(signal_id=20),
            _make_test_signal(signal_id=30),
        ]

        await pipeline._process_company(signals, dry_run=False)

        assert pipeline._store.mark_held.call_count == 3
        held_ids = [
            call[0][0] for call in pipeline._store.mark_held.call_args_list
        ]
        assert sorted(held_ids) == [10, 20, 30]

    @pytest.mark.asyncio
    async def test_suppressed_signal_not_affected_by_dry_run_fix(self, pipeline_db):
        """Suppressed signals should stay simulated during dry run."""
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=True)

        # Mock suppression to return a match
        suppression_entry = MagicMock()
        suppression_entry.notion_page_id = "page-existing"
        suppression_entry.status = "Source"
        pipeline._store.check_suppression = AsyncMock(return_value=suppression_entry)

        signals = [_make_test_signal()]

        result = await pipeline._process_company(signals, dry_run=True)

        assert result["decision"] == PushDecision.REJECT
        assert result["reason"] == "Suppressed"
        pipeline._store.mark_rejected.assert_not_called()


class TestDryRunRegressionCoverage:
    """Failing-first coverage for read-only process dry runs."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("routing", "expected_decision"),
        [
            (RoutingDecision.REJECTED, PushDecision.REJECT),
            (RoutingDecision.HELD, PushDecision.HOLD),
        ],
        ids=["thesis-rejected", "thesis-held"],
    )
    async def test_thesis_nonqualified_dry_run_does_not_persist(
        self,
        pipeline_db,
        routing: RoutingDecision,
        expected_decision: PushDecision,
    ):
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        pipeline._thesis_filter = MagicMock()
        pipeline._thesis_filter.classify = AsyncMock(return_value=_make_thesis_result(routing))

        result = await pipeline._process_company(
            [_make_test_signal()],
            dry_run=True,
            consolidated=_make_consolidated_signal(),
        )

        assert result["decision"] == expected_decision
        pipeline._store.save_thesis_classification.assert_not_called()
        pipeline._store.mark_rejected.assert_not_called()
        pipeline._store.update_signal_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_thesis_qualified_dry_run_does_not_persist_classification(self, pipeline_db):
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        pipeline._thesis_filter = MagicMock()
        pipeline._thesis_filter.classify = AsyncMock(
            return_value=_make_thesis_result(RoutingDecision.QUALIFIED)
        )

        result = await pipeline._process_company(
            [_make_test_signal()],
            dry_run=True,
            consolidated=_make_consolidated_signal(),
        )

        assert result["decision"] == PushDecision.AUTO_PUSH
        pipeline._store.save_thesis_classification.assert_not_called()

    @pytest.mark.asyncio
    async def test_verification_reject_dry_run_does_not_mark_rejected(self, pipeline_db):
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        pipeline._gate.evaluate = MagicMock(
            return_value=_make_reject_verification("verification reject")
        )

        result = await pipeline._process_company([_make_test_signal()], dry_run=True)

        assert result["decision"] == PushDecision.REJECT
        pipeline._store.mark_rejected.assert_not_called()

    @pytest.mark.asyncio
    async def test_confidence_ledger_not_persisted_in_dry_run(self, pipeline_db):
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)

        result = await pipeline._process_company([_make_test_signal()], dry_run=True)

        assert result["decision"] == PushDecision.AUTO_PUSH
        pipeline._store.save_confidence_ledger.assert_not_called()

    @pytest.mark.asyncio
    async def test_functional_schema_not_persisted_in_dry_run(self, pipeline_db):
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        schema = MagicMock()
        schema.customer_archetype = "consumer wellness buyer"
        schema.to_storage_dict.return_value = {
            "company_id": "company-test",
            "customer_archetype": "consumer wellness buyer",
        }
        pipeline._schema_extractor = MagicMock()
        pipeline._schema_extractor.extract = AsyncMock(return_value=schema)

        result = await pipeline._process_company(
            [_make_test_signal(company_id="company-test")],
            dry_run=True,
            consolidated=_make_consolidated_signal(),
        )

        assert result["schema_extracted"] is True
        pipeline._store.save_functional_schema.assert_not_called()

    @pytest.mark.asyncio
    async def test_exit_prediction_not_persisted_in_dry_run(self, pipeline_db):
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        exit_prediction = MagicMock()
        exit_prediction.deal_quality_score = 0.71
        exit_prediction.recommendation = "track"
        pipeline._exit_predictor = MagicMock()
        pipeline._exit_predictor.predict = AsyncMock(return_value=exit_prediction)

        result = await pipeline._process_company(
            [_make_test_signal()],
            dry_run=True,
            consolidated=_make_consolidated_signal(),
        )

        assert result["decision"] == PushDecision.AUTO_PUSH
        pipeline._exit_predictor.predict.assert_awaited_once()
        pipeline._store.store_exit_prediction.assert_not_called()

    @pytest.mark.asyncio
    async def test_investor_matching_dry_run_forces_no_persistence(self, pipeline_db):
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        investor_match_result = MagicMock()
        investor_match_result.matches = []
        pipeline._investor_matcher = MagicMock()
        pipeline._investor_matcher.match = AsyncMock(return_value=investor_match_result)
        consolidated = _make_consolidated_signal()
        consolidated.description = "Consumer wellness product with recurring usage"

        result = await pipeline._process_company(
            [_make_test_signal()],
            dry_run=True,
            consolidated=consolidated,
        )

        assert result["decision"] == PushDecision.AUTO_PUSH
        pipeline._investor_matcher.match.assert_awaited_once()
        assert pipeline._investor_matcher.match.await_args.kwargs["save_results"] is False

    @pytest.mark.asyncio
    async def test_process_stage_exception_dry_run_does_not_mark_rejected(self, pipeline_db):
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        pipeline._store.get_pending_signals = AsyncMock(return_value=[_make_test_signal()])
        pipeline._process_company = AsyncMock(side_effect=RuntimeError("boom"))

        stats = await pipeline._process_signals_stage(dry_run=True)

        assert stats["processed"] == 0
        pipeline._store.mark_rejected.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_pending_dry_run_skips_run_history_tracking(self, pipeline_db):
        pipeline = _build_pipeline(pipeline_db=pipeline_db, with_notion=False)
        expected_stats = {
            "processed": 0,
            "auto_push": 0,
            "needs_review": 0,
            "held": 0,
            "rejected": 0,
            "prospects_created": 0,
            "prospects_updated": 0,
            "prospects_skipped": 0,
        }
        pipeline.initialize = AsyncMock()
        pipeline._process_signals_stage = AsyncMock(return_value=expected_stats)
        pipeline._begin_run_tracking = AsyncMock()
        pipeline._end_run_tracking = AsyncMock()
        pipeline._drain_notion_outbox = AsyncMock()

        result = await pipeline.process_pending(dry_run=True)

        assert result == expected_stats
        pipeline._begin_run_tracking.assert_not_called()
        pipeline._end_run_tracking.assert_not_called()
        pipeline._drain_notion_outbox.assert_not_called()


# =============================================================================
# TEST CASE 5: claim_facts enabled + dry_run=True
# =============================================================================


class TestClaimFactsDryRun:
    """When claim facts are enabled, dry_run=True should extract but never persist."""

    @pytest.mark.asyncio
    async def test_extracts_but_does_not_persist(self, pipeline_db):
        pipeline = _build_pipeline(
            pipeline_db=pipeline_db,
            with_notion=False,
            use_claim_facts=True,
        )

        # Provide a single pending signal
        pipeline._store.get_pending_signals = AsyncMock(return_value=[_make_test_signal()])

        # Avoid exercising _process_company internals; we only care about the
        # post-consolidation claim facts extraction/persistence behavior.
        pipeline._process_company = AsyncMock(
            return_value={
                "decision": PushDecision.AUTO_PUSH,
                "notion_status": "dry_run",
            }
        )

        # Provide a deterministic ConsolidatedSignal for extraction input
        consolidated = ConsolidatedSignal(
            canonical_key="domain:test-dry-run.com",
            company_name="DryRunTestCo",
            contributing_signal_ids=[1],
            signal_types=["github_trending"],
            source_apis=["github"],
            aggregated_confidence=0.8,
            earliest_detected_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
            latest_detected_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
            merged_raw_data={},
        )
        pipeline._consolidator = MagicMock()
        pipeline._consolidator.consolidate = MagicMock(return_value=consolidated)

        # Mock claim extractor + store
        mock_extractor = MagicMock()
        mock_extractor.extract_batch = MagicMock(
            return_value={
                "entity-123": [
                    ClaimFact(
                        entity_id="entity-123",
                        predicate="company_name",
                        value_json='"DryRunTestCo"',
                        source_tier=3,
                        confidence=0.8,
                        valid_from="2026-01-15T00:00:00+00:00",
                        observed_at="2026-01-15T00:00:00+00:00",
                    )
                ]
            }
        )
        pipeline._claim_extractor = mock_extractor

        mock_claim_store = MagicMock()
        mock_claim_store.save_fact = AsyncMock()
        pipeline._claim_fact_store = mock_claim_store

        # Dry run: extraction should happen; persistence should not
        stats = await pipeline._process_signals_stage(dry_run=True)

        mock_extractor.extract_batch.assert_called_once()
        mock_claim_store.save_fact.assert_not_called()
        assert stats["claim_facts_extracted"] == 1
        assert stats["claim_facts_saved"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

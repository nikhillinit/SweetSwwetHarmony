"""Tests for confidence ledger persistence in _process_company().

Verifies:
- save_confidence_ledger is called after gate.evaluate()
- Non-fatal: pipeline continues even if ledger save fails
- All decision types (auto_push, needs_review, hold, reject) are persisted
- Hard-kill produces breakdown_kind='hard_kill'
- Conflicting signals produce needs_review with reason
- execution_id is passed from pipeline
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.signal_store import SignalStore, StoredSignal
from verification.verification_gate_v2 import (
    PushDecision,
    VerificationGate,
    VerificationResult,
    VerificationStatus,
)
from workflows.pipeline import DiscoveryPipeline, PipelineConfig, PipelineStats


# ---------------------------------------------------------------------------
# Shared template DB (migrations run once per module)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def template_db_path():
    fd, path = tempfile.mkstemp(suffix="_ledger_template.db")
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
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    shutil.copy2(template_db_path, path)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(
    signal_id: int = 1,
    canonical_key: str = "domain:ledger-test.com",
    company_id: str = None,
) -> StoredSignal:
    return StoredSignal(
        id=signal_id,
        signal_type="github_trending",
        source_api="github",
        canonical_key=canonical_key,
        company_name="LedgerTestCo",
        confidence=0.8,
        raw_data={"url": "https://github.com/test"},
        detected_at=datetime(2026, 3, 14, tzinfo=timezone.utc),
        created_at=datetime(2026, 3, 14, tzinfo=timezone.utc),
        company_id=company_id,
        processing_status="pending",
        notion_page_id=None,
        processed_at=None,
        error_message=None,
    )


def _make_verification(
    decision=PushDecision.AUTO_PUSH,
    status=VerificationStatus.MULTI_SOURCE,
    score=0.78,
    breakdown=None,
    reason="High confidence with 2 sources",
    details=None,
) -> VerificationResult:
    if breakdown is None:
        breakdown = {
            "overall": score,
            "base_score": 0.35,
            "multi_source_boost": 1.15,
            "convergence_boost": 1.0,
            "founder_boost": 0.0,
            "velocity_boost": 0.0,
            "enrichment_boost": 0.0,
            "community_sentiment_boost": 0.0,
            "score_recalibration_factor": 1.35,
            "policy_version": "v2.1",
            "signals_contributing": 2,
            "sources_checked": 2,
            "sources": ["github"],
            "signal_details": [],
            "calculation_method": "glass_ai_v2",
            "calculated_at": "2026-03-14T00:00:00+00:00",
        }
    return VerificationResult(
        decision=decision,
        verification_status=status,
        confidence_score=score,
        confidence_breakdown=breakdown,
        reason=reason,
        suggested_status="Source" if decision == PushDecision.AUTO_PUSH else "",
        signals_used=["github_trending"],
        sources_checked=["github"],
        verification_details=details or [],
    )


def _build_pipeline(pipeline_db: str, verification: VerificationResult = None) -> DiscoveryPipeline:
    config = PipelineConfig(
        db_path=pipeline_db,
        notion_api_key="test-key",
        notion_database_id="test-db",
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
        use_claim_facts=False,
        use_shadow_entity_resolution=False,
        use_functional_schema=False,
        use_thin_files=False,
    )
    pipeline = DiscoveryPipeline(config)

    mock_store = MagicMock(spec=SignalStore)
    mock_store.check_suppression = AsyncMock(return_value=None)
    mock_store.mark_pushed = AsyncMock()
    mock_store.mark_rejected = AsyncMock()
    mock_store.mark_held = AsyncMock()
    mock_store.mark_queued = AsyncMock()
    mock_store.log_shadow_computation = AsyncMock()
    mock_store.update_signal_status = AsyncMock()
    mock_store.save_confidence_ledger = AsyncMock(return_value=42)
    pipeline._store = mock_store

    if verification is None:
        verification = _make_verification()
    mock_gate = MagicMock(spec=VerificationGate)
    mock_gate.evaluate = MagicMock(return_value=verification)
    mock_gate.POLICY_VERSION = "v2.1"
    mock_gate.HIGH_CONFIDENCE_THRESHOLD = 0.7
    mock_gate.MEDIUM_CONFIDENCE_THRESHOLD = 0.4
    mock_gate.score_recalibration_factor = 1.35
    mock_gate.strict_mode = False
    pipeline._gate = mock_gate

    pipeline._notion = MagicMock()
    pipeline._push_to_notion = AsyncMock(
        return_value={"status": "queued", "outbox_id": "out-001", "idempotency_key": "idem-001"}
    )
    pipeline._run_stats = PipelineStats()
    pipeline._notifier = None
    pipeline._initialized = True
    pipeline._execution_id = "test-exec-001"

    return pipeline


# ===========================================================================
# Tests
# ===========================================================================

class TestPipelineLedger:

    @pytest.mark.asyncio
    async def test_pipeline_persists_ledger_after_evaluate(self, pipeline_db):
        pipeline = _build_pipeline(pipeline_db)
        signals = [_make_signal()]

        await pipeline._process_company(signals, dry_run=False)

        pipeline._store.save_confidence_ledger.assert_called_once()
        kwargs = pipeline._store.save_confidence_ledger.call_args.kwargs
        assert kwargs["canonical_key"] == "domain:ledger-test.com"
        assert kwargs["evaluation_origin"] == "pipeline"
        assert kwargs["policy_version"] == "v2.1"
        assert kwargs["signal_ids"] == [1]
        assert kwargs["routing_config"]["high_threshold"] == 0.7
        assert kwargs["routing_config"]["strict_mode"] is False
        assert kwargs["execution_id"] == "test-exec-001"

    @pytest.mark.asyncio
    async def test_ledger_save_failure_nonfatal(self, pipeline_db):
        pipeline = _build_pipeline(pipeline_db)
        pipeline._store.save_confidence_ledger = AsyncMock(
            side_effect=RuntimeError("DB locked")
        )
        signals = [_make_signal()]

        # Should NOT raise — non-fatal
        result = await pipeline._process_company(signals, dry_run=False)
        assert result is not None

    @pytest.mark.asyncio
    async def test_all_decisions_persisted(self, pipeline_db):
        """All four decision types should trigger ledger save."""
        for decision in [PushDecision.AUTO_PUSH, PushDecision.NEEDS_REVIEW,
                         PushDecision.HOLD, PushDecision.REJECT]:
            verification = _make_verification(
                decision=decision,
                reason=f"Test {decision.value}",
            )
            pipeline = _build_pipeline(pipeline_db, verification=verification)
            signals = [_make_signal()]
            await pipeline._process_company(signals, dry_run=False)
            pipeline._store.save_confidence_ledger.assert_called_once()

    @pytest.mark.asyncio
    async def test_hard_kill_persisted_with_reason(self, pipeline_db):
        verification = _make_verification(
            decision=PushDecision.REJECT,
            status=VerificationStatus.UNVERIFIED,
            score=0.0,
            breakdown={"hard_kill": True, "kill_signal": "company_dissolved"},
            reason="Hard kill signal: company_dissolved",
        )
        pipeline = _build_pipeline(pipeline_db, verification=verification)
        signals = [_make_signal()]

        await pipeline._process_company(signals, dry_run=False)

        kwargs = pipeline._store.save_confidence_ledger.call_args.kwargs
        vr = kwargs["verification_result"]
        assert vr.decision == PushDecision.REJECT
        assert vr.confidence_breakdown.get("hard_kill") is True
        assert "company_dissolved" in vr.reason

    @pytest.mark.asyncio
    async def test_conflicting_signals_reason(self, pipeline_db):
        verification = _make_verification(
            decision=PushDecision.NEEDS_REVIEW,
            status=VerificationStatus.CONFLICTING,
            score=0.55,
            reason="Conflicting signals detected",
        )
        pipeline = _build_pipeline(pipeline_db, verification=verification)
        signals = [_make_signal()]

        await pipeline._process_company(signals, dry_run=False)

        kwargs = pipeline._store.save_confidence_ledger.call_args.kwargs
        vr = kwargs["verification_result"]
        assert vr.verification_status == VerificationStatus.CONFLICTING
        assert "Conflicting" in vr.reason

    @pytest.mark.asyncio
    async def test_execution_id_passed_from_pipeline(self, pipeline_db):
        pipeline = _build_pipeline(pipeline_db)
        pipeline._execution_id = "my-run-id-xyz"
        signals = [_make_signal()]

        await pipeline._process_company(signals, dry_run=False)

        kwargs = pipeline._store.save_confidence_ledger.call_args.kwargs
        assert kwargs["execution_id"] == "my-run-id-xyz"

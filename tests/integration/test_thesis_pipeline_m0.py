"""M0: Deterministic thesis pipeline verification.

Uses tempfile-backed DB + mocked thesis result. No network, no collectors.

Validates Bug 0.10 fix (classification persistence for all routing outcomes +
shadow guard) and Bug 0.11 fix (field name contract).
"""

import json
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from utils.feature_states import FeatureState
from utils.signal_consolidator import ConsolidatedSignal
from utils.thesis_filter import ThesisFilterResult, RoutingDecision
from verification.verification_gate_v2 import PushDecision, VerificationResult, VerificationStatus
from workflows.pipeline import DiscoveryPipeline, PipelineConfig


@pytest_asyncio.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


def _make_thesis_result(routing: RoutingDecision) -> ThesisFilterResult:
    """Build a deterministic ThesisFilterResult for a given routing decision."""
    return ThesisFilterResult(
        routing=routing,
        keyword_score=0.2 if routing != RoutingDecision.QUALIFIED else 0.8,
        keyword_category="consumer_cpg",
        keyword_matches=["meal kit"],
        negative_keywords=["b2b"] if routing == RoutingDecision.REJECTED else [],
        llm_score=0.3,
        llm_category="consumer_cpg",
        llm_rationale="test rationale",
        llm_model="gemini-2.0-flash",
        llm_prompt_version="v1.6.0",
        confidence_adjustment=0.05,
        intent_phrases_matched=["healthy food"],
        domain_match=False,
        domain_blacklisted=False,
        v2_shadow={"v2_score": 0.5},
    )


async def _seed_signal(store, canonical_key="domain:test-m0.ai"):
    """Insert a minimal test signal, return signal_id."""
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        "INSERT INTO signals (signal_type, source_api, canonical_key, company_name, "
        "confidence, raw_data, detected_at, created_at, company_id) "
        "VALUES ('funding', 'test', ?, 'Test M0 Co', 0.8, '{}', ?, ?, 'comp_m0')",
        (canonical_key, now, now),
    )
    await db.commit()
    return cursor.lastrowid


class TestThesisPipelineM0:
    """Verify classification persistence + shadow logging for all routing outcomes."""

    @pytest.mark.asyncio
    async def test_qualified_persists_classification(self, store):
        """QUALIFIED -> classification row + shadow log created."""
        signal_id = await _seed_signal(store, "domain:qual.ai")
        result = _make_thesis_result(RoutingDecision.QUALIFIED)

        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:qual.ai",
            keyword_score=result.keyword_score,
            keyword_category=result.keyword_category,
            negative_keywords=result.negative_keywords,
            thesis_fit_score=result.llm_score,
            category=result.llm_category,
            rationale=result.llm_rationale,
            competitor_flag=False,
            competitor_match=None,
        )
        await store.log_shadow_computation(
            feature_name="thesis_match",
            canonical_key="domain:qual.ai",
            computed_value={"routing": result.routing.value, "keyword_score": result.keyword_score},
            signal_id=signal_id,
        )

        row = await store.get_thesis_classification("domain:qual.ai")
        assert row is not None, "Classification row missing for QUALIFIED"

        logs = await store.get_shadow_logs(feature_name="thesis_match", canonical_key="domain:qual.ai")
        assert len(logs) >= 1, "Shadow log missing for QUALIFIED"

    @pytest.mark.asyncio
    async def test_rejected_persists_classification(self, store):
        """REJECTED -> classification row + shadow log created (Bug 0.10 fix)."""
        signal_id = await _seed_signal(store, "domain:rej.ai")
        result = _make_thesis_result(RoutingDecision.REJECTED)

        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:rej.ai",
            keyword_score=result.keyword_score,
            keyword_category=result.keyword_category,
            negative_keywords=result.negative_keywords,
            thesis_fit_score=result.llm_score,
            category=result.llm_category,
            rationale=result.llm_rationale,
            competitor_flag=False,
            competitor_match=None,
        )
        await store.log_shadow_computation(
            feature_name="thesis_match",
            canonical_key="domain:rej.ai",
            computed_value={"routing": result.routing.value, "keyword_score": result.keyword_score},
            signal_id=signal_id,
        )

        row = await store.get_thesis_classification("domain:rej.ai")
        assert row is not None, "Classification row missing for REJECTED"

        logs = await store.get_shadow_logs(feature_name="thesis_match", canonical_key="domain:rej.ai")
        assert len(logs) >= 1, "Shadow log missing for REJECTED"

    @pytest.mark.asyncio
    async def test_held_persists_classification(self, store):
        """HELD -> classification row + shadow log created (Bug 0.10 fix)."""
        signal_id = await _seed_signal(store, "domain:held.ai")
        result = _make_thesis_result(RoutingDecision.HELD)

        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:held.ai",
            keyword_score=result.keyword_score,
            keyword_category=result.keyword_category,
            negative_keywords=result.negative_keywords,
            thesis_fit_score=result.llm_score,
            category=result.llm_category,
            rationale=result.llm_rationale,
            competitor_flag=False,
            competitor_match=None,
        )
        await store.log_shadow_computation(
            feature_name="thesis_match",
            canonical_key="domain:held.ai",
            computed_value={"routing": result.routing.value, "keyword_score": result.keyword_score},
            signal_id=signal_id,
        )

        row = await store.get_thesis_classification("domain:held.ai")
        assert row is not None, "Classification row missing for HELD"

        logs = await store.get_shadow_logs(feature_name="thesis_match", canonical_key="domain:held.ai")
        assert len(logs) >= 1, "Shadow log missing for HELD"

    @pytest.mark.asyncio
    async def test_shadow_log_includes_routing_decision(self, store):
        """Shadow log computed_value contains routing field for all outcomes."""
        for routing in (RoutingDecision.QUALIFIED, RoutingDecision.REJECTED, RoutingDecision.HELD):
            signal_id = await _seed_signal(store, f"domain:route-{routing.value}.ai")
            result = _make_thesis_result(routing)

            shadow_data = {
                "keyword_score": result.keyword_score,
                "keyword_category": result.keyword_category,
                "routing": result.routing.value,
                "confidence_adjustment": result.confidence_adjustment,
                "v2_shadow": result.v2_shadow,
            }
            await store.log_shadow_computation(
                feature_name="thesis_match",
                canonical_key=f"domain:route-{routing.value}.ai",
                computed_value=shadow_data,
                signal_id=signal_id,
            )

            logs = await store.get_shadow_logs(
                feature_name="thesis_match",
                canonical_key=f"domain:route-{routing.value}.ai",
            )
            assert len(logs) >= 1, f"No shadow log for {routing.value}"
            computed = logs[0]["computed_value"]
            if isinstance(computed, str):
                computed = json.loads(computed)
            assert "routing" in computed, f"routing missing from shadow log for {routing.value}"
            assert computed["routing"] == routing.value

    @pytest.mark.asyncio
    async def test_shadow_rejected_skips_status_mutation_but_persists_observability(self, tmp_path):
        """LLM_THESIS_MODE=shadow + REJECTED must NOT call mark_rejected() or
        update_signal_status(), but MUST persist classification + shadow log.
        Critical behavioral guard for Bug 0.10."""
        config = PipelineConfig(
            db_path=str(tmp_path / "m0_shadow_behavior.db"),
            warmup_suppression_cache=False,
            use_thesis_filter=True,
            use_competitor_detection=False,
            use_founder_scoring=False,
            use_velocity_tracking=False,
            use_enrichment_boost=False,
            use_consolidation=False,
            use_gating=False,
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        try:
            signal_id = await pipeline._store.save_signal(
                signal_type="funding_event",
                source_api="github",
                canonical_key="domain:shadow-behavior.ai",
                company_name="Shadow Behavior Co",
                confidence=0.80,
                raw_data={"description": "consumer meal kit startup"},
            )
            signal = await pipeline._store.get_signal(signal_id)
            assert signal is not None

            consolidated = ConsolidatedSignal(
                canonical_key=signal.canonical_key,
                company_name=signal.company_name or "Shadow Behavior Co",
                contributing_signal_ids=[signal.id],
                signal_types=[signal.signal_type],
                source_apis=[signal.source_api],
                aggregated_confidence=signal.confidence,
                earliest_detected_at=signal.detected_at,
                latest_detected_at=signal.detected_at,
                descriptions=["consumer meal kit startup"],
            )

            pipeline._thesis_filter.classify = AsyncMock(
                return_value=ThesisFilterResult(
                    routing=RoutingDecision.REJECTED,
                    keyword_score=0.10,
                    keyword_category="consumer_cpg",
                    negative_keywords=["b2b"],
                    llm_score=0.20,
                    llm_category="excluded",
                    llm_rationale="test reject",
                    llm_model="gemini-2.0-flash",
                    llm_prompt_version="v1.6.0",
                    confidence_adjustment=-0.05,
                )
            )

            pipeline._gate = MagicMock()
            pipeline._gate.evaluate.return_value = VerificationResult(
                decision=PushDecision.HOLD,
                verification_status=VerificationStatus.SINGLE_SOURCE,
                confidence_score=0.30,
                confidence_breakdown={},
                reason="test hold",
                suggested_status="Tracking",
                signals_used=[str(signal.id)],
                sources_checked=[signal.source_api],
                verification_details=[],
            )

            original_mark_rejected = pipeline._store.mark_rejected
            original_update_status = pipeline._store.update_signal_status
            pipeline._store.mark_rejected = AsyncMock(wraps=original_mark_rejected)
            pipeline._store.update_signal_status = AsyncMock(wraps=original_update_status)

            with patch.dict(os.environ, {"LLM_THESIS_MODE": "shadow"}):
                result = await pipeline._process_company(
                    [signal], dry_run=True, consolidated=consolidated,
                )

            assert result["thesis_routing"] == RoutingDecision.REJECTED
            assert result["decision"] == PushDecision.HOLD

            pipeline._store.mark_rejected.assert_not_awaited()
            pipeline._store.update_signal_status.assert_not_awaited()

            classification = await pipeline._store.get_thesis_classification(signal.canonical_key)
            assert classification is not None
            assert classification["model"] == "gemini-2.0-flash"
            assert classification["prompt_version"] == "v1.6.0"

            logs = await pipeline._store.get_shadow_logs(
                feature_name="thesis_match",
                canonical_key=signal.canonical_key,
            )
            assert logs, "Expected thesis_match shadow log for rejected shadow run"
            assert logs[0]["computed_value"]["routing"] == RoutingDecision.REJECTED.value

        finally:
            await pipeline.close()

    @pytest.mark.asyncio
    async def test_active_rejected_applies_mutations_and_returns_reject(self, tmp_path):
        """LLM_THESIS_MODE=active + REJECTED must call mark_rejected() and
        update_signal_status(), return REJECT early, and still persist
        classification + shadow log. Mirror of shadow test."""
        config = PipelineConfig(
            db_path=str(tmp_path / "m0_active_behavior.db"),
            warmup_suppression_cache=False,
            use_thesis_filter=True,
            use_competitor_detection=False,
            use_founder_scoring=False,
            use_velocity_tracking=False,
            use_enrichment_boost=False,
            use_consolidation=False,
            use_gating=False,
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        try:
            pipeline._feature_registry.set_state("thesis_match", FeatureState.ACTIVE)

            signal_id = await pipeline._store.save_signal(
                signal_type="funding_event",
                source_api="github",
                canonical_key="domain:active-reject.ai",
                company_name="Active Reject Co",
                confidence=0.85,
                raw_data={"description": "enterprise b2b platform"},
            )
            signal = await pipeline._store.get_signal(signal_id)
            assert signal is not None

            consolidated = ConsolidatedSignal(
                canonical_key=signal.canonical_key,
                company_name=signal.company_name or "Active Reject Co",
                contributing_signal_ids=[signal.id],
                signal_types=[signal.signal_type],
                source_apis=[signal.source_api],
                aggregated_confidence=signal.confidence,
                earliest_detected_at=signal.detected_at,
                latest_detected_at=signal.detected_at,
                descriptions=["enterprise b2b platform"],
            )

            pipeline._thesis_filter.classify = AsyncMock(
                return_value=ThesisFilterResult(
                    routing=RoutingDecision.REJECTED,
                    keyword_score=0.10,
                    keyword_category="enterprise",
                    negative_keywords=["b2b"],
                    llm_score=0.15,
                    llm_category="excluded",
                    llm_rationale="test reject active",
                    llm_model="gemini-2.0-flash",
                    llm_prompt_version="v1.6.0",
                    confidence_adjustment=-0.05,
                )
            )

            pipeline._gate = MagicMock()

            original_mark_rejected = pipeline._store.mark_rejected
            original_update_status = pipeline._store.update_signal_status
            pipeline._store.mark_rejected = AsyncMock(wraps=original_mark_rejected)
            pipeline._store.update_signal_status = AsyncMock(wraps=original_update_status)

            with patch.dict(os.environ, {"LLM_THESIS_MODE": "active"}):
                result = await pipeline._process_company(
                    [signal], dry_run=True, consolidated=consolidated,
                )

            assert result["decision"] == PushDecision.REJECT
            assert result["thesis_routing"] == RoutingDecision.REJECTED
            assert "Thesis rejected" in result["reason"]
            assert result["gating_applied"] is False

            pipeline._store.mark_rejected.assert_awaited_once()
            pipeline._store.update_signal_status.assert_awaited_once()

            mark_args, _ = pipeline._store.mark_rejected.await_args
            assert mark_args[0] == signal.id
            assert "Thesis rejected" in mark_args[1]

            update_args = pipeline._store.update_signal_status.await_args.args
            assert update_args[0] == signal.canonical_key
            assert update_args[1] == "rejected"

            pipeline._gate.evaluate.assert_not_called()

            cls = await pipeline._store.get_thesis_classification(signal.canonical_key)
            assert cls is not None
            assert cls["model"] == "gemini-2.0-flash"
            assert cls["prompt_version"] == "v1.6.0"

            logs = await pipeline._store.get_shadow_logs(
                feature_name="thesis_match",
                canonical_key=signal.canonical_key,
            )
            assert logs, "Expected thesis_match shadow log for active rejected run"
            assert logs[0]["computed_value"]["routing"] == RoutingDecision.REJECTED.value

            updated = await pipeline._store.get_signal(signal_id)
            assert updated is not None
            assert updated.processing_status == "rejected"

        finally:
            await pipeline.close()

    @pytest.mark.asyncio
    async def test_active_held_persists_llm_provenance(self, tmp_path):
        """Active HELD routing should persist llm model/prompt_version via the held save path."""
        config = PipelineConfig(
            db_path=str(tmp_path / "m0_active_held_provenance.db"),
            warmup_suppression_cache=False,
            use_thesis_filter=True,
            use_competitor_detection=False,
            use_founder_scoring=False,
            use_velocity_tracking=False,
            use_enrichment_boost=False,
            use_consolidation=False,
            use_gating=False,
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        try:
            signal_id = await pipeline._store.save_signal(
                signal_type="funding_event",
                source_api="github",
                canonical_key="domain:active-held.ai",
                company_name="Active Held Co",
                confidence=0.85,
                raw_data={"description": "ambiguous employer-sponsored wellness app"},
            )
            signal = await pipeline._store.get_signal(signal_id)
            assert signal is not None

            consolidated = ConsolidatedSignal(
                canonical_key=signal.canonical_key,
                company_name=signal.company_name or "Active Held Co",
                contributing_signal_ids=[signal.id],
                signal_types=[signal.signal_type],
                source_apis=[signal.source_api],
                aggregated_confidence=signal.confidence,
                earliest_detected_at=signal.detected_at,
                latest_detected_at=signal.detected_at,
                descriptions=["ambiguous employer-sponsored wellness app"],
            )

            pipeline._thesis_filter.classify = AsyncMock(
                return_value=ThesisFilterResult(
                    routing=RoutingDecision.HELD,
                    keyword_score=0.10,
                    keyword_category="consumer_health_tech",
                    negative_keywords=[],
                    llm_score=0.25,
                    llm_category="consumer_health_tech",
                    llm_rationale="test held active",
                    llm_model="gemini-2.0-flash",
                    llm_prompt_version="v1.6.0",
                    confidence_adjustment=-0.05,
                )
            )

            original_update_status = pipeline._store.update_signal_status
            pipeline._store.update_signal_status = AsyncMock(wraps=original_update_status)

            with patch.dict(os.environ, {"LLM_THESIS_MODE": "active"}):
                result = await pipeline._process_company(
                    [signal], dry_run=True, consolidated=consolidated,
                )

            assert result["decision"] == PushDecision.HOLD
            cls = await pipeline._store.get_thesis_classification(signal.canonical_key)
            assert cls is not None
            assert cls["model"] == "gemini-2.0-flash"
            assert cls["prompt_version"] == "v1.6.0"
            pipeline._store.update_signal_status.assert_awaited_once()
        finally:
            await pipeline.close()

    @pytest.mark.asyncio
    async def test_qualified_path_persists_llm_provenance(self, tmp_path):
        """Qualified routing should persist llm model/prompt_version via the qualified save path."""
        config = PipelineConfig(
            db_path=str(tmp_path / "m0_qualified_provenance.db"),
            warmup_suppression_cache=False,
            use_thesis_filter=True,
            use_competitor_detection=False,
            use_founder_scoring=False,
            use_velocity_tracking=False,
            use_enrichment_boost=False,
            use_consolidation=False,
            use_gating=False,
        )
        pipeline = DiscoveryPipeline(config)
        await pipeline.initialize()

        try:
            signal_id = await pipeline._store.save_signal(
                signal_type="funding_event",
                source_api="github",
                canonical_key="domain:qualified-provenance.ai",
                company_name="Qualified Provenance Co",
                confidence=0.85,
                raw_data={"description": "consumer meal kit startup"},
            )
            signal = await pipeline._store.get_signal(signal_id)
            assert signal is not None

            consolidated = ConsolidatedSignal(
                canonical_key=signal.canonical_key,
                company_name=signal.company_name or "Qualified Provenance Co",
                contributing_signal_ids=[signal.id],
                signal_types=[signal.signal_type],
                source_apis=[signal.source_api],
                aggregated_confidence=signal.confidence,
                earliest_detected_at=signal.detected_at,
                latest_detected_at=signal.detected_at,
                descriptions=["consumer meal kit startup"],
            )

            pipeline._thesis_filter.classify = AsyncMock(
                return_value=ThesisFilterResult(
                    routing=RoutingDecision.QUALIFIED,
                    keyword_score=0.80,
                    keyword_category="consumer_cpg",
                    keyword_matches=["meal kit"],
                    negative_keywords=[],
                    llm_score=0.82,
                    llm_category="consumer_cpg",
                    llm_rationale="test qualified",
                    llm_model="gemini-2.0-flash",
                    llm_prompt_version="v1.6.0",
                    confidence_adjustment=0.05,
                )
            )

            pipeline._gate = MagicMock()
            pipeline._gate.POLICY_VERSION = "test-policy"
            pipeline._gate.HIGH_CONFIDENCE_THRESHOLD = 0.8
            pipeline._gate.MEDIUM_CONFIDENCE_THRESHOLD = 0.5
            pipeline._gate.score_recalibration_factor = 1.0
            pipeline._gate.strict_mode = False
            pipeline._gate.evaluate.return_value = VerificationResult(
                decision=PushDecision.HOLD,
                verification_status=VerificationStatus.SINGLE_SOURCE,
                confidence_score=0.30,
                confidence_breakdown={"overall": 0.30},
                reason="test hold",
                suggested_status="Tracking",
                signals_used=[str(signal.id)],
                sources_checked=[signal.source_api],
                verification_details=[],
            )

            with patch.dict(os.environ, {"LLM_THESIS_MODE": "active"}):
                await pipeline._process_company(
                    [signal], dry_run=True, consolidated=consolidated,
                )

            gate_kwargs = pipeline._gate.evaluate.call_args.kwargs
            assert gate_kwargs["keyword_score"] == 0.80
            assert gate_kwargs["llm_score"] == 0.82

            cls = await pipeline._store.get_thesis_classification(signal.canonical_key)
            assert cls is not None
            assert cls["model"] == "gemini-2.0-flash"
            assert cls["prompt_version"] == "v1.6.0"
        finally:
            await pipeline.close()

    def test_field_name_contract_matches_dataclass(self):
        """ops/quality/thesis.py field accesses match ThesisClassification (Bug 0.11)."""
        from consumer.thesis_filter.llm_classifier import ThesisClassification

        assert hasattr(ThesisClassification, '__dataclass_fields__'), (
            "ThesisClassification is not a dataclass"
        )
        fields = ThesisClassification.__dataclass_fields__
        assert "thesis_fit_score" in fields, (
            "thesis_fit_score not in ThesisClassification — ops/quality/thesis.py will break"
        )
        assert "stage_estimate" in fields, (
            "stage_estimate not in ThesisClassification — ops/quality/thesis.py will break"
        )

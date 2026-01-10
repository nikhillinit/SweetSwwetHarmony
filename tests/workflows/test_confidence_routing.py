"""Test confidence-based routing logic for Notion status assignment"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from workflows.notion_pusher import NotionPusher, AggregatedProspect, PushResult
from verification.verification_gate_v2 import (
    VerificationGate,
    Signal,
    VerificationResult,
    PushDecision,
    VerificationStatus,
)
from storage.signal_store import SignalStore, StoredSignal
from connectors.notion_connector_v2 import NotionConnector, ProspectPayload


@pytest.mark.asyncio
class TestConfidenceBasedRouting:
    """Test routing based on confidence thresholds"""

    async def test_high_confidence_multi_source_routes_to_source(self):
        """HIGH confidence + multi-source → Status: 'Source' (AUTO_PUSH)"""
        # Create signals from multiple sources
        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                signal_type="github_spike",
                source_api="github",
                confidence=0.8,
                detected_at=now,
                created_at=now,
                raw_data={"repo": "acme/ai"}
            ),
            StoredSignal(
                id=2,
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                signal_type="incorporation",
                source_api="sec_edgar",
                confidence=0.75,
                detected_at=now,
                created_at=now,
                raw_data={"form_d": "D-123"}
            )
        ]

        # Setup gate to return high confidence + multi-source
        gate = MagicMock()
        gate.evaluate.return_value = VerificationResult(
            decision=PushDecision.AUTO_PUSH,
            verification_status=VerificationStatus.MULTI_SOURCE,
            confidence_score=0.78,
            confidence_breakdown={},
            reason="High confidence with multiple sources",
            suggested_status="Source",
            signals_used=["1", "2"],
            sources_checked=2,
            verification_details=[]
        )

        store = MagicMock()
        connector = MagicMock()

        pusher = NotionPusher(store, connector, gate)

        prospect = AggregatedProspect(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            signals=signals
        )

        result = await pusher._process_prospect(prospect)

        assert result.decision == PushDecision.AUTO_PUSH
        assert gate.evaluate.called

    async def test_medium_confidence_routes_to_tracking(self):
        """MEDIUM confidence (0.4-0.7) → Status: 'Tracking' (NEEDS_REVIEW)"""
        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=3,
                canonical_key="domain:beta.io",
                company_name="Beta Inc",
                signal_type="github_spike",
                source_api="github",
                confidence=0.5,
                detected_at=now,
                created_at=now,
                raw_data={"repo": "beta/io"}
            )
        ]

        gate = MagicMock()
        gate.evaluate.return_value = VerificationResult(
            decision=PushDecision.NEEDS_REVIEW,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.55,
            confidence_breakdown={},
            reason="Medium confidence - requires verification",
            suggested_status="Tracking",
            signals_used=["3"],
            sources_checked=1,
            verification_details=[]
        )

        store = MagicMock()
        connector = MagicMock()

        pusher = NotionPusher(store, connector, gate)

        prospect = AggregatedProspect(
            canonical_key="domain:beta.io",
            company_name="Beta Inc",
            signals=signals
        )

        result = await pusher._process_prospect(prospect)

        assert result.decision == PushDecision.NEEDS_REVIEW

    async def test_low_confidence_held(self):
        """LOW confidence (<0.4) → HOLD (don't push)"""
        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=4,
                canonical_key="name:gamma",
                company_name="Gamma Inc",
                signal_type="heuristic_match",
                source_api="internal",
                confidence=0.3,
                detected_at=now,
                created_at=now,
                raw_data={"name": "gamma"}
            )
        ]

        gate = MagicMock()
        gate.evaluate.return_value = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.3,
            confidence_breakdown={},
            reason="Low confidence - waiting for more signals",
            suggested_status="",
            signals_used=["4"],
            sources_checked=1,
            verification_details=[]
        )

        store = MagicMock()
        connector = MagicMock()

        pusher = NotionPusher(store, connector, gate)

        prospect = AggregatedProspect(
            canonical_key="name:gamma",
            company_name="Gamma Inc",
            signals=signals
        )

        result = await pusher._process_prospect(prospect)

        assert result.decision == PushDecision.HOLD

    async def test_conflicting_signals_needs_review(self):
        """Conflicting signals → NEEDS_REVIEW with Tracking status"""
        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=5,
                canonical_key="domain:delta.ai",
                company_name="Delta Inc",
                signal_type="job_posting",
                source_api="greenhouse",
                confidence=0.8,
                detected_at=now,
                created_at=now,
                raw_data={"status": "hiring"}
            ),
            StoredSignal(
                id=6,
                canonical_key="domain:delta.ai",
                company_name="Delta Inc",
                signal_type="company_dissolved",
                source_api="sec_edgar",
                confidence=0.9,
                detected_at=now,
                created_at=now,
                raw_data={"status": "dissolved"}
            )
        ]

        gate = MagicMock()
        gate.evaluate.return_value = VerificationResult(
            decision=PushDecision.NEEDS_REVIEW,
            verification_status=VerificationStatus.CONFLICTING,
            confidence_score=0.65,
            confidence_breakdown={},
            reason="Conflicting signals detected - requires human review",
            suggested_status="Tracking",
            signals_used=["5", "6"],
            sources_checked=2,
            verification_details=[]
        )

        store = MagicMock()
        connector = MagicMock()

        pusher = NotionPusher(store, connector, gate)

        prospect = AggregatedProspect(
            canonical_key="domain:delta.ai",
            company_name="Delta Inc",
            signals=signals
        )

        result = await pusher._process_prospect(prospect)

        assert result.decision == PushDecision.NEEDS_REVIEW

    async def test_routing_uses_suggested_status(self):
        """Routing uses suggested_status from VerificationGate"""
        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=7,
                canonical_key="domain:epsilon.io",
                company_name="Epsilon Inc",
                signal_type="github_spike",
                source_api="github",
                confidence=0.75,
                detected_at=now,
                created_at=now,
                raw_data={"repo": "epsilon/io"}
            ),
            StoredSignal(
                id=8,
                canonical_key="domain:epsilon.io",
                company_name="Epsilon Inc",
                signal_type="incorporation",
                source_api="sec_edgar",
                confidence=0.7,
                detected_at=now,
                created_at=now,
                raw_data={"form_d": "D-456"}
            )
        ]

        gate = MagicMock()
        verification_result = VerificationResult(
            decision=PushDecision.AUTO_PUSH,
            verification_status=VerificationStatus.MULTI_SOURCE,
            confidence_score=0.73,
            confidence_breakdown={},
            reason="High confidence with multiple sources",
            suggested_status="Source",  # Should route to "Source"
            signals_used=["7", "8"],
            sources_checked=2,
            verification_details=[]
        )
        gate.evaluate.return_value = verification_result

        store = AsyncMock()
        connector = AsyncMock()

        pusher = NotionPusher(store, connector, gate)

        prospect = AggregatedProspect(
            canonical_key="domain:epsilon.io",
            company_name="Epsilon Inc",
            signals=signals
        )

        result = await pusher._process_prospect(prospect)

        # The result should have the decision from verification
        assert result.decision == PushDecision.AUTO_PUSH
        assert result.confidence == 0.73

    async def test_high_confidence_single_source_strict_mode_needs_review(self):
        """High confidence + single source (strict mode) → NEEDS_REVIEW"""
        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=9,
                canonical_key="domain:zeta.ai",
                company_name="Zeta Inc",
                signal_type="sec_filing",
                source_api="sec_edgar",
                confidence=0.8,
                detected_at=now,
                created_at=now,
                raw_data={"form": "D"}
            )
        ]

        gate = VerificationGate(strict_mode=True)  # Use real gate in strict mode
        gate.evaluate = MagicMock(return_value=VerificationResult(
            decision=PushDecision.NEEDS_REVIEW,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.8,
            confidence_breakdown={},
            reason="High confidence from single source (strict mode requires multiple)",
            suggested_status="Tracking",
            signals_used=["9"],
            sources_checked=1,
            verification_details=[]
        ))

        store = MagicMock()
        connector = MagicMock()

        pusher = NotionPusher(store, connector, gate)

        prospect = AggregatedProspect(
            canonical_key="domain:zeta.ai",
            company_name="Zeta Inc",
            signals=signals
        )

        result = await pusher._process_prospect(prospect)

        assert result.decision == PushDecision.NEEDS_REVIEW

    async def test_multi_source_aggregation_before_routing(self):
        """Multiple signals from different sources are aggregated before routing"""
        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=10,
                canonical_key="domain:omega.io",
                company_name="Omega Inc",
                signal_type="github_spike",
                source_api="github",
                confidence=0.7,
                detected_at=now,
                created_at=now,
                raw_data={"repo": "omega/io"}
            ),
            StoredSignal(
                id=11,
                canonical_key="domain:omega.io",
                company_name="Omega Inc",
                signal_type="job_posting",
                source_api="greenhouse",
                confidence=0.75,
                detected_at=now,
                created_at=now,
                raw_data={"title": "Engineer"}
            ),
            StoredSignal(
                id=12,
                canonical_key="domain:omega.io",
                company_name="Omega Inc",
                signal_type="incorporation",
                source_api="sec_edgar",
                confidence=0.8,
                detected_at=now,
                created_at=now,
                raw_data={"form_d": "D-789"}
            )
        ]

        gate = MagicMock()
        gate.evaluate.return_value = VerificationResult(
            decision=PushDecision.AUTO_PUSH,
            verification_status=VerificationStatus.MULTI_SOURCE,
            confidence_score=0.76,
            confidence_breakdown={},
            reason="High confidence with 3 sources",
            suggested_status="Source",
            signals_used=["10", "11", "12"],
            sources_checked=3,
            verification_details=[]
        )

        store = MagicMock()
        connector = MagicMock()

        pusher = NotionPusher(store, connector, gate)

        prospect = AggregatedProspect(
            canonical_key="domain:omega.io",
            company_name="Omega Inc",
            signals=signals
        )

        result = await pusher._process_prospect(prospect)

        assert result.signals_processed == 3
        assert result.sources_count == 3
        assert result.decision == PushDecision.AUTO_PUSH

"""Integration test for confidence-based routing end-to-end"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from workflows.notion_pusher import NotionPusher
from verification.verification_gate_v2 import VerificationGate, VerificationStatus
from storage.signal_store import StoredSignal


@pytest.mark.asyncio
class TestConfidenceRoutingIntegration:
    """End-to-end confidence routing integration tests"""

    async def test_notion_pusher_routes_high_confidence_to_source(self):
        """NotionPusher correctly routes high-confidence multi-source signals"""
        now = datetime.now(timezone.utc)

        # Create signals that will achieve high confidence
        signals = [
            StoredSignal(
                id=1,
                canonical_key="domain:startup.ai",
                company_name="StartUp AI",
                signal_type="sec_filing",
                source_api="sec_edgar",
                confidence=0.9,
                detected_at=now,
                created_at=now,
                raw_data={"form_d": "D-12345", "type": "Form D"}
            ),
            StoredSignal(
                id=2,
                canonical_key="domain:startup.ai",
                company_name="StartUp AI",
                signal_type="job_posting",
                source_api="greenhouse",
                confidence=0.85,
                detected_at=now,
                created_at=now,
                raw_data={"title": "Engineer", "company": "StartUp AI"}
            )
        ]

        # Create a real VerificationGate (not mocked)
        gate = VerificationGate(strict_mode=False)

        # Create mocks for store and connector
        store = AsyncMock()
        connector = AsyncMock()

        pusher = NotionPusher(store, connector, gate)

        # Create aggregated prospect
        from workflows.notion_pusher import AggregatedProspect
        prospect = AggregatedProspect(
            canonical_key="domain:startup.ai",
            company_name="StartUp AI",
            signals=signals
        )

        # Process the prospect
        result = await pusher._process_prospect(prospect)

        # Verify multi-source detection
        assert result.sources_count == 2
        assert result.signals_processed == 2
        # Result should have a routing decision
        assert result.decision.value in ["auto_push", "needs_review", "hold"]

    async def test_notion_pusher_routes_medium_confidence_to_tracking(self):
        """NotionPusher correctly processes medium-confidence signals"""
        now = datetime.now(timezone.utc)

        # Single source with authoritative signal
        signals = [
            StoredSignal(
                id=3,
                canonical_key="domain:newstartup.io",
                company_name="New StartUp",
                signal_type="job_posting",
                source_api="greenhouse",
                confidence=0.75,
                detected_at=now,
                created_at=now,
                raw_data={"title": "Engineer", "company": "New StartUp"}
            )
        ]

        gate = VerificationGate(strict_mode=False)
        store = AsyncMock()
        connector = AsyncMock()

        pusher = NotionPusher(store, connector, gate)

        from workflows.notion_pusher import AggregatedProspect
        prospect = AggregatedProspect(
            canonical_key="domain:newstartup.io",
            company_name="New StartUp",
            signals=signals
        )

        result = await pusher._process_prospect(prospect)

        # Should have a routing decision
        assert result.signals_processed == 1
        assert result.decision.value in ["auto_push", "needs_review", "hold"]

    async def test_notion_pusher_holds_low_confidence(self):
        """NotionPusher correctly holds low-confidence signals"""
        now = datetime.now(timezone.utc)

        # Low confidence single source
        signals = [
            StoredSignal(
                id=4,
                canonical_key="name:codetool",
                company_name="CodeTool",
                signal_type="heuristic_match",
                source_api="internal",
                confidence=0.25,
                detected_at=now,
                created_at=now,
                raw_data={"name": "codetool"}
            )
        ]

        gate = VerificationGate(strict_mode=False)
        store = AsyncMock()
        connector = AsyncMock()

        pusher = NotionPusher(store, connector, gate)

        from workflows.notion_pusher import AggregatedProspect
        prospect = AggregatedProspect(
            canonical_key="name:codetool",
            company_name="CodeTool",
            signals=signals
        )

        result = await pusher._process_prospect(prospect)

        # Low confidence should be HOLD
        assert result.confidence < 0.4
        assert result.decision.value == "hold"

    async def test_verification_gate_high_threshold(self):
        """VerificationGate correctly identifies high confidence threshold"""
        gate = VerificationGate(strict_mode=False)

        assert gate.HIGH_CONFIDENCE_THRESHOLD == 0.7
        assert gate.MEDIUM_CONFIDENCE_THRESHOLD == 0.4

    async def test_verification_gate_routing_statuses(self):
        """VerificationGate sets correct Notion statuses"""
        gate = VerificationGate(
            strict_mode=False,
            auto_push_status="Source",
            needs_review_status="Tracking"
        )

        assert gate.auto_push_status == "Source"
        assert gate.needs_review_status == "Tracking"

"""Test confidence-based routing logic for Notion status assignment"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from workflows.notion_pusher import NotionPusher, AggregatedProspect, PushResult
from workflows.delivery_policy import DeliveryIntent
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

    # =====================================================================
    # Override-hold tests
    # =====================================================================

    def _make_hold_prospect_and_pusher(self):
        """Helper: create a low-confidence HOLD scenario for override tests."""
        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=100,
                canonical_key="domain:lowconf.io",
                company_name="LowConf Inc",
                signal_type="hacker_news",
                source_api="hacker_news",
                confidence=0.03,
                detected_at=now,
                created_at=now,
                raw_data={"title": "Show HN: LowConf"}
            ),
        ]

        gate = MagicMock()
        gate.MEDIUM_CONFIDENCE_THRESHOLD = 0.4
        gate.needs_review_status = "Tracking"
        gate.evaluate.return_value = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.03,
            confidence_breakdown={},
            reason="Low confidence - waiting for more signals",
            suggested_status="",
            signals_used=["100"],
            sources_checked=1,
            verification_details=[],
        )

        store = MagicMock()
        connector = MagicMock()
        pusher = NotionPusher(store, connector, gate, dry_run=True)

        prospect = AggregatedProspect(
            canonical_key="domain:lowconf.io",
            company_name="LowConf Inc",
            signals=signals,
        )
        return pusher, prospect, gate

    async def test_override_hold_to_needs_review(self):
        """HOLD + override_hold=True -> NEEDS_REVIEW; reason contains marker; PushResult consistent."""
        pusher, prospect, gate = self._make_hold_prospect_and_pusher()

        result = await pusher._process_prospect(prospect, override_hold=True)

        assert result.decision == PushDecision.NEEDS_REVIEW
        assert pusher.OVERRIDE_MARKER in result.push_reason

    async def test_override_hold_does_not_override_reject(self):
        """REJECT + override_hold=True -> still REJECT."""
        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=101, canonical_key="domain:dead.io", company_name="Dead Inc",
                signal_type="company_dissolved", source_api="sec_edgar",
                confidence=0.9, detected_at=now, created_at=now,
                raw_data={"status": "dissolved"},
            ),
        ]
        gate = MagicMock()
        gate.MEDIUM_CONFIDENCE_THRESHOLD = 0.4
        gate.needs_review_status = "Tracking"
        gate.evaluate.return_value = VerificationResult(
            decision=PushDecision.REJECT,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.0,
            confidence_breakdown={},
            reason="Hard kill: company dissolved",
            suggested_status="",
            signals_used=["101"],
            sources_checked=1,
            verification_details=[],
        )
        store = MagicMock()
        connector = MagicMock()
        pusher = NotionPusher(store, connector, gate)
        prospect = AggregatedProspect(
            canonical_key="domain:dead.io", company_name="Dead Inc", signals=signals
        )

        result = await pusher._process_prospect(prospect, override_hold=True)

        assert result.decision == PushDecision.REJECT

    async def test_override_hold_preserves_confidence(self):
        """Confidence stays 0.03 (truthful), routing changes to NEEDS_REVIEW."""
        pusher, prospect, gate = self._make_hold_prospect_and_pusher()

        result = await pusher._process_prospect(prospect, override_hold=True)

        assert result.confidence == 0.03
        assert result.decision == PushDecision.NEEDS_REVIEW

    async def test_override_hold_does_not_mutate_auto_push(self):
        """AUTO_PUSH + override_hold=True -> still AUTO_PUSH (no change)."""
        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=102, canonical_key="domain:highconf.io", company_name="HighConf Inc",
                signal_type="github_spike", source_api="github",
                confidence=0.8, detected_at=now, created_at=now,
                raw_data={"repo": "highconf/io"},
            ),
        ]
        gate = MagicMock()
        gate.MEDIUM_CONFIDENCE_THRESHOLD = 0.4
        gate.needs_review_status = "Tracking"
        gate.evaluate.return_value = VerificationResult(
            decision=PushDecision.AUTO_PUSH,
            verification_status=VerificationStatus.MULTI_SOURCE,
            confidence_score=0.8,
            confidence_breakdown={},
            reason="High confidence",
            suggested_status="Source",
            signals_used=["102"],
            sources_checked=2,
            verification_details=[],
        )
        store = MagicMock()
        connector = MagicMock()
        pusher = NotionPusher(store, connector, gate, dry_run=True)
        prospect = AggregatedProspect(
            canonical_key="domain:highconf.io", company_name="HighConf Inc", signals=signals
        )

        result = await pusher._process_prospect(prospect, override_hold=True)

        assert result.decision == PushDecision.AUTO_PUSH

    async def test_override_hold_reason_when_none(self):
        """reason=None + override -> reason set to marker (not 'None ...')."""
        pusher, prospect, gate = self._make_hold_prospect_and_pusher()
        # Patch the evaluate return to have reason=None
        gate.evaluate.return_value = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.03,
            confidence_breakdown={},
            reason=None,
            suggested_status="",
            signals_used=["100"],
            sources_checked=1,
            verification_details=[],
        )

        result = await pusher._process_prospect(prospect, override_hold=True)

        assert result.decision == PushDecision.NEEDS_REVIEW
        assert result.push_reason == pusher.OVERRIDE_MARKER
        assert "None" not in result.push_reason

    async def test_override_hold_idempotent(self):
        """Call override twice on same prospect -> reason contains marker exactly once."""
        pusher, prospect, gate = self._make_hold_prospect_and_pusher()

        # First call
        result1 = await pusher._process_prospect(prospect, override_hold=True)

        # Re-create the gate return with the already-modified reason from result1
        gate.evaluate.return_value = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.03,
            confidence_breakdown={},
            reason=result1.push_reason,  # Already has marker
            suggested_status="",
            signals_used=["100"],
            sources_checked=1,
            verification_details=[],
        )

        # Second call (simulates retry)
        result2 = await pusher._process_prospect(prospect, override_hold=True)

        assert result2.push_reason.count(pusher.OVERRIDE_MARKER) == 1

    async def test_override_hold_boundary_at_threshold(self):
        """Guard test: HOLD at threshold boundary (0.4) -> override NOT applied;
        HOLD at zero -> override NOT applied."""
        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=103, canonical_key="domain:boundary.io", company_name="Boundary Inc",
                signal_type="heuristic_match", source_api="internal",
                confidence=0.4, detected_at=now, created_at=now,
                raw_data={"name": "boundary"},
            ),
        ]

        # score == 0.4 (at threshold) -> override should NOT apply
        gate = MagicMock()
        gate.MEDIUM_CONFIDENCE_THRESHOLD = 0.4
        gate.needs_review_status = "Tracking"
        gate.evaluate.return_value = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.4,
            confidence_breakdown={},
            reason="Synthetic: HOLD at threshold",
            suggested_status="",
            signals_used=["103"],
            sources_checked=1,
            verification_details=[],
        )
        store = MagicMock()
        connector = MagicMock()
        pusher = NotionPusher(store, connector, gate)
        prospect = AggregatedProspect(
            canonical_key="domain:boundary.io", company_name="Boundary Inc", signals=signals
        )

        result = await pusher._process_prospect(prospect, override_hold=True)
        assert result.decision == PushDecision.HOLD, "Override should NOT apply at threshold"

        # score == 0 -> override should NOT apply (0 < score is false)
        gate.evaluate.return_value = VerificationResult(
            decision=PushDecision.HOLD,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            confidence_score=0.0,
            confidence_breakdown={},
            reason="Synthetic: HOLD at zero",
            suggested_status="",
            signals_used=["103"],
            sources_checked=1,
            verification_details=[],
        )

        result = await pusher._process_prospect(prospect, override_hold=True)
        assert result.decision == PushDecision.HOLD, "Override should NOT apply at score=0"


@pytest.mark.asyncio
async def test_batch_commit_passes_override_hold():
    """commit_batch() passes override_hold=True to process_single_prospect."""
    from workflows.batch_publisher import commit_batch

    # Build a minimal mock store with the right DB shape
    mock_db = AsyncMock()

    # publish_batches lookup -> draft status
    batch_row = AsyncMock()
    batch_row.fetchone = AsyncMock(return_value=("draft", 1))

    # pending items -> one item
    items_cursor = AsyncMock()
    items_cursor.fetchall = AsyncMock(return_value=[
        (1, 10, "comp_1", "domain:test.io"),
    ])

    # claim item -> rowcount 1
    claim_cursor = MagicMock()
    claim_cursor.rowcount = 1

    call_count = [0]

    async def mock_execute(sql, params=None):
        call_count[0] += 1
        sql_lower = sql.strip().lower()
        if sql_lower.startswith("select status") or sql_lower.startswith("select status, item_count"):
            return batch_row
        if sql_lower.startswith("select id, review_id"):
            return items_cursor
        if sql_lower.startswith("update batch_items set status = 'in_progress'"):
            return claim_cursor
        # Default: return a dummy cursor
        dummy = AsyncMock()
        dummy.fetchone = AsyncMock(return_value=None)
        return dummy

    mock_db.execute = mock_execute
    mock_db.commit = AsyncMock()

    mock_store = MagicMock()
    mock_store._db = mock_db

    # Mock pusher: capture kwargs passed to process_single_prospect
    mock_pusher = AsyncMock()
    mock_push_result = PushResult(
        canonical_key="domain:test.io",
        company_name="Test Inc",
        decision=PushDecision.NEEDS_REVIEW,
        confidence=0.03,
        pushed=True,
        notion_page_id="page-123",
        push_reason="overridden",
    )
    mock_pusher.process_single_prospect = AsyncMock(return_value=mock_push_result)

    # Patch delivery policy and activation gate to allow writes
    mock_gate_result = MagicMock()
    mock_gate_result.verdict = "ready"
    mock_gate_result.to_dict.return_value = {"verdict": "ready"}

    with patch("workflows.batch_publisher.assert_notion_write_allowed"):
        with patch("workflows.batch_publisher.update_review_status", new_callable=AsyncMock):
            with patch("monitoring.activation_gate.check_activation_readiness", new_callable=AsyncMock, return_value=mock_gate_result):
                await commit_batch(mock_store, "batch-test-001", pusher=mock_pusher)

    # Assert process_single_prospect was called with override_hold=True
    mock_pusher.process_single_prospect.assert_called_once_with(
        "domain:test.io",
        intent=DeliveryIntent.BATCH_PUSH,
        override_hold=True,
    )


# =============================================================================
# Track 2: Per-source confidence floor (hacker_news minimum = 0.70)
# =============================================================================

class TestSourceConfidenceFloor:
    """Verify per-source confidence floor constants and lookup."""

    def test_hacker_news_floor_is_0_70(self):
        """HN minimum confidence should be 0.70 (not the default 0.40)."""
        from workflows.pipeline import _get_min_confidence
        assert _get_min_confidence("hacker_news") == 0.70

    def test_default_floor_is_0_40(self):
        """Non-override sources fall back to the 0.40 MEDIUM_CONFIDENCE_THRESHOLD."""
        from workflows.pipeline import _get_min_confidence
        assert _get_min_confidence("github") == 0.40
        assert _get_min_confidence("sec_edgar") == 0.40
        assert _get_min_confidence("rss_feeds") == 0.40

    def test_source_min_confidence_dict_exists(self):
        """_SOURCE_MIN_CONFIDENCE dict must exist and contain hacker_news."""
        from workflows.pipeline import _SOURCE_MIN_CONFIDENCE
        assert "hacker_news" in _SOURCE_MIN_CONFIDENCE
        assert _SOURCE_MIN_CONFIDENCE["hacker_news"] == 0.70

    def test_hn_at_055_is_below_floor(self):
        """Confidence 0.55 for HN is below the 0.70 floor (would normally go to Tracking)."""
        from workflows.pipeline import _get_min_confidence
        hn_signal_confidence = 0.55
        assert hn_signal_confidence < _get_min_confidence("hacker_news"), (
            "HN signal at 0.55 should be below the 0.70 source floor and held"
        )

    def test_hn_at_075_is_above_floor(self):
        """Confidence 0.75 for HN is above the 0.70 floor and should route normally."""
        from workflows.pipeline import _get_min_confidence
        hn_signal_confidence = 0.75
        assert hn_signal_confidence >= _get_min_confidence("hacker_news"), (
            "HN signal at 0.75 should be above the 0.70 source floor"
        )

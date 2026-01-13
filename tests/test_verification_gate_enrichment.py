"""
Tests for VerificationGate with enrichment_boost parameter (Phase 2 enhancement).
"""

import pytest
from datetime import datetime, timezone, timedelta

from verification.verification_gate_v2 import (
    VerificationGate,
    Signal,
    VerificationStatus,
    PushDecision,
    ConfidenceBreakdown,
)


def create_signal(
    signal_type: str = "github_spike",
    source_api: str = "github",
    confidence: float = 0.7,
    age_days: int = 7,
) -> Signal:
    """Create a test signal."""
    return Signal(
        id=f"sig-{signal_type}-{age_days}",
        signal_type=signal_type,
        confidence=confidence,
        source_api=source_api,
        detected_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )


class TestEnrichmentBoostIntegration:
    """Tests for enrichment_boost parameter in VerificationGate."""

    def test_evaluate_accepts_enrichment_boost(self):
        """Test that evaluate() accepts enrichment_boost parameter."""
        gate = VerificationGate()
        signals = [create_signal("github_spike", "github", 0.6)]

        # Should not raise an error
        result = gate.evaluate(signals, enrichment_boost=0.03)

        assert result is not None
        assert result.confidence_score > 0

    def test_enrichment_boost_increases_confidence(self):
        """Test that enrichment_boost increases the confidence score."""
        gate = VerificationGate()
        signals = [create_signal("github_spike", "github", 0.6)]

        # Without enrichment boost
        result_no_boost = gate.evaluate(signals, enrichment_boost=0.0)

        # With enrichment boost
        result_with_boost = gate.evaluate(signals, enrichment_boost=0.04)

        assert result_with_boost.confidence_score > result_no_boost.confidence_score

    def test_enrichment_boost_is_capped(self):
        """Test that enrichment_boost is capped at ENRICHMENT_BOOST_WEIGHT (0.05)."""
        gate = VerificationGate()
        signals = [create_signal("github_spike", "github", 0.5)]

        # Excessive enrichment boost (higher than cap)
        result = gate.evaluate(signals, enrichment_boost=0.10)

        # The applied boost should be capped at 0.05
        breakdown = result.confidence_breakdown
        assert breakdown["enrichment_boost"] <= 0.05

    def test_enrichment_boost_in_breakdown(self):
        """Test that enrichment_boost appears in the confidence breakdown."""
        gate = VerificationGate()
        signals = [create_signal("github_spike", "github", 0.7)]

        result = gate.evaluate(signals, enrichment_boost=0.03)

        breakdown = result.confidence_breakdown
        assert "enrichment_boost" in breakdown
        assert breakdown["enrichment_boost"] == 0.03


class TestEnrichmentBoostWithOtherBoosts:
    """Tests for enrichment_boost combined with founder and velocity boosts."""

    def test_enrichment_combines_with_founder_boost(self):
        """Test that enrichment_boost combines with founder_score boost."""
        gate = VerificationGate(use_founder_scoring=True)
        signals = [create_signal("github_spike", "github", 0.5)]

        # Base score
        result_base = gate.evaluate(signals)

        # With founder + enrichment
        result_combined = gate.evaluate(
            signals,
            founder_score=0.7,
            enrichment_boost=0.03,
        )

        # Combined should be higher than base
        assert result_combined.confidence_score > result_base.confidence_score

        # Both boosts should be in breakdown
        breakdown = result_combined.confidence_breakdown
        assert breakdown["founder_boost"] > 0
        assert breakdown["enrichment_boost"] == 0.03

    def test_enrichment_combines_with_velocity_boost(self):
        """Test that enrichment_boost combines with velocity_boost."""
        gate = VerificationGate(use_velocity_scoring=True)
        signals = [create_signal("github_spike", "github", 0.5)]

        # Base score
        result_base = gate.evaluate(signals)

        # With velocity + enrichment
        result_combined = gate.evaluate(
            signals,
            velocity_boost=0.15,
            enrichment_boost=0.04,
        )

        # Combined should be higher than base
        assert result_combined.confidence_score > result_base.confidence_score

        # Both boosts should be in breakdown
        breakdown = result_combined.confidence_breakdown
        assert breakdown["velocity_boost"] > 0
        assert breakdown["enrichment_boost"] == 0.04

    def test_all_boosts_combined_capped_at_one(self):
        """Test that combined boosts (founder + velocity + enrichment) don't exceed 1.0."""
        gate = VerificationGate(
            use_founder_scoring=True,
            use_velocity_scoring=True,
        )

        # Very strong base signals
        signals = [
            create_signal("incorporation", "companies_house", 0.95, age_days=5),
            create_signal("github_spike", "github", 0.9, age_days=3),
            create_signal("hiring_signal", "job_boards", 0.9, age_days=1),
        ]

        # Max boosts
        result = gate.evaluate(
            signals,
            founder_score=1.0,
            velocity_boost=0.35,
            momentum_score=1.0,
            enrichment_boost=0.05,
        )

        assert result.confidence_score <= 1.0


class TestEnrichmentBoostSignalDetails:
    """Tests for enrichment_boost in signal_details."""

    def test_enrichment_boost_in_signal_details(self):
        """Test that enrichment_boost appears in signal_details when > 0."""
        gate = VerificationGate()
        signals = [create_signal("github_spike", "github", 0.7)]

        result = gate.evaluate(signals, enrichment_boost=0.04)

        signal_details = result.confidence_breakdown["signal_details"]

        # Should have enrichment_data entry
        enrichment_entries = [
            d for d in signal_details if d.get("type") == "enrichment_data"
        ]
        assert len(enrichment_entries) == 1
        assert enrichment_entries[0]["effect"] == "boost"
        assert enrichment_entries[0]["source"] == "consolidated_signal"
        assert enrichment_entries[0]["enrichment_boost"] == 0.04

    def test_no_enrichment_entry_when_zero(self):
        """Test that no enrichment_data entry appears when boost is 0."""
        gate = VerificationGate()
        signals = [create_signal("github_spike", "github", 0.7)]

        result = gate.evaluate(signals, enrichment_boost=0.0)

        signal_details = result.confidence_breakdown["signal_details"]

        # Should NOT have enrichment_data entry
        enrichment_entries = [
            d for d in signal_details if d.get("type") == "enrichment_data"
        ]
        assert len(enrichment_entries) == 0


class TestConfidenceBreakdownEnrichmentField:
    """Tests for the enrichment_boost field in ConfidenceBreakdown dataclass."""

    def test_breakdown_includes_enrichment_boost_field(self):
        """Test that ConfidenceBreakdown includes enrichment_boost field."""
        breakdown = ConfidenceBreakdown(
            overall=0.75,
            base_score=0.5,
            multi_source_boost=1.15,
            convergence_boost=1.2,
            signals_contributing=2,
            sources_checked=2,
            sources=["github", "companies_house"],
            signal_details=[],
            enrichment_boost=0.03,
        )

        assert breakdown.enrichment_boost == 0.03

    def test_breakdown_to_dict_includes_enrichment_boost(self):
        """Test that to_dict() includes enrichment_boost."""
        breakdown = ConfidenceBreakdown(
            overall=0.75,
            base_score=0.5,
            multi_source_boost=1.15,
            convergence_boost=1.2,
            signals_contributing=2,
            sources_checked=2,
            sources=["github", "companies_house"],
            signal_details=[],
            enrichment_boost=0.04,
        )

        d = breakdown.to_dict()

        assert "enrichment_boost" in d
        assert d["enrichment_boost"] == 0.04

    def test_breakdown_enrichment_boost_default_zero(self):
        """Test that enrichment_boost defaults to 0.0."""
        breakdown = ConfidenceBreakdown(
            overall=0.5,
            base_score=0.5,
            multi_source_boost=1.0,
            convergence_boost=1.0,
            signals_contributing=1,
            sources_checked=1,
            sources=["github"],
            signal_details=[],
        )

        assert breakdown.enrichment_boost == 0.0
        d = breakdown.to_dict()
        assert d["enrichment_boost"] == 0.0


class TestEnrichmentBoostDoesNotOverrideHardKill:
    """Test that enrichment_boost doesn't override hard kill signals."""

    def test_enrichment_doesnt_save_from_hard_kill(self):
        """Test that enrichment_boost doesn't prevent hard kill rejection."""
        gate = VerificationGate()

        signals = [
            create_signal("github_spike", "github", 0.9),
            Signal(
                id="sig-kill",
                signal_type="company_dissolved",
                confidence=1.0,
                source_api="companies_house",
                detected_at=datetime.now(timezone.utc),
            ),
        ]

        # Even with max enrichment boost
        result = gate.evaluate(signals, enrichment_boost=0.05)

        # Should still reject
        assert result.decision == PushDecision.REJECT
        assert result.confidence_score == 0.0

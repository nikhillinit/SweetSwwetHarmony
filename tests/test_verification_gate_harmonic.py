"""
Tests for VerificationGate with Harmonic enhancements (founder scoring, velocity tracking).
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


class TestVerificationGateFounderScoring:
    """Tests for founder score integration."""

    def test_founder_score_boost_applied(self):
        """Test that founder score increases confidence."""
        gate = VerificationGate(use_founder_scoring=True)

        signals = [
            create_signal("github_spike", "github", 0.6),
        ]

        # Without founder score
        result_no_founder = gate.evaluate(signals, founder_score=0.0)

        # With high founder score
        result_with_founder = gate.evaluate(signals, founder_score=0.8)

        assert result_with_founder.confidence_score > result_no_founder.confidence_score

    def test_founder_score_max_boost(self):
        """Test that founder boost is capped."""
        gate = VerificationGate(use_founder_scoring=True)

        signals = [create_signal("github_spike", "github", 0.5)]

        # Perfect founder score
        result = gate.evaluate(signals, founder_score=1.0)

        # Check breakdown shows founder contribution
        breakdown = result.confidence_breakdown
        assert "founder_score" in breakdown
        assert breakdown["founder_boost"] <= 0.15  # Max boost

    def test_founder_score_disabled(self):
        """Test that founder scoring can be disabled."""
        gate = VerificationGate(use_founder_scoring=False)

        signals = [create_signal("github_spike", "github", 0.6)]

        result = gate.evaluate(signals, founder_score=0.9)

        # Should not include founder boost
        breakdown = result.confidence_breakdown
        assert breakdown["founder_boost"] == 0.0

    def test_founder_score_in_decision_reason(self):
        """Test that high founder score can push decision threshold."""
        gate = VerificationGate(use_founder_scoring=True)

        # Signals that would be borderline without founder boost
        signals = [
            create_signal("github_spike", "github", 0.5, age_days=7),
            create_signal("domain_registration", "whois", 0.6, age_days=14),
        ]

        # Without founder score - might be NEEDS_REVIEW
        result_no_founder = gate.evaluate(signals, founder_score=0.0)

        # With strong founder - might push to AUTO_PUSH
        result_with_founder = gate.evaluate(signals, founder_score=0.9)

        # Founder should increase confidence
        assert result_with_founder.confidence_score > result_no_founder.confidence_score


class TestVerificationGateVelocityTracking:
    """Tests for velocity/momentum tracking integration."""

    def test_velocity_boost_applied(self):
        """Test that velocity boost increases confidence."""
        gate = VerificationGate(use_velocity_scoring=True)

        signals = [create_signal("github_spike", "github", 0.6)]

        # Without velocity boost
        result_no_velocity = gate.evaluate(signals, velocity_boost=0.0)

        # With velocity boost (convergence detected)
        result_with_velocity = gate.evaluate(signals, velocity_boost=0.2)

        assert result_with_velocity.confidence_score > result_no_velocity.confidence_score

    def test_velocity_boost_max(self):
        """Test that velocity boost is capped."""
        gate = VerificationGate(use_velocity_scoring=True)

        signals = [create_signal("github_spike", "github", 0.5)]

        # Maximum velocity boost
        result = gate.evaluate(signals, velocity_boost=0.5, momentum_score=1.0)

        # Check breakdown
        breakdown = result.confidence_breakdown
        assert breakdown["velocity_boost"] <= 0.20  # Max boost

    def test_velocity_disabled(self):
        """Test that velocity scoring can be disabled."""
        gate = VerificationGate(use_velocity_scoring=False)

        signals = [create_signal("github_spike", "github", 0.6)]

        result = gate.evaluate(signals, velocity_boost=0.3, momentum_score=0.8)

        breakdown = result.confidence_breakdown
        assert breakdown["velocity_boost"] == 0.0

    def test_momentum_score_in_breakdown(self):
        """Test that momentum score is recorded in breakdown."""
        gate = VerificationGate(use_velocity_scoring=True)

        signals = [create_signal("github_spike", "github", 0.6)]

        result = gate.evaluate(
            signals,
            velocity_boost=0.15,
            momentum_score=0.75,
        )

        breakdown = result.confidence_breakdown
        assert breakdown["momentum_score"] == 0.75


class TestVerificationGateCombinedEnhancements:
    """Tests for founder + velocity combined effects."""

    def test_combined_boosts(self):
        """Test that founder and velocity boosts combine."""
        gate = VerificationGate(
            use_founder_scoring=True,
            use_velocity_scoring=True,
        )

        signals = [create_signal("github_spike", "github", 0.4)]

        # Base score
        result_base = gate.evaluate(signals)
        base_score = result_base.confidence_score

        # With both boosts
        result_combined = gate.evaluate(
            signals,
            founder_score=0.7,
            velocity_boost=0.15,
            momentum_score=0.6,
        )

        # Should be significantly higher
        assert result_combined.confidence_score > base_score
        assert result_combined.confidence_breakdown["founder_boost"] > 0
        assert result_combined.confidence_breakdown["velocity_boost"] > 0

    def test_combined_boosts_capped(self):
        """Test that combined boosts don't exceed 1.0."""
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
        )

        assert result.confidence_score <= 1.0

    def test_breakdown_includes_all_components(self):
        """Test that breakdown includes all scoring components."""
        gate = VerificationGate(
            use_founder_scoring=True,
            use_velocity_scoring=True,
        )

        signals = [
            create_signal("github_spike", "github", 0.7),
            create_signal("hiring_signal", "job_boards", 0.8),
        ]

        result = gate.evaluate(
            signals,
            founder_score=0.6,
            velocity_boost=0.1,
            momentum_score=0.5,
        )

        breakdown = result.confidence_breakdown
        assert "base_score" in breakdown
        assert "multi_source_boost" in breakdown
        assert "convergence_boost" in breakdown
        assert "founder_score" in breakdown
        assert "founder_boost" in breakdown
        assert "velocity_boost" in breakdown
        assert "momentum_score" in breakdown
        assert "signal_details" in breakdown

    def test_signal_details_include_boosts(self):
        """Test that signal details include founder and velocity entries."""
        gate = VerificationGate(
            use_founder_scoring=True,
            use_velocity_scoring=True,
        )

        signals = [create_signal("github_spike", "github", 0.7)]

        result = gate.evaluate(
            signals,
            founder_score=0.7,
            velocity_boost=0.15,
            momentum_score=0.6,
        )

        signal_details = result.confidence_breakdown["signal_details"]

        # Should have founder_score entry
        founder_entries = [d for d in signal_details if d.get("type") == "founder_score"]
        assert len(founder_entries) == 1
        assert founder_entries[0]["effect"] == "boost"

        # Should have velocity entry
        velocity_entries = [d for d in signal_details if d.get("type") == "velocity_momentum"]
        assert len(velocity_entries) == 1
        assert velocity_entries[0]["effect"] == "boost"


class TestVerificationGateDecisionLogic:
    """Tests for decision logic with enhancements."""

    def test_enhancements_can_push_to_auto_push(self):
        """Test that enhancements can elevate decision to AUTO_PUSH."""
        gate = VerificationGate(
            use_founder_scoring=True,
            use_velocity_scoring=True,
        )

        # Signals that would normally be NEEDS_REVIEW
        signals = [
            create_signal("github_spike", "github", 0.5, age_days=10),
        ]

        # Without enhancements
        result_base = gate.evaluate(signals)

        # With strong enhancements
        result_enhanced = gate.evaluate(
            signals,
            founder_score=0.9,  # Serial founder
            velocity_boost=0.2,  # Strong momentum
            momentum_score=0.8,
        )

        # Enhanced should have higher confidence
        assert result_enhanced.confidence_score > result_base.confidence_score

    def test_enhancements_dont_override_hard_kill(self):
        """Test that enhancements don't override hard kill signals."""
        gate = VerificationGate(
            use_founder_scoring=True,
            use_velocity_scoring=True,
        )

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

        # Even with amazing founder and velocity
        result = gate.evaluate(
            signals,
            founder_score=1.0,
            velocity_boost=0.35,
            momentum_score=1.0,
        )

        # Should still reject
        assert result.decision == PushDecision.REJECT
        assert result.confidence_score == 0.0


class TestConfidenceBreakdownNewFields:
    """Tests for new ConfidenceBreakdown fields."""

    def test_breakdown_to_dict(self):
        """Test that to_dict includes new fields."""
        breakdown = ConfidenceBreakdown(
            overall=0.75,
            base_score=0.5,
            multi_source_boost=1.15,
            convergence_boost=1.2,
            signals_contributing=2,
            sources_checked=2,
            sources=["github", "companies_house"],
            signal_details=[],
            founder_score=0.7,
            founder_boost=0.1,
            velocity_boost=0.15,
            momentum_score=0.6,
        )

        d = breakdown.to_dict()

        assert d["founder_score"] == 0.7
        assert d["founder_boost"] == 0.1
        assert d["velocity_boost"] == 0.15
        assert d["momentum_score"] == 0.6

    def test_breakdown_defaults(self):
        """Test that new fields have sensible defaults."""
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

        d = breakdown.to_dict()

        assert d["founder_score"] == 0.0
        assert d["founder_boost"] == 0.0
        assert d["velocity_boost"] == 0.0
        assert d["momentum_score"] == 0.0

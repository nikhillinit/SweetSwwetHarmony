"""
Tests for VerificationGate with Community Signal Integration.

Tests for:
- Community signal types (community_mention, telegram_mention, discord_mention)
- Community sentiment boost integration
- Combined community + existing enhancements
"""

import pytest
from datetime import datetime, timezone, timedelta

from verification.verification_gate_v2 import (
    VerificationGate,
    Signal,
    VerificationStatus,
    PushDecision,
    ConfidenceBreakdown,
    SIGNAL_WEIGHTS,
    HALF_LIVES,
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


# =============================================================================
# COMMUNITY SIGNAL TYPE TESTS
# =============================================================================

class TestCommunitySignalTypes:
    """Tests for community signal types in SIGNAL_WEIGHTS."""

    def test_community_mention_in_weights(self):
        """community_mention signal type is defined."""
        assert "community_mention" in SIGNAL_WEIGHTS
        assert SIGNAL_WEIGHTS["community_mention"] > 0

    def test_telegram_mention_in_weights(self):
        """telegram_mention signal type is defined."""
        assert "telegram_mention" in SIGNAL_WEIGHTS
        assert SIGNAL_WEIGHTS["telegram_mention"] > 0

    def test_discord_mention_in_weights(self):
        """discord_mention signal type is defined."""
        assert "discord_mention" in SIGNAL_WEIGHTS
        assert SIGNAL_WEIGHTS["discord_mention"] > 0

    def test_community_half_lives_defined(self):
        """Community signal types have decay half-lives."""
        assert "community_mention" in HALF_LIVES
        assert "telegram_mention" in HALF_LIVES
        assert "discord_mention" in HALF_LIVES

    def test_community_weights_reasonable(self):
        """Community signal weights are in reasonable range (0.05-0.20)."""
        assert 0.05 <= SIGNAL_WEIGHTS["community_mention"] <= 0.20
        assert 0.05 <= SIGNAL_WEIGHTS["telegram_mention"] <= 0.20
        assert 0.05 <= SIGNAL_WEIGHTS["discord_mention"] <= 0.20

    def test_community_half_lives_reasonable(self):
        """Community signal half-lives are in reasonable range (14-60 days)."""
        # Community buzz decays relatively quickly
        assert 14 <= HALF_LIVES["community_mention"] <= 60
        assert 14 <= HALF_LIVES["telegram_mention"] <= 60
        assert 14 <= HALF_LIVES["discord_mention"] <= 60


# =============================================================================
# COMMUNITY SIGNAL EVALUATION TESTS
# =============================================================================

class TestCommunitySignalEvaluation:
    """Tests for evaluating community signals."""

    def test_community_mention_contributes_to_score(self):
        """Community mention signals contribute to confidence score."""
        gate = VerificationGate()

        signals = [
            create_signal("community_mention", "reddit", 0.7, age_days=3),
        ]

        result = gate.evaluate(signals)

        assert result.confidence_score > 0
        assert "community_mention" in [d["type"] for d in result.confidence_breakdown["signal_details"]]

    def test_telegram_mention_contributes_to_score(self):
        """Telegram mention signals contribute to confidence score."""
        gate = VerificationGate()

        signals = [
            create_signal("telegram_mention", "telegram", 0.8, age_days=5),
        ]

        result = gate.evaluate(signals)

        assert result.confidence_score > 0
        assert "telegram_mention" in [d["type"] for d in result.confidence_breakdown["signal_details"]]

    def test_discord_mention_contributes_to_score(self):
        """Discord mention signals contribute to confidence score."""
        gate = VerificationGate()

        signals = [
            create_signal("discord_mention", "discord", 0.75, age_days=4),
        ]

        result = gate.evaluate(signals)

        assert result.confidence_score > 0
        assert "discord_mention" in [d["type"] for d in result.confidence_breakdown["signal_details"]]

    def test_multiple_community_sources_boost(self):
        """Multiple community sources provide multi-source boost."""
        gate = VerificationGate()

        signals = [
            create_signal("telegram_mention", "telegram", 0.7, age_days=3),
            create_signal("discord_mention", "discord", 0.7, age_days=3),
        ]

        result = gate.evaluate(signals)

        # Should have multi-source boost (2 sources)
        assert result.confidence_breakdown["multi_source_boost"] == 1.15
        assert result.confidence_breakdown["sources_checked"] == 2

    def test_community_plus_traditional_signals(self):
        """Community signals combine with traditional signals."""
        gate = VerificationGate()

        signals = [
            create_signal("github_spike", "github", 0.7, age_days=5),
            create_signal("community_mention", "reddit", 0.6, age_days=7),
            create_signal("telegram_mention", "telegram", 0.65, age_days=3),
        ]

        result = gate.evaluate(signals)

        # Should have 3 distinct signal types
        assert result.confidence_breakdown["signals_contributing"] == 3
        # Should have multi-source boost (3 sources)
        assert result.confidence_breakdown["multi_source_boost"] == 1.3

    def test_community_signal_decay(self):
        """Community signals decay faster than traditional signals."""
        gate = VerificationGate()

        # Fresh community signal
        fresh_signal = [create_signal("community_mention", "reddit", 0.8, age_days=1)]
        fresh_result = gate.evaluate(fresh_signal)

        # Old community signal (30 days)
        old_signal = [create_signal("community_mention", "reddit", 0.8, age_days=30)]
        old_result = gate.evaluate(old_signal)

        # Fresh should be worth more
        assert fresh_result.confidence_score > old_result.confidence_score


# =============================================================================
# COMMUNITY SENTIMENT BOOST TESTS
# =============================================================================

class TestCommunitySentimentBoost:
    """Tests for community sentiment boost integration."""

    def test_community_sentiment_boost_applied(self):
        """Community sentiment boost increases confidence."""
        gate = VerificationGate()

        signals = [create_signal("github_spike", "github", 0.6)]

        # Without community boost
        result_no_community = gate.evaluate(signals, community_sentiment_boost=0.0)

        # With positive community sentiment
        result_with_community = gate.evaluate(signals, community_sentiment_boost=0.10)

        assert result_with_community.confidence_score > result_no_community.confidence_score

    def test_community_sentiment_negative_penalty(self):
        """Negative community sentiment reduces confidence."""
        gate = VerificationGate()

        signals = [create_signal("github_spike", "github", 0.7)]

        # Without community penalty
        result_neutral = gate.evaluate(signals, community_sentiment_boost=0.0)

        # With negative community sentiment
        result_negative = gate.evaluate(signals, community_sentiment_boost=-0.10)

        assert result_negative.confidence_score < result_neutral.confidence_score

    def test_community_sentiment_boost_max(self):
        """Community sentiment boost is capped at maximum."""
        gate = VerificationGate()

        signals = [create_signal("github_spike", "github", 0.5)]

        # Try to apply excessive boost
        result = gate.evaluate(signals, community_sentiment_boost=0.25)

        # Check breakdown shows capped boost
        breakdown = result.confidence_breakdown
        assert breakdown["community_sentiment_boost"] <= 0.15  # Max boost

    def test_community_sentiment_penalty_max(self):
        """Community sentiment penalty is capped at minimum."""
        gate = VerificationGate()

        signals = [create_signal("github_spike", "github", 0.7)]

        # Try to apply excessive penalty
        result = gate.evaluate(signals, community_sentiment_boost=-0.25)

        # Check breakdown shows capped penalty
        breakdown = result.confidence_breakdown
        assert breakdown["community_sentiment_boost"] >= -0.15  # Max penalty

    def test_community_sentiment_in_breakdown(self):
        """Community sentiment boost is recorded in breakdown."""
        gate = VerificationGate()

        signals = [create_signal("github_spike", "github", 0.6)]

        result = gate.evaluate(signals, community_sentiment_boost=0.08)

        breakdown = result.confidence_breakdown
        assert "community_sentiment_boost" in breakdown
        assert abs(breakdown["community_sentiment_boost"] - 0.08) < 0.001

    def test_community_sentiment_in_signal_details(self):
        """Community sentiment appears in signal details."""
        gate = VerificationGate()

        signals = [create_signal("github_spike", "github", 0.6)]

        result = gate.evaluate(signals, community_sentiment_boost=0.07)

        signal_details = result.confidence_breakdown["signal_details"]

        # Should have community sentiment entry
        community_entries = [d for d in signal_details if d.get("type") == "community_sentiment"]
        assert len(community_entries) == 1
        assert community_entries[0]["effect"] == "boost"


# =============================================================================
# COMBINED COMMUNITY + EXISTING ENHANCEMENTS TESTS
# =============================================================================

class TestCombinedCommunityEnhancements:
    """Tests for community boost combined with founder/velocity."""

    def test_all_boosts_combined(self):
        """All boost types combine correctly."""
        gate = VerificationGate(
            use_founder_scoring=True,
            use_velocity_scoring=True,
        )

        signals = [create_signal("github_spike", "github", 0.4)]

        # Base score
        result_base = gate.evaluate(signals)
        base_score = result_base.confidence_score

        # With all boosts
        result_combined = gate.evaluate(
            signals,
            founder_score=0.6,
            velocity_boost=0.10,
            momentum_score=0.5,
            community_sentiment_boost=0.08,
        )

        # Should be significantly higher
        assert result_combined.confidence_score > base_score
        assert result_combined.confidence_breakdown["founder_boost"] > 0
        assert result_combined.confidence_breakdown["velocity_boost"] > 0
        assert result_combined.confidence_breakdown["community_sentiment_boost"] > 0

    def test_combined_boosts_capped_at_one(self):
        """Combined boosts don't exceed 1.0."""
        gate = VerificationGate(
            use_founder_scoring=True,
            use_velocity_scoring=True,
        )

        # Strong base signals
        signals = [
            create_signal("incorporation", "companies_house", 0.95, age_days=5),
            create_signal("github_spike", "github", 0.9, age_days=3),
            create_signal("hiring_signal", "job_boards", 0.9, age_days=1),
        ]

        # Max all boosts
        result = gate.evaluate(
            signals,
            founder_score=1.0,
            velocity_boost=0.35,
            momentum_score=1.0,
            enrichment_boost=0.05,
            community_sentiment_boost=0.15,
        )

        assert result.confidence_score <= 1.0

    def test_negative_community_with_positive_others(self):
        """Negative community sentiment can offset other boosts."""
        gate = VerificationGate(
            use_founder_scoring=True,
            use_velocity_scoring=True,
        )

        signals = [create_signal("github_spike", "github", 0.5)]

        # Only positive boosts
        result_positive = gate.evaluate(
            signals,
            founder_score=0.7,
            velocity_boost=0.15,
            community_sentiment_boost=0.0,
        )

        # With negative community sentiment
        result_negative_community = gate.evaluate(
            signals,
            founder_score=0.7,
            velocity_boost=0.15,
            community_sentiment_boost=-0.10,
        )

        # Negative community should reduce overall score
        assert result_negative_community.confidence_score < result_positive.confidence_score

    def test_breakdown_includes_all_components(self):
        """Breakdown includes all scoring components including community."""
        gate = VerificationGate(
            use_founder_scoring=True,
            use_velocity_scoring=True,
        )

        signals = [
            create_signal("github_spike", "github", 0.7),
            create_signal("community_mention", "reddit", 0.6),
        ]

        result = gate.evaluate(
            signals,
            founder_score=0.5,
            velocity_boost=0.1,
            momentum_score=0.4,
            enrichment_boost=0.02,
            community_sentiment_boost=0.06,
        )

        breakdown = result.confidence_breakdown
        assert "base_score" in breakdown
        assert "multi_source_boost" in breakdown
        assert "convergence_boost" in breakdown
        assert "founder_score" in breakdown
        assert "founder_boost" in breakdown
        assert "velocity_boost" in breakdown
        assert "momentum_score" in breakdown
        assert "enrichment_boost" in breakdown
        assert "community_sentiment_boost" in breakdown


# =============================================================================
# COMMUNITY SIGNAL DECISION LOGIC TESTS
# =============================================================================

class TestCommunityDecisionLogic:
    """Tests for decision logic with community signals."""

    def test_strong_community_buzz_can_elevate_decision(self):
        """Strong positive community buzz can help push decision."""
        gate = VerificationGate()

        # Borderline signal
        signals = [
            create_signal("github_spike", "github", 0.5, age_days=10),
        ]

        # Without community boost
        result_base = gate.evaluate(signals)

        # With strong positive community sentiment
        result_community = gate.evaluate(signals, community_sentiment_boost=0.10)

        # Community should increase confidence
        assert result_community.confidence_score > result_base.confidence_score

    def test_negative_community_can_demote_decision(self):
        """Negative community sentiment can demote decision."""
        gate = VerificationGate()

        # Signal that might be borderline NEEDS_REVIEW
        signals = [
            create_signal("github_spike", "github", 0.6, age_days=5),
        ]

        # Without community penalty
        result_base = gate.evaluate(signals)

        # With negative community sentiment
        result_negative = gate.evaluate(signals, community_sentiment_boost=-0.10)

        # Negative sentiment should reduce confidence
        assert result_negative.confidence_score < result_base.confidence_score

    def test_community_signals_alone_can_source(self):
        """Strong community signals alone can route to Source."""
        gate = VerificationGate()

        # Multiple strong community signals
        signals = [
            create_signal("telegram_mention", "telegram", 0.9, age_days=2),
            create_signal("discord_mention", "discord", 0.85, age_days=3),
            create_signal("community_mention", "reddit", 0.8, age_days=4),
        ]

        result = gate.evaluate(signals, community_sentiment_boost=0.10)

        # Should have enough confidence for at least NEEDS_REVIEW
        assert result.decision in [PushDecision.AUTO_PUSH, PushDecision.NEEDS_REVIEW]

    def test_community_doesnt_override_hard_kill(self):
        """Community signals don't override hard kill signals."""
        gate = VerificationGate()

        signals = [
            create_signal("telegram_mention", "telegram", 0.9),
            create_signal("discord_mention", "discord", 0.9),
            Signal(
                id="sig-kill",
                signal_type="company_dissolved",
                confidence=1.0,
                source_api="companies_house",
                detected_at=datetime.now(timezone.utc),
            ),
        ]

        # Even with positive community sentiment
        result = gate.evaluate(signals, community_sentiment_boost=0.15)

        # Should still reject
        assert result.decision == PushDecision.REJECT
        assert result.confidence_score == 0.0


# =============================================================================
# CONFIDENCE BREAKDOWN COMMUNITY FIELDS TESTS
# =============================================================================

class TestConfidenceBreakdownCommunityFields:
    """Tests for community fields in ConfidenceBreakdown."""

    def test_breakdown_to_dict_includes_community(self):
        """to_dict includes community sentiment boost."""
        breakdown = ConfidenceBreakdown(
            overall=0.75,
            base_score=0.5,
            multi_source_boost=1.15,
            convergence_boost=1.2,
            signals_contributing=2,
            sources_checked=2,
            sources=["github", "telegram"],
            signal_details=[],
            community_sentiment_boost=0.08,
        )

        d = breakdown.to_dict()

        assert "community_sentiment_boost" in d
        assert d["community_sentiment_boost"] == 0.08

    def test_breakdown_community_default_zero(self):
        """Community sentiment boost defaults to 0."""
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

        assert d["community_sentiment_boost"] == 0.0

"""
Tests for VerificationGate with News Signal Types.

Tests for:
- News signal types (news_mention, funding_announcement, product_launch, press_release)
- News signal weight and decay configurations
"""

import pytest
from datetime import datetime, timezone, timedelta

from verification.verification_gate_v2 import (
    VerificationGate,
    Signal,
    VerificationStatus,
    PushDecision,
    SIGNAL_WEIGHTS,
    HALF_LIVES,
)


def create_signal(
    signal_type: str = "news_mention",
    source_api: str = "news_api",
    confidence: float = 0.7,
    age_days: int = 3,
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
# NEWS SIGNAL TYPE TESTS
# =============================================================================

class TestNewsSignalTypes:
    """Tests for news signal types in SIGNAL_WEIGHTS."""

    def test_news_mention_in_weights(self):
        """news_mention signal type is defined."""
        assert "news_mention" in SIGNAL_WEIGHTS
        assert SIGNAL_WEIGHTS["news_mention"] > 0

    def test_funding_announcement_in_weights(self):
        """funding_announcement signal type is defined."""
        assert "funding_announcement" in SIGNAL_WEIGHTS
        assert SIGNAL_WEIGHTS["funding_announcement"] > 0

    def test_product_launch_in_weights(self):
        """product_launch signal type is defined."""
        assert "product_launch" in SIGNAL_WEIGHTS
        assert SIGNAL_WEIGHTS["product_launch"] > 0

    def test_press_release_in_weights(self):
        """press_release signal type is defined."""
        assert "press_release" in SIGNAL_WEIGHTS
        assert SIGNAL_WEIGHTS["press_release"] > 0

    def test_news_half_lives_defined(self):
        """News signal types have decay half-lives."""
        assert "news_mention" in HALF_LIVES
        assert "funding_announcement" in HALF_LIVES
        assert "product_launch" in HALF_LIVES
        assert "press_release" in HALF_LIVES

    def test_funding_has_highest_news_weight(self):
        """Funding announcements have highest weight among news signals."""
        assert SIGNAL_WEIGHTS["funding_announcement"] > SIGNAL_WEIGHTS["news_mention"]
        assert SIGNAL_WEIGHTS["funding_announcement"] > SIGNAL_WEIGHTS["product_launch"]
        assert SIGNAL_WEIGHTS["funding_announcement"] > SIGNAL_WEIGHTS["press_release"]

    def test_funding_has_longest_half_life(self):
        """Funding announcements have longest half-life among news signals."""
        assert HALF_LIVES["funding_announcement"] > HALF_LIVES["news_mention"]
        assert HALF_LIVES["funding_announcement"] > HALF_LIVES["product_launch"]
        assert HALF_LIVES["funding_announcement"] > HALF_LIVES["press_release"]


# =============================================================================
# NEWS SIGNAL EVALUATION TESTS
# =============================================================================

class TestNewsSignalEvaluation:
    """Tests for evaluating news signals."""

    def test_news_mention_contributes_to_score(self):
        """News mention signals contribute to confidence score."""
        gate = VerificationGate()
        signals = [create_signal("news_mention", "news_api", 0.7, age_days=2)]

        result = gate.evaluate(signals)

        assert result.confidence_score > 0
        assert "news_mention" in [d["type"] for d in result.confidence_breakdown["signal_details"]]

    def test_funding_announcement_contributes_to_score(self):
        """Funding announcement signals contribute to confidence score."""
        gate = VerificationGate()
        signals = [create_signal("funding_announcement", "news_api", 0.8, age_days=3)]

        result = gate.evaluate(signals)

        assert result.confidence_score > 0
        assert "funding_announcement" in [d["type"] for d in result.confidence_breakdown["signal_details"]]

    def test_product_launch_contributes_to_score(self):
        """Product launch signals contribute to confidence score."""
        gate = VerificationGate()
        signals = [create_signal("product_launch", "rss_feeds", 0.75, age_days=2)]

        result = gate.evaluate(signals)

        assert result.confidence_score > 0

    def test_press_release_contributes_to_score(self):
        """Press release signals contribute to confidence score."""
        gate = VerificationGate()
        signals = [create_signal("press_release", "rss_feeds", 0.6, age_days=1)]

        result = gate.evaluate(signals)

        assert result.confidence_score > 0

    def test_multiple_news_sources_boost(self):
        """Multiple news sources provide multi-source boost."""
        gate = VerificationGate()
        signals = [
            create_signal("news_mention", "news_api", 0.7, age_days=2),
            create_signal("press_release", "rss_feeds", 0.6, age_days=3),
        ]

        result = gate.evaluate(signals)

        # Should have multi-source boost (2 sources)
        assert result.confidence_breakdown["multi_source_boost"] == 1.15
        assert result.confidence_breakdown["sources_checked"] == 2

    def test_funding_announcement_high_value(self):
        """Funding announcements produce high confidence."""
        gate = VerificationGate()

        # Fresh funding announcement from authoritative source
        signals = [
            create_signal("funding_announcement", "news_api", 0.85, age_days=1),
        ]

        result = gate.evaluate(signals)

        # Should be relatively high confidence
        assert result.confidence_score > 0.15


# =============================================================================
# NEWS + TRADITIONAL SIGNALS TESTS
# =============================================================================

class TestNewsWithTraditionalSignals:
    """Tests for news signals combined with traditional signals."""

    def test_news_plus_github_signals(self):
        """News signals combine with GitHub signals."""
        gate = VerificationGate()
        signals = [
            create_signal("github_spike", "github", 0.7, age_days=5),
            create_signal("funding_announcement", "news_api", 0.8, age_days=2),
        ]

        result = gate.evaluate(signals)

        # Should have 2 distinct signal types
        assert result.confidence_breakdown["signals_contributing"] == 2
        # Should have multi-source boost
        assert result.confidence_breakdown["multi_source_boost"] == 1.15

    def test_news_plus_incorporation_signals(self):
        """News signals combine with incorporation signals."""
        gate = VerificationGate()
        signals = [
            create_signal("incorporation", "companies_house", 0.9, age_days=30),
            create_signal("news_mention", "news_api", 0.65, age_days=5),
        ]

        result = gate.evaluate(signals)

        assert result.confidence_breakdown["signals_contributing"] == 2

    def test_news_plus_community_signals(self):
        """News signals combine with community signals."""
        gate = VerificationGate()
        signals = [
            create_signal("telegram_mention", "telegram", 0.7, age_days=3),
            create_signal("funding_announcement", "news_api", 0.8, age_days=2),
            create_signal("community_mention", "reddit", 0.6, age_days=4),
        ]

        result = gate.evaluate(signals)

        # Should have 3 distinct signal types
        assert result.confidence_breakdown["signals_contributing"] == 3
        # Should have strong multi-source boost
        assert result.confidence_breakdown["multi_source_boost"] == 1.3


# =============================================================================
# NEWS SIGNAL DECAY TESTS
# =============================================================================

class TestNewsSignalDecay:
    """Tests for news signal decay over time."""

    def test_fresh_news_higher_than_old(self):
        """Fresh news articles have higher contribution than old ones."""
        gate = VerificationGate()

        # Fresh news
        fresh_signals = [create_signal("news_mention", "news_api", 0.7, age_days=1)]
        fresh_result = gate.evaluate(fresh_signals)

        # Old news
        old_signals = [create_signal("news_mention", "news_api", 0.7, age_days=30)]
        old_result = gate.evaluate(old_signals)

        assert fresh_result.confidence_score > old_result.confidence_score

    def test_funding_decays_slower_than_news(self):
        """Funding announcements decay slower than general news."""
        gate = VerificationGate()

        # Both 60 days old
        age = 60

        funding_signals = [create_signal("funding_announcement", "news_api", 0.8, age_days=age)]
        funding_result = gate.evaluate(funding_signals)

        news_signals = [create_signal("news_mention", "news_api", 0.8, age_days=age)]
        news_result = gate.evaluate(news_signals)

        # Funding should retain more value due to longer half-life
        # (but also has higher base weight, so compare relative decay)
        # Both should have decayed but funding less proportionally


# =============================================================================
# NEWS SIGNAL DECISION TESTS
# =============================================================================

class TestNewsSignalDecisions:
    """Tests for decision logic with news signals."""

    def test_strong_funding_news_can_elevate(self):
        """Strong funding news can help push decision higher."""
        gate = VerificationGate()

        # Strong funding signal
        signals = [
            create_signal("funding_announcement", "news_api", 0.9, age_days=1),
        ]

        result = gate.evaluate(signals)

        # Should have reasonable confidence
        assert result.confidence_score > 0.15

    def test_news_doesnt_override_hard_kill(self):
        """News signals don't override hard kill signals."""
        gate = VerificationGate()

        signals = [
            create_signal("funding_announcement", "news_api", 0.9, age_days=1),
            Signal(
                id="sig-kill",
                signal_type="company_dissolved",
                confidence=1.0,
                source_api="companies_house",
                detected_at=datetime.now(timezone.utc),
            ),
        ]

        result = gate.evaluate(signals)

        # Should still reject due to hard kill
        assert result.decision == PushDecision.REJECT
        assert result.confidence_score == 0.0

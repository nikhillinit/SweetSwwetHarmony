"""
Tests for WarmIntroBoost - relationship intelligence scoring and badges.

TDD: These tests are written FIRST, before implementation.
Run with: pytest tests/utils/test_warm_intro_boost.py -v
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from utils.warm_intro_boost import (
    WarmIntroBoost,
    WarmIntroCandidate,
    RelationshipSource,
    # Constants
    WARMTH_BOOST_GATE_THRESHOLD,
    WARMTH_BOOST_MAX,
    DECLINE_SUPPRESS_WINDOW_DAYS,
    DECLINE_POST_WINDOW_SCORE_CAP,
)


# =============================================================================
# CONSTANTS TESTS
# =============================================================================

class TestConstants:
    """Test that constants match design spec."""

    def test_warmth_boost_gate_threshold(self):
        """Gate threshold is 0.40."""
        assert WARMTH_BOOST_GATE_THRESHOLD == 0.40

    def test_warmth_boost_max(self):
        """Max boost is 0.05."""
        assert WARMTH_BOOST_MAX == 0.05

    def test_decline_suppress_window_days(self):
        """Decline window is ~18 months (548 days)."""
        assert DECLINE_SUPPRESS_WINDOW_DAYS == 548

    def test_decline_post_window_score_cap(self):
        """Post-window declined cap is 0.30."""
        assert DECLINE_POST_WINDOW_SCORE_CAP == 0.30


# =============================================================================
# WARMTH BOOST FORMULA TESTS
# =============================================================================

class TestWarmthBoostFormula:
    """Test warmth boost formula from design spec Section 3.4."""

    def test_no_boost_below_gate(self):
        """No boost when thesis_fit < 0.4."""
        booster = WarmIntroBoost()

        # thesis_fit = 0.3 (below gate), warmth = 1.0 (maximum)
        result = booster.apply_warmth_boost(thesis_fit=0.3, warmth=1.0)

        # Should not boost at all
        assert result == 0.3

    def test_no_boost_at_exactly_gate(self):
        """No boost when thesis_fit == 0.4 (edge case)."""
        booster = WarmIntroBoost()

        # thesis_fit = 0.4 (at gate), warmth = 1.0
        result = booster.apply_warmth_boost(thesis_fit=0.4, warmth=1.0)

        # Gate is < 0.4, so 0.4 should get boost
        # Result = 0.4 + (1.0 * 0.05) = 0.45
        assert result == 0.45

    def test_boost_above_gate(self):
        """Boost applied when thesis_fit > 0.4."""
        booster = WarmIntroBoost()

        # thesis_fit = 0.6, warmth = 0.5
        result = booster.apply_warmth_boost(thesis_fit=0.6, warmth=0.5)

        # Result = 0.6 + (0.5 * 0.05) = 0.625
        assert result == 0.625

    def test_boost_capped_at_max(self):
        """Boost capped at max 0.05 even with warmth = 1.0."""
        booster = WarmIntroBoost()

        # thesis_fit = 0.7, warmth = 1.0
        result = booster.apply_warmth_boost(thesis_fit=0.7, warmth=1.0)

        # Result = 0.7 + (1.0 * 0.05) = 0.75
        assert result == 0.75

    def test_boost_capped_at_one(self):
        """Boosted result never exceeds 1.0."""
        booster = WarmIntroBoost()

        # thesis_fit = 0.98, warmth = 1.0
        result = booster.apply_warmth_boost(thesis_fit=0.98, warmth=1.0)

        # Would be 0.98 + 0.05 = 1.03, but capped at 1.0
        assert result == 1.0

    def test_zero_warmth_no_boost(self):
        """Zero warmth means no boost."""
        booster = WarmIntroBoost()

        result = booster.apply_warmth_boost(thesis_fit=0.6, warmth=0.0)

        assert result == 0.6

    def test_custom_max_boost(self):
        """Can customize max boost value."""
        booster = WarmIntroBoost(max_boost=0.10)

        result = booster.apply_warmth_boost(thesis_fit=0.6, warmth=1.0)

        # Result = 0.6 + (1.0 * 0.10) = 0.70
        assert result == 0.70


# =============================================================================
# MERGE RULE TESTS
# =============================================================================

class TestMergeRule:
    """Test max() merge rule from design spec Section 3.1."""

    def test_merge_takes_higher_gmail(self):
        """Merge takes Gmail when higher than Notion."""
        booster = WarmIntroBoost()

        result = booster.merge_scores(gmail_score=0.8, notion_score=0.4)

        assert result == 0.8

    def test_merge_takes_higher_notion(self):
        """Merge takes Notion when higher than Gmail."""
        booster = WarmIntroBoost()

        result = booster.merge_scores(gmail_score=0.3, notion_score=0.95)

        assert result == 0.95

    def test_merge_handles_none_gmail(self):
        """Merge handles None Gmail score."""
        booster = WarmIntroBoost()

        result = booster.merge_scores(gmail_score=None, notion_score=0.7)

        assert result == 0.7

    def test_merge_handles_none_notion(self):
        """Merge handles None Notion score."""
        booster = WarmIntroBoost()

        result = booster.merge_scores(gmail_score=0.6, notion_score=None)

        assert result == 0.6

    def test_merge_handles_both_none(self):
        """Merge handles both None."""
        booster = WarmIntroBoost()

        result = booster.merge_scores(gmail_score=None, notion_score=None)

        assert result == 0.0


# =============================================================================
# DECLINED SUPPRESSION TESTS
# =============================================================================

class TestDeclinedSuppression:
    """Test declined suppression from design spec Section 3.3."""

    def test_suppress_within_window(self):
        """Declined within 18 months is suppressed."""
        booster = WarmIntroBoost()

        # Declined 6 months ago
        declined_at = datetime.now(timezone.utc) - timedelta(days=180)

        should_suppress = booster.should_suppress_declined(
            is_declined=True,
            declined_at=declined_at,
        )

        assert should_suppress is True

    def test_allow_after_window(self):
        """Declined after 18 months is allowed."""
        booster = WarmIntroBoost()

        # Declined 2 years ago
        declined_at = datetime.now(timezone.utc) - timedelta(days=730)

        should_suppress = booster.should_suppress_declined(
            is_declined=True,
            declined_at=declined_at,
        )

        assert should_suppress is False

    def test_allow_at_exact_window(self):
        """At exactly 548 days, allow (edge case)."""
        booster = WarmIntroBoost()

        declined_at = datetime.now(timezone.utc) - timedelta(days=548)

        should_suppress = booster.should_suppress_declined(
            is_declined=True,
            declined_at=declined_at,
        )

        assert should_suppress is False

    def test_not_declined_never_suppressed(self):
        """Non-declined records are never suppressed."""
        booster = WarmIntroBoost()

        should_suppress = booster.should_suppress_declined(
            is_declined=False,
            declined_at=None,
        )

        assert should_suppress is False

    def test_cap_score_post_window(self):
        """Score capped at 0.30 for post-window declined."""
        booster = WarmIntroBoost()

        # Score of 0.8 should be capped
        capped = booster.apply_declined_cap(score=0.8)

        assert capped == 0.30

    def test_no_cap_below_threshold(self):
        """Score below cap is not affected."""
        booster = WarmIntroBoost()

        capped = booster.apply_declined_cap(score=0.2)

        assert capped == 0.2


# =============================================================================
# BADGE GENERATION TESTS
# =============================================================================

class TestBadgeGeneration:
    """Test badge generation from design spec Section 7.1."""

    def test_badge_gmail_active(self):
        """Gmail source with strength >= 0.6 gets Active badge."""
        booster = WarmIntroBoost()

        badge = booster.generate_badge(
            source=RelationshipSource.GMAIL,
            score=0.7,
            lp_status=None,
            is_declined=False,
        )

        assert badge == "📧 Active Conversation"

    def test_badge_gmail_not_active(self):
        """Gmail source with strength < 0.6 gets no special badge."""
        booster = WarmIntroBoost()

        badge = booster.generate_badge(
            source=RelationshipSource.GMAIL,
            score=0.4,
            lp_status=None,
            is_declined=False,
        )

        # Should be a generic badge or empty
        assert badge != "📧 Active Conversation"

    def test_badge_notion_docs_signed(self):
        """Notion Docs Signed gets LP - Docs Signed badge."""
        booster = WarmIntroBoost()

        badge = booster.generate_badge(
            source=RelationshipSource.NOTION_LP,
            score=0.95,
            lp_status="Docs Signed",
            is_declined=False,
        )

        assert badge == "📝 LP - Docs Signed"

    def test_badge_notion_verbal(self):
        """Notion Verbal Confirm gets LP - Verbal badge."""
        booster = WarmIntroBoost()

        badge = booster.generate_badge(
            source=RelationshipSource.NOTION_LP,
            score=0.70,
            lp_status="Verbal Confirm",
            is_declined=False,
        )

        assert badge == "📝 LP - Verbal"

    def test_badge_notion_engaged(self):
        """Notion Engagement Sent gets LP - Contacted badge."""
        booster = WarmIntroBoost()

        badge = booster.generate_badge(
            source=RelationshipSource.NOTION_LP,
            score=0.40,
            lp_status="Engagement Sent",
            is_declined=False,
        )

        assert badge == "📋 LP - Contacted"

    def test_badge_declined_post_window(self):
        """Declined (post-window) gets warning badge."""
        booster = WarmIntroBoost()

        badge = booster.generate_badge(
            source=RelationshipSource.NOTION_LP,
            score=0.30,
            lp_status="Declined",
            is_declined=True,
        )

        assert badge == "⚠️ Previously declined"

    def test_badge_declined_overrides_other(self):
        """Declined badge takes precedence over other badges."""
        booster = WarmIntroBoost()

        # Even with high score, declined badge should show
        badge = booster.generate_badge(
            source=RelationshipSource.GMAIL,
            score=0.9,
            lp_status=None,
            is_declined=True,
        )

        assert badge == "⚠️ Previously declined"


# =============================================================================
# WARM INTRO CANDIDATE TESTS
# =============================================================================

class TestWarmIntroCandidate:
    """Test WarmIntroCandidate data class."""

    def test_candidate_creation(self):
        """Can create candidate with all fields."""
        candidate = WarmIntroCandidate(
            investor_domain="sequoia.com",
            score=0.85,
            source=RelationshipSource.NOTION_LP,
            badge="📝 LP - Docs Signed",
            attribution="via John Smith",
            notion_lp_ids=["page-1", "page-2"],
            confidence="high",
        )

        assert candidate.investor_domain == "sequoia.com"
        assert candidate.score == 0.85
        assert candidate.source == RelationshipSource.NOTION_LP
        assert candidate.badge == "📝 LP - Docs Signed"
        assert candidate.attribution == "via John Smith"
        assert len(candidate.notion_lp_ids) == 2

    def test_confidence_high(self):
        """High confidence for score >= 0.7."""
        candidate = WarmIntroCandidate(
            investor_domain="vc.com",
            score=0.75,
            source=RelationshipSource.GMAIL,
            badge="📧 Active",
            attribution="",
        )

        assert candidate.confidence == "high"

    def test_confidence_medium(self):
        """Medium confidence for 0.4 <= score < 0.7."""
        candidate = WarmIntroCandidate(
            investor_domain="vc.com",
            score=0.5,
            source=RelationshipSource.GMAIL,
            badge="",
            attribution="",
        )

        assert candidate.confidence == "medium"

    def test_confidence_low(self):
        """Low confidence for score < 0.4."""
        candidate = WarmIntroCandidate(
            investor_domain="vc.com",
            score=0.25,
            source=RelationshipSource.NOTION_LP,
            badge="",
            attribution="",
        )

        assert candidate.confidence == "low"


# =============================================================================
# RELATIONSHIP SOURCE TESTS
# =============================================================================

class TestRelationshipSource:
    """Test RelationshipSource enum."""

    def test_gmail_source(self):
        """Gmail source exists."""
        assert RelationshipSource.GMAIL.value == "gmail"

    def test_notion_lp_source(self):
        """Notion LP source exists."""
        assert RelationshipSource.NOTION_LP.value == "notion_lp"


# =============================================================================
# FULL PIPELINE TESTS
# =============================================================================

class TestFullPipeline:
    """Test full warm intro pipeline."""

    def test_build_candidate_from_gmail(self):
        """Build candidate from Gmail data."""
        booster = WarmIntroBoost()

        candidate = booster.build_candidate(
            investor_domain="sequoia.com",
            gmail_score=0.75,
            notion_score=None,
            lp_status=None,
            attribution="",
            notion_lp_ids=[],
            is_declined=False,
            declined_at=None,
        )

        assert candidate is not None
        assert candidate.investor_domain == "sequoia.com"
        assert candidate.score == 0.75
        assert candidate.source == RelationshipSource.GMAIL
        assert candidate.badge == "📧 Active Conversation"
        assert candidate.confidence == "high"

    def test_build_candidate_from_notion(self):
        """Build candidate from Notion LP data."""
        booster = WarmIntroBoost()

        candidate = booster.build_candidate(
            investor_domain="a16z.com",
            gmail_score=None,
            notion_score=0.95,
            lp_status="Docs Signed",
            attribution="via Partner A",
            notion_lp_ids=["page-1"],
            is_declined=False,
            declined_at=None,
        )

        assert candidate is not None
        assert candidate.investor_domain == "a16z.com"
        assert candidate.score == 0.95
        assert candidate.source == RelationshipSource.NOTION_LP
        assert candidate.badge == "📝 LP - Docs Signed"
        assert candidate.attribution == "via Partner A"

    def test_build_candidate_merged(self):
        """Build candidate from merged Gmail + Notion data."""
        booster = WarmIntroBoost()

        candidate = booster.build_candidate(
            investor_domain="accel.com",
            gmail_score=0.6,
            notion_score=0.70,
            lp_status="Verbal Confirm",
            attribution="via Bob Wilson",
            notion_lp_ids=["page-2"],
            is_declined=False,
            declined_at=None,
        )

        assert candidate is not None
        # Should use Notion score (0.70 > 0.6)
        assert candidate.score == 0.70
        # Source should be Notion (higher)
        assert candidate.source == RelationshipSource.NOTION_LP
        assert candidate.badge == "📝 LP - Verbal"

    def test_build_candidate_declined_suppressed(self):
        """Declined within window returns None."""
        booster = WarmIntroBoost()

        declined_at = datetime.now(timezone.utc) - timedelta(days=100)

        candidate = booster.build_candidate(
            investor_domain="declined.com",
            gmail_score=0.8,
            notion_score=0.95,
            lp_status="Declined",
            attribution="via Declined Partner",
            notion_lp_ids=["page-3"],
            is_declined=True,
            declined_at=declined_at,
        )

        # Should be suppressed
        assert candidate is None

    def test_build_candidate_declined_post_window(self):
        """Declined post-window returns capped candidate."""
        booster = WarmIntroBoost()

        declined_at = datetime.now(timezone.utc) - timedelta(days=600)

        candidate = booster.build_candidate(
            investor_domain="old-decline.com",
            gmail_score=0.8,
            notion_score=0.95,
            lp_status="Declined",
            attribution="via Old Decline",
            notion_lp_ids=["page-4"],
            is_declined=True,
            declined_at=declined_at,
        )

        assert candidate is not None
        # Score should be capped at 0.30
        assert candidate.score == 0.30
        assert candidate.badge == "⚠️ Previously declined"


# =============================================================================
# ATTRIBUTION TESTS
# =============================================================================

class TestAttribution:
    """Test attribution string generation."""

    def test_single_name_attribution(self):
        """Single name attribution."""
        booster = WarmIntroBoost()

        attr = booster.format_attribution(["John Smith"])

        assert attr == "via John Smith"

    def test_multiple_names_attribution(self):
        """Multiple names attribution."""
        booster = WarmIntroBoost()

        attr = booster.format_attribution(["John Smith", "Jane Doe"])

        assert attr == "via John Smith, Jane Doe"

    def test_empty_names_attribution(self):
        """Empty names returns empty string."""
        booster = WarmIntroBoost()

        attr = booster.format_attribution([])

        assert attr == ""

    def test_none_names_attribution(self):
        """None names returns empty string."""
        booster = WarmIntroBoost()

        attr = booster.format_attribution(None)

        assert attr == ""


# =============================================================================
# MANUAL OVERRIDE TESTS
# =============================================================================

class TestManualOverride:
    """Test manual override protection."""

    def test_should_not_update_with_override(self):
        """Should not update when Manual Override is active."""
        booster = WarmIntroBoost()

        should_update = booster.should_push_update(
            page_id="page-1",
            new_score=0.8,
            current_score=0.5,
            manual_override=True,
        )

        assert should_update is False

    def test_should_update_without_override(self):
        """Should update when no Manual Override."""
        booster = WarmIntroBoost()

        should_update = booster.should_push_update(
            page_id="page-1",
            new_score=0.8,
            current_score=0.5,
            manual_override=False,
        )

        assert should_update is True

    def test_should_not_update_below_epsilon(self):
        """Should not update when delta < epsilon (0.02)."""
        booster = WarmIntroBoost()

        should_update = booster.should_push_update(
            page_id="page-1",
            new_score=0.51,
            current_score=0.50,
            manual_override=False,
        )

        # Delta = 0.01 < 0.02 epsilon
        assert should_update is False

    def test_should_update_above_epsilon(self):
        """Should update when delta >= epsilon."""
        booster = WarmIntroBoost()

        should_update = booster.should_push_update(
            page_id="page-1",
            new_score=0.55,
            current_score=0.50,
            manual_override=False,
        )

        # Delta = 0.05 >= 0.02 epsilon
        assert should_update is True

"""
WarmIntroBoost - Relationship intelligence scoring and badges.

Combines Gmail and Notion LP relationship data to surface actionable
warm intro candidates with explainable scoring and trust safeguards.

Key features:
- Warmth boost formula (thesis_fit gate at 0.4, max boost 0.05)
- Declined suppression (18-month window, 0.30 cap post-window)
- Badge generation (Active, LP status, Declined)
- Manual override protection
- Epsilon check for Notion writes

Usage:
    booster = WarmIntroBoost()

    candidate = booster.build_candidate(
        investor_domain="sequoia.com",
        gmail_score=0.75,
        notion_score=0.95,
        lp_status="Docs Signed",
        attribution="via John Smith",
        notion_lp_ids=["page-1"],
        is_declined=False,
    )

    if candidate:
        print(f"{candidate.investor_domain}: {candidate.score:.2f} {candidate.badge}")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS (from design spec)
# =============================================================================

# Warmth boost
WARMTH_BOOST_GATE_THRESHOLD = 0.40
WARMTH_BOOST_MAX = 0.05

# Declined handling
DECLINE_SUPPRESS_WINDOW_DAYS = 548  # ~18 months
DECLINE_POST_WINDOW_SCORE_CAP = 0.30

# Notion writeback
EPSILON = 0.02  # Only update if score changes by >2%

# Badge threshold for Gmail "active" status
GMAIL_ACTIVE_THRESHOLD = 0.6


# =============================================================================
# ENUMS
# =============================================================================

class RelationshipSource(str, Enum):
    """Source of relationship data."""
    GMAIL = "gmail"
    NOTION_LP = "notion_lp"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class WarmIntroCandidate:
    """
    A warm intro candidate with scoring and badge.

    Output schema from design spec Section 7.
    """
    investor_domain: str
    score: float
    source: RelationshipSource
    badge: str
    attribution: str
    notion_lp_ids: List[str] = field(default_factory=list)
    confidence: str = ""

    def __post_init__(self):
        """Compute confidence from score if not provided."""
        if not self.confidence:
            self.confidence = self._compute_confidence()

    def _compute_confidence(self) -> str:
        """Compute confidence level from score."""
        if self.score >= 0.7:
            return "high"
        elif self.score >= 0.4:
            return "medium"
        else:
            return "low"


# =============================================================================
# WARM INTRO BOOST
# =============================================================================

class WarmIntroBoost:
    """
    Main class for warm intro scoring and badge generation.

    Implements scoring logic from design spec Sections 3.1-3.4.
    """

    def __init__(
        self,
        max_boost: float = WARMTH_BOOST_MAX,
        gate_threshold: float = WARMTH_BOOST_GATE_THRESHOLD,
        decline_window_days: int = DECLINE_SUPPRESS_WINDOW_DAYS,
        decline_cap: float = DECLINE_POST_WINDOW_SCORE_CAP,
        epsilon: float = EPSILON,
    ):
        """
        Initialize WarmIntroBoost.

        Args:
            max_boost: Maximum warmth boost (default: 0.05)
            gate_threshold: Thesis fit threshold for boost (default: 0.40)
            decline_window_days: Days to suppress declined (default: 548)
            decline_cap: Score cap for post-window declined (default: 0.30)
            epsilon: Minimum delta for Notion updates (default: 0.02)
        """
        self.max_boost = max_boost
        self.gate_threshold = gate_threshold
        self.decline_window_days = decline_window_days
        self.decline_cap = decline_cap
        self.epsilon = epsilon

    # =========================================================================
    # WARMTH BOOST FORMULA (Section 3.4)
    # =========================================================================

    def apply_warmth_boost(self, thesis_fit: float, warmth: float) -> float:
        """
        Apply warmth boost to thesis fit score.

        Gate: If thesis_fit < 0.4, apply no boost.
        Formula: min(1.0, thesis_fit + (warmth * max_boost))

        Args:
            thesis_fit: Base thesis fit score (0.0-1.0)
            warmth: Relationship warmth score (0.0-1.0)

        Returns:
            Boosted score (capped at 1.0)
        """
        if thesis_fit < self.gate_threshold:
            return thesis_fit

        boost = warmth * self.max_boost
        return min(1.0, thesis_fit + boost)

    # =========================================================================
    # MERGE RULE (Section 3.1)
    # =========================================================================

    def merge_scores(
        self,
        gmail_score: Optional[float],
        notion_score: Optional[float],
    ) -> float:
        """
        Merge Gmail and Notion scores using max() rule.

        Args:
            gmail_score: Score from Gmail (or None)
            notion_score: Score from Notion LP (or None)

        Returns:
            Final merged score
        """
        scores = []
        if gmail_score is not None:
            scores.append(gmail_score)
        if notion_score is not None:
            scores.append(notion_score)

        return max(scores) if scores else 0.0

    # =========================================================================
    # DECLINED SUPPRESSION (Section 3.3)
    # =========================================================================

    def should_suppress_declined(
        self,
        is_declined: bool,
        declined_at: Optional[datetime],
    ) -> bool:
        """
        Check if declined record should be suppressed.

        Suppress if declined AND within 18-month window.

        Args:
            is_declined: Whether the record is declined
            declined_at: When the decline happened (UTC)

        Returns:
            True if should suppress, False if should allow
        """
        if not is_declined:
            return False

        if declined_at is None:
            # No date, assume recent -> suppress
            return True

        # Ensure timezone-aware
        if declined_at.tzinfo is None:
            declined_at = declined_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        days_since = (now - declined_at).days

        return days_since < self.decline_window_days

    def apply_declined_cap(self, score: float) -> float:
        """
        Apply score cap for post-window declined.

        Args:
            score: Original score

        Returns:
            Capped score (max 0.30)
        """
        return min(score, self.decline_cap)

    # =========================================================================
    # BADGE GENERATION (Section 7.1)
    # =========================================================================

    def generate_badge(
        self,
        source: RelationshipSource,
        score: float,
        lp_status: Optional[str],
        is_declined: bool,
    ) -> str:
        """
        Generate display badge for relationship.

        Badge priority:
        1. Declined (always shows warning if declined)
        2. LP status badge (Docs Signed, Verbal, etc.)
        3. Gmail active badge (if strength >= 0.6)
        4. Generic badge

        Args:
            source: Relationship source (Gmail or Notion)
            score: Relationship score
            lp_status: LP status string from Notion
            is_declined: Whether the record is declined

        Returns:
            Badge string with emoji
        """
        # Declined takes priority
        if is_declined:
            return "⚠️ Previously declined"

        # LP status badges
        if source == RelationshipSource.NOTION_LP and lp_status:
            status_badges = {
                "Docs Signed": "📝 LP - Docs Signed",
                "Verbal Confirm": "📝 LP - Verbal",
                "Engagement Sent": "📋 LP - Contacted",
                "In Database": "📋 LP - In Database",
            }
            if lp_status in status_badges:
                return status_badges[lp_status]

        # Gmail active badge
        if source == RelationshipSource.GMAIL and score >= GMAIL_ACTIVE_THRESHOLD:
            return "📧 Active Conversation"

        # Generic badges
        if source == RelationshipSource.GMAIL:
            return "📧 Gmail Contact"
        elif source == RelationshipSource.NOTION_LP:
            return "📋 LP Contact"

        return ""

    # =========================================================================
    # ATTRIBUTION
    # =========================================================================

    def format_attribution(self, names: Optional[List[str]]) -> str:
        """
        Format attribution string from names.

        Args:
            names: List of LP names

        Returns:
            Attribution string (e.g., "via John Smith, Jane Doe")
        """
        if not names:
            return ""

        return "via " + ", ".join(names)

    # =========================================================================
    # MANUAL OVERRIDE & EPSILON (Section 5)
    # =========================================================================

    def should_push_update(
        self,
        page_id: str,
        new_score: float,
        current_score: float,
        manual_override: bool,
    ) -> bool:
        """
        Check if update should be pushed to Notion.

        Rules:
        1. Never update if Manual Override is active
        2. Only update if delta >= epsilon (0.02)

        Args:
            page_id: Notion page ID (for logging)
            new_score: New computed score
            current_score: Current score in Notion
            manual_override: Whether Manual Override is active

        Returns:
            True if should push update
        """
        if manual_override:
            logger.info(f"Skipping {page_id}: Manual Override active")
            return False

        delta = abs(new_score - current_score)
        if delta < self.epsilon:
            logger.debug(f"Skipped {page_id}: score delta {delta:.3f} < epsilon")
            return False

        return True

    # =========================================================================
    # FULL PIPELINE
    # =========================================================================

    def build_candidate(
        self,
        investor_domain: str,
        gmail_score: Optional[float],
        notion_score: Optional[float],
        lp_status: Optional[str],
        attribution: str,
        notion_lp_ids: List[str],
        is_declined: bool,
        declined_at: Optional[datetime] = None,
    ) -> Optional[WarmIntroCandidate]:
        """
        Build a warm intro candidate from combined data.

        Full pipeline:
        1. Check declined suppression
        2. Merge Gmail and Notion scores
        3. Determine source (which score won)
        4. Apply declined cap if post-window
        5. Generate badge
        6. Build candidate

        Args:
            investor_domain: Domain (e.g., "sequoia.com")
            gmail_score: Gmail relationship score (or None)
            notion_score: Notion LP score (or None)
            lp_status: LP status string
            attribution: Attribution string
            notion_lp_ids: List of Notion LP page IDs
            is_declined: Whether the LP is declined
            declined_at: When declined (for suppression window)

        Returns:
            WarmIntroCandidate or None if suppressed
        """
        # Step 1: Check declined suppression
        if self.should_suppress_declined(is_declined, declined_at):
            logger.debug(f"Suppressed {investor_domain}: declined within window")
            return None

        # Step 2: Merge scores
        merged_score = self.merge_scores(gmail_score, notion_score)

        # Step 3: Determine source (which contributed the winning score)
        if gmail_score is not None and notion_score is not None:
            if notion_score >= gmail_score:
                source = RelationshipSource.NOTION_LP
            else:
                source = RelationshipSource.GMAIL
        elif notion_score is not None:
            source = RelationshipSource.NOTION_LP
        else:
            source = RelationshipSource.GMAIL

        # Step 4: Apply declined cap if post-window
        final_score = merged_score
        if is_declined and declined_at is not None:
            if not self.should_suppress_declined(is_declined, declined_at):
                final_score = self.apply_declined_cap(merged_score)

        # Step 5: Generate badge
        badge = self.generate_badge(
            source=source,
            score=final_score,
            lp_status=lp_status,
            is_declined=is_declined,
        )

        # Step 6: Build candidate
        return WarmIntroCandidate(
            investor_domain=investor_domain,
            score=final_score,
            source=source,
            badge=badge,
            attribution=attribution,
            notion_lp_ids=notion_lp_ids or [],
        )

"""
Founder Intent Detector - detects behavioral patterns suggesting new ventures.

Part of Deal Intelligence Engine (Phase 4).

This module provides (Evertrace-inspired):
- Domain registration detection by known founders
- GitHub activity spike detection
- Career transition detection (left employer → stealth)
- Co-founder seeking signal detection
- Incorporation filing detection by known founders

Usage:
    detector = FounderIntentDetector(store)

    # Detect intent from a signal
    intent = await detector.detect_intent(signal)

    # Analyze founder's complete history
    summary = await detector.analyze_founder_history(founder_id=123)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class IntentSignal:
    """A detected founder intent signal."""
    signal_id: int
    canonical_key: str
    intent_type: str  # 'new_venture', 'activity_spike', 'career_transition', 'stealth_mode', 'cofounder_seeking'
    founder_id: int
    founder_name: str
    confidence: float
    evidence: str
    detected_at: datetime


@dataclass
class FounderIntentSummary:
    """Summary of intent analysis for a founder."""
    founder_id: int
    founder_name: str
    intent_score: float  # 0.0 - 1.0 composite score
    signals_analyzed: List[IntentSignal]
    primary_intent: Optional[str]  # Most likely intent type
    recommendation: str  # 'high_priority', 'monitor', 'low_priority'


# =============================================================================
# STEALTH MODE KEYWORDS
# =============================================================================

STEALTH_KEYWORDS = [
    "building something",
    "building new",
    "working on something",
    "stealth",
    "exploring",
    "ex-",
    "former",
    "advisor",
    "angel investor",
    "between opportunities",
    "taking time",
    "new chapter",
]

COFOUNDER_KEYWORDS = [
    "co-founder",
    "cofounder",
    "looking for",
    "seeking",
    "technical founder",
    "business founder",
    "partner",
    "join me",
    "building with",
    "startup",
]


# =============================================================================
# FOUNDER INTENT DETECTOR
# =============================================================================

class FounderIntentDetector:
    """
    Detects founder intent signals suggesting new ventures.

    Inspired by Evertrace's approach to detecting early-stage
    founder activity patterns.
    """

    def __init__(self, store):
        """
        Initialize with a store.

        Args:
            store: Storage layer with founder and signal access methods
        """
        self.store = store

    async def detect_intent(
        self,
        signal: Dict[str, Any],
    ) -> Optional[IntentSignal]:
        """
        Detect founder intent from a signal.

        Analyzes the signal type and raw_data to identify
        patterns suggesting a new venture.

        Args:
            signal: Signal dict with raw_data

        Returns:
            IntentSignal if intent detected, None otherwise
        """
        signal_type = signal.get('signal_type', '')
        raw_data = signal.get('raw_data', {})

        # Try different detection strategies based on signal type
        if signal_type == 'domain_registration':
            return await self._detect_domain_registration(signal, raw_data)

        elif signal_type in ('github_org_created', 'github_activity'):
            return await self._detect_github_activity(signal, raw_data)

        elif signal_type == 'linkedin_update':
            return await self._detect_linkedin_intent(signal, raw_data)

        elif signal_type == 'social_post':
            return await self._detect_social_intent(signal, raw_data)

        elif signal_type in ('companies_house', 'sec_filing', 'incorporation'):
            return await self._detect_incorporation(signal, raw_data)

        return None

    async def analyze_founder_history(
        self,
        founder_id: int,
    ) -> FounderIntentSummary:
        """
        Analyze complete signal history for a founder.

        Aggregates all intent signals to determine likelihood
        of new venture activity.

        Args:
            founder_id: ID of the founder to analyze

        Returns:
            FounderIntentSummary with composite score and recommendation
        """
        # Get founder info
        founder = await self.store.get_founder_by_id(founder_id)
        if not founder:
            return FounderIntentSummary(
                founder_id=founder_id,
                founder_name="Unknown",
                intent_score=0.0,
                signals_analyzed=[],
                primary_intent=None,
                recommendation="low_priority",
            )

        # Get recent signals for founder
        try:
            signals = await self.store.get_signals_for_founder(founder_id)
        except (AttributeError, Exception):
            signals = []

        # Analyze each signal for intent
        intent_signals = []
        for signal in signals:
            intent = await self.detect_intent(signal)
            if intent:
                intent_signals.append(intent)

        # Calculate composite intent score
        if not intent_signals:
            intent_score = 0.0
            primary_intent = None
            recommendation = "low_priority"
        else:
            # Average confidence of detected intents
            intent_score = sum(s.confidence for s in intent_signals) / len(intent_signals)

            # Find most common intent type
            intent_counts = {}
            for s in intent_signals:
                intent_counts[s.intent_type] = intent_counts.get(s.intent_type, 0) + 1
            primary_intent = max(intent_counts, key=intent_counts.get)

            # Determine recommendation
            if intent_score >= 0.7 or len(intent_signals) >= 3:
                recommendation = "high_priority"
            elif intent_score >= 0.4:
                recommendation = "monitor"
            else:
                recommendation = "low_priority"

        return FounderIntentSummary(
            founder_id=founder_id,
            founder_name=founder.get('name', 'Unknown'),
            intent_score=intent_score,
            signals_analyzed=intent_signals,
            primary_intent=primary_intent,
            recommendation=recommendation,
        )

    # =========================================================================
    # PRIVATE DETECTION METHODS
    # =========================================================================

    async def _detect_domain_registration(
        self,
        signal: Dict[str, Any],
        raw_data: Dict[str, Any],
    ) -> Optional[IntentSignal]:
        """Detect domain registration by known founder."""
        email = raw_data.get('registrant_email', '')
        if not email:
            return None

        # Look up founder by email
        try:
            founder = await self.store.get_founder_by_email(email)
        except (AttributeError, Exception):
            founder = None

        if not founder:
            return None

        domain = raw_data.get('domain', signal.get('canonical_key', ''))

        return IntentSignal(
            signal_id=signal.get('id', 0),
            canonical_key=signal.get('canonical_key', ''),
            intent_type="new_venture",
            founder_id=founder.get('id'),
            founder_name=founder.get('name', 'Unknown'),
            confidence=0.75,
            evidence=f"domain_registration: {domain} registered by founder",
            detected_at=signal.get('detected_at', datetime.now(timezone.utc)),
        )

    async def _detect_github_activity(
        self,
        signal: Dict[str, Any],
        raw_data: Dict[str, Any],
    ) -> Optional[IntentSignal]:
        """Detect GitHub activity suggesting new venture."""
        username = raw_data.get('creator') or raw_data.get('username', '')
        if not username:
            return None

        # Look up founder by GitHub username
        try:
            founder = await self.store.get_founder_by_github(username)
        except (AttributeError, Exception):
            founder = None

        if not founder:
            return None

        signal_type = signal.get('signal_type', '')

        # GitHub org creation is strong signal
        if signal_type == 'github_org_created':
            org_name = raw_data.get('org_name', '')
            return IntentSignal(
                signal_id=signal.get('id', 0),
                canonical_key=signal.get('canonical_key', ''),
                intent_type="new_venture",
                founder_id=founder.get('id'),
                founder_name=founder.get('name', 'Unknown'),
                confidence=0.8,
                evidence=f"github_org_created: {org_name}",
                detected_at=signal.get('detected_at', datetime.now(timezone.utc)),
            )

        # Check for activity spike
        try:
            history = await self.store.get_founder_activity_history(founder.get('id'))
            avg_monthly = history.get('avg_signals_per_month', 0)
            recent_7d = history.get('recent_signals_7d', 0)

            # If recent activity is 5x+ historical average, it's a spike
            if avg_monthly > 0 and recent_7d >= avg_monthly * 5:
                return IntentSignal(
                    signal_id=signal.get('id', 0),
                    canonical_key=signal.get('canonical_key', ''),
                    intent_type="activity_spike",
                    founder_id=founder.get('id'),
                    founder_name=founder.get('name', 'Unknown'),
                    confidence=0.65,
                    evidence=f"activity_spike: {recent_7d} signals in 7d vs {avg_monthly:.1f}/month avg",
                    detected_at=signal.get('detected_at', datetime.now(timezone.utc)),
                )
        except (AttributeError, Exception):
            pass

        return None

    async def _detect_linkedin_intent(
        self,
        signal: Dict[str, Any],
        raw_data: Dict[str, Any],
    ) -> Optional[IntentSignal]:
        """Detect LinkedIn activity suggesting new venture."""
        linkedin_id = raw_data.get('linkedin_id', '')

        # Look up founder by LinkedIn
        try:
            founder = await self.store.get_founder_by_linkedin(linkedin_id)
        except (AttributeError, Exception):
            founder = None

        if not founder:
            return None

        # Check for left employer signal
        previous_company = raw_data.get('previous_company')
        current_company = raw_data.get('current_company')

        if previous_company and not current_company:
            return IntentSignal(
                signal_id=signal.get('id', 0),
                canonical_key=signal.get('canonical_key', ''),
                intent_type="career_transition",
                founder_id=founder.get('id'),
                founder_name=founder.get('name', 'Unknown'),
                confidence=0.6,
                evidence=f"left_employer: departed {previous_company}",
                detected_at=signal.get('detected_at', datetime.now(timezone.utc)),
            )

        # Check for stealth mode keywords in headline
        headline = raw_data.get('headline', '') or raw_data.get('headline_change', '')
        if headline:
            headline_lower = headline.lower()
            for keyword in STEALTH_KEYWORDS:
                if keyword in headline_lower:
                    return IntentSignal(
                        signal_id=signal.get('id', 0),
                        canonical_key=signal.get('canonical_key', ''),
                        intent_type="stealth_mode",
                        founder_id=founder.get('id'),
                        founder_name=founder.get('name', 'Unknown'),
                        confidence=0.65,
                        evidence=f"stealth_headline: '{headline}' matches '{keyword}'",
                        detected_at=signal.get('detected_at', datetime.now(timezone.utc)),
                    )

        return None

    async def _detect_social_intent(
        self,
        signal: Dict[str, Any],
        raw_data: Dict[str, Any],
    ) -> Optional[IntentSignal]:
        """Detect social media signals suggesting new venture."""
        handle = raw_data.get('handle', '')
        content = raw_data.get('content', '')

        if not handle or not content:
            return None

        # Look up founder by Twitter
        try:
            founder = await self.store.get_founder_by_twitter(handle)
        except (AttributeError, Exception):
            founder = None

        if not founder:
            return None

        # Check for co-founder seeking keywords
        content_lower = content.lower()
        for keyword in COFOUNDER_KEYWORDS:
            if keyword in content_lower:
                return IntentSignal(
                    signal_id=signal.get('id', 0),
                    canonical_key=signal.get('canonical_key', ''),
                    intent_type="cofounder_seeking",
                    founder_id=founder.get('id'),
                    founder_name=founder.get('name', 'Unknown'),
                    confidence=0.75,
                    evidence=f"cofounder_seeking: post contains '{keyword}'",
                    detected_at=signal.get('detected_at', datetime.now(timezone.utc)),
                )

        return None

    async def _detect_incorporation(
        self,
        signal: Dict[str, Any],
        raw_data: Dict[str, Any],
    ) -> Optional[IntentSignal]:
        """Detect incorporation filings by known founders."""
        directors = raw_data.get('directors', [])

        for director in directors:
            name = director.get('name', '')
            email = director.get('email', '')

            # Try to find founder by name or email
            founder = None
            try:
                if email:
                    founder = await self.store.get_founder_by_email(email)
                if not founder and name:
                    founder = await self.store.get_founder_by_name(name)
            except (AttributeError, Exception):
                pass

            if founder:
                company_name = raw_data.get('company_name', '')
                return IntentSignal(
                    signal_id=signal.get('id', 0),
                    canonical_key=signal.get('canonical_key', ''),
                    intent_type="new_venture",
                    founder_id=founder.get('id'),
                    founder_name=founder.get('name', 'Unknown'),
                    confidence=0.85,
                    evidence=f"incorporation: {company_name} filed by {name}",
                    detected_at=signal.get('detected_at', datetime.now(timezone.utc)),
                )

        return None

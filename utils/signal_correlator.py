"""
Signal Correlator - Links signals to founders and detects serial founder ventures.

Part of Deal Intelligence Engine (Phase 1).

This module provides:
- Correlation of signals to known founders by email, GitHub, LinkedIn
- Detection of founders in new signals
- Tracking of serial founder ventures across canonical keys

Usage:
    correlator = SignalCorrelator(signal_store)

    # Find all signals for a founder
    signals = await correlator.correlate_founder_signals(founder_id=1)

    # Check if signal links to known founder
    founder = await correlator.detect_founder_in_signal(signal)

    # Find all ventures for serial founder
    ventures = await correlator.find_serial_founder_ventures(founder_id=1)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class CorrelatedSignal:
    """A signal correlated to a founder."""
    signal_id: int
    canonical_key: str
    correlation_type: str  # 'email', 'github', 'linkedin', 'domain', 'name'
    confidence: float  # 0.0 - 1.0
    matched_value: str = ""  # The value that matched (e.g., email address)


@dataclass
class DetectedFounder:
    """A founder detected in a signal."""
    founder_id: int
    founder_name: str
    founder_key: str
    confidence: float  # 0.0 - 1.0
    match_type: str  # 'email', 'github', 'linkedin', 'name'
    matched_value: str = ""


# =============================================================================
# EMAIL EXTRACTION PATTERNS
# =============================================================================

EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

# Fields in raw_data that commonly contain emails
EMAIL_FIELDS = [
    'email', 'contact_email', 'author_email', 'registrant_email',
    'admin_email', 'tech_email', 'contact', 'owner_email',
    'founder_email', 'ceo_email', 'registrant',
]

# Fields that contain GitHub usernames
GITHUB_FIELDS = [
    'username', 'github_username', 'author', 'owner',
    'github_user', 'github_handle', 'creator',
]


# =============================================================================
# SIGNAL CORRELATOR
# =============================================================================

class SignalCorrelator:
    """
    Correlates signals to founders and detects serial founder ventures.

    Inspired by Evertrace's approach to linking scattered signals
    to reveal founder intent and track serial entrepreneurs.
    """

    def __init__(self, store):
        """
        Initialize with a signal/founder store.

        Args:
            store: Storage layer with founder and signal access methods
        """
        self.store = store

    async def correlate_founder_signals(
        self,
        founder_id: int,
    ) -> List[CorrelatedSignal]:
        """
        Find all signals that correlate to a specific founder.

        Searches signals for founder's email, GitHub username, LinkedIn,
        and other identifiers.

        Args:
            founder_id: ID of the founder to correlate

        Returns:
            List of CorrelatedSignal objects with match details
        """
        # Get founder profile
        founder = await self.store.get_founder_by_id(founder_id)
        if not founder:
            logger.warning(f"Founder {founder_id} not found")
            return []

        # Get signals to search through
        signals = await self.store.get_signals_for_correlation()
        if not signals:
            return []

        # Build list of identifiers to search for
        identifiers = self._extract_founder_identifiers(founder)
        if not identifiers:
            logger.debug(f"No identifiers found for founder {founder_id}")
            return []

        # Search signals for matches
        results = []
        for signal in signals:
            match = self._check_signal_for_founder(signal, identifiers, founder)
            if match:
                results.append(match)

        logger.info(
            f"Found {len(results)} correlated signals for founder {founder_id}"
        )
        return results

    async def detect_founder_in_signal(
        self,
        signal: Dict[str, Any],
    ) -> Optional[DetectedFounder]:
        """
        Check if a signal contains references to a known founder.

        Extracts identifiers (emails, GitHub usernames) from signal
        and searches founder database for matches.

        Args:
            signal: Signal dict with raw_data

        Returns:
            DetectedFounder if match found, None otherwise
        """
        # Extract potential identifiers from signal
        signal_identifiers = self._extract_identifiers_from_signal(signal)
        if not signal_identifiers:
            return None

        # Search for matching founders
        founders = await self.store.search_founders_by_identifiers(signal_identifiers)
        if not founders:
            return None

        # Return best match (first found)
        founder = founders[0]
        match_type, matched_value = self._determine_match_type(
            signal_identifiers, founder
        )

        return DetectedFounder(
            founder_id=founder.get('id'),
            founder_name=founder.get('name', 'Unknown'),
            founder_key=founder.get('founder_key', ''),
            confidence=0.85,  # High confidence for exact identifier match
            match_type=match_type,
            matched_value=matched_value,
        )

    async def find_serial_founder_ventures(
        self,
        founder_id: int,
    ) -> List[str]:
        """
        Find all ventures (canonical keys) associated with a founder.

        Useful for detecting serial entrepreneurs and tracking their
        portfolio of companies.

        Args:
            founder_id: ID of the founder

        Returns:
            List of unique canonical_key values
        """
        # Get founder info
        founder = await self.store.get_founder_by_id(founder_id)
        if not founder:
            return []

        # Get all signals linked to this founder
        signals = await self.store.get_signals_for_founder(founder_id)
        if not signals:
            return []

        # Extract unique canonical keys
        ventures = set()
        for signal in signals:
            canonical_key = signal.get('canonical_key')
            if canonical_key:
                ventures.add(canonical_key)

        return list(ventures)

    # =========================================================================
    # PRIVATE HELPER METHODS
    # =========================================================================

    def _extract_founder_identifiers(
        self,
        founder: Dict[str, Any],
    ) -> Dict[str, str]:
        """Extract searchable identifiers from founder profile."""
        identifiers = {}

        if founder.get('email'):
            identifiers['email'] = founder['email'].lower()

        if founder.get('github_username'):
            identifiers['github'] = founder['github_username'].lower()

        if founder.get('linkedin_url'):
            identifiers['linkedin'] = founder['linkedin_url'].lower()

        if founder.get('twitter_handle'):
            identifiers['twitter'] = founder['twitter_handle'].lower()

        return identifiers

    def _check_signal_for_founder(
        self,
        signal: Dict[str, Any],
        identifiers: Dict[str, str],
        founder: Dict[str, Any],
    ) -> Optional[CorrelatedSignal]:
        """Check if signal contains any of the founder's identifiers."""
        raw_data = signal.get('raw_data', {})
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except json.JSONDecodeError:
                return None

        # Check email match
        if 'email' in identifiers:
            found_email = self._find_email_in_data(raw_data)
            if found_email and found_email.lower() == identifiers['email']:
                return CorrelatedSignal(
                    signal_id=signal.get('id'),
                    canonical_key=signal.get('canonical_key', ''),
                    correlation_type='email',
                    confidence=0.9,
                    matched_value=found_email,
                )

        # Check GitHub match
        if 'github' in identifiers:
            found_github = self._find_github_in_data(raw_data)
            if found_github and found_github.lower() == identifiers['github']:
                return CorrelatedSignal(
                    signal_id=signal.get('id'),
                    canonical_key=signal.get('canonical_key', ''),
                    correlation_type='github',
                    confidence=0.85,
                    matched_value=found_github,
                )

        # Check canonical key domain match (same company)
        if founder.get('canonical_key'):
            if signal.get('canonical_key') == founder.get('canonical_key'):
                return CorrelatedSignal(
                    signal_id=signal.get('id'),
                    canonical_key=signal.get('canonical_key', ''),
                    correlation_type='domain',
                    confidence=0.7,
                    matched_value=signal.get('canonical_key', ''),
                )

        return None

    def _find_email_in_data(self, data: Dict[str, Any]) -> Optional[str]:
        """Find email address in raw_data dict."""
        # Check known email fields
        for field in EMAIL_FIELDS:
            if field in data and data[field]:
                value = str(data[field])
                if '@' in value:
                    return value

        # Search all string values for email pattern
        for key, value in data.items():
            if isinstance(value, str) and '@' in value:
                match = EMAIL_PATTERN.search(value)
                if match:
                    return match.group(0)

        return None

    def _find_github_in_data(self, data: Dict[str, Any]) -> Optional[str]:
        """Find GitHub username in raw_data dict."""
        for field in GITHUB_FIELDS:
            if field in data and data[field]:
                return str(data[field])
        return None

    def _extract_identifiers_from_signal(
        self,
        signal: Dict[str, Any],
    ) -> Dict[str, str]:
        """Extract potential founder identifiers from a signal."""
        identifiers = {}
        raw_data = signal.get('raw_data', {})

        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except json.JSONDecodeError:
                raw_data = {}

        # Extract email
        email = self._find_email_in_data(raw_data)
        if email:
            identifiers['email'] = email.lower()

        # Extract GitHub username
        github = self._find_github_in_data(raw_data)
        if github:
            identifiers['github'] = github.lower()

        return identifiers

    def _determine_match_type(
        self,
        signal_identifiers: Dict[str, str],
        founder: Dict[str, Any],
    ) -> tuple[str, str]:
        """Determine the type of match between signal and founder."""
        # Check email match
        if 'email' in signal_identifiers:
            founder_email = founder.get('email', '').lower()
            if founder_email and founder_email == signal_identifiers['email']:
                return 'email', signal_identifiers['email']

        # Check GitHub match
        if 'github' in signal_identifiers:
            founder_github = founder.get('github_username', '').lower()
            if founder_github and founder_github == signal_identifiers['github']:
                return 'github', signal_identifiers['github']

        return 'unknown', ''

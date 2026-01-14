"""
Tests for Signal Correlator - links signals to founders and detects serial founder ventures.

TDD Approach: Tests written before implementation.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from dataclasses import dataclass
from typing import List, Optional, Dict, Any


class TestCorrelateFounderSignals:
    """Test correlate_founder_signals(founder_id) - finds all signals linked to a founder."""

    @pytest.mark.asyncio
    async def test_correlate_by_email_match(self):
        """Should find signals where founder email appears in raw_data."""
        from utils.signal_correlator import SignalCorrelator, CorrelatedSignal
        from storage.signal_store import SignalStore

        # Create mock store
        mock_store = AsyncMock(spec=SignalStore)

        # Founder with known email
        mock_founder = {
            "id": 1,
            "name": "Jane Doe",
            "email": "jane@acme.ai",
            "canonical_key": "domain:acme.ai",
        }

        # Signals - one matches founder email
        mock_signals = [
            {
                "id": 1,
                "canonical_key": "domain:acme.ai",
                "signal_type": "github_trending",
                "raw_data": {"author_email": "jane@acme.ai", "stars": 500},
            },
            {
                "id": 2,
                "canonical_key": "domain:other.com",
                "signal_type": "hiring_signal",
                "raw_data": {"contact": "bob@other.com"},
            },
        ]

        mock_store.get_founder_by_id = AsyncMock(return_value=mock_founder)
        mock_store.get_signals_for_correlation = AsyncMock(return_value=mock_signals)

        correlator = SignalCorrelator(mock_store)
        results = await correlator.correlate_founder_signals(founder_id=1)

        # Should find the signal with matching email
        assert len(results) == 1
        assert results[0].signal_id == 1
        assert results[0].correlation_type == "email"
        assert results[0].confidence >= 0.8

    @pytest.mark.asyncio
    async def test_correlate_by_github_username(self):
        """Should find signals where founder GitHub username appears."""
        from utils.signal_correlator import SignalCorrelator, CorrelatedSignal

        mock_store = AsyncMock()
        mock_founder = {
            "id": 1,
            "name": "John Smith",
            "github_username": "johnsmith",
            "canonical_key": "domain:startup.io",
        }

        mock_signals = [
            {
                "id": 10,
                "canonical_key": "github_org:johnsmith",
                "signal_type": "github_activity",
                "raw_data": {"username": "johnsmith", "commits": 50},
            },
        ]

        mock_store.get_founder_by_id = AsyncMock(return_value=mock_founder)
        mock_store.get_signals_for_correlation = AsyncMock(return_value=mock_signals)

        correlator = SignalCorrelator(mock_store)
        results = await correlator.correlate_founder_signals(founder_id=1)

        assert len(results) == 1
        assert results[0].signal_id == 10
        assert results[0].correlation_type == "github"

    @pytest.mark.asyncio
    async def test_correlate_returns_empty_for_no_matches(self):
        """Should return empty list when no signals match founder."""
        from utils.signal_correlator import SignalCorrelator

        mock_store = AsyncMock()
        mock_founder = {
            "id": 1,
            "name": "Unknown Person",
            "email": "unknown@nowhere.com",
            "canonical_key": "domain:nowhere.com",
        }

        mock_signals = [
            {
                "id": 1,
                "canonical_key": "domain:other.com",
                "signal_type": "hiring_signal",
                "raw_data": {"contact": "bob@other.com"},
            },
        ]

        mock_store.get_founder_by_id = AsyncMock(return_value=mock_founder)
        mock_store.get_signals_for_correlation = AsyncMock(return_value=mock_signals)

        correlator = SignalCorrelator(mock_store)
        results = await correlator.correlate_founder_signals(founder_id=1)

        assert len(results) == 0


class TestDetectFounderInSignal:
    """Test detect_founder_in_signal(signal) - checks if signal links to known founder."""

    @pytest.mark.asyncio
    async def test_detect_founder_from_email_in_raw_data(self):
        """Should detect founder when their email is in signal raw_data."""
        from utils.signal_correlator import SignalCorrelator, DetectedFounder

        mock_store = AsyncMock()

        # Known founder in database
        mock_founders = [
            {
                "id": 5,
                "name": "Sarah Chen",
                "email": "sarah@newco.ai",
                "founder_key": "email:sarah@newco.ai",
            }
        ]

        # Signal with email in raw_data
        signal = {
            "id": 100,
            "canonical_key": "domain:newco.ai",
            "signal_type": "incorporation",
            "raw_data": {"contact_email": "sarah@newco.ai", "company": "NewCo AI"},
        }

        mock_store.search_founders_by_identifiers = AsyncMock(return_value=mock_founders)

        correlator = SignalCorrelator(mock_store)
        result = await correlator.detect_founder_in_signal(signal)

        assert result is not None
        assert result.founder_id == 5
        assert result.founder_name == "Sarah Chen"
        assert result.confidence >= 0.8

    @pytest.mark.asyncio
    async def test_detect_returns_none_for_unknown_founder(self):
        """Should return None when signal doesn't match any known founder."""
        from utils.signal_correlator import SignalCorrelator

        mock_store = AsyncMock()
        mock_store.search_founders_by_identifiers = AsyncMock(return_value=[])

        signal = {
            "id": 200,
            "canonical_key": "domain:random.com",
            "signal_type": "domain_registration",
            "raw_data": {"registrant": "unknown@random.com"},
        }

        correlator = SignalCorrelator(mock_store)
        result = await correlator.detect_founder_in_signal(signal)

        assert result is None


class TestFindSerialFounderVentures:
    """Test find_serial_founder_ventures(founder_id) - tracks all ventures of a serial founder."""

    @pytest.mark.asyncio
    async def test_find_multiple_ventures_for_serial_founder(self):
        """Should find all canonical_keys where founder has been involved."""
        from utils.signal_correlator import SignalCorrelator

        mock_store = AsyncMock()

        # Serial founder linked to multiple companies
        mock_founder = {
            "id": 1,
            "name": "Serial Sam",
            "email": "sam@founder.com",
            "is_serial_founder": True,
        }

        # Signals from different ventures
        mock_signals = [
            {"id": 1, "canonical_key": "domain:startup1.com", "signal_type": "incorporation"},
            {"id": 2, "canonical_key": "domain:startup2.io", "signal_type": "github_activity"},
            {"id": 3, "canonical_key": "domain:startup1.com", "signal_type": "hiring_signal"},
            {"id": 4, "canonical_key": "domain:startup3.ai", "signal_type": "funding_event"},
        ]

        mock_store.get_founder_by_id = AsyncMock(return_value=mock_founder)
        mock_store.get_signals_for_founder = AsyncMock(return_value=mock_signals)

        correlator = SignalCorrelator(mock_store)
        ventures = await correlator.find_serial_founder_ventures(founder_id=1)

        # Should find 3 unique ventures
        assert len(ventures) == 3
        assert "domain:startup1.com" in ventures
        assert "domain:startup2.io" in ventures
        assert "domain:startup3.ai" in ventures

    @pytest.mark.asyncio
    async def test_returns_empty_for_first_time_founder(self):
        """Should return single venture for non-serial founder."""
        from utils.signal_correlator import SignalCorrelator

        mock_store = AsyncMock()
        mock_founder = {
            "id": 2,
            "name": "First Timer",
            "is_serial_founder": False,
        }

        mock_signals = [
            {"id": 1, "canonical_key": "domain:firstcompany.com", "signal_type": "incorporation"},
        ]

        mock_store.get_founder_by_id = AsyncMock(return_value=mock_founder)
        mock_store.get_signals_for_founder = AsyncMock(return_value=mock_signals)

        correlator = SignalCorrelator(mock_store)
        ventures = await correlator.find_serial_founder_ventures(founder_id=2)

        assert len(ventures) == 1
        assert "domain:firstcompany.com" in ventures

"""
Tests for Founder Intent Detector - detects behavioral patterns suggesting new ventures.

TDD Approach: Tests written before implementation.

Founder intent patterns include (Evertrace-inspired):
- Domain registration by known founder
- GitHub org/repo creation spikes
- Career transition signals (left employer → stealth)
- Co-founder seeking signals from social media
- Incorporation filing by known founder
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from typing import List, Dict, Any


class TestDetectDomainRegistration:
    """Test detection of domain registrations by known founders."""

    @pytest.mark.asyncio
    async def test_detect_domain_registration_by_founder(self):
        """Should detect when a known founder registers a new domain."""
        from utils.founder_intent import FounderIntentDetector, IntentSignal

        mock_store = AsyncMock()

        # Known founder in database
        mock_founder = {
            "id": 1,
            "name": "Jane Founder",
            "email": "jane@oldcompany.com",
            "founder_key": "email:jane@oldcompany.com",
        }

        # Domain registration signal with founder's email
        signal = {
            "id": 100,
            "canonical_key": "domain:newstartup.ai",
            "signal_type": "domain_registration",
            "detected_at": datetime.now(timezone.utc),
            "raw_data": {
                "registrant_email": "jane@oldcompany.com",
                "domain": "newstartup.ai",
                "registration_date": "2026-01-10",
            },
        }

        mock_store.get_founder_by_email = AsyncMock(return_value=mock_founder)

        detector = FounderIntentDetector(mock_store)
        result = await detector.detect_intent(signal)

        assert result is not None
        assert result.intent_type == "new_venture"
        assert result.founder_id == 1
        assert result.confidence >= 0.7
        assert "domain_registration" in result.evidence

    @pytest.mark.asyncio
    async def test_no_detection_for_unknown_registrant(self):
        """Should return None when domain registrant is unknown."""
        from utils.founder_intent import FounderIntentDetector

        mock_store = AsyncMock()
        mock_store.get_founder_by_email = AsyncMock(return_value=None)

        signal = {
            "id": 101,
            "canonical_key": "domain:random.com",
            "signal_type": "domain_registration",
            "detected_at": datetime.now(timezone.utc),
            "raw_data": {
                "registrant_email": "unknown@random.com",
                "domain": "random.com",
            },
        }

        detector = FounderIntentDetector(mock_store)
        result = await detector.detect_intent(signal)

        assert result is None


class TestDetectGitHubActivity:
    """Test detection of GitHub activity suggesting new venture."""

    @pytest.mark.asyncio
    async def test_detect_github_org_creation(self):
        """Should detect when founder creates new GitHub organization."""
        from utils.founder_intent import FounderIntentDetector, IntentSignal

        mock_store = AsyncMock()

        mock_founder = {
            "id": 2,
            "name": "Dev Founder",
            "github_username": "devfounder",
        }

        signal = {
            "id": 200,
            "canonical_key": "github_org:newventure",
            "signal_type": "github_org_created",
            "detected_at": datetime.now(timezone.utc),
            "raw_data": {
                "org_name": "newventure",
                "creator": "devfounder",
                "created_at": "2026-01-12",
            },
        }

        mock_store.get_founder_by_github = AsyncMock(return_value=mock_founder)

        detector = FounderIntentDetector(mock_store)
        result = await detector.detect_intent(signal)

        assert result is not None
        assert result.intent_type == "new_venture"
        assert result.founder_id == 2
        assert "github_org_created" in result.evidence

    @pytest.mark.asyncio
    async def test_detect_activity_spike(self):
        """Should detect unusual spike in GitHub activity from founder."""
        from utils.founder_intent import FounderIntentDetector, IntentSignal

        mock_store = AsyncMock()

        mock_founder = {
            "id": 3,
            "name": "Active Founder",
            "github_username": "activefounder",
        }

        # Recent signals showing activity spike
        recent_signals = [
            {"id": i, "signal_type": "github_activity", "detected_at": datetime.now(timezone.utc) - timedelta(days=i)}
            for i in range(10)  # 10 activity signals in last 10 days
        ]

        # Historical average was 1 per month
        mock_store.get_founder_by_github = AsyncMock(return_value=mock_founder)
        mock_store.get_founder_activity_history = AsyncMock(return_value={
            "avg_signals_per_month": 1.0,
            "recent_signals_7d": 10,
        })

        signal = {
            "id": 300,
            "canonical_key": "github_org:activefounder",
            "signal_type": "github_activity",
            "detected_at": datetime.now(timezone.utc),
            "raw_data": {
                "username": "activefounder",
                "commits": 50,
                "new_repos": 3,
            },
        }

        detector = FounderIntentDetector(mock_store)
        result = await detector.detect_intent(signal)

        assert result is not None
        assert result.intent_type == "activity_spike"
        assert result.confidence >= 0.6


class TestDetectCareerTransition:
    """Test detection of career transition suggesting stealth mode."""

    @pytest.mark.asyncio
    async def test_detect_left_employer(self):
        """Should detect when founder leaves employer (potential stealth)."""
        from utils.founder_intent import FounderIntentDetector, IntentSignal

        mock_store = AsyncMock()

        mock_founder = {
            "id": 4,
            "name": "Stealth Founder",
            "linkedin_url": "linkedin.com/in/stealthfounder",
            "current_company": "BigTech Corp",
        }

        signal = {
            "id": 400,
            "canonical_key": "linkedin:stealthfounder",
            "signal_type": "linkedin_update",
            "detected_at": datetime.now(timezone.utc),
            "raw_data": {
                "linkedin_id": "stealthfounder",
                "headline_change": "Exploring new opportunities",
                "previous_company": "BigTech Corp",
                "current_company": None,  # Left employer
            },
        }

        mock_store.get_founder_by_linkedin = AsyncMock(return_value=mock_founder)

        detector = FounderIntentDetector(mock_store)
        result = await detector.detect_intent(signal)

        assert result is not None
        assert result.intent_type == "career_transition"
        assert result.confidence >= 0.5
        assert "left_employer" in result.evidence

    @pytest.mark.asyncio
    async def test_detect_stealth_mode_headline(self):
        """Should detect stealth mode keywords in LinkedIn headline."""
        from utils.founder_intent import FounderIntentDetector

        mock_store = AsyncMock()

        mock_founder = {
            "id": 5,
            "name": "Building Founder",
            "linkedin_url": "linkedin.com/in/buildingfounder",
        }

        signal = {
            "id": 401,
            "canonical_key": "linkedin:buildingfounder",
            "signal_type": "linkedin_update",
            "detected_at": datetime.now(timezone.utc),
            "raw_data": {
                "linkedin_id": "buildingfounder",
                "headline": "Building something new | Ex-Google",
                "headline_change": "Building something new",
            },
        }

        mock_store.get_founder_by_linkedin = AsyncMock(return_value=mock_founder)

        detector = FounderIntentDetector(mock_store)
        result = await detector.detect_intent(signal)

        assert result is not None
        assert result.intent_type == "stealth_mode"
        assert result.confidence >= 0.6


class TestDetectCofounderSeeking:
    """Test detection of co-founder seeking signals."""

    @pytest.mark.asyncio
    async def test_detect_cofounder_seeking_post(self):
        """Should detect when founder is seeking co-founder."""
        from utils.founder_intent import FounderIntentDetector, IntentSignal

        mock_store = AsyncMock()

        mock_founder = {
            "id": 6,
            "name": "Solo Founder",
            "twitter_handle": "solofounder",
        }

        signal = {
            "id": 500,
            "canonical_key": "twitter:solofounder",
            "signal_type": "social_post",
            "detected_at": datetime.now(timezone.utc),
            "raw_data": {
                "handle": "solofounder",
                "content": "Looking for a technical co-founder for my AI startup. DM if interested!",
                "platform": "twitter",
            },
        }

        mock_store.get_founder_by_twitter = AsyncMock(return_value=mock_founder)

        detector = FounderIntentDetector(mock_store)
        result = await detector.detect_intent(signal)

        assert result is not None
        assert result.intent_type == "cofounder_seeking"
        assert result.confidence >= 0.7
        assert "cofounder" in result.evidence.lower() or "co-founder" in result.evidence.lower()


class TestDetectIncorporation:
    """Test detection of incorporation filings by known founders."""

    @pytest.mark.asyncio
    async def test_detect_incorporation_by_founder(self):
        """Should detect when founder files incorporation."""
        from utils.founder_intent import FounderIntentDetector, IntentSignal

        mock_store = AsyncMock()

        mock_founder = {
            "id": 7,
            "name": "Corporate Founder",
            "email": "corp@founder.com",
        }

        signal = {
            "id": 600,
            "canonical_key": "company:newcorp-inc",
            "signal_type": "companies_house",
            "detected_at": datetime.now(timezone.utc),
            "raw_data": {
                "company_name": "NewCorp Inc",
                "directors": [{"name": "Corporate Founder", "email": "corp@founder.com"}],
                "incorporation_date": "2026-01-08",
            },
        }

        mock_store.get_founder_by_email = AsyncMock(return_value=mock_founder)
        mock_store.get_founder_by_name = AsyncMock(return_value=mock_founder)

        detector = FounderIntentDetector(mock_store)
        result = await detector.detect_intent(signal)

        assert result is not None
        assert result.intent_type == "new_venture"
        assert result.founder_id == 7
        assert result.confidence >= 0.8
        assert "incorporation" in result.evidence


class TestIntentSignalDataclass:
    """Test IntentSignal dataclass structure."""

    def test_intent_signal_has_required_fields(self):
        """IntentSignal should have all required fields."""
        from utils.founder_intent import IntentSignal

        intent = IntentSignal(
            signal_id=100,
            canonical_key="domain:startup.ai",
            intent_type="new_venture",
            founder_id=1,
            founder_name="Jane Founder",
            confidence=0.85,
            evidence="domain_registration: startup.ai registered by founder",
            detected_at=datetime.now(timezone.utc),
        )

        assert intent.signal_id == 100
        assert intent.canonical_key == "domain:startup.ai"
        assert intent.intent_type == "new_venture"
        assert intent.founder_id == 1
        assert intent.confidence == 0.85


class TestAnalyzeFounderHistory:
    """Test analyze_founder_history() for comprehensive intent analysis."""

    @pytest.mark.asyncio
    async def test_analyze_founder_with_multiple_signals(self):
        """Should analyze all recent signals for a founder."""
        from utils.founder_intent import FounderIntentDetector, FounderIntentSummary

        mock_store = AsyncMock()

        # Multiple signals from same founder
        founder_signals = [
            {"id": 1, "signal_type": "domain_registration", "raw_data": {"domain": "new.ai"}},
            {"id": 2, "signal_type": "github_org_created", "raw_data": {"org_name": "newai"}},
            {"id": 3, "signal_type": "linkedin_update", "raw_data": {"headline": "Building new things"}},
        ]

        mock_store.get_signals_for_founder = AsyncMock(return_value=founder_signals)
        mock_store.get_founder_by_id = AsyncMock(return_value={
            "id": 10,
            "name": "Multi-Signal Founder",
            "email": "multi@founder.com",
        })

        detector = FounderIntentDetector(mock_store)
        summary = await detector.analyze_founder_history(founder_id=10)

        assert isinstance(summary, FounderIntentSummary)
        assert summary.founder_id == 10
        assert summary.intent_score > 0.0  # Should have some intent score
        assert len(summary.signals_analyzed) > 0

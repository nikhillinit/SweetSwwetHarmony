"""
Tests for distribution/builders/digest_builder.py

Priority tests:
1. URL Correctness - PUBLIC_* base URLs render correctly
2. Anti-Spam Selection - Already-digested companies excluded
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from distribution.config import DistributionConfig
from distribution.builders.digest_builder import (
    DigestBuilder,
    DigestCompany,
    DigestResult,
)


def make_config(
    api_url: str = "https://api.example.com",
    profile_url: str = None,
) -> DistributionConfig:
    """Create a test config."""
    return DistributionConfig(
        public_api_base_url=api_url,
        public_profile_base_url=profile_url,
        digest_from_email="deals@example.com",
        digest_to_emails=["gp@example.com"],
        email_transport="console",
    )


def make_inbox_company(
    canonical_key: str,
    company_name: str = "Test Co",
    confidence: float = 0.8,
    days_ago: int = 1,
):
    """Create a mock InboxCompany."""
    now = datetime.now(timezone.utc)
    mock = MagicMock()
    mock.canonical_key = canonical_key
    mock.company_name = company_name
    mock.max_confidence = confidence
    mock.sources = "GitHub, SEC"
    mock.first_seen = now - timedelta(days=days_ago)
    mock.last_seen = now - timedelta(days=days_ago)
    return mock


class TestURLCorrectness:
    """
    PRIORITY 1: Verify PUBLIC_* base URLs render correctly in digests.

    Wrong URLs = broken magic links = silent failure.
    """

    @pytest.mark.asyncio
    async def test_action_urls_use_public_api_base_url(self):
        """Track/Pass URLs should use PUBLIC_API_BASE_URL."""
        config = make_config(api_url="https://api.prod.example.com")
        builder = DigestBuilder(config)

        # Mock store
        mock_store = MagicMock()
        mock_store.get_inbox_companies = AsyncMock(return_value=[
            make_inbox_company("domain:test.com"),
        ])
        mock_store.get_company_by_key = AsyncMock(return_value={
            "one_liner": "Test description",
            "website": "https://test.com",
        })
        mock_store._db = MagicMock()
        mock_store._db.execute = AsyncMock(return_value=MagicMock(fetchall=AsyncMock(return_value=[])))
        mock_store.reserve_token_nonce = AsyncMock()
        mock_store.get_stats = AsyncMock(return_value={})

        result = await builder.build_weekly_digest(
            store=mock_store,
            recipient="gp@example.com",
        )

        # Track and Pass URLs should contain the correct base URL
        assert "https://api.prod.example.com/api/v1/actions/execute" in result.html

    @pytest.mark.asyncio
    async def test_details_url_uses_public_profile_base_url(self):
        """View Details URL should use PUBLIC_PROFILE_BASE_URL."""
        config = make_config(
            api_url="https://api.example.com",
            profile_url="https://profile.example.com",
        )
        builder = DigestBuilder(config)

        mock_store = MagicMock()
        mock_store.get_inbox_companies = AsyncMock(return_value=[
            make_inbox_company("domain:test.com"),
        ])
        mock_store.get_company_by_key = AsyncMock(return_value={
            "one_liner": "Test description",
            "website": "https://test.com",
        })
        mock_store._db = MagicMock()
        mock_store._db.execute = AsyncMock(return_value=MagicMock(fetchall=AsyncMock(return_value=[])))
        mock_store.reserve_token_nonce = AsyncMock()
        mock_store.get_stats = AsyncMock(return_value={})

        result = await builder.build_weekly_digest(
            store=mock_store,
            recipient="gp@example.com",
        )

        # Details URL should use profile base URL
        assert "https://profile.example.com/api/v1/companies/domain:test.com/public" in result.html

    @pytest.mark.asyncio
    async def test_profile_url_defaults_to_api_url(self):
        """If PUBLIC_PROFILE_BASE_URL not set, should use PUBLIC_API_BASE_URL."""
        config = make_config(api_url="https://api.example.com", profile_url=None)
        builder = DigestBuilder(config)

        mock_store = MagicMock()
        mock_store.get_inbox_companies = AsyncMock(return_value=[
            make_inbox_company("domain:test.com"),
        ])
        mock_store.get_company_by_key = AsyncMock(return_value={})
        mock_store._db = MagicMock()
        mock_store._db.execute = AsyncMock(return_value=MagicMock(fetchall=AsyncMock(return_value=[])))
        mock_store.reserve_token_nonce = AsyncMock()
        mock_store.get_stats = AsyncMock(return_value={})

        result = await builder.build_weekly_digest(
            store=mock_store,
            recipient="gp@example.com",
        )

        # Details URL should fall back to API URL
        assert "https://api.example.com/api/v1/companies/domain:test.com/public" in result.html


class TestAntiSpamSelection:
    """
    PRIORITY 5: Verify already-digested companies are excluded.

    Sending same companies every week = GP trust collapse.
    """

    @pytest.mark.asyncio
    async def test_recently_digested_companies_excluded(self):
        """Companies with digest_sent in last 7 days should be excluded."""
        config = make_config()
        builder = DigestBuilder(config)

        # Mock store with 2 companies
        company1 = make_inbox_company("domain:company1.com", "Company 1")
        company2 = make_inbox_company("domain:company2.com", "Company 2")

        mock_store = MagicMock()
        mock_store.get_inbox_companies = AsyncMock(return_value=[company1, company2])
        mock_store.get_company_by_key = AsyncMock(return_value={})
        mock_store.reserve_token_nonce = AsyncMock()
        mock_store.get_stats = AsyncMock(return_value={})

        # Mock DB query: company1 was already digested
        mock_cursor = MagicMock()
        mock_cursor.fetchall = AsyncMock(return_value=[("domain:company1.com",)])
        mock_store._db = MagicMock()
        mock_store._db.execute = AsyncMock(return_value=mock_cursor)

        result = await builder.build_weekly_digest(
            store=mock_store,
            recipient="gp@example.com",
        )

        # Only company2 should be in the digest
        assert result.company_count == 1
        assert "domain:company2.com" in result.company_keys
        assert "domain:company1.com" not in result.company_keys

    @pytest.mark.asyncio
    async def test_old_companies_excluded(self):
        """Companies with no recent signals should be excluded."""
        config = make_config()
        builder = DigestBuilder(config)

        # One recent company, one old company
        recent_company = make_inbox_company("domain:recent.com", "Recent Co", days_ago=2)
        old_company = make_inbox_company("domain:old.com", "Old Co", days_ago=30)

        mock_store = MagicMock()
        mock_store.get_inbox_companies = AsyncMock(return_value=[recent_company, old_company])
        mock_store.get_company_by_key = AsyncMock(return_value={})
        mock_store.reserve_token_nonce = AsyncMock()
        mock_store.get_stats = AsyncMock(return_value={})

        # No companies digested yet
        mock_cursor = MagicMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_store._db = MagicMock()
        mock_store._db.execute = AsyncMock(return_value=mock_cursor)

        result = await builder.build_weekly_digest(
            store=mock_store,
            recipient="gp@example.com",
        )

        # Only recent company should be included
        assert "domain:recent.com" in result.company_keys
        assert "domain:old.com" not in result.company_keys

    @pytest.mark.asyncio
    async def test_companies_sorted_by_confidence(self):
        """Companies should be sorted by confidence (highest first)."""
        config = make_config()
        builder = DigestBuilder(config)

        low_conf = make_inbox_company("domain:low.com", "Low Conf", confidence=0.3)
        high_conf = make_inbox_company("domain:high.com", "High Conf", confidence=0.9)
        mid_conf = make_inbox_company("domain:mid.com", "Mid Conf", confidence=0.6)

        mock_store = MagicMock()
        mock_store.get_inbox_companies = AsyncMock(return_value=[low_conf, high_conf, mid_conf])
        mock_store.get_company_by_key = AsyncMock(return_value={})
        mock_store.reserve_token_nonce = AsyncMock()
        mock_store.get_stats = AsyncMock(return_value={})

        mock_cursor = MagicMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_store._db = MagicMock()
        mock_store._db.execute = AsyncMock(return_value=mock_cursor)

        result = await builder.build_weekly_digest(
            store=mock_store,
            recipient="gp@example.com",
        )

        # Should be sorted: high, mid, low
        assert result.company_keys[0] == "domain:high.com"
        assert result.company_keys[1] == "domain:mid.com"
        assert result.company_keys[2] == "domain:low.com"

    @pytest.mark.asyncio
    async def test_max_companies_limit_enforced(self):
        """Should not exceed max_companies_per_digest."""
        config = make_config()
        config.max_companies_per_digest = 2
        builder = DigestBuilder(config)

        companies = [
            make_inbox_company(f"domain:company{i}.com", f"Company {i}", confidence=0.9 - i * 0.1)
            for i in range(5)
        ]

        mock_store = MagicMock()
        mock_store.get_inbox_companies = AsyncMock(return_value=companies)
        mock_store.get_company_by_key = AsyncMock(return_value={})
        mock_store.reserve_token_nonce = AsyncMock()
        mock_store.get_stats = AsyncMock(return_value={})

        mock_cursor = MagicMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_store._db = MagicMock()
        mock_store._db.execute = AsyncMock(return_value=mock_cursor)

        result = await builder.build_weekly_digest(
            store=mock_store,
            recipient="gp@example.com",
        )

        # Should only include 2 companies (the highest confidence ones)
        assert result.company_count == 2


class TestDigestOutput:
    """Tests for digest output format."""

    @pytest.mark.asyncio
    async def test_empty_digest_when_no_companies(self):
        """Should handle empty company list gracefully."""
        config = make_config()
        builder = DigestBuilder(config)

        mock_store = MagicMock()
        mock_store.get_inbox_companies = AsyncMock(return_value=[])
        mock_store.get_stats = AsyncMock(return_value={})
        mock_store._db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_store._db.execute = AsyncMock(return_value=mock_cursor)

        result = await builder.build_weekly_digest(
            store=mock_store,
            recipient="gp@example.com",
        )

        assert result.company_count == 0
        assert result.company_keys == []
        assert len(result.html) > 0  # Should still render template

    @pytest.mark.asyncio
    async def test_both_html_and_text_generated(self):
        """Should generate both HTML and plain text versions."""
        config = make_config()
        builder = DigestBuilder(config)

        mock_store = MagicMock()
        mock_store.get_inbox_companies = AsyncMock(return_value=[
            make_inbox_company("domain:test.com"),
        ])
        mock_store.get_company_by_key = AsyncMock(return_value={"one_liner": "Test"})
        mock_store.reserve_token_nonce = AsyncMock()
        mock_store.get_stats = AsyncMock(return_value={})
        mock_store._db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_store._db.execute = AsyncMock(return_value=mock_cursor)

        result = await builder.build_weekly_digest(
            store=mock_store,
            recipient="gp@example.com",
        )

        assert len(result.html) > 0
        assert len(result.text) > 0
        assert "<html" in result.html.lower()
        assert "<html" not in result.text.lower()

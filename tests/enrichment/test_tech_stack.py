"""Tests for tech stack enrichment client."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


class TestTechStackResult:
    """Tests for TechStackResult dataclass."""

    def test_result_fields(self):
        """TechStackResult should have all required fields."""
        from enrichment.tech_stack import TechStackResult
        result = TechStackResult(
            domain="acme.com",
            technologies=["React", "Node.js", "AWS"],
            categories={"frontend": ["React"], "backend": ["Node.js"]},
            analytics=["Google Analytics"],
            hosting=["AWS"]
        )
        assert result.domain == "acme.com"
        assert "React" in result.technologies
        assert "frontend" in result.categories
        assert "Google Analytics" in result.analytics

    def test_result_optional_fields(self):
        """TechStackResult optional fields should have defaults."""
        from enrichment.tech_stack import TechStackResult
        result = TechStackResult(
            domain="test.com",
            technologies=["React"],
            categories={},
            analytics=[],
            hosting=[]
        )
        assert result.cdn is None
        assert result.cms is None
        assert result.ecommerce is None


class TestTechStackClient:
    """Tests for TechStack client."""

    def test_client_initialization(self):
        """TechStackClient should initialize correctly."""
        from enrichment.tech_stack import TechStackClient
        client = TechStackClient()
        assert client is not None

    def test_client_with_api_key(self):
        """TechStackClient should accept API key."""
        from enrichment.tech_stack import TechStackClient
        client = TechStackClient(api_key="test-key")
        assert client.api_key == "test-key"

    @pytest.mark.asyncio
    async def test_analyze_domain_returns_result(self):
        """analyze should return TechStackResult."""
        from enrichment.tech_stack import TechStackClient, TechStackResult
        client = TechStackClient()
        with patch.object(client, '_fetch_tech_stack', new_callable=AsyncMock) as mock:
            mock.return_value = TechStackResult(
                domain="test.com",
                technologies=["React"],
                categories={},
                analytics=[],
                hosting=[]
            )
            result = await client.analyze("test.com")
            assert result is not None
            assert result.domain == "test.com"

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self):
        """analyze should handle errors gracefully."""
        from enrichment.tech_stack import TechStackClient
        client = TechStackClient()
        with patch.object(client, '_fetch_tech_stack', new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("API error")
            result = await client.analyze("test.com")
            # Should return empty result, not raise
            assert result is not None
            assert len(result.technologies) == 0

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        """Client should enforce rate limiting."""
        from enrichment.tech_stack import TechStackClient
        client = TechStackClient()
        assert hasattr(client, '_last_request_time')
        assert client.RATE_LIMIT_DELAY > 0

    @pytest.mark.asyncio
    async def test_analyze_batch(self):
        """analyze_batch should return results for multiple domains."""
        from enrichment.tech_stack import TechStackClient, TechStackResult
        client = TechStackClient()
        with patch.object(client, '_fetch_tech_stack', new_callable=AsyncMock) as mock:
            mock.return_value = TechStackResult(
                domain="test.com",
                technologies=["React"],
                categories={},
                analytics=[],
                hosting=[]
            )
            results = await client.analyze_batch(["test1.com", "test2.com"])
            assert len(results) == 2

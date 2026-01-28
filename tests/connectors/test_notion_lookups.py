"""
Tests for NotionConnector lookup and query operations.

Covers:
- _find_by_discovery_id: Find by discovery ID
- _find_by_canonical_key: Find by canonical key
- _find_by_website: Find by website URL
- get_suppression_list: Get deals to suppress
- get_portfolio_companies: Get funded companies
"""

import os
import sys
from typing import Any, Dict

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from connectors.notion_connector_v2 import (
    NotionConnector,
    ProspectPayload,
    InvestmentStage,
)

from tests.connectors.conftest import MockNotionTransport


# =============================================================================
# FIND BY DISCOVERY ID TESTS
# =============================================================================

class TestFindByDiscoveryId:
    """Tests for _find_by_discovery_id method."""

    @pytest.mark.asyncio
    async def test_find_by_discovery_id_found(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should return page when discovery ID matches."""
        page = {
            "id": "page-123",
            "properties": {
                "Discovery ID": {"rich_text": [{"text": {"content": "disc-test"}}]},
            },
        }
        mock_transport.add_query_response([page])

        result = await connector._find_by_discovery_id("disc-test")

        assert result is not None
        assert result["id"] == "page-123"

    @pytest.mark.asyncio
    async def test_find_by_discovery_id_not_found(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should return None when no match."""
        mock_transport.add_query_response([])

        result = await connector._find_by_discovery_id("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_find_by_discovery_id_empty_string(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should return None for empty discovery ID."""
        result = await connector._find_by_discovery_id("")

        assert result is None

    @pytest.mark.asyncio
    async def test_find_by_discovery_id_none(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should return None for None discovery ID."""
        result = await connector._find_by_discovery_id(None)

        assert result is None


# =============================================================================
# FIND BY CANONICAL KEY TESTS
# =============================================================================

class TestFindByCanonicalKey:
    """Tests for _find_by_canonical_key method."""

    @pytest.mark.asyncio
    async def test_find_by_canonical_key_found(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should return page when canonical key matches."""
        page = {
            "id": "page-456",
            "properties": {
                "Canonical Key": {"rich_text": [{"text": {"content": "domain:test.com"}}]},
            },
        }
        mock_transport.add_query_response([page])

        result = await connector._find_by_canonical_key("domain:test.com")

        assert result is not None
        assert result["id"] == "page-456"

    @pytest.mark.asyncio
    async def test_find_by_canonical_key_normalizes(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should normalize canonical key for matching."""
        page = {
            "id": "page-789",
            "properties": {
                "Canonical Key": {"rich_text": [{"text": {"content": "domain:test.com"}}]},
            },
        }
        mock_transport.add_query_response([page])

        # Should match even with different case
        result = await connector._find_by_canonical_key("DOMAIN:TEST.COM")

        assert result is not None

    @pytest.mark.asyncio
    async def test_find_by_canonical_key_not_found(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should return None when no match."""
        mock_transport.add_query_response([])

        result = await connector._find_by_canonical_key("domain:nonexistent.com")

        assert result is None


# =============================================================================
# FIND BY WEBSITE TESTS
# =============================================================================

class TestFindByWebsite:
    """Tests for _find_by_website method."""

    @pytest.mark.asyncio
    async def test_find_by_website_found(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should return page when website matches."""
        page = {
            "id": "page-web",
            "properties": {
                "Website": {"url": "https://example.com"},
            },
        }
        mock_transport.add_query_response([page])

        result = await connector._find_by_website("https://example.com")

        assert result is not None
        assert result["id"] == "page-web"

    @pytest.mark.asyncio
    async def test_find_by_website_normalizes_url(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should normalize website URL for matching."""
        page = {
            "id": "page-norm",
            "properties": {
                "Website": {"url": "https://www.example.com/"},
            },
        }
        mock_transport.add_query_response([page])

        # Should match with different format
        result = await connector._find_by_website("http://example.com")

        assert result is not None

    @pytest.mark.asyncio
    async def test_find_by_website_not_found(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should return None when no match."""
        mock_transport.add_query_response([])

        result = await connector._find_by_website("https://nonexistent.com")

        assert result is None


# =============================================================================
# GET SUPPRESSION LIST TESTS
# =============================================================================

class TestGetSuppressionList:
    """Tests for get_suppression_list method."""

    @pytest.mark.asyncio
    async def test_get_suppression_list_returns_dict(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should return dict keyed by identifiers."""
        page = {
            "id": "suppressed-1",
            "properties": {
                "Status": {"select": {"name": "Passed"}},
                "Discovery ID": {"rich_text": [{"text": {"content": "disc-sup"}}]},
                "Canonical Key": {"rich_text": [{"text": {"content": "domain:suppressed.com"}}]},
                "Website": {"url": "https://suppressed.com"},
            },
        }
        mock_transport.add_query_response([page])

        result = await connector.get_suppression_list(force_refresh=True)

        assert "discovery:disc-sup" in result
        assert "canonical:domain:suppressed.com" in result
        assert "website:suppressed.com" in result

    @pytest.mark.asyncio
    async def test_get_suppression_list_caches(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should cache results."""
        page = {
            "id": "cached-1",
            "properties": {
                "Status": {"select": {"name": "Funded"}},
                "Discovery ID": {"rich_text": [{"text": {"content": "disc-cache"}}]},
            },
        }
        mock_transport.add_query_response([page])

        # First call
        result1 = await connector.get_suppression_list(force_refresh=True)

        # Second call should use cache (no new query response added)
        result2 = await connector.get_suppression_list()

        assert result1 == result2

    @pytest.mark.asyncio
    async def test_get_suppression_list_force_refresh(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Force refresh should bypass cache."""
        page1 = {
            "id": "page-1",
            "properties": {
                "Status": {"select": {"name": "Passed"}},
                "Discovery ID": {"rich_text": [{"text": {"content": "disc-1"}}]},
            },
        }
        page2 = {
            "id": "page-2",
            "properties": {
                "Status": {"select": {"name": "Passed"}},
                "Discovery ID": {"rich_text": [{"text": {"content": "disc-2"}}]},
            },
        }

        mock_transport.add_query_response([page1])
        result1 = await connector.get_suppression_list(force_refresh=True)

        mock_transport.add_query_response([page2])
        result2 = await connector.get_suppression_list(force_refresh=True)

        assert "discovery:disc-1" in result1
        assert "discovery:disc-2" in result2

    @pytest.mark.asyncio
    async def test_get_suppression_list_includes_all_statuses(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should include all suppression statuses."""
        pages = []
        for status in ["Passed", "Lost", "Funded", "Source"]:
            pages.append({
                "id": f"page-{status}",
                "properties": {
                    "Status": {"select": {"name": status}},
                    "Discovery ID": {"rich_text": [{"text": {"content": f"disc-{status}"}}]},
                },
            })

        mock_transport.add_query_response(pages)

        result = await connector.get_suppression_list(force_refresh=True)

        assert len(result) >= 4  # At least one entry per status


# =============================================================================
# GET PORTFOLIO COMPANIES TESTS
# =============================================================================

class TestGetPortfolioCompanies:
    """Tests for get_portfolio_companies method."""

    @pytest.mark.asyncio
    async def test_get_portfolio_companies_returns_list(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should return list of funded companies."""
        pages = [
            {
                "id": "funded-1",
                "properties": {
                    "Company Name": {"title": [{"text": {"content": "Portfolio Co 1"}}]},
                    "Status": {"select": {"name": "Funded"}},
                    "Website": {"url": "https://portfolio1.com"},
                    "Sector": {"select": {"name": "CPG"}},
                },
            },
            {
                "id": "funded-2",
                "properties": {
                    "Company Name": {"title": [{"text": {"content": "Portfolio Co 2"}}]},
                    "Status": {"select": {"name": "Funded"}},
                    "Website": {"url": "https://portfolio2.com"},
                    "Sector": {"select": {"name": "Healthcare"}},
                },
            },
        ]
        mock_transport.add_query_response(pages)

        result = await connector.get_portfolio_companies()

        assert len(result) == 2
        assert result[0]["company_name"] == "Portfolio Co 1"
        assert result[1]["company_name"] == "Portfolio Co 2"

    @pytest.mark.asyncio
    async def test_get_portfolio_companies_extracts_fields(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should extract company_name, website, sector."""
        page = {
            "id": "funded-full",
            "properties": {
                "Company Name": {"title": [{"text": {"content": "Full Portfolio Co"}}]},
                "Status": {"select": {"name": "Funded"}},
                "Website": {"url": "https://fullportfolio.com"},
                "Sector": {"select": {"name": "AI / ML"}},
            },
        }
        mock_transport.add_query_response([page])

        result = await connector.get_portfolio_companies()

        assert len(result) == 1
        company = result[0]
        assert company["page_id"] == "funded-full"
        assert company["company_name"] == "Full Portfolio Co"
        assert company["website"] == "https://fullportfolio.com"
        assert company["sector"] == "AI / ML"

    @pytest.mark.asyncio
    async def test_get_portfolio_companies_empty(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should return empty list when no funded companies."""
        mock_transport.add_query_response([])

        result = await connector.get_portfolio_companies()

        assert result == []


# =============================================================================
# CACHE INVALIDATION TESTS
# =============================================================================

class TestCacheInvalidation:
    """Tests for cache invalidation."""

    @pytest.mark.asyncio
    async def test_invalidate_cache_forces_refresh(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """invalidate_cache should force next call to refresh."""
        page1 = {
            "id": "page-1",
            "properties": {
                "Status": {"select": {"name": "Passed"}},
                "Discovery ID": {"rich_text": [{"text": {"content": "disc-1"}}]},
            },
        }
        mock_transport.add_query_response([page1])
        await connector.get_suppression_list(force_refresh=True)

        connector.invalidate_cache()

        # Now it should make a new request
        page2 = {
            "id": "page-2",
            "properties": {
                "Status": {"select": {"name": "Passed"}},
                "Discovery ID": {"rich_text": [{"text": {"content": "disc-2"}}]},
            },
        }
        mock_transport.add_query_response([page2])
        result = await connector.get_suppression_list()

        assert "discovery:disc-2" in result

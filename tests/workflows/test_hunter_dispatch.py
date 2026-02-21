"""
Tests for _hunter_collector_dispatch extraction and canonical key logic.

Verifies that HN/news dispatch uses the shared extractor and produces
correct canonical keys (domain:* or name_loc:*), preserving raw titles
in raw_data instead of company_name.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture
def mock_httpx_response():
    """Helper to create a mock httpx response."""

    def _make(json_data, status_code=200):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = json_data
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    return _make


class TestHNDispatchExtraction:
    """Test HN dispatch uses extractor and produces canonical keys."""

    @pytest.mark.asyncio
    async def test_hn_dispatch_extracts_company_name(self, mock_httpx_response):
        """HN dispatch should extract company name from title, not use raw title."""
        from run_pipeline import _hunter_collector_dispatch

        hn_response = {
            "hits": [
                {
                    "title": "Acme (acme.ai) raises $5M Series A",
                    "url": "https://techcrunch.com/acme",
                    "objectID": "12345",
                    "points": 100,
                    "num_comments": 30,
                },
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                return_value=mock_httpx_response(hn_response)
            )

            results = await _hunter_collector_dispatch("hacker_news", "acme")

        assert len(results) == 1
        r = results[0]
        # company_name should be extracted (not the raw title)
        assert r["company_name"] != "Acme (acme.ai) raises $5M Series A"
        # raw_data preserves original title
        assert r["raw_data"]["title"] == "Acme (acme.ai) raises $5M Series A"
        # canonical_key should be domain-based (acme.ai is not a publisher)
        assert r["canonical_key"].startswith("domain:") or r["canonical_key"].startswith("name_loc:")

    @pytest.mark.asyncio
    async def test_hn_dispatch_canonical_key_from_title(self, mock_httpx_response):
        """HN dispatch should produce name_loc:* key from extracted company name."""
        from run_pipeline import _hunter_collector_dispatch

        hn_response = {
            "hits": [
                {
                    "title": "FreshMeals launches meal kit delivery service",
                    "url": "https://freshmeals.com/launch",
                    "objectID": "99999",
                    "points": 50,
                    "num_comments": 10,
                },
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                return_value=mock_httpx_response(hn_response)
            )

            results = await _hunter_collector_dispatch("hacker_news", "freshmeals")

        r = results[0]
        assert r["company_name"] == "FreshMeals"
        # canonical key from name extraction (URL is in raw_data, not title text)
        assert r["canonical_key"].startswith("name_loc:") or r["canonical_key"].startswith("domain:")


class TestNewsDispatchExtraction:
    """Test news_api dispatch uses extractor and produces canonical keys."""

    @pytest.mark.asyncio
    async def test_news_dispatch_extracts_company_name(
        self, mock_httpx_response, monkeypatch
    ):
        """News dispatch should extract company name, not use raw title."""
        monkeypatch.setenv("GNEWS_API_KEY", "test_key")
        from run_pipeline import _hunter_collector_dispatch

        news_response = {
            "articles": [
                {
                    "title": "HealthBuddy raises $10M to expand wellness platform",
                    "description": "Consumer health startup HealthBuddy announced...",
                    "url": "https://techcrunch.com/healthbuddy",
                    "source": {"name": "TechCrunch"},
                },
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                return_value=mock_httpx_response(news_response)
            )

            results = await _hunter_collector_dispatch("news_api", "health wellness")

        assert len(results) == 1
        r = results[0]
        assert r["company_name"] == "HealthBuddy"
        # canonical_key should exist (name_loc since TechCrunch URL is a publisher)
        assert r["canonical_key"].startswith("name_loc:")

    @pytest.mark.asyncio
    async def test_news_dispatch_produces_canonical_key(
        self, mock_httpx_response, monkeypatch
    ):
        """News dispatch should always produce canonical_key (unlike before)."""
        monkeypatch.setenv("GNEWS_API_KEY", "test_key")
        from run_pipeline import _hunter_collector_dispatch

        news_response = {
            "articles": [
                {
                    "title": "Wanderlust (wanderlust.co) announces travel booking platform",
                    "description": "Travel startup Wanderlust...",
                    "url": "https://prnewswire.com/wanderlust",
                    "source": {"name": "PR Newswire"},
                },
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                return_value=mock_httpx_response(news_response)
            )

            results = await _hunter_collector_dispatch("news_api", "travel")

        r = results[0]
        assert r["canonical_key"], "canonical_key must not be empty"


class TestDispatchInvariants:
    """Test cross-cutting invariants for all dispatch paths."""

    @pytest.mark.asyncio
    async def test_raw_title_in_raw_data_not_company_name(self, mock_httpx_response):
        """Raw title should appear in raw_data, not as company_name."""
        from run_pipeline import _hunter_collector_dispatch

        hn_response = {
            "hits": [
                {
                    "title": "Some random HN post title without company info",
                    "url": "https://example.com",
                    "objectID": "11111",
                    "points": 5,
                    "num_comments": 1,
                },
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                return_value=mock_httpx_response(hn_response)
            )

            results = await _hunter_collector_dispatch("hacker_news", "random")

        r = results[0]
        # Raw title preserved in raw_data
        assert r["raw_data"]["title"] == "Some random HN post title without company info"
        # company_name should NOT be the raw title (either extracted or empty)
        assert r["company_name"] != "Some random HN post title without company info"

    @pytest.mark.asyncio
    async def test_nonempty_company_name_implies_nonempty_canonical_key(
        self, mock_httpx_response
    ):
        """Invariant: non-empty company_name implies non-empty canonical_key."""
        from run_pipeline import _hunter_collector_dispatch

        hn_response = {
            "hits": [
                {
                    "title": "Acme raises $5M",
                    "url": "",
                    "objectID": "22222",
                    "points": 10,
                    "num_comments": 2,
                },
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                return_value=mock_httpx_response(hn_response)
            )

            results = await _hunter_collector_dispatch("hacker_news", "acme")

        for r in results:
            if r["company_name"]:
                assert r["canonical_key"], (
                    f"Non-empty company_name '{r['company_name']}' "
                    f"must have non-empty canonical_key"
                )


class TestLaunchHNDispatch:
    """Test Launch HN prefix handling in dispatch."""

    @pytest.mark.asyncio
    async def test_launch_hn_no_url_name_fallback(self, mock_httpx_response):
        """Launch HN with no URL produces name_loc key from HN body parse."""
        from run_pipeline import _hunter_collector_dispatch

        hn_response = {
            "hits": [
                {
                    "title": "Launch HN: Queenly (YC W21) \u2014 Marketplace and search engine for formalwear",
                    "url": "",
                    "objectID": "33333",
                    "points": 80,
                    "num_comments": 20,
                },
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                return_value=mock_httpx_response(hn_response)
            )

            results = await _hunter_collector_dispatch("hacker_news", "queenly")

        assert len(results) == 1
        r = results[0]
        assert r["company_name"] == "Queenly"
        assert r["canonical_key"] == "name_loc:queenly"

    @pytest.mark.asyncio
    async def test_launch_hn_with_domain_prefers_domain_key(self, mock_httpx_response):
        """Launch HN with promoted domain in title produces domain:* key."""
        from run_pipeline import _hunter_collector_dispatch

        hn_response = {
            "hits": [
                {
                    "title": "Launch HN: Acme (acme.ai) \u2014 a great tool",
                    "url": "https://acme.ai",
                    "objectID": "44444",
                    "points": 100,
                    "num_comments": 30,
                },
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                return_value=mock_httpx_response(hn_response)
            )

            results = await _hunter_collector_dispatch("hacker_news", "acme")

        assert len(results) == 1
        r = results[0]
        assert r["canonical_key"].startswith("domain:")

    @pytest.mark.asyncio
    async def test_launch_hn_blocked_domain_falls_to_name(self, mock_httpx_response):
        """Launch HN with blocked domain (github.com) uses name_loc key."""
        from run_pipeline import _hunter_collector_dispatch

        hn_response = {
            "hits": [
                {
                    "title": "Launch HN: Foo \u2014 a great tool",
                    "url": "https://github.com/foo/bar",
                    "objectID": "55555",
                    "points": 60,
                    "num_comments": 15,
                },
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                return_value=mock_httpx_response(hn_response)
            )

            results = await _hunter_collector_dispatch("hacker_news", "foo")

        assert len(results) == 1
        r = results[0]
        assert r["company_name"] == "Foo"
        assert r["canonical_key"] == "name_loc:foo"

"""
Tests for PubMed E-utilities API Client.

Tests cover:
- Client initialization and configuration
- Article parsing with complete and missing fields
- Search methods with mocked HTTP responses
- Error handling for API failures
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from enrichment.pubmed import PubMedClient
from storage.health_enrichment import Publication


# =============================================================================
# TEST FIXTURES
# =============================================================================


@pytest.fixture
def client() -> PubMedClient:
    """Create a PubMedClient instance for testing."""
    return PubMedClient(rate_limit=3.0)


@pytest.fixture
def sample_article_data() -> dict:
    """Sample article data matching PubMed esummary response format."""
    return {
        "uid": "12345678",
        "title": "A Study of Novel Drug X for Treatment of Diabetes",
        "authors": [
            {"name": "Smith J", "authtype": "Author"},
            {"name": "Jones A", "authtype": "Author"},
            {"name": "Brown K", "authtype": "Author"},
        ],
        "source": "Journal of Medical Research",
        "pubdate": "2024 Jan",
        "pmcrefcount": 25,
    }


@pytest.fixture
def minimal_article_data() -> dict:
    """Minimal article data with only required fields."""
    return {
        "uid": "87654321",
        "title": "Minimal Article",
    }


@pytest.fixture
def sample_esearch_response() -> dict:
    """Sample esearch response from PubMed API."""
    return {
        "esearchresult": {
            "count": "2",
            "retmax": "10",
            "retstart": "0",
            "idlist": ["12345678", "87654321"],
        }
    }


@pytest.fixture
def sample_esummary_response(sample_article_data: dict) -> dict:
    """Sample esummary response from PubMed API."""
    return {
        "result": {
            "uids": ["12345678"],
            "12345678": sample_article_data,
        }
    }


# =============================================================================
# TestPubMedClientBasics
# =============================================================================


class TestPubMedClientBasics:
    """Tests for basic client functionality."""

    def test_client_exists(self) -> None:
        """Test that PubMedClient class exists and can be instantiated."""
        client = PubMedClient()
        assert client is not None
        assert isinstance(client, PubMedClient)

    def test_default_rate_limit(self) -> None:
        """Test that default rate limit is 3.0 requests per second."""
        client = PubMedClient()
        assert client.rate_limit == 3.0

    def test_api_key_configuration(self) -> None:
        """Test that API key can be configured."""
        client = PubMedClient(api_key="test-api-key")
        assert client.api_key == "test-api-key"

    def test_api_key_default_is_none(self) -> None:
        """Test that API key defaults to None."""
        client = PubMedClient()
        assert client.api_key is None

    def test_custom_rate_limit(self) -> None:
        """Test that custom rate limit can be set."""
        client = PubMedClient(rate_limit=10.0)
        assert client.rate_limit == 10.0

    def test_rate_limit_interval_calculation(self) -> None:
        """Test that minimum interval is calculated correctly from rate limit."""
        client = PubMedClient(rate_limit=3.0)
        assert abs(client._min_interval - (1.0 / 3.0)) < 0.001

    def test_has_required_methods(self) -> None:
        """Test that client has all required methods."""
        client = PubMedClient()
        assert hasattr(client, "search_by_author")
        assert hasattr(client, "search_by_affiliation")
        assert hasattr(client, "get_publication")
        assert hasattr(client, "_parse_article")


# =============================================================================
# TestParseArticle
# =============================================================================


class TestParseArticle:
    """Tests for article parsing functionality."""

    def test_parse_article_with_all_fields(
        self, client: PubMedClient, sample_article_data: dict
    ) -> None:
        """Test parsing an article with all fields populated."""
        publication = client._parse_article(sample_article_data)

        assert publication is not None
        assert isinstance(publication, Publication)
        assert publication.pmid == "12345678"
        assert publication.title == "A Study of Novel Drug X for Treatment of Diabetes"
        assert publication.authors == "Smith J, Jones A, Brown K"
        assert publication.journal == "Journal of Medical Research"
        assert publication.citation_count == 25
        assert publication.fetched_at is not None

    def test_parse_article_with_missing_fields(
        self, client: PubMedClient, minimal_article_data: dict
    ) -> None:
        """Test parsing an article with only required fields."""
        publication = client._parse_article(minimal_article_data)

        assert publication is not None
        assert isinstance(publication, Publication)
        assert publication.pmid == "87654321"
        assert publication.title == "Minimal Article"
        assert publication.authors is None
        assert publication.journal is None
        assert publication.citation_count is None

    def test_parse_article_handles_missing_pmid(self, client: PubMedClient) -> None:
        """Test that parsing fails gracefully for missing uid."""
        article = {"title": "Article Without PMID"}
        publication = client._parse_article(article)
        assert publication is None

    def test_parse_article_handles_missing_title(self, client: PubMedClient) -> None:
        """Test that parsing handles missing title gracefully."""
        article = {"uid": "12345678"}
        publication = client._parse_article(article)
        # Should still succeed with empty title
        assert publication is not None
        assert publication.title == ""

    def test_parse_article_handles_empty_record(self, client: PubMedClient) -> None:
        """Test that parsing handles empty record gracefully."""
        publication = client._parse_article({})
        assert publication is None

    def test_parse_article_formats_authors_as_comma_separated(
        self, client: PubMedClient
    ) -> None:
        """Test that authors are formatted as comma-separated string."""
        article = {
            "uid": "11111111",
            "title": "Test Article",
            "authors": [
                {"name": "First A"},
                {"name": "Second B"},
                {"name": "Third C"},
            ],
        }
        publication = client._parse_article(article)
        assert publication is not None
        assert publication.authors == "First A, Second B, Third C"

    def test_parse_article_handles_empty_authors_list(
        self, client: PubMedClient
    ) -> None:
        """Test that empty authors list is handled gracefully."""
        article = {
            "uid": "11111111",
            "title": "Test Article",
            "authors": [],
        }
        publication = client._parse_article(article)
        assert publication is not None
        assert publication.authors is None

    def test_parse_article_parses_pub_date(self, client: PubMedClient) -> None:
        """Test that publication date is parsed correctly."""
        article = {
            "uid": "11111111",
            "title": "Test Article",
            "pubdate": "2024 Jan 15",
        }
        publication = client._parse_article(article)
        assert publication is not None
        assert publication.pub_date == date(2024, 1, 15)

    def test_parse_article_handles_partial_date(self, client: PubMedClient) -> None:
        """Test that partial dates (year only) are handled."""
        article = {
            "uid": "11111111",
            "title": "Test Article",
            "pubdate": "2024",
        }
        publication = client._parse_article(article)
        assert publication is not None
        assert publication.pub_date == date(2024, 1, 1)


# =============================================================================
# TestSearchMethods
# =============================================================================


class TestSearchMethods:
    """Tests for search methods with mocked HTTP responses."""

    @pytest.mark.asyncio
    async def test_search_by_author_returns_publications(
        self,
        client: PubMedClient,
        sample_esearch_response: dict,
        sample_esummary_response: dict,
    ) -> None:
        """Test that search_by_author returns a list of publications on success."""
        # Create mock responses for esearch and esummary
        mock_esearch_response = MagicMock()
        mock_esearch_response.status_code = 200
        mock_esearch_response.json.return_value = sample_esearch_response
        mock_esearch_response.raise_for_status = MagicMock()

        mock_esummary_response = MagicMock()
        mock_esummary_response.status_code = 200
        mock_esummary_response.json.return_value = sample_esummary_response
        mock_esummary_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            # Return different responses for different API calls
            mock_client.get.side_effect = [mock_esearch_response, mock_esummary_response]
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            publications = await client.search_by_author("Smith J", max_results=10)

            assert len(publications) == 1
            assert publications[0].pmid == "12345678"
            assert "Study of Novel Drug X" in publications[0].title

    @pytest.mark.asyncio
    async def test_search_by_author_handles_error(self, client: PubMedClient) -> None:
        """Test that search_by_author returns empty list on HTTP error."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=MagicMock(status_code=500),
            )
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            publications = await client.search_by_author("Unknown Author")

            assert publications == []

    @pytest.mark.asyncio
    async def test_search_by_affiliation_returns_publications(
        self,
        client: PubMedClient,
        sample_esearch_response: dict,
        sample_esummary_response: dict,
    ) -> None:
        """Test that search_by_affiliation returns publications on success."""
        mock_esearch_response = MagicMock()
        mock_esearch_response.status_code = 200
        mock_esearch_response.json.return_value = sample_esearch_response
        mock_esearch_response.raise_for_status = MagicMock()

        mock_esummary_response = MagicMock()
        mock_esummary_response.status_code = 200
        mock_esummary_response.json.return_value = sample_esummary_response
        mock_esummary_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = [mock_esearch_response, mock_esummary_response]
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            publications = await client.search_by_affiliation(
                "Harvard University", max_results=10
            )

            assert len(publications) >= 1
            assert publications[0].pmid == "12345678"

    @pytest.mark.asyncio
    async def test_search_by_affiliation_handles_error(
        self, client: PubMedClient
    ) -> None:
        """Test that search_by_affiliation returns empty list on error."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.RequestError("Connection failed")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            publications = await client.search_by_affiliation("Unknown Institution")

            assert publications == []

    @pytest.mark.asyncio
    async def test_get_publication_returns_publication(
        self, client: PubMedClient, sample_esummary_response: dict
    ) -> None:
        """Test that get_publication returns a single publication on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_esummary_response
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            publication = await client.get_publication("12345678")

            assert publication is not None
            assert publication.pmid == "12345678"
            assert "Novel Drug X" in publication.title

    @pytest.mark.asyncio
    async def test_get_publication_returns_none_on_error(
        self, client: PubMedClient
    ) -> None:
        """Test that get_publication returns None on HTTP error."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=MagicMock(),
                response=MagicMock(status_code=404),
            )
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            publication = await client.get_publication("99999999")

            assert publication is None

    @pytest.mark.asyncio
    async def test_get_publication_returns_none_on_empty_results(
        self, client: PubMedClient
    ) -> None:
        """Test that get_publication returns None when PMID not found."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "uids": [],
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            publication = await client.get_publication("00000000")

            assert publication is None

    @pytest.mark.asyncio
    async def test_get_publication_returns_none_on_request_error(
        self, client: PubMedClient
    ) -> None:
        """Test that get_publication returns None on request error."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.RequestError("Timeout")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            publication = await client.get_publication("12345678")

            assert publication is None

    @pytest.mark.asyncio
    async def test_search_handles_empty_results(self, client: PubMedClient) -> None:
        """Test that search handles empty results gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "esearchresult": {
                "count": "0",
                "idlist": [],
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            publications = await client.search_by_author("Unknown Author XYZ")

            assert publications == []

    @pytest.mark.asyncio
    async def test_search_includes_api_key_when_configured(
        self, sample_esearch_response: dict, sample_esummary_response: dict
    ) -> None:
        """Test that API key is included in request when configured."""
        client_with_key = PubMedClient(api_key="my-api-key")

        mock_esearch_response = MagicMock()
        mock_esearch_response.status_code = 200
        mock_esearch_response.json.return_value = sample_esearch_response
        mock_esearch_response.raise_for_status = MagicMock()

        mock_esummary_response = MagicMock()
        mock_esummary_response.status_code = 200
        mock_esummary_response.json.return_value = sample_esummary_response
        mock_esummary_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = [mock_esearch_response, mock_esummary_response]
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            await client_with_key.search_by_author("Smith J")

            # Verify API key was included in params
            call_args = mock_client.get.call_args_list[0]
            assert call_args[1]["params"]["api_key"] == "my-api-key"

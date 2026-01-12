"""
Tests for Yelp Fusion API Client.

Tests cover:
- YelpBusiness dataclass field validation
- Client initialization and configuration
- Rate limiting with per-second and hourly limits
- Search methods with mocked HTTP responses
- Error handling for API failures
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from enrichment.yelp_fusion import YelpBusiness, YelpFusionClient


# =============================================================================
# TEST FIXTURES
# =============================================================================


@pytest.fixture
def client() -> YelpFusionClient:
    """Create a YelpFusionClient instance for testing."""
    return YelpFusionClient(api_key="test-api-key", rate_limit=5.0, hourly_limit=50)


@pytest.fixture
def sample_yelp_business_response() -> dict:
    """Sample Yelp business matching Yelp Fusion API response format."""
    return {
        "id": "abc123-yelp-id",
        "name": "Amazing Restaurant",
        "rating": 4.5,
        "review_count": 125,
        "price": "$$",
        "categories": [
            {"alias": "italian", "title": "Italian"},
            {"alias": "pizza", "title": "Pizza"},
        ],
        "url": "https://www.yelp.com/biz/amazing-restaurant",
        "location": {
            "city": "San Francisco",
            "state": "CA",
            "address1": "123 Main St",
        },
    }


@pytest.fixture
def minimal_yelp_business_response() -> dict:
    """Minimal Yelp business response with only required fields."""
    return {
        "id": "min123-yelp-id",
        "name": "Basic Place",
        "rating": 3.0,
        "review_count": 10,
        "categories": [],
        "url": "https://www.yelp.com/biz/basic-place",
    }


@pytest.fixture
def sample_search_response(sample_yelp_business_response: dict) -> dict:
    """Sample search response from Yelp Fusion API."""
    return {
        "total": 1,
        "businesses": [sample_yelp_business_response],
        "region": {
            "center": {"longitude": -122.4194, "latitude": 37.7749}
        },
    }


# =============================================================================
# TestYelpBusinessDataclass
# =============================================================================


class TestYelpBusinessDataclass:
    """Tests for YelpBusiness dataclass."""

    def test_yelp_business_fields(self) -> None:
        """Test that YelpBusiness has all required fields."""
        business = YelpBusiness(
            entity_id="entity-123",
            yelp_id="yelp-abc123",
            name="Test Restaurant",
            rating=4.5,
            review_count=100,
            price="$$",
            categories=["Italian", "Pizza"],
            url="https://www.yelp.com/biz/test-restaurant",
            fetched_at=datetime.utcnow(),
        )

        assert business.entity_id == "entity-123"
        assert business.yelp_id == "yelp-abc123"
        assert business.name == "Test Restaurant"
        assert business.rating == 4.5
        assert business.review_count == 100
        assert business.price == "$$"
        assert business.categories == ["Italian", "Pizza"]
        assert business.url == "https://www.yelp.com/biz/test-restaurant"
        assert business.fetched_at is not None
        assert business.id is None  # Default value

    def test_yelp_business_optional_price(self) -> None:
        """Test that price field is optional."""
        business = YelpBusiness(
            entity_id="entity-123",
            yelp_id="yelp-abc123",
            name="Test Restaurant",
            rating=4.5,
            review_count=100,
            price=None,
            categories=["Italian"],
            url="https://www.yelp.com/biz/test-restaurant",
            fetched_at=datetime.utcnow(),
        )

        assert business.price is None

    def test_yelp_business_with_db_id(self) -> None:
        """Test that YelpBusiness can have a database ID."""
        business = YelpBusiness(
            entity_id="entity-123",
            yelp_id="yelp-abc123",
            name="Test Restaurant",
            rating=4.5,
            review_count=100,
            price="$$",
            categories=["Italian"],
            url="https://www.yelp.com/biz/test-restaurant",
            fetched_at=datetime.utcnow(),
            id=42,
        )

        assert business.id == 42


# =============================================================================
# TestYelpFusionClientBasics
# =============================================================================


class TestYelpFusionClientBasics:
    """Tests for basic client functionality."""

    def test_client_initialization(self) -> None:
        """Test that YelpFusionClient class exists and can be instantiated."""
        client = YelpFusionClient(api_key="test-key")
        assert client is not None
        assert isinstance(client, YelpFusionClient)
        assert client.api_key == "test-key"

    def test_default_rate_limit(self) -> None:
        """Test that default rate limit is 5.0 requests per second."""
        client = YelpFusionClient(api_key="test-key")
        assert client.rate_limit == 5.0

    def test_default_hourly_limit(self) -> None:
        """Test that default hourly limit is 50 requests per hour."""
        client = YelpFusionClient(api_key="test-key")
        assert client.hourly_limit == 50

    def test_client_custom_rate_limit(self) -> None:
        """Test that custom rate limit can be set."""
        client = YelpFusionClient(api_key="test-key", rate_limit=10.0)
        assert client.rate_limit == 10.0

    def test_client_custom_hourly_limit(self) -> None:
        """Test that custom hourly limit can be set."""
        client = YelpFusionClient(api_key="test-key", hourly_limit=100)
        assert client.hourly_limit == 100

    def test_rate_limit_interval_calculation(self) -> None:
        """Test that minimum interval is calculated correctly from rate limit."""
        client = YelpFusionClient(api_key="test-key", rate_limit=5.0)
        assert client._min_interval == 0.2  # 1.0 / 5.0

    def test_has_required_methods(self) -> None:
        """Test that client has all required methods."""
        client = YelpFusionClient(api_key="test-key")
        assert hasattr(client, "search_by_name")
        assert hasattr(client, "get_business")
        assert hasattr(client, "_wait_for_rate_limit")
        assert hasattr(client, "_make_request")


# =============================================================================
# TestSearchMethods
# =============================================================================


class TestSearchMethods:
    """Tests for search methods with mocked HTTP responses."""

    @pytest.mark.asyncio
    async def test_search_by_name_returns_businesses(
        self, client: YelpFusionClient, sample_search_response: dict
    ) -> None:
        """Test that search_by_name returns a list of YelpBusiness on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_search_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = sample_search_response

            businesses = await client.search_by_name(
                "Amazing Restaurant", "San Francisco, CA", max_results=10
            )

            assert len(businesses) == 1
            assert businesses[0].name == "Amazing Restaurant"
            assert businesses[0].yelp_id == "abc123-yelp-id"
            assert businesses[0].rating == 4.5
            assert businesses[0].review_count == 125
            assert businesses[0].price == "$$"
            assert "Italian" in businesses[0].categories
            assert "Pizza" in businesses[0].categories

    @pytest.mark.asyncio
    async def test_search_handles_empty_results(
        self, client: YelpFusionClient
    ) -> None:
        """Test that search_by_name returns empty list when no results found."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"total": 0, "businesses": []}

            businesses = await client.search_by_name(
                "NonExistent Restaurant", "Nowhere, XX"
            )

            assert businesses == []

    @pytest.mark.asyncio
    async def test_search_handles_api_error(
        self, client: YelpFusionClient
    ) -> None:
        """Test that search_by_name returns empty list on API error."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.side_effect = httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=MagicMock(status_code=500),
            )

            businesses = await client.search_by_name(
                "Some Restaurant", "Some City, CA"
            )

            assert businesses == []

    @pytest.mark.asyncio
    async def test_search_respects_max_results(
        self, client: YelpFusionClient, sample_yelp_business_response: dict
    ) -> None:
        """Test that search limits results to max_results."""
        multi_results = {
            "total": 5,
            "businesses": [sample_yelp_business_response for _ in range(5)],
        }

        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = multi_results

            await client.search_by_name("Restaurant", "City", max_results=3)

            # Verify limit was set correctly in the request
            call_args = mock_request.call_args
            assert call_args[1]["params"]["limit"] == 3


# =============================================================================
# TestGetBusiness
# =============================================================================


class TestGetBusiness:
    """Tests for get_business method."""

    @pytest.mark.asyncio
    async def test_get_business_returns_business(
        self, client: YelpFusionClient, sample_yelp_business_response: dict
    ) -> None:
        """Test that get_business returns a YelpBusiness on success."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = sample_yelp_business_response

            business = await client.get_business("abc123-yelp-id")

            assert business is not None
            assert business.yelp_id == "abc123-yelp-id"
            assert business.name == "Amazing Restaurant"
            assert business.rating == 4.5

    @pytest.mark.asyncio
    async def test_get_business_returns_none_on_404(
        self, client: YelpFusionClient
    ) -> None:
        """Test that get_business returns None when business is not found."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=MagicMock(),
                response=MagicMock(status_code=404),
            )

            business = await client.get_business("nonexistent-id")

            assert business is None

    @pytest.mark.asyncio
    async def test_get_business_returns_none_on_request_error(
        self, client: YelpFusionClient
    ) -> None:
        """Test that get_business returns None on request error."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.side_effect = httpx.RequestError("Timeout")

            business = await client.get_business("some-id")

            assert business is None


# =============================================================================
# TestRateLimiting
# =============================================================================


class TestRateLimiting:
    """Tests for rate limiting functionality."""

    @pytest.mark.asyncio
    async def test_rate_limiting(self, client: YelpFusionClient) -> None:
        """Test that rate limiting enforces minimum interval between requests."""
        # Track when rate limit waits complete
        wait_times = []

        async def track_wait():
            wait_times.append(asyncio.get_event_loop().time())
            # Call the actual wait_for_rate_limit method behavior
            async with client._semaphore:
                if client._last_request_time is not None:
                    elapsed = asyncio.get_event_loop().time() - client._last_request_time
                    if elapsed < client._min_interval:
                        await asyncio.sleep(client._min_interval - elapsed)
                client._last_request_time = asyncio.get_event_loop().time()

        # Make two rapid requests
        await client._wait_for_rate_limit()
        start_time = asyncio.get_event_loop().time()
        await client._wait_for_rate_limit()
        elapsed = asyncio.get_event_loop().time() - start_time

        # Second request should have waited at least min_interval (minus some tolerance)
        assert elapsed >= client._min_interval * 0.9

    @pytest.mark.asyncio
    async def test_hourly_limit_tracking(self) -> None:
        """Test that client tracks requests for hourly limiting."""
        client = YelpFusionClient(api_key="test-key", hourly_limit=5)

        # Client should have a deque for tracking hourly requests
        assert hasattr(client, "_hourly_requests")

        # Initially should be empty
        assert len(client._hourly_requests) == 0

    @pytest.mark.asyncio
    async def test_make_request_adds_auth_header(
        self, client: YelpFusionClient
    ) -> None:
        """Test that _make_request adds the Authorization header."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"businesses": []}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_http_client = AsyncMock()
            mock_http_client.get.return_value = mock_response
            mock_http_client.__aenter__.return_value = mock_http_client
            mock_http_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_http_client

            await client._make_request("/businesses/search", params={"term": "test"})

            # Verify Authorization header was included
            call_kwargs = mock_http_client.get.call_args[1]
            assert "headers" in call_kwargs
            assert call_kwargs["headers"]["Authorization"] == "Bearer test-api-key"

"""
Tests for Google Places API Client.

Tests cover:
- GooglePlace dataclass field validation
- Client initialization and configuration
- Rate limiting with hourly limits
- Search methods with mocked HTTP responses
- Error handling for API failures
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from enrichment.google_places import GooglePlace, GooglePlacesClient


# =============================================================================
# TEST FIXTURES
# =============================================================================


@pytest.fixture
def client() -> GooglePlacesClient:
    """Create a GooglePlacesClient instance for testing."""
    return GooglePlacesClient(api_key="test-api-key", hourly_limit=50)


@pytest.fixture
def sample_google_place_response() -> dict:
    """Sample Google Place matching Google Places API response format."""
    return {
        "place_id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
        "name": "Amazing Restaurant",
        "rating": 4.5,
        "user_ratings_total": 125,
        "price_level": 2,
        "types": ["restaurant", "food", "point_of_interest", "establishment"],
        "website": "https://www.amazingrestaurant.com",
    }


@pytest.fixture
def minimal_google_place_response() -> dict:
    """Minimal Google Place response with only required fields."""
    return {
        "place_id": "ChIJN1t_tDeuEmsRUsoyG83frY5",
        "name": "Basic Place",
        "rating": 3.0,
        "user_ratings_total": 10,
        "types": ["establishment"],
    }


@pytest.fixture
def sample_search_response(sample_google_place_response: dict) -> dict:
    """Sample search response from Google Places API."""
    return {
        "results": [sample_google_place_response],
        "status": "OK",
    }


@pytest.fixture
def sample_details_response(sample_google_place_response: dict) -> dict:
    """Sample details response from Google Places API."""
    return {
        "result": sample_google_place_response,
        "status": "OK",
    }


# =============================================================================
# TestGooglePlaceDataclass
# =============================================================================


class TestGooglePlaceDataclass:
    """Tests for GooglePlace dataclass."""

    def test_google_place_fields(self) -> None:
        """Test that GooglePlace has all required fields."""
        place = GooglePlace(
            entity_id="entity-123",
            place_id="ChIJN1t_tDeuEmsRUsoyG83frY4",
            name="Test Restaurant",
            rating=4.5,
            user_ratings_total=100,
            price_level=2,
            types=["restaurant", "food"],
            website="https://www.test.com",
            fetched_at=datetime.utcnow(),
        )

        assert place.entity_id == "entity-123"
        assert place.place_id == "ChIJN1t_tDeuEmsRUsoyG83frY4"
        assert place.name == "Test Restaurant"
        assert place.rating == 4.5
        assert place.user_ratings_total == 100
        assert place.price_level == 2
        assert place.types == ["restaurant", "food"]
        assert place.website == "https://www.test.com"
        assert place.fetched_at is not None
        assert place.id is None  # Default value

    def test_google_place_optional_price_level(self) -> None:
        """Test that price_level field is optional."""
        place = GooglePlace(
            entity_id="entity-123",
            place_id="ChIJN1t_tDeuEmsRUsoyG83frY4",
            name="Test Restaurant",
            rating=4.5,
            user_ratings_total=100,
            price_level=None,
            types=["restaurant"],
            website="https://www.test.com",
            fetched_at=datetime.utcnow(),
        )

        assert place.price_level is None

    def test_google_place_optional_website(self) -> None:
        """Test that website field is optional."""
        place = GooglePlace(
            entity_id="entity-123",
            place_id="ChIJN1t_tDeuEmsRUsoyG83frY4",
            name="Test Restaurant",
            rating=4.5,
            user_ratings_total=100,
            price_level=2,
            types=["restaurant"],
            website=None,
            fetched_at=datetime.utcnow(),
        )

        assert place.website is None

    def test_google_place_with_db_id(self) -> None:
        """Test that GooglePlace can have a database ID."""
        place = GooglePlace(
            entity_id="entity-123",
            place_id="ChIJN1t_tDeuEmsRUsoyG83frY4",
            name="Test Restaurant",
            rating=4.5,
            user_ratings_total=100,
            price_level=2,
            types=["restaurant"],
            website="https://www.test.com",
            fetched_at=datetime.utcnow(),
            id=42,
        )

        assert place.id == 42


# =============================================================================
# TestGooglePlacesClientBasics
# =============================================================================


class TestGooglePlacesClientBasics:
    """Tests for basic client functionality."""

    def test_client_initialization(self) -> None:
        """Test that GooglePlacesClient class exists and can be instantiated."""
        client = GooglePlacesClient(api_key="test-key")
        assert client is not None
        assert isinstance(client, GooglePlacesClient)
        assert client.api_key == "test-key"

    def test_default_hourly_limit(self) -> None:
        """Test that default hourly limit is 50 requests per hour."""
        client = GooglePlacesClient(api_key="test-key")
        assert client.hourly_limit == 50

    def test_client_custom_hourly_limit(self) -> None:
        """Test that custom hourly limit can be set."""
        client = GooglePlacesClient(api_key="test-key", hourly_limit=100)
        assert client.hourly_limit == 100

    def test_has_required_methods(self) -> None:
        """Test that client has all required methods."""
        client = GooglePlacesClient(api_key="test-key")
        assert hasattr(client, "search_places")
        assert hasattr(client, "get_place_details")
        assert hasattr(client, "_wait_for_rate_limit")
        assert hasattr(client, "_make_request")


# =============================================================================
# TestSearchMethods
# =============================================================================


class TestSearchMethods:
    """Tests for search methods with mocked HTTP responses."""

    @pytest.mark.asyncio
    async def test_search_returns_places(
        self, client: GooglePlacesClient, sample_search_response: dict
    ) -> None:
        """Test that search_places returns a list of GooglePlace on success."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = sample_search_response

            places = await client.search_places(
                "Amazing Restaurant", location="San Francisco, CA", max_results=10
            )

            assert len(places) == 1
            assert places[0].name == "Amazing Restaurant"
            assert places[0].place_id == "ChIJN1t_tDeuEmsRUsoyG83frY4"
            assert places[0].rating == 4.5
            assert places[0].user_ratings_total == 125
            assert places[0].price_level == 2
            assert "restaurant" in places[0].types
            assert "food" in places[0].types

    @pytest.mark.asyncio
    async def test_handles_zero_results(
        self, client: GooglePlacesClient
    ) -> None:
        """Test that search_places returns empty list when no results found."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"results": [], "status": "ZERO_RESULTS"}

            places = await client.search_places(
                "NonExistent Restaurant", location="Nowhere, XX"
            )

            assert places == []

    @pytest.mark.asyncio
    async def test_search_handles_api_error(
        self, client: GooglePlacesClient
    ) -> None:
        """Test that search_places returns empty list on API error."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.side_effect = httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=MagicMock(status_code=500),
            )

            places = await client.search_places(
                "Some Restaurant", location="Some City, CA"
            )

            assert places == []

    @pytest.mark.asyncio
    async def test_search_without_location(
        self, client: GooglePlacesClient, sample_search_response: dict
    ) -> None:
        """Test that search_places works without location parameter."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = sample_search_response

            places = await client.search_places("Amazing Restaurant")

            assert len(places) == 1
            # Verify location was not included in the query
            mock_request.assert_called_once()


# =============================================================================
# TestGetPlaceDetails
# =============================================================================


class TestGetPlaceDetails:
    """Tests for get_place_details method."""

    @pytest.mark.asyncio
    async def test_get_place_details(
        self, client: GooglePlacesClient, sample_details_response: dict
    ) -> None:
        """Test that get_place_details returns a GooglePlace on success."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = sample_details_response

            place = await client.get_place_details("ChIJN1t_tDeuEmsRUsoyG83frY4")

            assert place is not None
            assert place.place_id == "ChIJN1t_tDeuEmsRUsoyG83frY4"
            assert place.name == "Amazing Restaurant"
            assert place.rating == 4.5

    @pytest.mark.asyncio
    async def test_get_place_details_returns_none_on_not_found(
        self, client: GooglePlacesClient
    ) -> None:
        """Test that get_place_details returns None when place is not found."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"status": "NOT_FOUND", "result": {}}

            place = await client.get_place_details("nonexistent-id")

            assert place is None

    @pytest.mark.asyncio
    async def test_get_place_details_returns_none_on_request_error(
        self, client: GooglePlacesClient
    ) -> None:
        """Test that get_place_details returns None on request error."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.side_effect = httpx.RequestError("Timeout")

            place = await client.get_place_details("some-id")

            assert place is None


# =============================================================================
# TestRateLimiting
# =============================================================================


class TestRateLimiting:
    """Tests for rate limiting functionality."""

    @pytest.mark.asyncio
    async def test_hourly_limit_tracking(self) -> None:
        """Test that client tracks requests for hourly limiting."""
        client = GooglePlacesClient(api_key="test-key", hourly_limit=5)

        # Client should have a deque for tracking hourly requests
        assert hasattr(client, "_hourly_requests")

        # Initially should be empty
        assert len(client._hourly_requests) == 0

    @pytest.mark.asyncio
    async def test_make_request_adds_api_key(
        self, client: GooglePlacesClient
    ) -> None:
        """Test that _make_request adds the API key as a query parameter."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [], "status": "OK"}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_http_client = AsyncMock()
            mock_http_client.get.return_value = mock_response
            mock_http_client.__aenter__.return_value = mock_http_client
            mock_http_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_http_client

            await client._make_request("/textsearch/json", params={"query": "test"})

            # Verify API key was included in params
            call_kwargs = mock_http_client.get.call_args[1]
            assert "params" in call_kwargs
            assert call_kwargs["params"]["key"] == "test-api-key"

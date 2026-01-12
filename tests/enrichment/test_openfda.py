"""
Tests for OpenFDA API Client.

Tests cover:
- Client initialization and configuration
- 510k record parsing with complete and missing fields
- Search methods with mocked HTTP responses
- Error handling for API failures
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from enrichment.openfda import OpenFDAClient
from storage.health_enrichment import FDAClearance


# =============================================================================
# TEST FIXTURES
# =============================================================================


@pytest.fixture
def client() -> OpenFDAClient:
    """Create an OpenFDAClient instance for testing."""
    return OpenFDAClient(rate_limit=4.0)


@pytest.fixture
def sample_510k_record() -> dict:
    """Sample 510k record matching OpenFDA API response format."""
    return {
        "k_number": "K123456",
        "device_name": "Cardiac Monitor",
        "applicant": "Company Name Inc",
        "decision_date": "2024-01-15",
        "decision_description": "SUBSTANTIALLY EQUIVALENT",
        "product_code": "DQA",
        "device_class": "2",
        "regulation_number": "870.2800",
    }


@pytest.fixture
def minimal_510k_record() -> dict:
    """Minimal 510k record with only required fields."""
    return {
        "k_number": "K654321",
        "device_name": "Basic Device",
    }


@pytest.fixture
def sample_search_response(sample_510k_record: dict) -> dict:
    """Sample search response from OpenFDA API."""
    return {
        "meta": {
            "results": {
                "skip": 0,
                "limit": 10,
                "total": 1,
            }
        },
        "results": [sample_510k_record],
    }


# =============================================================================
# TestOpenFDAClientBasics
# =============================================================================


class TestOpenFDAClientBasics:
    """Tests for basic client functionality."""

    def test_client_exists(self) -> None:
        """Test that OpenFDAClient class exists and can be instantiated."""
        client = OpenFDAClient()
        assert client is not None
        assert isinstance(client, OpenFDAClient)

    def test_default_rate_limit(self) -> None:
        """Test that default rate limit is 4.0 requests per second."""
        client = OpenFDAClient()
        assert client.rate_limit == 4.0

    def test_api_key_configuration(self) -> None:
        """Test that API key can be configured."""
        client = OpenFDAClient(api_key="test-api-key")
        assert client.api_key == "test-api-key"

    def test_api_key_default_is_none(self) -> None:
        """Test that API key defaults to None."""
        client = OpenFDAClient()
        assert client.api_key is None

    def test_custom_rate_limit(self) -> None:
        """Test that custom rate limit can be set."""
        client = OpenFDAClient(rate_limit=10.0)
        assert client.rate_limit == 10.0

    def test_rate_limit_interval_calculation(self) -> None:
        """Test that minimum interval is calculated correctly from rate limit."""
        client = OpenFDAClient(rate_limit=4.0)
        assert client._min_interval == 0.25  # 1.0 / 4.0

    def test_has_required_methods(self) -> None:
        """Test that client has all required methods."""
        client = OpenFDAClient()
        assert hasattr(client, "search_510k_by_applicant")
        assert hasattr(client, "search_510k_by_device")
        assert hasattr(client, "get_510k")
        assert hasattr(client, "_parse_510k")


# =============================================================================
# TestParse510k
# =============================================================================


class TestParse510k:
    """Tests for 510k parsing functionality."""

    def test_parse_510k_with_all_fields(
        self, client: OpenFDAClient, sample_510k_record: dict
    ) -> None:
        """Test parsing a 510k record with all fields populated."""
        clearance = client._parse_510k(sample_510k_record)

        assert clearance is not None
        assert isinstance(clearance, FDAClearance)
        assert clearance.application_number == "K123456"
        assert clearance.device_name == "Cardiac Monitor"
        assert clearance.device_class == "II"  # Normalized from "2"
        assert clearance.clearance_type == "510k"
        assert clearance.decision == "SUBSTANTIALLY EQUIVALENT"
        assert clearance.decision_date == date(2024, 1, 15)
        assert clearance.fetched_at is not None

    def test_parse_510k_with_missing_fields(
        self, client: OpenFDAClient, minimal_510k_record: dict
    ) -> None:
        """Test parsing a 510k record with only required fields."""
        clearance = client._parse_510k(minimal_510k_record)

        assert clearance is not None
        assert isinstance(clearance, FDAClearance)
        assert clearance.application_number == "K654321"
        assert clearance.device_name == "Basic Device"
        assert clearance.device_class is None
        assert clearance.clearance_type == "510k"
        assert clearance.decision is None
        assert clearance.decision_date is None

    def test_parse_510k_normalizes_device_class(self, client: OpenFDAClient) -> None:
        """Test that device class is normalized from numeric to Roman numeral."""
        # Test class 1
        record_1 = {"k_number": "K111", "device_name": "Device", "device_class": "1"}
        clearance_1 = client._parse_510k(record_1)
        assert clearance_1.device_class == "I"

        # Test class 2
        record_2 = {"k_number": "K222", "device_name": "Device", "device_class": "2"}
        clearance_2 = client._parse_510k(record_2)
        assert clearance_2.device_class == "II"

        # Test class 3
        record_3 = {"k_number": "K333", "device_name": "Device", "device_class": "3"}
        clearance_3 = client._parse_510k(record_3)
        assert clearance_3.device_class == "III"

    def test_parse_510k_handles_missing_k_number(self, client: OpenFDAClient) -> None:
        """Test that parsing fails gracefully for missing k_number."""
        record = {"device_name": "Device Without Number"}
        clearance = client._parse_510k(record)
        assert clearance is None

    def test_parse_510k_handles_missing_device_name(self, client: OpenFDAClient) -> None:
        """Test that parsing fails gracefully for missing device_name."""
        record = {"k_number": "K123456"}
        clearance = client._parse_510k(record)
        assert clearance is None

    def test_parse_510k_handles_empty_record(self, client: OpenFDAClient) -> None:
        """Test that parsing handles empty record gracefully."""
        clearance = client._parse_510k({})
        assert clearance is None

    def test_parse_510k_handles_invalid_date(self, client: OpenFDAClient) -> None:
        """Test that parsing handles invalid date format gracefully."""
        record = {
            "k_number": "K123456",
            "device_name": "Device",
            "decision_date": "invalid-date",
        }
        clearance = client._parse_510k(record)
        assert clearance is not None
        assert clearance.decision_date is None


# =============================================================================
# TestSearchMethods
# =============================================================================


class TestSearchMethods:
    """Tests for search methods with mocked HTTP responses."""

    @pytest.mark.asyncio
    async def test_search_by_applicant_returns_clearances(
        self, client: OpenFDAClient, sample_search_response: dict
    ) -> None:
        """Test that search_510k_by_applicant returns a list of clearances on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_search_response
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            clearances = await client.search_510k_by_applicant(
                "Company Name Inc", max_results=10
            )

            assert len(clearances) == 1
            assert clearances[0].application_number == "K123456"
            assert clearances[0].device_name == "Cardiac Monitor"

    @pytest.mark.asyncio
    async def test_search_by_applicant_handles_error(
        self, client: OpenFDAClient
    ) -> None:
        """Test that search_510k_by_applicant returns empty list on HTTP error."""
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

            clearances = await client.search_510k_by_applicant("NonExistent Corp")

            assert clearances == []

    @pytest.mark.asyncio
    async def test_search_by_device_returns_clearances(
        self, client: OpenFDAClient, sample_search_response: dict
    ) -> None:
        """Test that search_510k_by_device returns a list of clearances on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_search_response
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            clearances = await client.search_510k_by_device(
                "Cardiac Monitor", max_results=10
            )

            assert len(clearances) == 1
            assert clearances[0].device_name == "Cardiac Monitor"

    @pytest.mark.asyncio
    async def test_search_by_device_handles_error(self, client: OpenFDAClient) -> None:
        """Test that search_510k_by_device returns empty list on request error."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.RequestError("Connection failed")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            clearances = await client.search_510k_by_device("Unknown Device")

            assert clearances == []

    @pytest.mark.asyncio
    async def test_get_510k_returns_clearance(
        self, client: OpenFDAClient, sample_510k_record: dict
    ) -> None:
        """Test that get_510k returns a single clearance on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "meta": {"results": {"total": 1}},
            "results": [sample_510k_record],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            clearance = await client.get_510k("K123456")

            assert clearance is not None
            assert clearance.application_number == "K123456"
            assert clearance.device_class == "II"

    @pytest.mark.asyncio
    async def test_get_510k_returns_none_on_404(self, client: OpenFDAClient) -> None:
        """Test that get_510k returns None when clearance is not found."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=MagicMock(),
                response=mock_response,
            )
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            clearance = await client.get_510k("K999999")

            assert clearance is None

    @pytest.mark.asyncio
    async def test_get_510k_returns_none_on_empty_results(
        self, client: OpenFDAClient
    ) -> None:
        """Test that get_510k returns None when no results are found."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"meta": {"results": {"total": 0}}, "results": []}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            clearance = await client.get_510k("K000000")

            assert clearance is None

    @pytest.mark.asyncio
    async def test_get_510k_returns_none_on_request_error(
        self, client: OpenFDAClient
    ) -> None:
        """Test that get_510k returns None on request error."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.RequestError("Timeout")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            clearance = await client.get_510k("K123456")

            assert clearance is None

    @pytest.mark.asyncio
    async def test_search_handles_empty_response(self, client: OpenFDAClient) -> None:
        """Test that search handles empty results gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "meta": {"results": {"total": 0}},
            "results": [],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            clearances = await client.search_510k_by_applicant("Unknown Company")

            assert clearances == []

    @pytest.mark.asyncio
    async def test_search_includes_api_key_when_configured(
        self, sample_search_response: dict
    ) -> None:
        """Test that API key is included in request when configured."""
        client_with_key = OpenFDAClient(api_key="my-api-key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_search_response
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            await client_with_key.search_510k_by_applicant("Company")

            # Verify API key was included in params
            call_args = mock_client.get.call_args
            assert call_args[1]["params"]["api_key"] == "my-api-key"

    @pytest.mark.asyncio
    async def test_search_respects_max_results(
        self, client: OpenFDAClient, sample_510k_record: dict
    ) -> None:
        """Test that search limits results to max_results."""
        response_data = {
            "meta": {"results": {"total": 5}},
            "results": [sample_510k_record for _ in range(5)],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_data
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            # Request with max_results=3
            await client.search_510k_by_applicant("Acme", max_results=3)

            # Verify limit was set correctly in the request
            call_args = mock_client.get.call_args
            assert call_args[1]["params"]["limit"] == 3

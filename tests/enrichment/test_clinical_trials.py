"""
Tests for ClinicalTrials.gov API Client.

Tests cover:
- Client initialization and configuration
- Study parsing with complete and missing fields
- Search methods with mocked HTTP responses
- Error handling for API failures
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from enrichment.clinical_trials import ClinicalTrialsClient
from storage.health_enrichment import ClinicalTrial


# =============================================================================
# TEST FIXTURES
# =============================================================================


@pytest.fixture
def client() -> ClinicalTrialsClient:
    """Create a ClinicalTrialsClient instance for testing."""
    return ClinicalTrialsClient(rate_limit=3.0)


@pytest.fixture
def sample_study_data() -> dict:
    """Sample study data matching ClinicalTrials.gov API v2 response format."""
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT12345678",
                "officialTitle": "A Phase 2 Study of Drug X for Diabetes",
                "briefTitle": "Drug X Diabetes Study",
            },
            "statusModule": {
                "overallStatus": "Recruiting",
                "startDateStruct": {"date": "2023-01-15"},
                "completionDateStruct": {"date": "2025-12-31"},
            },
            "designModule": {
                "phases": ["PHASE2"],
                "enrollmentInfo": {"count": 100},
            },
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "Acme Therapeutics"}
            },
            "conditionsModule": {
                "conditions": ["Type 2 Diabetes", "Obesity"]
            },
        }
    }


@pytest.fixture
def minimal_study_data() -> dict:
    """Minimal study data with only required fields."""
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT87654321",
                "briefTitle": "Minimal Study",
            },
        }
    }


@pytest.fixture
def sample_search_response(sample_study_data: dict) -> dict:
    """Sample search response from ClinicalTrials.gov API."""
    return {
        "studies": [sample_study_data],
        "totalCount": 1,
    }


# =============================================================================
# TestClinicalTrialsClientBasics
# =============================================================================


class TestClinicalTrialsClientBasics:
    """Tests for basic client functionality."""

    def test_client_exists(self) -> None:
        """Test that ClinicalTrialsClient class exists and can be instantiated."""
        client = ClinicalTrialsClient()
        assert client is not None
        assert isinstance(client, ClinicalTrialsClient)

    def test_default_rate_limit(self) -> None:
        """Test that default rate limit is 3.0 requests per second."""
        client = ClinicalTrialsClient()
        assert client.rate_limit == 3.0

    def test_custom_rate_limit(self) -> None:
        """Test that custom rate limit can be set."""
        client = ClinicalTrialsClient(rate_limit=5.0)
        assert client.rate_limit == 5.0

    def test_rate_limit_interval_calculation(self) -> None:
        """Test that minimum interval is calculated correctly from rate limit."""
        client = ClinicalTrialsClient(rate_limit=2.0)
        assert client._min_interval == 0.5  # 1.0 / 2.0

    def test_has_required_methods(self) -> None:
        """Test that client has all required async methods."""
        client = ClinicalTrialsClient()
        assert hasattr(client, "search_by_sponsor")
        assert hasattr(client, "search_by_condition")
        assert hasattr(client, "get_study")
        assert hasattr(client, "_parse_study")


# =============================================================================
# TestParseStudy
# =============================================================================


class TestParseStudy:
    """Tests for study parsing functionality."""

    def test_parse_study_with_all_fields(
        self, client: ClinicalTrialsClient, sample_study_data: dict
    ) -> None:
        """Test parsing a study with all fields populated."""
        trial = client._parse_study(sample_study_data)

        assert trial is not None
        assert isinstance(trial, ClinicalTrial)
        assert trial.nct_id == "NCT12345678"
        assert trial.title == "A Phase 2 Study of Drug X for Diabetes"
        assert trial.phase == "Phase 2"
        assert trial.status == "Recruiting"
        assert trial.enrollment == 100
        assert trial.conditions == ["Type 2 Diabetes", "Obesity"]
        assert trial.start_date == date(2023, 1, 15)
        assert trial.completion_date == date(2025, 12, 31)
        assert trial.fetched_at is not None

    def test_parse_study_with_missing_fields(
        self, client: ClinicalTrialsClient, minimal_study_data: dict
    ) -> None:
        """Test parsing a study with only required fields."""
        trial = client._parse_study(minimal_study_data)

        assert trial is not None
        assert isinstance(trial, ClinicalTrial)
        assert trial.nct_id == "NCT87654321"
        assert trial.title == "Minimal Study"
        assert trial.phase is None
        assert trial.status is None
        assert trial.enrollment is None
        assert trial.conditions == []
        assert trial.start_date is None
        assert trial.completion_date is None

    def test_parse_study_uses_brief_title_as_fallback(
        self, client: ClinicalTrialsClient
    ) -> None:
        """Test that briefTitle is used when officialTitle is missing."""
        study_data = {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT11111111",
                    "briefTitle": "Brief Title Only",
                },
            }
        }
        trial = client._parse_study(study_data)

        assert trial is not None
        assert trial.title == "Brief Title Only"

    def test_parse_study_missing_nct_id_returns_none(
        self, client: ClinicalTrialsClient
    ) -> None:
        """Test that study without NCT ID returns None."""
        study_data = {
            "protocolSection": {
                "identificationModule": {
                    "officialTitle": "Study Without ID",
                },
            }
        }
        trial = client._parse_study(study_data)
        assert trial is None

    def test_parse_study_empty_data_returns_none(
        self, client: ClinicalTrialsClient
    ) -> None:
        """Test that empty study data returns None."""
        trial = client._parse_study({})
        assert trial is None

    def test_normalize_phase_variations(self, client: ClinicalTrialsClient) -> None:
        """Test phase normalization for various API formats."""
        assert client._normalize_phase("PHASE1") == "Phase 1"
        assert client._normalize_phase("PHASE2") == "Phase 2"
        assert client._normalize_phase("PHASE3") == "Phase 3"
        assert client._normalize_phase("PHASE4") == "Phase 4"
        assert client._normalize_phase("EARLY_PHASE1") == "Early Phase 1"
        assert client._normalize_phase("NA") == "Not Applicable"
        assert client._normalize_phase("UNKNOWN") == "UNKNOWN"
        assert client._normalize_phase("") == ""

    def test_parse_date_formats(self, client: ClinicalTrialsClient) -> None:
        """Test date parsing for various API date formats."""
        assert client._parse_date("2023-01-15") == date(2023, 1, 15)
        assert client._parse_date("2023-01") == date(2023, 1, 1)
        assert client._parse_date("January 2023") == date(2023, 1, 1)
        assert client._parse_date(None) is None
        assert client._parse_date("") is None
        assert client._parse_date("invalid date") is None


# =============================================================================
# TestSearchMethods
# =============================================================================


class TestSearchMethods:
    """Tests for search methods with mocked HTTP responses."""

    @pytest.mark.asyncio
    async def test_search_by_sponsor_returns_trials(
        self, client: ClinicalTrialsClient, sample_search_response: dict
    ) -> None:
        """Test that search_by_sponsor returns a list of trials on success."""
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

            trials = await client.search_by_sponsor("Acme Therapeutics", max_results=10)

            assert len(trials) == 1
            assert trials[0].nct_id == "NCT12345678"
            assert trials[0].status == "Recruiting"

    @pytest.mark.asyncio
    async def test_search_by_sponsor_handles_error(
        self, client: ClinicalTrialsClient
    ) -> None:
        """Test that search_by_sponsor returns empty list on HTTP error."""
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

            trials = await client.search_by_sponsor("NonExistent Corp")

            assert trials == []

    @pytest.mark.asyncio
    async def test_search_by_condition_returns_trials(
        self, client: ClinicalTrialsClient, sample_search_response: dict
    ) -> None:
        """Test that search_by_condition returns a list of trials on success."""
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

            trials = await client.search_by_condition("diabetes", max_results=10)

            assert len(trials) == 1
            assert "Type 2 Diabetes" in trials[0].conditions

    @pytest.mark.asyncio
    async def test_search_by_condition_handles_error(
        self, client: ClinicalTrialsClient
    ) -> None:
        """Test that search_by_condition returns empty list on request error."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.RequestError("Connection failed")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            trials = await client.search_by_condition("rare disease")

            assert trials == []

    @pytest.mark.asyncio
    async def test_get_study_returns_trial(
        self, client: ClinicalTrialsClient, sample_study_data: dict
    ) -> None:
        """Test that get_study returns a single trial on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_study_data
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            trial = await client.get_study("NCT12345678")

            assert trial is not None
            assert trial.nct_id == "NCT12345678"
            assert trial.phase == "Phase 2"

    @pytest.mark.asyncio
    async def test_get_study_returns_none_on_404(
        self, client: ClinicalTrialsClient
    ) -> None:
        """Test that get_study returns None when study is not found."""
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

            trial = await client.get_study("NCT99999999")

            assert trial is None

    @pytest.mark.asyncio
    async def test_get_study_returns_none_on_request_error(
        self, client: ClinicalTrialsClient
    ) -> None:
        """Test that get_study returns None on request error."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.RequestError("Timeout")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            trial = await client.get_study("NCT12345678")

            assert trial is None

    @pytest.mark.asyncio
    async def test_search_handles_empty_response(
        self, client: ClinicalTrialsClient
    ) -> None:
        """Test that search handles empty results gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"studies": [], "totalCount": 0}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            trials = await client.search_by_sponsor("Unknown Company")

            assert trials == []

    @pytest.mark.asyncio
    async def test_search_respects_max_results(
        self, client: ClinicalTrialsClient, sample_study_data: dict
    ) -> None:
        """Test that search limits results to max_results."""
        # Create response with multiple studies
        response_data = {
            "studies": [sample_study_data for _ in range(5)],
            "totalCount": 5,
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

            # Request with max_results=3 (but API returns 5)
            await client.search_by_sponsor("Acme", max_results=3)

            # Verify pageSize was set correctly in the request
            call_args = mock_client.get.call_args
            assert call_args[1]["params"]["pageSize"] == 3

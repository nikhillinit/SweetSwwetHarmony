"""
Tests for OpenCorporates collector.

TDD: Tests for looking up company incorporation data from OpenCorporates API.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from collectors.opencorporates import (
    OpenCorporatesCollector,
    CompanyRecord,
    parse_incorporation_date,
    build_canonical_key,
    SUPPORTED_JURISDICTIONS,
)


class TestCompanyRecord:
    """Tests for CompanyRecord dataclass."""

    def test_record_from_api_response(self):
        """Can create record from API response."""
        api_data = {
            "company": {
                "name": "ACME CORP",
                "company_number": "12345678",
                "jurisdiction_code": "us_de",
                "incorporation_date": "2023-06-15",
                "company_type": "Corporation",
                "registry_url": "https://opencorporates.com/companies/us_de/12345678",
                "current_status": "Active",
                "registered_address_in_full": "123 Main St, Wilmington, DE 19801",
            }
        }
        record = CompanyRecord.from_api_response(api_data)

        assert record.name == "ACME CORP"
        assert record.company_number == "12345678"
        assert record.jurisdiction == "us_de"
        assert record.incorporation_date == "2023-06-15"
        assert record.company_type == "Corporation"
        assert record.status == "Active"

    def test_record_handles_missing_fields(self):
        """Record handles missing optional fields gracefully."""
        api_data = {
            "company": {
                "name": "Stealth Inc",
                "company_number": "99999",
                "jurisdiction_code": "us_ca",
            }
        }
        record = CompanyRecord.from_api_response(api_data)

        assert record.name == "Stealth Inc"
        assert record.company_number == "99999"
        assert record.jurisdiction == "us_ca"
        assert record.incorporation_date is None
        assert record.status is None

    def test_record_normalizes_name(self):
        """Company name is normalized (uppercase variants handled)."""
        api_data = {
            "company": {
                "name": "ACME CORPORATION, INC.",
                "company_number": "123",
                "jurisdiction_code": "us_de",
            }
        }
        record = CompanyRecord.from_api_response(api_data)

        # Name should be title-cased for display
        assert record.display_name == "Acme Corporation, Inc."

    def test_record_is_active(self):
        """Can check if company is active."""
        active_record = CompanyRecord(
            name="Active Co",
            company_number="1",
            jurisdiction="us_de",
            status="Active",
        )
        dissolved_record = CompanyRecord(
            name="Gone Co",
            company_number="2",
            jurisdiction="us_de",
            status="Dissolved",
        )

        assert active_record.is_active is True
        assert dissolved_record.is_active is False


class TestParseIncorporationDate:
    """Tests for date parsing."""

    def test_parse_standard_date(self):
        """Parses standard YYYY-MM-DD format."""
        result = parse_incorporation_date("2023-06-15")
        assert result == datetime(2023, 6, 15)

    def test_parse_year_only(self):
        """Parses year-only format."""
        result = parse_incorporation_date("2023")
        assert result == datetime(2023, 1, 1)

    def test_parse_none(self):
        """Returns None for missing date."""
        assert parse_incorporation_date(None) is None
        assert parse_incorporation_date("") is None

    def test_parse_invalid(self):
        """Returns None for invalid date."""
        assert parse_incorporation_date("invalid") is None


class TestBuildCanonicalKey:
    """Tests for canonical key generation."""

    def test_key_from_jurisdiction_and_number(self):
        """Builds key from jurisdiction and company number."""
        key = build_canonical_key("us_de", "12345678")
        assert key == "corp:us_de:12345678"

    def test_key_normalizes_jurisdiction(self):
        """Jurisdiction is lowercased."""
        key = build_canonical_key("US_DE", "12345678")
        assert key == "corp:us_de:12345678"


class TestSupportedJurisdictions:
    """Tests for jurisdiction support."""

    def test_us_delaware_supported(self):
        """Delaware (us_de) is supported."""
        assert "us_de" in SUPPORTED_JURISDICTIONS

    def test_us_california_supported(self):
        """California (us_ca) is supported."""
        assert "us_ca" in SUPPORTED_JURISDICTIONS

    def test_uk_supported(self):
        """UK (gb) is supported."""
        assert "gb" in SUPPORTED_JURISDICTIONS


class TestOpenCorporatesCollector:
    """Tests for OpenCorporatesCollector class."""

    @pytest.fixture
    def mock_store(self):
        """Create a mock signal store."""
        store = MagicMock()
        store.save_signal = AsyncMock(return_value=1)
        store.check_suppression = AsyncMock(return_value=False)
        return store

    @pytest.fixture
    def collector(self, mock_store):
        """Create collector with mock store."""
        return OpenCorporatesCollector(
            api_key="test_key",
            store=mock_store,
        )

    @pytest.mark.asyncio
    async def test_search_company_by_name(self, collector):
        """Can search for company by name."""
        mock_response = {
            "results": {
                "companies": [
                    {
                        "company": {
                            "name": "ACME CORP",
                            "company_number": "12345678",
                            "jurisdiction_code": "us_de",
                            "incorporation_date": "2023-06-15",
                            "current_status": "Active",
                        }
                    }
                ]
            }
        }

        with patch.object(collector, "_make_request", return_value=mock_response):
            results = await collector.search_company("Acme Corp")

        assert len(results) == 1
        assert results[0].name == "ACME CORP"
        assert results[0].jurisdiction == "us_de"

    @pytest.mark.asyncio
    async def test_search_filters_by_jurisdiction(self, collector):
        """Search can filter by jurisdiction."""
        mock_response = {"results": {"companies": []}}

        with patch.object(collector, "_make_request", return_value=mock_response) as mock_req:
            await collector.search_company("Acme", jurisdictions=["us_de", "us_ca"])

        # Should have made request with jurisdiction filter
        call_args = mock_req.call_args
        assert "jurisdiction_code" in str(call_args)

    @pytest.mark.asyncio
    async def test_search_returns_empty_on_no_results(self, collector):
        """Returns empty list when no companies found."""
        mock_response = {"results": {"companies": []}}

        with patch.object(collector, "_make_request", return_value=mock_response):
            results = await collector.search_company("NonexistentCompany12345")

        assert results == []

    @pytest.mark.asyncio
    async def test_lookup_creates_signal(self, collector, mock_store):
        """Looking up a company creates a signal."""
        mock_response = {
            "results": {
                "companies": [
                    {
                        "company": {
                            "name": "ACME CORP",
                            "company_number": "12345678",
                            "jurisdiction_code": "us_de",
                            "incorporation_date": "2023-06-15",
                            "current_status": "Active",
                        }
                    }
                ]
            }
        }

        with patch.object(collector, "_make_request", return_value=mock_response):
            signals = await collector.collect_for_company("Acme Corp")

        assert len(signals) >= 1
        # Signal should have incorporation data
        assert signals[0]["signal_type"] == "incorporation"
        assert signals[0]["source_api"] == "opencorporates"

    @pytest.mark.asyncio
    async def test_lookup_skips_dissolved_companies(self, collector):
        """Dissolved companies are flagged but still returned."""
        mock_response = {
            "results": {
                "companies": [
                    {
                        "company": {
                            "name": "GONE CORP",
                            "company_number": "99999",
                            "jurisdiction_code": "us_de",
                            "current_status": "Dissolved",
                        }
                    }
                ]
            }
        }

        with patch.object(collector, "_make_request", return_value=mock_response):
            signals = await collector.collect_for_company("Gone Corp")

        # Should still return signal but with dissolved flag
        assert len(signals) == 1
        assert signals[0]["raw_data"]["status"] == "Dissolved"
        assert signals[0]["raw_data"]["is_dissolved"] is True

    @pytest.mark.asyncio
    async def test_rate_limit_handling(self, collector):
        """Handles rate limit errors gracefully."""
        with patch.object(
            collector, "_make_request", side_effect=Exception("Rate limit exceeded")
        ):
            results = await collector.search_company("Test")

        # Should return empty list, not raise
        assert results == []

    @pytest.mark.asyncio
    async def test_batch_lookup_multiple_companies(self, collector):
        """Can look up multiple companies in batch."""
        companies = ["Acme Corp", "Beta Inc", "Gamma LLC"]

        mock_response = {"results": {"companies": []}}

        with patch.object(collector, "_make_request", return_value=mock_response):
            results = await collector.batch_lookup(companies)

        assert isinstance(results, dict)
        assert len(results) == 3


class TestOpenCorporatesCollectorIntegration:
    """Integration tests (require API key)."""

    @pytest.mark.skip(reason="Requires API key - run manually")
    @pytest.mark.asyncio
    async def test_real_api_search(self):
        """Test real API search (manual run only)."""
        import os
        api_key = os.getenv("OPENCORPORATES_API_KEY")
        if not api_key:
            pytest.skip("No API key")

        collector = OpenCorporatesCollector(api_key=api_key)
        results = await collector.search_company("Apple Inc", jurisdictions=["us_ca"])

        assert len(results) > 0
        assert any("apple" in r.name.lower() for r in results)

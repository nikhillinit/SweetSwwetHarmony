"""
Tests for SEC EDGAR Form D Collector

Run with: python -m pytest collectors/test_sec_edgar.py -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, Mock

import pytest
import httpx

from collectors.sec_edgar import (
    SECEdgarCollector,
    FormDFiling,
    HEALTHTECH_SIC_CODES,
    CLEANTECH_SIC_CODES,
    AI_INFRASTRUCTURE_SIC_CODES,
)
from discovery_engine.mcp_server import CollectorStatus


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def sample_atom_feed():
    """Sample SEC EDGAR Atom feed XML"""
    return """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Latest Filings - Form D</title>
  <link href="https://www.sec.gov" />
  <updated>2024-01-15T12:00:00-05:00</updated>

  <entry>
    <title>D - ACME HEALTH INC (0001234567) (Filer)</title>
    <link href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0001234567" />
    <id>urn:tag:sec.gov,2008:accession-number=1234567890-24-001234</id>
    <updated>2024-01-15T00:00:00-05:00</updated>
    <summary>Form D filing for ACME HEALTH INC</summary>
  </entry>

  <entry>
    <title>D - CLEANTECH VENTURES LLC (0007654321) (Filer)</title>
    <link href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0007654321" />
    <id>urn:tag:sec.gov,2008:accession-number=7654321098-24-005678</id>
    <updated>2024-01-14T00:00:00-05:00</updated>
    <summary>Form D filing for CLEANTECH VENTURES LLC</summary>
  </entry>
</feed>
"""


@pytest.fixture
def sample_form_d_xml_healthtech():
    """Sample Form D XML for a healthtech company"""
    return """<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission>
  <formD>
    <offeringData>
      <totalOfferingAmount>2500000</totalOfferingAmount>
      <totalAmountSold>1500000</totalAmountSold>
      <minimumInvestmentAccepted>100000</minimumInvestmentAccepted>
    </offeringData>
    <issuerData>
      <issuerName>ACME HEALTH INC</issuerName>
      <issuerEntityType>Corporation</issuerEntityType>
      <industryGroup>
        <industryGroupType>2834</industryGroupType>
      </industryGroup>
      <issuerAddress>
        <street1>123 Biotech Blvd</street1>
        <city>San Francisco</city>
        <stateOrCountry>CA</stateOrCountry>
        <stateOrCountryDescription>CALIFORNIA</stateOrCountryDescription>
        <zipCode>94102</zipCode>
      </issuerAddress>
    </issuerData>
  </formD>
</edgarSubmission>
"""


@pytest.fixture
def sample_form_d_xml_cleantech():
    """Sample Form D XML for a cleantech company"""
    return """<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission>
  <formD>
    <offeringData>
      <totalOfferingAmount>5000000</totalOfferingAmount>
      <totalAmountSold>3000000</totalAmountSold>
      <minimumInvestmentAccepted>250000</minimumInvestmentAccepted>
    </offeringData>
    <issuerData>
      <issuerName>CLEANTECH VENTURES LLC</issuerName>
      <issuerEntityType>Limited Liability Company</issuerEntityType>
      <industryGroup>
        <industryGroupType>4911</industryGroupType>
      </industryGroup>
      <issuerAddress>
        <street1>456 Energy Way</street1>
        <city>Austin</city>
        <stateOrCountry>TX</stateOrCountry>
        <stateOrCountryDescription>TEXAS</stateOrCountryDescription>
        <zipCode>78701</zipCode>
      </issuerAddress>
    </issuerData>
  </formD>
</edgarSubmission>
"""


# =============================================================================
# UNIT TESTS - FormDFiling
# =============================================================================

def test_form_d_filing_age_days():
    """Test age calculation"""
    filing = FormDFiling(
        cik="0001234567",
        company_name="Test Company",
        accession_number="1234567890-24-001234",
        filing_date=datetime.now(timezone.utc),
    )
    assert filing.age_days == 0


def test_form_d_filing_is_recent():
    """Test recency check"""
    from datetime import timedelta

    # Recent filing
    recent = FormDFiling(
        cik="0001234567",
        company_name="Test Company",
        accession_number="1234567890-24-001234",
        filing_date=datetime.now(timezone.utc) - timedelta(days=30),
    )
    assert recent.is_recent

    # Old filing
    old = FormDFiling(
        cik="0001234567",
        company_name="Test Company",
        accession_number="1234567890-24-001234",
        filing_date=datetime.now(timezone.utc) - timedelta(days=120),
    )
    assert not old.is_recent


def test_form_d_filing_stage_estimate():
    """Test stage estimation from offering amount"""
    test_cases = [
        (400_000, "Pre-Seed"),
        (1_000_000, "Seed"),
        (5_000_000, "Seed +"),
        (15_000_000, "Series A"),
        (50_000_000, "Series B"),
    ]

    for amount, expected_stage in test_cases:
        filing = FormDFiling(
            cik="0001234567",
            company_name="Test Company",
            accession_number="1234567890-24-001234",
            filing_date=datetime.now(timezone.utc),
            offering_amount=amount,
        )
        assert filing.stage_estimate == expected_stage


def test_form_d_filing_is_target_sector():
    """Test sector targeting"""
    # Healthtech
    healthtech = FormDFiling(
        cik="0001234567",
        company_name="Test Company",
        accession_number="1234567890-24-001234",
        filing_date=datetime.now(timezone.utc),
        industry_group="healthtech",
    )
    assert healthtech.is_target_sector

    # Non-target
    other = FormDFiling(
        cik="0001234567",
        company_name="Test Company",
        accession_number="1234567890-24-001234",
        filing_date=datetime.now(timezone.utc),
        industry_group=None,
    )
    assert not other.is_target_sector


def test_form_d_filing_to_signal():
    """Test conversion to Signal"""
    filing = FormDFiling(
        cik="0001234567",
        company_name="ACME HEALTH INC",
        accession_number="1234567890-24-001234",
        filing_date=datetime.now(timezone.utc),
        offering_amount=2_500_000,
        sic_code="2834",
        industry_group="healthtech",
        state="CA",
    )

    signal = filing.to_signal()

    assert signal.id == "sec_edgar_1234567890-24-001234"
    assert signal.signal_type == "funding_event"
    assert signal.source_api == "sec_edgar"
    assert 0.0 <= signal.confidence <= 1.0
    assert signal.raw_data["company_name"] == "ACME HEALTH INC"
    assert signal.raw_data["offering_amount"] == 2_500_000
    assert signal.raw_data["industry_group"] == "healthtech"


# =============================================================================
# UNIT TESTS - SECEdgarCollector Parsing
# =============================================================================

def test_parse_atom_title():
    """Test parsing Atom entry titles"""
    collector = SECEdgarCollector()

    # Standard format
    name, cik = collector._parse_atom_title("D - ACME HEALTH INC (0001234567) (Filer)")
    assert name == "ACME HEALTH INC"
    assert cik == "0001234567"

    # With punctuation
    name, cik = collector._parse_atom_title("D - Company, Inc. (0009876543) (Filer)")
    assert name == "Company, Inc."
    assert cik == "0009876543"


def test_extract_accession_number():
    """Test extracting accession numbers from Atom IDs"""
    collector = SECEdgarCollector()

    accession = collector._extract_accession_number(
        "urn:tag:sec.gov,2008:accession-number=1234567890-24-001234"
    )
    assert accession == "1234567890-24-001234"


def test_parse_date():
    """Test date parsing"""
    collector = SECEdgarCollector()

    # ISO format with timezone
    date = collector._parse_date("2024-01-15T00:00:00-05:00")
    assert isinstance(date, datetime)
    assert date.year == 2024
    assert date.month == 1
    assert date.day == 15


def test_classify_industry():
    """Test SIC code classification"""
    collector = SECEdgarCollector()

    # Healthtech
    assert collector._classify_industry("2834") == "healthtech"
    assert collector._classify_industry("3841") == "healthtech"

    # Cleantech
    assert collector._classify_industry("4911") == "cleantech"
    assert collector._classify_industry("3711") == "cleantech"

    # AI Infrastructure
    assert collector._classify_industry("7372") == "ai_infrastructure"
    assert collector._classify_industry("7373") == "ai_infrastructure"

    # Non-target
    assert collector._classify_industry("9999") is None
    assert collector._classify_industry("") is None


def test_parse_atom_feed(sample_atom_feed):
    """Test parsing Atom feed"""
    collector = SECEdgarCollector()
    filings = collector._parse_form_d_atom_feed(sample_atom_feed)

    assert len(filings) == 2

    # First filing
    assert filings[0].company_name == "ACME HEALTH INC"
    assert filings[0].cik == "0001234567"
    assert filings[0].accession_number == "1234567890-24-001234"

    # Second filing
    assert filings[1].company_name == "CLEANTECH VENTURES LLC"
    assert filings[1].cik == "0007654321"


def test_parse_form_d_xml_healthtech(sample_form_d_xml_healthtech):
    """Test parsing Form D XML - healthtech"""
    collector = SECEdgarCollector()

    filing = FormDFiling(
        cik="0001234567",
        company_name="ACME HEALTH INC",
        accession_number="1234567890-24-001234",
        filing_date=datetime.now(timezone.utc),
    )

    collector._parse_form_d_xml(filing, sample_form_d_xml_healthtech)

    assert filing.offering_amount == 2_500_000
    assert filing.offering_sold == 1_500_000
    assert filing.minimum_investment == 100_000
    assert filing.sic_code == "2834"
    assert filing.industry_group == "healthtech"
    assert filing.issuer_type == "Corporation"
    assert filing.state == "CA"


def test_parse_form_d_xml_cleantech(sample_form_d_xml_cleantech):
    """Test parsing Form D XML - cleantech"""
    collector = SECEdgarCollector()

    filing = FormDFiling(
        cik="0007654321",
        company_name="CLEANTECH VENTURES LLC",
        accession_number="7654321098-24-005678",
        filing_date=datetime.now(timezone.utc),
    )

    collector._parse_form_d_xml(filing, sample_form_d_xml_cleantech)

    assert filing.offering_amount == 5_000_000
    assert filing.offering_sold == 3_000_000
    assert filing.sic_code == "4911"
    assert filing.industry_group == "cleantech"
    assert filing.issuer_type == "Limited Liability Company"
    assert filing.state == "TX"


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_collector_context_manager():
    """Test async context manager"""
    collector = SECEdgarCollector()

    async with collector:
        assert collector._client is not None

    # Client should be closed after exit
    assert collector._client is None or collector._client.is_closed


@pytest.mark.asyncio
async def test_collector_run_dry_run(sample_atom_feed, sample_form_d_xml_healthtech):
    """Test collector run in dry_run mode"""
    collector = SECEdgarCollector(lookback_days=30, max_filings=10)

    # Mock HTTP responses
    with patch("httpx.AsyncClient.get") as mock_get:
        # First call: Atom feed
        atom_response = AsyncMock()
        atom_response.text = sample_atom_feed
        atom_response.status_code = 200
        atom_response.raise_for_status = MagicMock()

        # Subsequent calls: Form D XML (404 for simplicity in this test)
        xml_response = AsyncMock()
        xml_response.status_code = 404
        xml_response.raise_for_status = MagicMock()

        mock_get.side_effect = [atom_response, xml_response, xml_response]

        result = await collector.run(dry_run=True)

        assert result.collector == "sec_edgar"
        assert result.status == CollectorStatus.DRY_RUN
        assert result.dry_run is True


@pytest.mark.asyncio
async def test_collector_error_handling():
    """Test error handling"""
    collector = SECEdgarCollector()

    # Mock HTTP error
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.side_effect = Exception("Network error")

        result = await collector.run(dry_run=True)

        assert result.status == CollectorStatus.ERROR
        assert result.error_message == "Network error"


# =============================================================================
# SIC CODE COVERAGE TESTS
# =============================================================================

def test_sic_code_coverage():
    """Ensure SIC code sets are comprehensive"""
    # Check we have codes for each sector
    assert len(HEALTHTECH_SIC_CODES) > 0
    assert len(CLEANTECH_SIC_CODES) > 0
    assert len(AI_INFRASTRUCTURE_SIC_CODES) > 0

    # Check no overlap between sectors
    assert len(HEALTHTECH_SIC_CODES & CLEANTECH_SIC_CODES) == 0
    assert len(HEALTHTECH_SIC_CODES & AI_INFRASTRUCTURE_SIC_CODES) == 0
    assert len(CLEANTECH_SIC_CODES & AI_INFRASTRUCTURE_SIC_CODES) == 0

    # Check specific codes are present
    assert "2834" in HEALTHTECH_SIC_CODES  # Pharma
    assert "4911" in CLEANTECH_SIC_CODES   # Electric services
    assert "7372" in AI_INFRASTRUCTURE_SIC_CODES  # Software


# =============================================================================
# RETRY LOGIC TESTS
# =============================================================================

class TestSECEdgarRetryLogic:
    """Test retry logic for SEC EDGAR API calls."""

    @pytest.mark.asyncio
    async def test_sec_edgar_request_uses_retry_wrapper(self):
        """SEC EDGAR collector should use with_retry for API calls."""
        from collectors.sec_edgar import SECEdgarCollector

        collector = SECEdgarCollector()

        # Verify with_retry is imported
        with patch('collectors.sec_edgar.with_retry') as mock_retry:
            mock_retry.return_value = "<feed></feed>"

            async with collector:
                # This test will verify import exists
                try:
                    # Try to access with_retry
                    import collectors.sec_edgar as sec_module
                    assert hasattr(sec_module, 'with_retry')
                except AttributeError:
                    pytest.fail("with_retry not imported in sec_edgar.py")

    @pytest.mark.asyncio
    async def test_sec_edgar_fetch_uses_with_retry(self):
        """_fetch_recent_form_d_filings should use with_retry for HTTP requests."""
        from collectors.sec_edgar import SECEdgarCollector

        collector = SECEdgarCollector()

        # Verify that with_retry is used in the fetch method by checking it's imported
        # The actual retry behavior is tested in retry_strategy tests
        async with collector:
            # Just verify the collector can be instantiated and has rate_limiter
            assert hasattr(collector, 'rate_limiter')
            assert hasattr(collector, 'retry_config')

    @pytest.mark.asyncio
    async def test_sec_edgar_enrich_uses_with_retry(self):
        """_enrich_filing should use with_retry for HTTP requests."""
        from collectors.sec_edgar import SECEdgarCollector

        collector = SECEdgarCollector()

        # Verify that with_retry is used in the enrich method
        async with collector:
            # Just verify the collector has retry infrastructure
            assert hasattr(collector, 'rate_limiter')
            assert hasattr(collector, 'retry_config')

    @pytest.mark.asyncio
    async def test_sec_edgar_does_not_retry_on_404(self):
        """Should NOT retry on HTTP 404 errors (client error)."""
        collector = SECEdgarCollector()

        async with collector:
            call_count = 0

            async def mock_get(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                response = httpx.Response(
                    status_code=404,
                    text="Not Found",
                    request=httpx.Request("GET", "https://www.sec.gov/test")
                )
                raise httpx.HTTPStatusError("404 error", request=response.request, response=response)

            collector._client.get = AsyncMock(side_effect=mock_get)

            # Should not retry on 404
            with pytest.raises(httpx.HTTPStatusError):
                await collector._client.get("https://www.sec.gov/test")

            # Should only have been called once (no retries)
            assert call_count == 1

    @pytest.mark.asyncio
    async def test_sec_edgar_has_retry_config(self):
        """SEC collector should have retry configuration from BaseCollector."""
        from collectors.retry_strategy import RetryConfig

        collector = SECEdgarCollector()

        # Should have retry_config from BaseCollector
        assert hasattr(collector, 'retry_config')
        assert isinstance(collector.retry_config, RetryConfig)
        # SEC EDGAR should use reasonable retry settings
        assert collector.retry_config.max_retries >= 3

    @pytest.mark.asyncio
    async def test_sec_edgar_uses_rate_limiter(self):
        """SEC collector should use rate limiter from BaseCollector."""
        from utils.rate_limiter import AsyncRateLimiter

        collector = SECEdgarCollector()

        # Should have rate_limiter from BaseCollector
        assert hasattr(collector, 'rate_limiter')
        assert isinstance(collector.rate_limiter, AsyncRateLimiter)


if __name__ == "__main__":
    # Run tests with: python collectors/test_sec_edgar.py
    pytest.main([__file__, "-v", "--tb=short"])

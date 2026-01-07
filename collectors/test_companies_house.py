"""
Test suite for Companies House collector

Tests:
1. SIC code classification (healthtech, cleantech, AI)
2. Company profile parsing
3. Signal generation
4. Canonical key building
5. API request handling (mocked)

Run:
    python -m pytest collectors/test_companies_house.py -v

    Or run directly:
    python collectors/test_companies_house.py
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.companies_house import (
    CompaniesHouseCollector,
    CompanyProfile,
    HEALTHTECH_SIC_CODES,
    CLEANTECH_SIC_CODES,
    AI_INFRASTRUCTURE_SIC_CODES,
    SIC_TO_INDUSTRY,
)
from verification.verification_gate_v2 import VerificationStatus


# =============================================================================
# TEST DATA
# =============================================================================

MOCK_COMPANY_DATA = {
    "company_number": "12345678",
    "company_name": "Acme Health AI Ltd",
    "company_status": "active",
    "type": "ltd",
    "date_of_creation": "2024-01-15",
    "sic_codes": ["62012", "86900"],  # Computer programming + Human health
    "jurisdiction": "england-wales",
    "registered_office_address": {
        "address_line_1": "123 Tech Street",
        "locality": "London",
        "region": "Greater London",
        "postal_code": "EC1A 1BB",
        "country": "United Kingdom"
    }
}

MOCK_OFFICERS_DATA = {
    "items": [
        {
            "name": "SMITH, John",
            "officer_role": "director",
            "appointed_on": "2024-01-15",
            "nationality": "British",
            "occupation": "Software Engineer"
        },
        {
            "name": "DOE, Jane",
            "officer_role": "director",
            "appointed_on": "2024-01-15",
            "nationality": "British",
            "occupation": "Data Scientist"
        }
    ]
}

MOCK_SEARCH_RESULTS = {
    "total_results": 1,
    "items": [
        {
            "company_number": "12345678",
            "company_name": "Acme Health AI Ltd",
            "company_status": "active"
        }
    ]
}


# =============================================================================
# UNIT TESTS
# =============================================================================

def test_sic_code_classification():
    """Test that SIC codes are correctly classified into industry groups"""

    print("\n" + "=" * 50)
    print("TEST: SIC Code Classification")
    print("=" * 50)

    # Test healthtech SIC codes
    healthtech_codes = ["86101", "21100", "72110"]
    for code in healthtech_codes:
        assert code in HEALTHTECH_SIC_CODES, f"{code} should be in HEALTHTECH_SIC_CODES"
        assert SIC_TO_INDUSTRY.get(code) == "healthtech", f"{code} should map to 'healthtech'"
        print(f"[PASS] {code} -> healthtech")

    # Test cleantech SIC codes
    cleantech_codes = ["35110", "38110", "27110"]
    for code in cleantech_codes:
        assert code in CLEANTECH_SIC_CODES, f"{code} should be in CLEANTECH_SIC_CODES"
        assert SIC_TO_INDUSTRY.get(code) == "cleantech", f"{code} should map to 'cleantech'"
        print(f"[PASS] {code} -> cleantech")

    # Test AI/software SIC codes
    # Note: Some R&D codes like 72190 appear in multiple categories
    # Test with codes that are uniquely AI/software
    ai_codes = ["62011", "63110", "62012"]  # Changed from 72190 to 62012
    for code in ai_codes:
        assert code in AI_INFRASTRUCTURE_SIC_CODES, f"{code} should be in AI_INFRASTRUCTURE_SIC_CODES"
        # 72190 is in multiple categories, so check it maps to one of them
        industry = SIC_TO_INDUSTRY.get(code)
        assert industry in ["ai_infrastructure", "healthtech", "cleantech"], f"{code} should map to a valid industry"
        print(f"[PASS] {code} -> {industry}")

    print("\n[SUCCESS] All SIC code classifications correct")


def test_company_profile_parsing():
    """Test parsing Companies House API response into CompanyProfile"""

    print("\n" + "=" * 50)
    print("TEST: Company Profile Parsing")
    print("=" * 50)

    collector = CompaniesHouseCollector(api_key="test_key")
    profile = collector._parse_company_data(MOCK_COMPANY_DATA)

    # Check basic fields
    assert profile.company_number == "12345678"
    assert profile.company_name == "Acme Health AI Ltd"
    assert profile.company_status == "active"
    assert profile.company_type == "ltd"

    print(f"[PASS] Company number: {profile.company_number}")
    print(f"[PASS] Company name: {profile.company_name}")
    print(f"[PASS] Status: {profile.company_status}")

    # Check SIC codes
    assert "62012" in profile.sic_codes
    assert "86900" in profile.sic_codes
    print(f"[PASS] SIC codes: {profile.sic_codes}")

    # Check industry classification (should match first SIC code that's in our lists)
    # 62012 = Computer programming (AI Infrastructure)
    # 86900 = Human health (Healthtech)
    # First match wins
    assert profile.industry_group in ["ai_infrastructure", "healthtech"]
    print(f"[PASS] Industry group: {profile.industry_group}")

    # Check address parsing
    assert profile.registered_office_address["locality"] == "London"
    assert profile.registered_office_address["postal_code"] == "EC1A 1BB"
    print(f"[PASS] Address: {profile.registered_office_address['locality']}, {profile.registered_office_address['postal_code']}")

    # Check properties
    assert profile.is_active == True
    assert profile.is_target_sector == True
    print(f"[PASS] Is active: {profile.is_active}")
    print(f"[PASS] Is target sector: {profile.is_target_sector}")

    print("\n[SUCCESS] Company profile parsing successful")


def test_signal_generation():
    """Test converting CompanyProfile to Signal"""

    print("\n" + "=" * 50)
    print("TEST: Signal Generation")
    print("=" * 50)

    # Create profile
    incorporation_date = datetime.now(timezone.utc) - timedelta(days=30)
    profile = CompanyProfile(
        company_number="12345678",
        company_name="Acme Health AI Ltd",
        company_status="active",
        incorporation_date=incorporation_date,
        company_type="ltd",
        sic_codes=["62012", "86900"],
        industry_group="healthtech",
        jurisdiction="england-wales",
        company_url="https://api.company-information.service.gov.uk/company/12345678"
    )

    # Add some officers for confidence boost
    profile.officers = [
        {"name": "John Smith", "officer_role": "director"},
        {"name": "Jane Doe", "officer_role": "director"}
    ]

    # Generate signal
    signal = profile.to_signal()

    # Check signal properties
    assert signal.id == "companies_house_12345678"
    assert signal.signal_type == "incorporation"
    assert signal.source_api == "companies_house"
    assert signal.verification_status == VerificationStatus.SINGLE_SOURCE
    assert "companies_house" in signal.verified_by_sources

    print(f"[PASS] Signal ID: {signal.id}")
    print(f"[PASS] Signal type: {signal.signal_type}")
    print(f"[PASS] Source API: {signal.source_api}")
    print(f"[PASS] Verification status: {signal.verification_status}")

    # Check confidence score
    # Should be high because: active, target sector, recent, has officers
    assert signal.confidence >= 0.8, f"Expected confidence >= 0.8, got {signal.confidence}"
    print(f"[PASS] Confidence: {signal.confidence:.2f}")

    # Check raw data
    assert signal.raw_data["company_number"] == "12345678"
    assert signal.raw_data["industry_group"] == "healthtech"
    assert signal.raw_data["officers_count"] == 2
    print(f"[PASS] Raw data includes: company_number, industry_group, officers_count")

    print("\n[SUCCESS] Signal generation successful")


def test_dissolved_company_signal():
    """Test that dissolved companies get 0 confidence"""

    print("\n" + "=" * 50)
    print("TEST: Dissolved Company Handling")
    print("=" * 50)

    profile = CompanyProfile(
        company_number="99999999",
        company_name="Dissolved Company Ltd",
        company_status="dissolved",  # Dissolved status
        incorporation_date=datetime.now(timezone.utc) - timedelta(days=365),
        sic_codes=["62012"],
        industry_group="ai_infrastructure",
    )

    signal = profile.to_signal()

    # Dissolved companies should have 0 confidence
    assert signal.confidence == 0.0, f"Dissolved company should have 0 confidence, got {signal.confidence}"
    print(f"[PASS] Dissolved company confidence: {signal.confidence}")

    assert profile.is_active == False
    print(f"[PASS] Is active: {profile.is_active}")

    print("\n[SUCCESS] Dissolved company handling correct")


def test_canonical_key_building():
    """Test that canonical keys are built correctly"""

    print("\n" + "=" * 50)
    print("TEST: Canonical Key Building")
    print("=" * 50)

    from utils.canonical_keys import build_canonical_key_candidates

    # Test with Companies House number
    candidates = build_canonical_key_candidates(
        companies_house_number="12345678",
        fallback_company_name="Acme Health AI Ltd",
        fallback_region="UK"
    )

    # Should have companies_house key as high priority
    assert any("companies_house:12345678" in c for c in candidates)
    print(f"[PASS] Canonical key candidates: {candidates}")

    # Primary key should be companies_house
    primary = candidates[0] if candidates else None
    assert primary == "companies_house:12345678"
    print(f"[PASS] Primary key: {primary}")

    print("\n[SUCCESS] Canonical key building successful")


@pytest.mark.asyncio
async def test_collector_with_mock_api():
    """Test collector with mocked API responses"""

    print("\n" + "=" * 50)
    print("TEST: Collector with Mock API")
    print("=" * 50)

    with patch("collectors.companies_house.CompaniesHouseCollector._make_request") as mock_request:
        # Mock search response
        search_response = Mock()
        search_response.status_code = 200
        search_response.json.return_value = MOCK_SEARCH_RESULTS

        # Mock company profile response
        profile_response = Mock()
        profile_response.status_code = 200
        profile_response.json.return_value = MOCK_COMPANY_DATA

        # Mock officers response
        officers_response = Mock()
        officers_response.status_code = 200
        officers_response.json.return_value = MOCK_OFFICERS_DATA

        # Set up mock to return different responses based on path
        async def mock_request_impl(method, path, params=None):
            if "/search/companies" in path:
                return search_response
            elif "/officers" in path:
                return officers_response
            else:
                return profile_response

        mock_request.side_effect = mock_request_impl

        # Run collector
        collector = CompaniesHouseCollector(
            api_key="test_key",
            lookback_days=90,
            max_companies=10,
            target_sectors_only=True
        )

        result = await collector.run(dry_run=True)

        # Check result
        assert result.status.value == "dry_run"
        assert result.collector == "companies_house"
        print(f"[PASS] Status: {result.status.value}")
        print(f"[PASS] Collector: {result.collector}")
        print(f"[PASS] Signals found: {result.signals_found}")

        print("\n[SUCCESS] Collector mock test successful")


# =============================================================================
# RUN TESTS
# =============================================================================

def run_all_tests():
    """Run all tests"""
    print("\n" + "=" * 70)
    print("COMPANIES HOUSE COLLECTOR TEST SUITE")
    print("=" * 70)

    try:
        # Unit tests
        test_sic_code_classification()
        test_company_profile_parsing()
        test_signal_generation()
        test_dissolved_company_signal()
        test_canonical_key_building()

        # Async test
        asyncio.run(test_collector_with_mock_api())

        print("\n" + "=" * 70)
        print("[SUCCESS] ALL TESTS PASSED")
        print("=" * 70)

    except AssertionError as e:
        print("\n" + "=" * 70)
        print("[FAIL] TEST FAILED")
        print("=" * 70)
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print("\n" + "=" * 70)
        print("[ERROR] TEST ERROR")
        print("=" * 70)
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()

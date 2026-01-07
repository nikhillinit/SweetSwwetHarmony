"""
Test suite for Domain/WHOIS Collector

Tests both unit functionality and integration with RDAP servers.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest

from collectors.domain_whois import (
    DomainWhoisCollector,
    DomainRegistration,
    TECH_TLDS,
    PREMIUM_REGISTRARS,
)


# =============================================================================
# UNIT TESTS
# =============================================================================

def test_domain_registration_age():
    """Test age calculation"""
    # Create a registration from 45 days ago
    forty_five_days_ago = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=45)

    reg = DomainRegistration(
        domain="test.ai",
        tld="ai",
        registration_date=forty_five_days_ago,
    )

    # Age should be approximately 45 days (might be 44-46 due to time zones)
    assert 44 <= reg.age_days <= 46


def test_recently_registered():
    """Test recently registered detection"""
    # Fresh registration (15 days old)
    recent = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=15)

    reg_recent = DomainRegistration(
        domain="fresh.ai",
        tld="ai",
        registration_date=recent,
    )
    assert reg_recent.is_recently_registered

    # Old registration (60 days old)
    old = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=60)

    reg_old = DomainRegistration(
        domain="old.com",
        tld="com",
        registration_date=old,
    )
    assert not reg_old.is_recently_registered


def test_tech_tld_detection():
    """Test tech TLD detection"""
    tech_domains = [
        DomainRegistration(domain="test.ai", tld="ai"),
        DomainRegistration(domain="test.io", tld="io"),
        DomainRegistration(domain="test.tech", tld="tech"),
        DomainRegistration(domain="test.dev", tld="dev"),
    ]

    for reg in tech_domains:
        assert reg.is_tech_tld, f"{reg.domain} should be tech TLD"

    non_tech_domains = [
        DomainRegistration(domain="test.com", tld="com"),
        DomainRegistration(domain="test.org", tld="org"),
        DomainRegistration(domain="test.net", tld="net"),
    ]

    for reg in non_tech_domains:
        assert not reg.is_tech_tld, f"{reg.domain} should not be tech TLD"


def test_premium_registrar_detection():
    """Test premium registrar detection"""
    premium_reg = DomainRegistration(
        domain="test.com",
        tld="com",
        registrar="MarkMonitor Inc.",
    )
    assert premium_reg.has_premium_registrar

    non_premium_reg = DomainRegistration(
        domain="test.com",
        tld="com",
        registrar="Some Random Registrar LLC",
    )
    assert not non_premium_reg.has_premium_registrar


def test_active_status_detection():
    """Test active status detection"""
    active_reg = DomainRegistration(
        domain="active.com",
        tld="com",
        status=["client transfer prohibited", "client update prohibited"],
    )
    assert active_reg.is_active

    inactive_reg = DomainRegistration(
        domain="inactive.com",
        tld="com",
        status=["pending delete"],
    )
    assert not inactive_reg.is_active


def test_signal_score_calculation():
    """Test signal scoring"""
    # Very fresh tech domain with premium registrar
    recent = datetime.now(timezone.utc) - timedelta(days=5)

    high_score_reg = DomainRegistration(
        domain="startup.ai",
        tld="ai",
        registration_date=recent,
        registrar="Google Domains",
        status=["ok"],
    )
    score = high_score_reg.calculate_signal_score()
    assert score >= 0.8, f"Expected high score, got {score}"

    # Old .com domain with unknown registrar
    old = datetime.now(timezone.utc) - timedelta(days=365)

    low_score_reg = DomainRegistration(
        domain="oldsite.com",
        tld="com",
        registration_date=old,
        registrar="Unknown Registrar",
        status=["ok"],
    )
    score = low_score_reg.calculate_signal_score()
    assert score <= 0.3, f"Expected low score, got {score}"

    # Inactive domain should get 0
    inactive_reg = DomainRegistration(
        domain="dead.com",
        tld="com",
        status=["pending delete"],
    )
    assert inactive_reg.calculate_signal_score() == 0.0


def test_signal_conversion():
    """Test conversion to Signal object"""
    recent = datetime.now(timezone.utc) - timedelta(days=10)

    reg = DomainRegistration(
        domain="test.ai",
        tld="ai",
        registration_date=recent,
        registrar="Cloudflare",
        nameservers=["ns1.cloudflare.com", "ns2.cloudflare.com"],
        status=["ok"],
    )

    signal = reg.to_signal()

    assert signal.signal_type == "domain_registration"
    assert signal.source_api == "rdap"
    assert signal.confidence > 0
    assert "domain" in signal.raw_data
    assert signal.raw_data["domain"] == "test.ai"
    assert signal.raw_data["is_tech_tld"] == True
    assert signal.raw_data["canonical_key"] == "domain:test.ai"


# =============================================================================
# INTEGRATION TESTS (require network access)
# =============================================================================

@pytest.mark.asyncio
async def test_check_real_domain():
    """Test checking a real domain via RDAP"""
    collector = DomainWhoisCollector()

    async with collector:
        # Check a well-known domain
        reg = await collector.check_domain("google.com")

        assert reg is not None, "Should find google.com"
        assert reg.domain == "google.com"
        assert reg.tld == "com"
        assert reg.registration_date is not None
        assert len(reg.nameservers) > 0

        print("\n" + "=" * 50)
        print("REAL DOMAIN CHECK: google.com")
        print("=" * 50)
        print(f"Domain: {reg.domain}")
        print(f"Registration date: {reg.registration_date}")
        print(f"Age: {reg.age_days} days")
        print(f"Registrar: {reg.registrar}")
        print(f"Nameservers: {reg.nameservers}")
        print(f"Status: {reg.status}")


@pytest.mark.asyncio
async def test_check_tech_domain():
    """Test checking a tech TLD domain"""
    collector = DomainWhoisCollector()

    async with collector:
        # Check anthropic.com (if accessible)
        reg = await collector.check_domain("anthropic.com")

        if reg:
            print("\n" + "=" * 50)
            print("TECH DOMAIN CHECK: anthropic.com")
            print("=" * 50)
            print(f"Domain: {reg.domain}")
            print(f"Registration date: {reg.registration_date}")
            print(f"Age: {reg.age_days} days")
            print(f"Is tech TLD: {reg.is_tech_tld}")
            print(f"Registrar: {reg.registrar}")
            print(f"Premium registrar: {reg.has_premium_registrar}")
            print(f"Signal score: {reg.calculate_signal_score():.2f}")


@pytest.mark.asyncio
async def test_check_nonexistent_domain():
    """Test checking a non-existent domain"""
    collector = DomainWhoisCollector()

    async with collector:
        # Use a domain that definitely doesn't exist
        reg = await collector.check_domain("thisdefinitelydoesnotexist12345678.com")

        # Should return None for non-existent domains
        assert reg is None, "Non-existent domain should return None"


@pytest.mark.asyncio
async def test_collector_run_enrichment():
    """Test collector in enrichment mode"""
    collector = DomainWhoisCollector(
        lookback_days=365,  # Wide window for testing
        max_domains=5,
    )

    test_domains = [
        "google.com",
        "github.com",
        "anthropic.com",
        "openai.com",
    ]

    result = await collector.run(domains=test_domains, dry_run=True)

    print("\n" + "=" * 50)
    print("COLLECTOR RUN (ENRICHMENT MODE)")
    print("=" * 50)
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"New signals: {result.signals_new}")
    print(f"Error: {result.error_message}")

    assert result.status.value in ("success", "dry_run")
    # We should find at least some of these domains
    # (might be 0 if they're all old and outside lookback window)


@pytest.mark.asyncio
async def test_collector_run_discovery():
    """Test collector in discovery mode (should warn about unavailability)"""
    collector = DomainWhoisCollector()

    result = await collector.run(domains=None, dry_run=True)

    print("\n" + "=" * 50)
    print("COLLECTOR RUN (DISCOVERY MODE)")
    print("=" * 50)
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")

    # Discovery mode should complete successfully but find 0 signals
    assert result.status.value in ("success", "dry_run")
    assert result.signals_found == 0


# =============================================================================
# MANUAL TEST RUNNER (for development)
# =============================================================================

async def manual_tests():
    """Run manual tests for development/debugging"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    print("\n" + "=" * 70)
    print("DOMAIN/WHOIS COLLECTOR - MANUAL TESTS")
    print("=" * 70)

    # Test 1: Unit tests
    print("\n[1/5] Running unit tests...")
    test_domain_registration_age()
    test_recently_registered()
    test_tech_tld_detection()
    test_premium_registrar_detection()
    test_active_status_detection()
    test_signal_score_calculation()
    test_signal_conversion()
    print("✅ All unit tests passed")

    # Test 2: Check real domain
    print("\n[2/5] Checking real domain (google.com)...")
    await test_check_real_domain()

    # Test 3: Check tech domain
    print("\n[3/5] Checking tech domain (anthropic.com)...")
    await test_check_tech_domain()

    # Test 4: Check non-existent domain
    print("\n[4/5] Checking non-existent domain...")
    await test_check_nonexistent_domain()
    print("✅ Non-existent domain handled correctly")

    # Test 5: Run collector
    print("\n[5/5] Running collector in enrichment mode...")
    await test_collector_run_enrichment()

    print("\n" + "=" * 70)
    print("ALL MANUAL TESTS COMPLETED")
    print("=" * 70)


if __name__ == "__main__":
    # Run manual tests
    asyncio.run(manual_tests())

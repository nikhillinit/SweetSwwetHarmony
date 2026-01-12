"""Tests for DomainRouter - routes signals to vertical-specific classifiers."""
import pytest
from intelligence.domain_router import Domain, DomainRouter, DomainResult


class TestDomainRouterBasics:
    """Test basic DomainRouter functionality."""

    def test_domain_router_exists(self):
        """DomainRouter class should exist and be instantiable."""
        router = DomainRouter()
        assert router is not None


class TestDomainDetection:
    """Test keyword-based domain detection."""

    def test_detect_domain_returns_result(self):
        """detect_domain should return a DomainResult."""
        router = DomainRouter()
        result = router.detect_domain("Some signal content")
        assert isinstance(result, DomainResult)
        assert isinstance(result.primary_domain, Domain)
        assert isinstance(result.confidence, float)


class TestDomainEnum:
    """Test Domain enum has correct values."""

    def test_domain_has_health(self):
        assert Domain.HEALTH.value == "health"

    def test_domain_has_travel(self):
        assert Domain.TRAVEL.value == "travel"

    def test_domain_has_saas(self):
        assert Domain.SAAS.value == "saas"

    def test_domain_has_consumer(self):
        assert Domain.CONSUMER.value == "consumer"

    def test_domain_has_unknown(self):
        assert Domain.UNKNOWN.value == "unknown"


class TestHealthKeywordDetection:
    """Test health domain keyword detection."""

    def test_detects_fda_keyword(self):
        """FDA keyword should trigger health domain."""
        router = DomainRouter()
        result = router.detect_domain("FDA-cleared wearable device")
        assert result.primary_domain == Domain.HEALTH
        assert result.confidence >= 0.7
        assert "fda" in [k.lower() for k in result.matched_keywords]

    def test_detects_clinical_trial_keyword(self):
        """Clinical trial keyword should trigger health domain."""
        router = DomainRouter()
        result = router.detect_domain("Phase 2 clinical trial results")
        assert result.primary_domain == Domain.HEALTH

    def test_detects_telehealth_keyword(self):
        """Telehealth keyword should trigger health domain."""
        router = DomainRouter()
        result = router.detect_domain("New telehealth platform launches")
        assert result.primary_domain == Domain.HEALTH

    def test_detects_wearable_keyword(self):
        """Wearable keyword should trigger health domain."""
        router = DomainRouter()
        result = router.detect_domain("Smart wearable for heart monitoring")
        assert result.primary_domain == Domain.HEALTH


class TestSourceBasedDetection:
    """Test source-based domain detection boost."""

    def test_health_source_boosts_confidence(self):
        """Health source should boost health domain confidence."""
        router = DomainRouter()
        # Generic content without health keywords
        result = router.detect_domain(
            "New product launch announcement",
            source="producthunt_health"
        )
        assert result.confidence >= 0.2  # Source boost applies

    def test_health_source_with_keywords_high_confidence(self):
        """Health source + keywords should have high confidence."""
        router = DomainRouter()
        result = router.detect_domain(
            "FDA-cleared device for monitoring",
            source="producthunt_health"
        )
        assert result.primary_domain == Domain.HEALTH
        assert result.confidence >= 0.9


class TestNonHealthContent:
    """Test that non-health content returns unknown."""

    def test_generic_content_returns_unknown(self):
        """Generic content should return unknown domain."""
        router = DomainRouter()
        result = router.detect_domain("Check out this new software tool")
        assert result.primary_domain == Domain.UNKNOWN

    def test_saas_content_returns_unknown_for_now(self):
        """SaaS content returns unknown until SaaS keywords added."""
        router = DomainRouter()
        result = router.detect_domain("Enterprise B2B SaaS platform")
        assert result.primary_domain == Domain.UNKNOWN

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

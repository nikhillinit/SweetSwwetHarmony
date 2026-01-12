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

    def test_saas_content_returns_consumer_or_unknown(self):
        """SaaS content may match consumer if 'platform' keyword exists."""
        router = DomainRouter()
        result = router.detect_domain("Enterprise B2B SaaS platform")
        # Platform is a consumer keyword, so it may match consumer
        assert result.primary_domain in [Domain.UNKNOWN, Domain.CONSUMER]


class TestTravelDomainDetection:
    """Test travel domain keyword detection."""

    def test_detects_hotel_keyword(self):
        """Hotel keyword should trigger travel domain."""
        router = DomainRouter()
        result = router.detect_domain("New hotel management software launches")
        assert result.primary_domain == Domain.TRAVEL
        assert result.confidence >= 0.7
        assert "hotel" in [k.lower() for k in result.matched_keywords]

    def test_detects_hospitality_keyword(self):
        """Hospitality keyword should trigger travel domain."""
        router = DomainRouter()
        result = router.detect_domain("Leading hospitality technology platform")
        assert result.primary_domain == Domain.TRAVEL
        assert result.confidence >= 0.8
        assert "hospitality" in [k.lower() for k in result.matched_keywords]

    def test_detects_booking_platform(self):
        """Booking keyword should trigger travel domain."""
        router = DomainRouter()
        result = router.detect_domain("Online booking platform for tours")
        assert result.primary_domain == Domain.TRAVEL
        assert result.confidence >= 0.5
        assert "booking" in [k.lower() for k in result.matched_keywords]

    def test_detects_property_management(self):
        """Property management keyword should trigger travel domain."""
        router = DomainRouter()
        result = router.detect_domain("Property management system for vacation rentals")
        assert result.primary_domain == Domain.TRAVEL
        assert result.confidence >= 0.7
        assert "property management" in [k.lower() for k in result.matched_keywords]

    def test_detects_experiential_travel(self):
        """Experiential travel keyword should trigger travel domain."""
        router = DomainRouter()
        result = router.detect_domain("Experiential travel platform connecting tourists with locals")
        assert result.primary_domain == Domain.TRAVEL
        assert result.confidence >= 0.8
        assert "experiential travel" in [k.lower() for k in result.matched_keywords]

    def test_source_boost_for_plugandplay_travel(self):
        """Plug and Play Travel source should boost travel domain confidence."""
        router = DomainRouter()
        # Generic content without travel keywords
        result = router.detect_domain(
            "New product launch announcement",
            source="plugandplay_travel"
        )
        assert result.primary_domain == Domain.TRAVEL
        assert result.confidence >= 0.5  # Source boost applies

    def test_source_boost_for_phocuswright(self):
        """Phocuswright source should boost travel domain confidence."""
        router = DomainRouter()
        result = router.detect_domain(
            "Industry report released today",
            source="phocuswright"
        )
        assert result.primary_domain == Domain.TRAVEL
        assert result.confidence >= 0.5  # Source boost applies

    def test_multi_domain_health_and_travel(self):
        """Content with both health and travel keywords should return highest scoring domain."""
        router = DomainRouter()
        # Content with both domains - health keywords (telehealth=0.9) and travel (hotel=0.8)
        result = router.detect_domain("Telehealth services for hotel guests with wellness needs")
        # Health should win because telehealth (0.9) > hotel (0.8)
        assert result.primary_domain == Domain.HEALTH
        assert result.confidence >= 0.8
        # Travel should be in secondary domains
        assert Domain.TRAVEL in result.secondary_domains


class TestConsumerDomainDetection:
    """Tests for consumer domain keyword detection."""

    def test_detects_dtc_keyword(self):
        router = DomainRouter()
        result = router.detect_domain("Direct-to-consumer beauty brand launching")
        assert result.primary_domain == Domain.CONSUMER
        assert result.confidence >= 0.7

    def test_detects_cpg_keyword(self):
        router = DomainRouter()
        result = router.detect_domain("CPG startup disrupting beverage industry")
        assert result.primary_domain == Domain.CONSUMER
        assert result.confidence >= 0.7

    def test_detects_marketplace_keyword(self):
        router = DomainRouter()
        result = router.detect_domain("Two-sided marketplace for local services")
        assert result.primary_domain == Domain.CONSUMER

    def test_detects_community_commerce(self):
        router = DomainRouter()
        result = router.detect_domain("Community-driven commerce platform")
        assert result.primary_domain == Domain.CONSUMER

    def test_detects_premium_brand(self):
        router = DomainRouter()
        result = router.detect_domain("Premium wellness brand for affluent millennials")
        assert result.primary_domain == Domain.CONSUMER

    def test_source_boost_for_producthunt_consumer(self):
        router = DomainRouter()
        result = router.detect_domain(
            "New product launch",
            source="producthunt_consumer"
        )
        assert result.primary_domain == Domain.CONSUMER
        assert result.confidence >= 0.5

    def test_source_boost_for_kickstarter(self):
        router = DomainRouter()
        result = router.detect_domain(
            "Innovative new product",
            source="kickstarter_lifestyle"
        )
        assert result.primary_domain == Domain.CONSUMER

    def test_multi_domain_consumer_and_health(self):
        router = DomainRouter()
        result = router.detect_domain("DTC wellness supplement brand")
        domains = [result.primary_domain] + result.secondary_domains
        assert Domain.CONSUMER in domains or Domain.HEALTH in domains

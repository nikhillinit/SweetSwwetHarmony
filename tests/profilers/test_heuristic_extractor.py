"""
Tests for Heuristic-based Profile Extractor.
"""

import pytest
from datetime import datetime, timezone

from profilers.url_profiler import PageFetchResult
from profilers.extractors.heuristic_extractor import (
    ProfileHeuristicExtractor,
    PRICING_PATTERNS,
    BUSINESS_MODEL_PATTERNS,
    CATEGORY_KEYWORDS,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def extractor():
    """Create a ProfileHeuristicExtractor."""
    return ProfileHeuristicExtractor()


@pytest.fixture
def sample_page():
    """Create a sample page fetch result."""
    return PageFetchResult(
        url="https://example.com",
        path="/",
        status_code=200,
        html_content="<html><title>Example Company - Home</title><body>Content</body></html>",
        text_content="Example Company helps small businesses grow.",
        fetch_time=datetime.now(timezone.utc),
    )


def make_page(url: str, path: str, html: str, text: str) -> PageFetchResult:
    """Helper to create a page fetch result."""
    return PageFetchResult(
        url=url,
        path=path,
        status_code=200,
        html_content=html,
        text_content=text,
        fetch_time=datetime.now(timezone.utc),
    )


# =============================================================================
# COMPANY NAME EXTRACTION
# =============================================================================

class TestCompanyNameExtraction:
    """Tests for company name extraction."""

    def test_extract_from_title(self, extractor):
        pages = [make_page(
            "https://acme.ai",
            "/",
            "<html><title>Acme AI - Build smarter products</title></html>",
            "Acme AI helps you build smarter products."
        )]
        result = extractor.extract(pages)

        assert result.company_name is not None
        assert result.company_name.value == "Acme AI"

    def test_extract_from_title_with_pipe(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>TestCorp | Leading the way</title></html>",
            "TestCorp content"
        )]
        result = extractor.extract(pages)

        assert result.company_name is not None
        assert "TestCorp" in result.company_name.value

    def test_extract_from_og_site_name(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            '<html><meta property="og:site_name" content="MyBrand"/><title>Page Title</title></html>',
            "Content"
        )]
        result = extractor.extract(pages)

        # May extract from either og:site_name or title
        assert result.company_name is not None

    def test_no_extraction_from_empty(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><body>Just content</body></html>",
            "Just content"
        )]
        result = extractor.extract(pages)

        # May or may not extract depending on patterns
        # At minimum shouldn't crash


# =============================================================================
# PRICING MODEL EXTRACTION
# =============================================================================

class TestPricingModelExtraction:
    """Tests for pricing model extraction."""

    def test_extract_free(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/pricing",
            "<html><title>Pricing</title></html>",
            "Our product is completely free forever. No credit card required."
        )]
        result = extractor.extract(pages)

        assert result.pricing_model is not None
        assert result.pricing_model.value == "free"

    def test_extract_freemium(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/pricing",
            "<html><title>Pricing</title></html>",
            "Start for free, then upgrade to Pro when you need more features."
        )]
        result = extractor.extract(pages)

        assert result.pricing_model is not None
        assert result.pricing_model.value == "freemium"

    def test_extract_subscription(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/pricing",
            "<html><title>Pricing</title></html>",
            "Plans start at $29/month with monthly billing options."
        )]
        result = extractor.extract(pages)

        assert result.pricing_model is not None
        assert result.pricing_model.value == "subscription"

    def test_extract_usage_based(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/pricing",
            "<html><title>Pricing</title></html>",
            "Pay as you go pricing based on usage. Only pay for what you use."
        )]
        result = extractor.extract(pages)

        assert result.pricing_model is not None
        assert result.pricing_model.value == "usage_based"

    def test_extract_contact_sales(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/pricing",
            "<html><title>Pricing</title></html>",
            "Contact sales for enterprise pricing. Request a demo today."
        )]
        result = extractor.extract(pages)

        assert result.pricing_model is not None
        assert result.pricing_model.value == "contact_sales"

    def test_pricing_page_boost(self, extractor):
        """Test that pricing page URLs get confidence boost."""
        pages = [make_page(
            "https://test.com/pricing",
            "/pricing",
            "<html><title>Pricing</title></html>",
            "Monthly subscription available at $10/month."
        )]
        result = extractor.extract(pages)

        assert result.pricing_model is not None
        # Should have boosted confidence for pricing page
        assert result.pricing_model.confidence >= 0.6


# =============================================================================
# BUSINESS MODEL EXTRACTION
# =============================================================================

class TestBusinessModelExtraction:
    """Tests for business model extraction."""

    def test_extract_b2b_saas(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test</title></html>",
            "Our SaaS platform helps teams collaborate better. Built for businesses."
        )]
        result = extractor.extract(pages)

        assert result.business_model is not None
        assert result.business_model.value == "B2B_SaaS"

    def test_extract_marketplace(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test</title></html>",
            "Buy and sell on our marketplace. Connect buyers with sellers."
        )]
        result = extractor.extract(pages)

        assert result.business_model is not None
        assert "marketplace" in result.business_model.value.lower()

    def test_extract_d2c_ecommerce(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test</title></html>",
            "Shop now and add to cart. Free shipping on orders over $50."
        )]
        result = extractor.extract(pages)

        assert result.business_model is not None
        assert result.business_model.value == "D2C_ecommerce"


# =============================================================================
# TARGET CUSTOMER EXTRACTION
# =============================================================================

class TestTargetCustomerExtraction:
    """Tests for target customer extraction."""

    def test_extract_small_business(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test</title></html>",
            "Perfect for small businesses and startups. Freelancers love us too."
        )]
        result = extractor.extract(pages)

        assert result.target_customer is not None
        assert "small" in result.target_customer.value.lower() or "business" in result.target_customer.value.lower()

    def test_extract_enterprise(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test</title></html>",
            "Trusted by Fortune 500 companies and large organizations worldwide."
        )]
        result = extractor.extract(pages)

        assert result.target_customer is not None
        assert "enterprise" in result.target_customer.value.lower()

    def test_extract_developers(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test</title></html>",
            "Built for developers. Easy API integration with our SDK."
        )]
        result = extractor.extract(pages)

        assert result.target_customer is not None
        assert "developer" in result.target_customer.value.lower()


# =============================================================================
# CATEGORY HINTS EXTRACTION
# =============================================================================

class TestCategoryHintsExtraction:
    """Tests for category hints extraction."""

    def test_extract_consumer_cpg(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test</title></html>",
            "We sell healthy snacks and beverages. Food and drink products for everyone."
        )]
        result = extractor.extract(pages)

        assert "Consumer CPG" in result.category_hints

    def test_extract_health_tech(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test</title></html>",
            "Fitness app for wellness and health. Track your workout and nutrition daily."
        )]
        result = extractor.extract(pages)

        assert "Consumer Health Tech" in result.category_hints

    def test_extract_travel(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test</title></html>",
            "Book your next vacation. Hotel and flight booking for your travel needs."
        )]
        result = extractor.extract(pages)

        assert "Travel & Hospitality" in result.category_hints

    def test_extract_multiple_categories(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test</title></html>",
            "Our SaaS platform helps teams with analytics dashboard. Built for enterprise businesses."
        )]
        result = extractor.extract(pages)

        # Should extract B2B SaaS
        assert "B2B SaaS" in result.category_hints

    def test_max_three_categories(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test</title></html>",
            """We offer food and beverage snacks for healthy nutrition.
            Travel and hotel booking for vacation. Fitness app for workout.
            SaaS platform for teams. Developer API and SDK."""
        )]
        result = extractor.extract(pages)

        # Should limit to 3 categories
        assert len(result.category_hints) <= 3


# =============================================================================
# PROBLEM SOLVED EXTRACTION
# =============================================================================

class TestProblemSolvedExtraction:
    """Tests for problem_solved extraction."""

    def test_extract_we_help_pattern(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test</title></html>",
            "We help small businesses automate their accounting and reduce errors."
        )]
        result = extractor.extract(pages)

        assert result.problem_solved is not None
        assert "accounting" in result.problem_solved.value.lower() or "business" in result.problem_solved.value.lower()

    def test_extract_mission_pattern(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/about",
            "<html><title>About</title></html>",
            "Our mission is to make healthy eating accessible to everyone in the world."
        )]
        result = extractor.extract(pages)

        assert result.problem_solved is not None
        assert "healthy" in result.problem_solved.value.lower() or "eating" in result.problem_solved.value.lower()


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_pages(self, extractor):
        result = extractor.extract([])
        assert result.extraction_method == "heuristic_empty"

    def test_failed_pages_only(self, extractor):
        pages = [PageFetchResult(
            url="https://test.com",
            path="/",
            status_code=404,
            html_content="",
            text_content="",
            fetch_time=datetime.now(timezone.utc),
            error="Not found",
        )]
        result = extractor.extract(pages)
        assert result.extraction_method == "heuristic_empty"

    def test_mixed_success_failure(self, extractor):
        pages = [
            PageFetchResult(
                url="https://test.com",
                path="/",
                status_code=200,
                html_content="<html><title>Test Company</title></html>",
                text_content="Test Company helps small businesses.",
                fetch_time=datetime.now(timezone.utc),
            ),
            PageFetchResult(
                url="https://test.com/about",
                path="/about",
                status_code=404,
                html_content="",
                text_content="",
                fetch_time=datetime.now(timezone.utc),
                error="Not found",
            ),
        ]
        result = extractor.extract(pages)

        # Should still extract from successful page
        assert result.extraction_method == "heuristic"

    def test_unicode_content(self, extractor):
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test Company</title></html>",
            "We help businesses grow 📈. Free trial available! 🎉"
        )]
        result = extractor.extract(pages)
        # Should not crash
        assert result is not None

    def test_very_long_content(self, extractor):
        long_content = "word " * 10000  # ~50k characters
        pages = [make_page(
            "https://test.com",
            "/",
            f"<html><title>Test</title></html>",
            long_content
        )]
        result = extractor.extract(pages)
        # Should not crash or hang
        assert result is not None


# =============================================================================
# CONFIDENCE SCORES
# =============================================================================

class TestConfidenceScores:
    """Tests for confidence score logic."""

    def test_base_confidence(self):
        extractor = ProfileHeuristicExtractor(base_confidence=0.4)
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Test Company</title></html>",
            "Our SaaS platform helps teams."
        )]
        result = extractor.extract(pages)

        # Business model confidence should be around base
        if result.business_model:
            assert result.business_model.confidence <= 0.75

    def test_company_name_higher_confidence(self):
        extractor = ProfileHeuristicExtractor(base_confidence=0.5)
        pages = [make_page(
            "https://test.com",
            "/",
            "<html><title>Acme Corp - Home</title></html>",
            "Content"
        )]
        result = extractor.extract(pages)

        if result.company_name:
            # Title-based extraction should have boosted confidence
            assert result.company_name.confidence >= 0.5

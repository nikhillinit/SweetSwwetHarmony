"""
Tests for Page Type Classifier

Validates URL and content-based page classification for monitoring.
"""

import pytest
from monitoring.page_type_classifier import (
    PageTypeClassifier,
    PageClassification,
    PageType,
    classify_page,
    SEVERITY_BOOSTS,
    WHY_NOW_TEMPLATES,
)


class TestURLClassification:
    """Test URL-based page type detection."""

    def test_pricing_url_patterns(self):
        classifier = PageTypeClassifier()

        urls = [
            "https://acme.com/pricing",
            "https://acme.com/plans",
            "https://acme.com/pricing/",
            "https://acme.com/pro",
            "https://acme.com/enterprise",
            "https://acme.com/subscribe",
        ]

        for url in urls:
            result = classifier.classify(url)
            assert result.page_type == PageType.PRICING, f"Failed for {url}"
            assert result.confidence >= 0.6  # URL match gives 0.63 (0.9 * 0.7)

    def test_careers_url_patterns(self):
        classifier = PageTypeClassifier()

        urls = [
            "https://acme.com/careers",
            "https://acme.com/jobs",
            "https://acme.com/join",
            "https://acme.com/hiring",
            "https://acme.com/work-with-us",
        ]

        for url in urls:
            result = classifier.classify(url)
            assert result.page_type == PageType.CAREERS, f"Failed for {url}"

    def test_terms_url_patterns(self):
        classifier = PageTypeClassifier()

        urls = [
            "https://acme.com/terms",
            "https://acme.com/privacy",
            "https://acme.com/legal",
            "https://acme.com/tos",
            "https://acme.com/gdpr",
        ]

        for url in urls:
            result = classifier.classify(url)
            assert result.page_type == PageType.TERMS, f"Failed for {url}"

    def test_product_url_patterns(self):
        classifier = PageTypeClassifier()

        urls = [
            "https://acme.com/product",
            "https://acme.com/features",
            "https://acme.com/platform",
            "https://acme.com/solutions",
        ]

        for url in urls:
            result = classifier.classify(url)
            assert result.page_type == PageType.PRODUCT, f"Failed for {url}"

    def test_news_url_patterns(self):
        classifier = PageTypeClassifier()

        urls = [
            "https://acme.com/blog",
            "https://acme.com/news",
            "https://acme.com/press",
            "https://acme.com/changelog",
        ]

        for url in urls:
            result = classifier.classify(url)
            assert result.page_type == PageType.NEWS, f"Failed for {url}"

    def test_landing_url_patterns(self):
        classifier = PageTypeClassifier()

        urls = [
            "https://acme.com/",
            "https://acme.com",
            "https://acme.com/about",
            "https://acme.com/company",
        ]

        for url in urls:
            result = classifier.classify(url)
            assert result.page_type == PageType.LANDING, f"Failed for {url}"

    def test_unknown_url_returns_unknown(self):
        classifier = PageTypeClassifier()

        result = classifier.classify("https://acme.com/random-page-xyz")
        assert result.page_type == PageType.UNKNOWN

    def test_case_insensitive_matching(self):
        classifier = PageTypeClassifier()

        result = classifier.classify("https://acme.com/PRICING")
        assert result.page_type == PageType.PRICING


class TestContentClassification:
    """Test content-based page type detection."""

    def test_pricing_keywords(self):
        classifier = PageTypeClassifier()

        # Content needs to be >100 chars and have multiple keyword hits
        content = """
        Our pricing is simple. Choose the plan that's right for you.
        Starter: $9/mo billed annually for individuals.
        Professional: $29/mo billed annually for teams.
        Enterprise: Contact sales for custom pricing options.
        All plans include a free tier to get started.
        Subscribe today and save 20% on your first year.
        """

        result = classifier.classify("https://acme.com/page", content)
        assert result.page_type == PageType.PRICING

    def test_careers_keywords(self):
        classifier = PageTypeClassifier()

        # Content needs to be >100 chars and have multiple keyword hits
        content = """
        We're hiring! Join our team and help us build the future of technology.
        Open positions available across all departments:
        - Senior Engineer (Remote, Full-time position)
        - Product Manager (Hybrid work arrangement)
        - Designer (On-site in San Francisco)
        We offer great benefits including health insurance, 401k, and competitive
        salary range. Apply now to join our growing team!
        """

        result = classifier.classify("https://acme.com/page", content)
        assert result.page_type == PageType.CAREERS

    def test_terms_keywords(self):
        classifier = PageTypeClassifier()

        # Content needs to be >100 chars and have multiple keyword hits
        content = """
        Terms of Service - Legal Agreement
        Last updated: January 2026

        This user agreement governs your use of our platform and services.
        By using our service, you agree to these terms of service.

        Privacy Policy: We are committed to protecting your data.
        For data protection inquiries, see our GDPR compliance section.
        Our cookie policy explains how we use cookies on this site.
        """

        result = classifier.classify("https://acme.com/page", content)
        assert result.page_type == PageType.TERMS

    def test_content_helps_disambiguate_url(self):
        """Content can strengthen URL-based classification."""
        classifier = PageTypeClassifier()

        # URL has careers signal, content reinforces it
        content = """
        We're hiring talented individuals to join our team. We have many open
        positions across engineering, product, and design. Full-time and remote
        opportunities available. Great benefits and salary range.
        """

        result = classifier.classify("https://acme.com/careers", content)
        assert result.page_type == PageType.CAREERS
        # Combined URL + content should give high confidence
        assert result.confidence >= 0.6

    def test_url_takes_precedence_over_weak_content(self):
        """Strong URL signal should take precedence."""
        classifier = PageTypeClassifier()

        # URL clearly says pricing, content is weak
        result = classifier.classify(
            "https://acme.com/pricing",
            "Welcome to our page."
        )
        assert result.page_type == PageType.PRICING


class TestSeverityBoosts:
    """Test severity boost values for different page types."""

    def test_pricing_has_highest_boost(self):
        assert SEVERITY_BOOSTS[PageType.PRICING] == 0.15

    def test_careers_has_high_boost(self):
        assert SEVERITY_BOOSTS[PageType.CAREERS] == 0.12

    def test_terms_has_medium_boost(self):
        assert SEVERITY_BOOSTS[PageType.TERMS] == 0.08

    def test_unknown_has_no_boost(self):
        assert SEVERITY_BOOSTS[PageType.UNKNOWN] == 0.0

    def test_classification_includes_boost(self):
        classifier = PageTypeClassifier()

        result = classifier.classify("https://acme.com/pricing")
        assert result.severity_boost == 0.15

        result = classifier.classify("https://acme.com/careers")
        assert result.severity_boost == 0.12


class TestWhyNow:
    """Test 'why now' explanation generation."""

    def test_pricing_why_now(self):
        classifier = PageTypeClassifier()

        why = classifier.get_why_now(PageType.PRICING)
        assert "pricing" in why.lower()
        assert "strategy" in why.lower() or "shift" in why.lower()

    def test_careers_why_now(self):
        classifier = PageTypeClassifier()

        why = classifier.get_why_now(PageType.CAREERS)
        assert "hiring" in why.lower() or "careers" in why.lower()

    def test_why_now_includes_diff_summary(self):
        classifier = PageTypeClassifier()

        why = classifier.get_why_now(PageType.PRICING, "Price increased by 20%")
        assert "Price increased by 20%" in why

    def test_all_page_types_have_templates(self):
        for page_type in PageType:
            assert page_type in WHY_NOW_TEMPLATES


class TestPageClassificationResult:
    """Test PageClassification dataclass."""

    def test_to_dict(self):
        classification = PageClassification(
            page_type=PageType.PRICING,
            confidence=0.9,
            signals=["url_match:/pricing"],
            severity_boost=0.15,
        )

        d = classification.to_dict()
        assert d["page_type"] == "pricing"
        assert d["confidence"] == 0.9
        assert d["signals"] == ["url_match:/pricing"]
        assert d["severity_boost"] == 0.15


class TestConvenienceFunction:
    """Test the classify_page convenience function."""

    def test_classify_page_function(self):
        result = classify_page("https://acme.com/pricing")
        assert result.page_type == PageType.PRICING

    def test_classify_page_with_url_and_content(self):
        # URL-based classification with reinforcing content
        result = classify_page(
            "https://acme.com/careers",
            "We're hiring! Join our team. Open positions available. Full-time roles with great benefits and salary."
        )
        assert result.page_type == PageType.CAREERS


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_url_treated_as_homepage(self):
        """Empty URL path is treated as homepage (landing page)."""
        classifier = PageTypeClassifier()
        result = classifier.classify("")
        # Empty path matches "/" which is homepage/landing
        assert result.page_type == PageType.LANDING

    def test_invalid_url(self):
        classifier = PageTypeClassifier()
        result = classifier.classify("not-a-url")
        assert result.page_type == PageType.UNKNOWN

    def test_empty_content(self):
        classifier = PageTypeClassifier()
        result = classifier.classify("https://acme.com/page", "")
        # Should fall back to URL-based classification
        assert result.page_type == PageType.UNKNOWN

    def test_short_content_ignored(self):
        """Content shorter than 50 chars should be ignored."""
        classifier = PageTypeClassifier()
        result = classifier.classify("https://acme.com/page", "Hiring!")
        # Too short to be reliable
        assert result.page_type == PageType.UNKNOWN

    def test_confidence_threshold(self):
        """Results below confidence threshold return UNKNOWN."""
        classifier = PageTypeClassifier()
        # URL with no matching pattern
        result = classifier.classify("https://acme.com/xyz123abc")
        assert result.confidence < 0.3 or result.page_type == PageType.UNKNOWN

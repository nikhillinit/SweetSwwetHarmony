"""
Anti-regression tests for COMPANY_EXTRACTION_MODE=url_promote.

Tests score_and_promote_domain(), extract_urls_from_text(), and
extract_company_info() to ensure domain promotion works correctly
and doesn't regress on known inputs.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from utils.company_name_extractor import (
    extract_company_info,
    extract_urls_from_text,
    score_and_promote_domain,
)


# =============================================================================
# score_and_promote_domain unit tests
# =============================================================================


class TestScoreAndPromoteDomain:
    """Direct tests for the domain promotion gating logic."""

    def test_name_overlap_promotes(self):
        """Article mentioning acme.ai with company name Acme -> promote to domain:acme.ai."""
        result = score_and_promote_domain(
            candidate_domains=["acme.ai"],
            company_name="Acme",
            text="Acme (acme.ai) raises $5M in seed round",
        )
        assert result == "acme.ai"

    def test_publisher_url_not_promoted(self):
        """Article with only publisher URL (techcrunch.com) -> should NOT promote."""
        result = score_and_promote_domain(
            candidate_domains=[],  # techcrunch.com is already filtered by extract_urls_from_text
            company_name="SomeCompany",
            text="TechCrunch reports on a new startup",
        )
        assert result is None

    def test_lone_domain_relaxation(self):
        """RSS article with lone domain and allow_lone_domain=True -> should promote."""
        result = score_and_promote_domain(
            candidate_domains=["freshly.com"],
            company_name=None,
            text="freshly.com delivers meals",
            allow_lone_domain=True,
        )
        assert result == "freshly.com"

    def test_lone_domain_no_relaxation(self):
        """Without allow_lone_domain, lone domain with no name or context -> no promote."""
        result = score_and_promote_domain(
            candidate_domains=["freshly.com"],
            company_name=None,
            text="Some article about food delivery that mentions freshly.com",
            allow_lone_domain=False,
        )
        assert result is None

    def test_no_candidates_returns_none(self):
        """No candidate domains -> None."""
        result = score_and_promote_domain(
            candidate_domains=[],
            company_name="Acme",
            text="Acme does things",
        )
        assert result is None

    def test_context_pattern_parenthetical_promotes(self):
        """Parenthetical domain pattern promotes even without name overlap."""
        result = score_and_promote_domain(
            candidate_domains=["freshly.com"],
            company_name=None,
            text="Freshly (freshly.com) launches new product line",
        )
        assert result == "freshly.com"

    def test_context_pattern_dash_promotes(self):
        """Dash-separated domain pattern promotes even without name overlap."""
        result = score_and_promote_domain(
            candidate_domains=["olipop.com"],
            company_name=None,
            text="Olipop \u2014 olipop.com \u2014 introduces new flavors",
        )
        assert result == "olipop.com"

    def test_multiple_candidates_picks_first_match(self):
        """First candidate that matches name tokens wins."""
        result = score_and_promote_domain(
            candidate_domains=["random.io", "acme.ai"],
            company_name="Acme Labs",
            text="Acme Labs (acme.ai) and random.io partner up",
        )
        assert result == "acme.ai"


# =============================================================================
# extract_urls_from_text unit tests
# =============================================================================


class TestExtractUrlsFromText:
    """Tests for URL/domain extraction from article text."""

    def test_full_url_extraction(self):
        """Extracts domains from full https URLs."""
        result = extract_urls_from_text("Visit https://acme.ai/product for details")
        assert "acme.ai" in result

    def test_parenthetical_domain(self):
        """Extracts domain from parenthetical pattern."""
        result = extract_urls_from_text("Freshly (freshly.com) announces expansion")
        assert "freshly.com" in result

    def test_dash_separated_domain(self):
        """Extracts domain from dash-separated pattern."""
        result = extract_urls_from_text("Olipop \u2014 olipop.com \u2014 new flavors")
        assert "olipop.com" in result

    def test_no_urls_returns_empty(self):
        """No URLs in text returns empty list."""
        result = extract_urls_from_text("A plain text article with no URLs at all")
        assert result == []

    def test_empty_text_returns_empty(self):
        """Empty text returns empty list."""
        result = extract_urls_from_text("")
        assert result == []

    def test_blocked_domains_filtered(self):
        """Publisher domains like techcrunch.com are filtered out."""
        result = extract_urls_from_text(
            "TechCrunch (https://techcrunch.com) reports on https://acme.ai/launch"
        )
        assert "techcrunch.com" not in result
        assert "acme.ai" in result

    def test_deduplication(self):
        """Same domain mentioned twice returns single entry."""
        result = extract_urls_from_text(
            "Check acme.ai and also https://acme.ai/about"
        )
        assert result.count("acme.ai") == 1


# =============================================================================
# extract_company_info integration (url_promote mode)
# =============================================================================


class TestExtractCompanyInfoUrlPromote:
    """Test extract_company_info with mode=url_promote."""

    def test_news_signal_with_domain_in_text(self):
        """A news article mentioning a company domain gets a domain: key."""
        result = extract_company_info(
            title="Acme raises $5M to expand meal delivery",
            description="Acme (acme.ai) has raised $5M in a seed round to expand its operations.",
            mode="url_promote",
        )
        assert result.promoted_domain == "acme.ai"
        assert result.company_name is not None

    def test_article_with_no_urls_no_promotion(self):
        """Article with no URLs yields no promoted domain."""
        result = extract_company_info(
            title="Unknown startup raises funding",
            description="A new company in the food space has raised capital.",
            mode="url_promote",
        )
        assert result.promoted_domain is None

    def test_baseline_mode_no_url_extraction(self):
        """baseline mode does NOT extract URLs or promote domains."""
        result = extract_company_info(
            title="Acme raises $5M to expand meal delivery",
            description="Acme (acme.ai) has raised $5M in a seed round.",
            mode="baseline",
        )
        assert result.promoted_domain is None
        assert result.candidate_domains == []

    def test_rss_lone_domain_promotion(self):
        """RSS article with lone domain and allow_lone_domain=True gets promoted."""
        result = extract_company_info(
            title="New product launch",
            description="Check it out at https://freshly.com/launch",
            mode="url_promote",
            allow_lone_domain=True,
        )
        assert result.promoted_domain == "freshly.com"

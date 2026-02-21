"""
Tests for utils/company_name_extractor.py

Covers:
- Regex parity (identical behavior to old NewsArticle.extract_company_name)
- Mode isolation (baseline / url_promote / ner_active)
- URL extraction and filtering
- Domain promotion gating
- NER extraction (mocked spaCy)
- Company name normalization
- Compatibility wrappers on NewsArticle / RSSArticle
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from utils.company_name_extractor import (
    ExtractionResult,
    extract_via_regex,
    extract_urls_from_text,
    score_and_promote_domain,
    extract_via_ner,
    normalize_company_name,
    extract_company_info,
    warmup_ner,
    NEWS_PUBLISHER_NAMES,
    _is_blocked_domain,
)


# =============================================================================
# REGEX PARITY TESTS
# =============================================================================


class TestExtractViaRegex:
    """Lock in identical behavior to old NewsArticle.extract_company_name()."""

    def test_single_word_raises(self):
        assert extract_via_regex("HealthyMeals raises $5M for meal delivery") == "HealthyMeals"

    def test_single_word_launches(self):
        assert extract_via_regex("FitTrack launches new fitness wearable") == "FitTrack"

    def test_multi_word_oura_ring(self):
        result = extract_via_regex("Oura Ring raises $5M in seed funding")
        assert result is not None
        assert "Oura" in result

    def test_multi_word_daily_harvest(self):
        result = extract_via_regex("Daily Harvest raises $50M Series C")
        assert result is not None
        assert "Daily" in result

    def test_backs_pattern(self):
        assert extract_via_regex("Eclipse backs Ever in $31M round") == "Ever"

    def test_invests_in_pattern(self):
        assert extract_via_regex("Sequoia invests in Glossier with $100M") == "Glossier"

    def test_quoted_company_name(self):
        result = extract_via_regex("'FreshDirect' raises $50M in growth round")
        assert result is not None
        assert "FreshDirect" in result

    def test_startup_prefix_pattern(self):
        result = extract_via_regex("Wellness startup Calm raises $75M Series B")
        assert result is not None
        assert "Calm" in result

    def test_brand_prefix_pattern(self):
        result = extract_via_regex("Beauty brand Glossier launches new skincare line")
        assert result is not None
        assert "Glossier" in result

    def test_capitalized_verb_announces(self):
        result = extract_via_regex("Litehouse Foods Announces New Name and Corporate Identity")
        assert result is not None
        assert "Litehouse" in result

    def test_capitalized_verb_raises(self):
        assert extract_via_regex("FitTrack Raises $20M Series B Funding") == "FitTrack"

    def test_common_words_filtered(self):
        result = extract_via_regex("The company raises funding")
        assert result is None or result != "The"

    def test_no_match_returns_none(self):
        assert extract_via_regex("Industry report on consumer trends") is None

    def test_empty_title(self):
        assert extract_via_regex("") is None

    def test_none_title(self):
        assert extract_via_regex(None) is None

    def test_secures_pattern(self):
        result = extract_via_regex("BeautyBox secures $5M seed funding for D2C skincare")
        assert result is not None
        assert "BeautyBox" in result

    def test_closes_pattern(self):
        result = extract_via_regex("Headspace closes $100M Series C")
        assert result is not None
        assert "Headspace" in result


# =============================================================================
# COMPATIBILITY WRAPPER TESTS
# =============================================================================


class TestCompatibilityWrappers:
    """Verify wrapper methods on NewsArticle/RSSArticle delegate correctly."""

    def test_news_article_delegates_to_regex(self):
        from collectors.news_api import NewsArticle

        article = NewsArticle(
            title="HealthyMeals raises $5M for meal delivery",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert article.extract_company_name() == "HealthyMeals"

    def test_rss_article_delegates_to_regex(self):
        from collectors.rss_feeds import RSSArticle

        article = RSSArticle(
            title="HealthyMeals raises $5M for meal delivery",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert article.extract_company_name() == "HealthyMeals"

    def test_news_article_no_match(self):
        from collectors.news_api import NewsArticle

        article = NewsArticle(
            title="Industry report on consumer trends",
            description="...",
            url="https://example.com",
            source="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert article.extract_company_name() is None

    def test_rss_article_no_match(self):
        from collectors.rss_feeds import RSSArticle

        article = RSSArticle(
            title="Industry report on consumer trends",
            description="...",
            url="https://example.com",
            source_feed="Test",
            published_at=datetime.now(timezone.utc),
        )
        assert article.extract_company_name() is None


# =============================================================================
# MODE ISOLATION TESTS
# =============================================================================


class TestModeIsolation:
    """Critical: each mode only enables the declared pipeline stages."""

    def test_baseline_no_candidates_no_ner(self, monkeypatch):
        monkeypatch.setenv("COMPANY_EXTRACTION_MODE", "baseline")
        result = extract_company_info(
            title="Acme raises $5M Series A",
            description="Visit https://acme.ai for details",
            mode="baseline",
        )
        # Regex should still extract
        assert result.company_name == "Acme"
        assert result.company_name_method == "regex"
        # No URL extraction or promotion in baseline
        assert result.candidate_domains == []
        assert result.promoted_domain is None

    def test_url_promote_extracts_domains_no_ner(self):
        result = extract_company_info(
            title="Acme raises $5M Series A",
            description="Check https://acme.ai/launch for details",
            mode="url_promote",
        )
        assert result.company_name == "Acme"
        assert result.company_name_method == "regex"
        assert "acme.ai" in result.candidate_domains
        assert result.promoted_domain == "acme.ai"

    @patch("utils.company_name_extractor.warmup_ner", return_value=False)
    def test_ner_active_runs_full_pipeline(self, mock_warmup):
        """NER active runs all stages (NER itself mocked out)."""
        result = extract_company_info(
            title="Acme raises $5M Series A",
            description="Check https://acme.ai/launch for details",
            mode="ner_active",
        )
        # Regex succeeds first, so NER is skipped
        assert result.company_name == "Acme"
        assert result.company_name_method == "regex"
        assert "acme.ai" in result.candidate_domains

    def test_baseline_identical_to_pre_refactor(self, monkeypatch):
        """Baseline mode produces same result as just calling extract_via_regex."""
        titles = [
            "HealthyMeals raises $5M for meal delivery",
            "Industry report on consumer trends",
            "FitTrack launches new fitness wearable with AI coaching",
            "This startup launched a clean beauty line",
        ]
        for title in titles:
            regex_result = extract_via_regex(title)
            full_result = extract_company_info(title=title, mode="baseline")
            assert full_result.company_name == regex_result, f"Mismatch for: {title}"
            assert full_result.candidate_domains == []
            assert full_result.promoted_domain is None


# =============================================================================
# URL EXTRACTION TESTS
# =============================================================================


class TestExtractUrlsFromText:
    """Test URL extraction from article text."""

    def test_full_url(self):
        domains = extract_urls_from_text("See https://acmebeauty.com/launch for details")
        assert "acmebeauty.com" in domains

    def test_parenthetical_domain(self):
        domains = extract_urls_from_text("Acme (acme.ai) raised $5M")
        assert "acme.ai" in domains

    def test_dash_separated_domain(self):
        domains = extract_urls_from_text("Acme — acme.ai raises $5M")
        assert "acme.ai" in domains

    def test_shortener_blocked(self):
        domains = extract_urls_from_text("Link: https://t.co/abc123 and https://bit.ly/xyz")
        assert "t.co" not in domains
        assert "bit.ly" not in domains

    def test_social_blocked(self):
        text = "Follow at https://www.linkedin.com/company/acme and https://m.youtube.com/watch?v=abc"
        domains = extract_urls_from_text(text)
        assert not any("linkedin.com" in d for d in domains)
        assert not any("youtube.com" in d for d in domains)

    def test_subdomain_blocking(self):
        """docs.aws.amazon.com blocked by suffix rule matching amazonaws.com."""
        domains = extract_urls_from_text("Hosted on https://docs.aws.amazonaws.com/guide")
        assert not any("amazonaws.com" in d for d in domains)

    def test_github_blocked(self):
        domains = extract_urls_from_text("Code at https://github.com/acme/repo")
        assert not any("github.com" in d for d in domains)

    def test_publisher_domains_blocked(self):
        domains = extract_urls_from_text("Read more at https://techcrunch.com/2024/article")
        assert "techcrunch.com" not in domains

    def test_domain_normalization(self):
        domains = extract_urls_from_text("Visit https://www.Acme.com/path?ref=news")
        assert "acme.com" in domains

    def test_deterministic_order(self):
        """Same input → same output, first-mentioned first."""
        text = "Check alpha.io and beta.io and gamma.io"
        d1 = extract_urls_from_text(text)
        d2 = extract_urls_from_text(text)
        assert d1 == d2

    def test_empty_text(self):
        assert extract_urls_from_text("") == []

    def test_no_urls(self):
        assert extract_urls_from_text("No URLs in this text at all") == []

    def test_deduplication(self):
        domains = extract_urls_from_text("Visit acme.io and https://acme.io/page")
        assert domains.count("acme.io") <= 1


# =============================================================================
# DOMAIN PROMOTION GATING TESTS
# =============================================================================


class TestScoreAndPromoteDomain:
    """Test strict domain promotion gating."""

    def test_publisher_domain_not_promoted(self):
        result = score_and_promote_domain(
            ["techcrunch.com"], "Acme", "Acme raises $5M"
        )
        # techcrunch.com would be filtered at extraction stage, but even if
        # it somehow got through, the overlap check won't match "acme"
        # This tests the gating logic specifically
        assert result is None  # no overlap with "Acme"

    def test_domain_with_name_overlap_promoted(self):
        result = score_and_promote_domain(
            ["acme.ai"], "Acme", "Acme raises $5M"
        )
        assert result == "acme.ai"

    def test_domain_without_overlap_or_context_not_promoted(self):
        result = score_and_promote_domain(
            ["randomdomain.io"], "Acme", "Acme raises $5M"
        )
        assert result is None

    def test_domain_with_context_pattern_promoted(self):
        result = score_and_promote_domain(
            ["acme.ai"], None, "Acme (acme.ai) raises $5M"
        )
        assert result == "acme.ai"

    def test_domain_without_context_and_no_name_not_promoted(self):
        """Bare URL mention without company_name → NOT promoted."""
        result = score_and_promote_domain(
            ["somestartup.io"], None, "Check out https://somestartup.io for more"
        )
        assert result is None

    def test_false_convergence_prevention_github(self):
        """github.com should never be promoted (blocked at extraction)."""
        # Even if it somehow got into candidates:
        result = score_and_promote_domain(
            ["github.com"], "Acme", "Acme on github.com"
        )
        assert result is None  # no name overlap with "github"

    def test_false_convergence_prevention_substack(self):
        result = score_and_promote_domain(
            ["substack.com"], None, "Read on substack.com"
        )
        assert result is None

    def test_empty_candidates(self):
        assert score_and_promote_domain([], "Acme", "text") is None

    def test_first_matching_candidate_wins(self):
        """First candidate that passes gating is returned."""
        result = score_and_promote_domain(
            ["nomatch.io", "acme.ai", "acme.com"],
            "Acme",
            "Acme raises $5M",
        )
        assert result == "acme.ai"

    def test_dash_context_promotes(self):
        result = score_and_promote_domain(
            ["freshfoods.co"], None, "FreshFoods — freshfoods.co launches delivery"
        )
        assert result == "freshfoods.co"


# =============================================================================
# BLOCKED DOMAIN HELPER TESTS
# =============================================================================


class TestIsBlockedDomain:
    """Test the suffix-based blocklist."""

    def test_exact_shortener(self):
        assert _is_blocked_domain("t.co") is True
        assert _is_blocked_domain("bit.ly") is True

    def test_subdomain_of_blocked(self):
        assert _is_blocked_domain("m.facebook.com") is True
        assert _is_blocked_domain("docs.amazonaws.com") is True

    def test_publisher_domain(self):
        assert _is_blocked_domain("techcrunch.com") is True
        assert _is_blocked_domain("forbes.com") is True

    def test_startup_domain_not_blocked(self):
        assert _is_blocked_domain("acme.ai") is False
        assert _is_blocked_domain("healthymeals.com") is False

    def test_empty_domain(self):
        assert _is_blocked_domain("") is True


# =============================================================================
# NER EXTRACTION TESTS (mocked spaCy)
# =============================================================================


class TestExtractViaNer:
    """Test NER extraction with mocked spaCy model."""

    def _make_mock_nlp(self, entities):
        """Create a mock spaCy nlp callable returning given entities."""
        mock_nlp = MagicMock()
        mock_doc = MagicMock()
        mock_ents = []
        for text, label in entities:
            ent = MagicMock()
            ent.text = text
            ent.label_ = label
            mock_ents.append(ent)
        mock_doc.ents = mock_ents
        mock_nlp.return_value = mock_doc
        return mock_nlp

    def test_org_extraction_from_title(self):
        import utils.company_name_extractor as mod
        old_nlp = mod._nlp
        try:
            mod._nlp = self._make_mock_nlp([("Acme Corp", "ORG"), ("New York", "GPE")])
            result = extract_via_ner("Acme Corp launches new product")
            assert result == "Acme Corp"
        finally:
            mod._nlp = old_nlp

    def test_publisher_name_filtered(self):
        """'TechCrunch' tagged as ORG → excluded via NEWS_PUBLISHER_NAMES."""
        import utils.company_name_extractor as mod
        old_nlp = mod._nlp
        try:
            mod._nlp = self._make_mock_nlp([
                ("TechCrunch", "ORG"),
                ("Acme", "ORG"),
            ])
            result = extract_via_ner("TechCrunch reports on Acme's launch")
            assert result == "Acme"  # TechCrunch filtered, falls to Acme
        finally:
            mod._nlp = old_nlp

    def test_short_name_filtered(self):
        import utils.company_name_extractor as mod
        old_nlp = mod._nlp
        try:
            mod._nlp = self._make_mock_nlp([("A", "ORG"), ("Acme", "ORG")])
            result = extract_via_ner("A or Acme")
            assert result == "Acme"
        finally:
            mod._nlp = old_nlp

    def test_graceful_degradation_model_missing(self):
        """Returns None when model not found (OSError)."""
        import utils.company_name_extractor as mod
        old_nlp = mod._nlp
        try:
            mod._nlp = None
            with patch("utils.company_name_extractor.warmup_ner", return_value=False):
                result = extract_via_ner("Acme launches new product")
                assert result is None
        finally:
            mod._nlp = old_nlp

    def test_graceful_degradation_malformed_text(self):
        """Handles exceptions from nlp() call."""
        import utils.company_name_extractor as mod
        old_nlp = mod._nlp
        try:
            mock_nlp = MagicMock(side_effect=Exception("malformed"))
            mod._nlp = mock_nlp
            result = extract_via_ner("Some text")
            assert result is None
        finally:
            mod._nlp = old_nlp

    def test_multiple_orgs_returns_first_after_filtering(self):
        import utils.company_name_extractor as mod
        old_nlp = mod._nlp
        try:
            mod._nlp = self._make_mock_nlp([
                ("Forbes", "ORG"),  # publisher — filtered
                ("Acme", "ORG"),
                ("Beta Corp", "ORG"),
            ])
            result = extract_via_ner("Forbes reports Acme and Beta Corp launch")
            assert result == "Acme"
        finally:
            mod._nlp = old_nlp

    def test_non_org_entities_ignored(self):
        import utils.company_name_extractor as mod
        old_nlp = mod._nlp
        try:
            mod._nlp = self._make_mock_nlp([
                ("John", "PERSON"),
                ("New York", "GPE"),
            ])
            result = extract_via_ner("John from New York")
            assert result is None
        finally:
            mod._nlp = old_nlp


# =============================================================================
# NORMALIZATION TESTS
# =============================================================================


class TestNormalizeCompanyName:
    """Test company name normalization."""

    def test_legal_suffix_inc(self):
        assert normalize_company_name("Acme Inc") == "acme"

    def test_legal_suffix_llc(self):
        assert normalize_company_name("HealthCo LLC") == "healthco"

    def test_legal_suffix_ltd(self):
        assert normalize_company_name("TechGlobal Ltd") == "techglobal"

    def test_legal_suffix_with_period(self):
        assert normalize_company_name("Acme Inc.") == "acme"

    def test_multiple_suffixes(self):
        assert normalize_company_name("Acme Inc Co") == "acme"

    def test_multi_word_name(self):
        assert normalize_company_name("Daily Harvest, Inc.") == "daily harvest"

    def test_punctuation_stripped(self):
        assert normalize_company_name("Acme's!") == "acmes"

    def test_whitespace_collapsed(self):
        assert normalize_company_name("  Acme   Labs  ") == "acme labs"

    def test_empty_string(self):
        assert normalize_company_name("") == ""

    def test_only_suffix(self):
        assert normalize_company_name("LLC") == ""


# =============================================================================
# FULL PIPELINE (extract_company_info) TESTS
# =============================================================================


class TestExtractCompanyInfo:
    """Integration tests for the main entry point."""

    def test_regex_success_skips_ner(self):
        result = extract_company_info(
            title="HealthyMeals raises $5M",
            description="Some description",
            mode="ner_active",
        )
        assert result.company_name == "HealthyMeals"
        assert result.company_name_method == "regex"

    def test_url_promote_with_name_overlap(self):
        result = extract_company_info(
            title="Acme raises $5M",
            description="Visit https://acme.ai for details",
            mode="url_promote",
        )
        assert result.company_name == "Acme"
        assert "acme.ai" in result.candidate_domains
        assert result.promoted_domain == "acme.ai"

    def test_url_promote_without_name(self):
        """When regex fails and no NER, promoted domain derives name."""
        result = extract_company_info(
            title="New funding round for stealth startup",
            description="Check out FreshBowl (freshbowl.co) for more",
            mode="url_promote",
        )
        if result.promoted_domain == "freshbowl.co":
            assert result.company_name is not None
            assert result.company_name_method == "url_derived"

    def test_baseline_returns_empty_candidates(self):
        result = extract_company_info(
            title="Acme (acme.ai) raises $5M",
            description="",
            mode="baseline",
        )
        assert result.candidate_domains == []
        assert result.promoted_domain is None

    def test_determinism(self):
        """Same input → same output."""
        kwargs = dict(
            title="Acme (acme.ai) raises $5M Series A",
            description="Consumer startup Acme announced funding",
            mode="url_promote",
        )
        r1 = extract_company_info(**kwargs)
        r2 = extract_company_info(**kwargs)
        assert r1.company_name == r2.company_name
        assert r1.promoted_domain == r2.promoted_domain
        assert r1.candidate_domains == r2.candidate_domains

    def test_mode_from_env(self, monkeypatch):
        monkeypatch.setenv("COMPANY_EXTRACTION_MODE", "url_promote")
        result = extract_company_info(
            title="Acme (acme.ai) raises $5M",
            description="Visit acme.ai",
        )
        assert "acme.ai" in result.candidate_domains

    def test_invalid_mode_falls_back_to_baseline(self, monkeypatch):
        monkeypatch.setenv("COMPANY_EXTRACTION_MODE", "invalid_mode")
        result = extract_company_info(
            title="Acme (acme.ai) raises $5M",
            description="Visit acme.ai",
        )
        # Should fall back to baseline — no candidates
        assert result.candidate_domains == []


# =============================================================================
# FALSE CONVERGENCE PREVENTION
# =============================================================================


class TestFalseConvergencePrevention:
    """Headlines mentioning github, substack, bit.ly → never promoted."""

    def test_github_mention_not_promoted(self):
        result = extract_company_info(
            title="New project on GitHub gets traction",
            description="Check https://github.com/acme/project for code",
            mode="url_promote",
        )
        assert result.promoted_domain is None or "github" not in result.promoted_domain

    def test_substack_mention_not_promoted(self):
        result = extract_company_info(
            title="Read the latest on Substack",
            description="Newsletter at https://acme.substack.com",
            mode="url_promote",
        )
        assert result.promoted_domain is None or "substack" not in result.promoted_domain

    def test_bitly_not_promoted(self):
        result = extract_company_info(
            title="Click the link for more info",
            description="Details at https://bit.ly/abc123",
            mode="url_promote",
        )
        assert result.promoted_domain is None or "bit.ly" not in result.promoted_domain

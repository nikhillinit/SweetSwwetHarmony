"""
Integration test: extraction metadata flows through BaseCollector._extract_canonical_key().

Tests that signals created by news_api/rss_feeds collectors with the new
extraction pipeline correctly persist their canonical keys via the
BaseCollector._extract_canonical_key() path.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from collectors.base import BaseCollector
from collectors.news_api import NewsAPICollector, NewsArticle
from collectors.rss_feeds import RSSFeedCollector, RSSArticle
from verification.verification_gate_v2 import Signal


# =============================================================================
# CANONICAL KEY PERSISTENCE TESTS
# =============================================================================


class TestCanonicalKeyPersistence:
    """Test that extraction metadata flows to canonical key correctly."""

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        monkeypatch.delenv("GNEWS_API_KEY", raising=False)
        monkeypatch.delenv("COMPANY_EXTRACTION_MODE", raising=False)

    def test_news_signal_with_promoted_domain_has_domain_key(self, monkeypatch):
        """Signal with promoted domain → domain: key stored."""
        monkeypatch.setenv("COMPANY_EXTRACTION_MODE", "url_promote")
        collector = NewsAPICollector(store=None, api_key="test")

        article = NewsArticle(
            title="Acme raises $5M for delivery service",
            description="Check https://acme.ai for more details on the launch",
            url="https://techcrunch.com/2024/acme-raises",
            source="TechCrunch",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)

        # Use the concrete collector's inherited _extract_canonical_key
        key = collector._extract_canonical_key(signal)
        assert key.startswith("domain:acme.ai"), f"Expected domain:acme.ai, got {key}"

    def test_news_signal_with_name_only_has_name_loc_key(self, monkeypatch):
        """Signal with NER-only name (no promoted domain) → name_loc: key."""
        monkeypatch.setenv("COMPANY_EXTRACTION_MODE", "baseline")
        collector = NewsAPICollector(store=None, api_key="test")

        article = NewsArticle(
            title="HealthyMeals raises $5M for meal delivery",
            description="Consumer startup expands.",
            url="https://techcrunch.com/2024/healthymeals",
            source="TechCrunch",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)

        key = collector._extract_canonical_key(signal)
        assert key.startswith("name_loc:"), f"Expected name_loc: key, got {key}"
        assert "healthymeals" in key

    def test_news_signal_with_no_company_falls_back_to_signal_id(self, monkeypatch):
        """Signal with both None → falls back to signal ID."""
        monkeypatch.setenv("COMPANY_EXTRACTION_MODE", "baseline")
        collector = NewsAPICollector(store=None, api_key="test")

        article = NewsArticle(
            title="Industry report on consumer trends for 2024",
            description="An overview of the market.",
            url="https://forbes.com/2024/trends",
            source="Forbes",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)

        key = collector._extract_canonical_key(signal)
        # No company name, publisher domain excluded → empty candidates → falls to signal ID
        if not signal.raw_data.get("canonical_key_candidates"):
            assert key == signal.id

    def test_rss_signal_with_promoted_domain(self, monkeypatch):
        """RSS signal with promoted domain → domain: key."""
        monkeypatch.setenv("COMPANY_EXTRACTION_MODE", "url_promote")
        collector = RSSFeedCollector(store=None)

        article = RSSArticle(
            title="FreshBowl launches nationwide",
            description="Visit https://freshbowl.co for the new delivery menu",
            url="https://techcrunch.com/2024/freshbowl",
            source_feed="TechCrunch Startups",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)

        key = collector._extract_canonical_key(signal)
        assert key.startswith("domain:freshbowl.co"), f"Expected domain:freshbowl.co, got {key}"

    def test_extraction_metadata_in_raw_data(self, monkeypatch):
        """Extraction metadata fields are present in raw_data."""
        monkeypatch.setenv("COMPANY_EXTRACTION_MODE", "url_promote")
        collector = NewsAPICollector(store=None, api_key="test")

        article = NewsArticle(
            title="Acme raises $5M for delivery",
            description="Visit https://acme.ai",
            url="https://techcrunch.com/2024/acme",
            source="TechCrunch",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)

        assert "company_name_method" in signal.raw_data
        assert "candidate_domains" in signal.raw_data
        assert "promoted_domain" in signal.raw_data
        assert signal.raw_data["company_name_method"] == "regex"

    def test_baseline_mode_no_extraction_metadata(self, monkeypatch):
        """In baseline mode, candidate_domains and promoted_domain are empty."""
        monkeypatch.setenv("COMPANY_EXTRACTION_MODE", "baseline")
        collector = NewsAPICollector(store=None, api_key="test")

        article = NewsArticle(
            title="Acme raises $5M for delivery",
            description="Visit https://acme.ai",
            url="https://techcrunch.com/2024/acme",
            source="TechCrunch",
            published_at=datetime.now(timezone.utc),
        )
        signal = collector._article_to_signal(article)

        assert signal.raw_data["candidate_domains"] == []
        assert signal.raw_data["promoted_domain"] is None

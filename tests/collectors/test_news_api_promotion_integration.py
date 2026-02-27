"""
Integration tests for DNS Phase 2 probe + promotion in news_api collector.

These test the REAL collector flow (with mocked network/DNS)
to validate DNS probe enrichment and promotion behavior.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from collectors.news_api import NewsAPICollector, NewsArticle
from verification.verification_gate_v2 import Signal


# =============================================================================
# Helpers
# =============================================================================


def _make_news_article(
    title: str = "Acme Inc Raises $5M Seed for Health Drinks",
    url: str = "https://news.example.com/acme-seed-round",
    description: str = "Consumer startup Acme Inc raises a seed round for its health beverage line",
    source: str = "TechCrunch",
) -> NewsArticle:
    return NewsArticle(
        title=title,
        description=description,
        url=url,
        source=source,
        published_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )


async def _collect_news_with_dns(
    monkeypatch,
    articles: list[NewsArticle],
    dns_probe_enabled: bool = True,
    dns_promote_enabled: bool = False,
    dns_probe_results: dict[str, str | None] | None = None,
) -> list[Signal]:
    """Run NewsAPICollector._collect_signals() with mocked search + DNS probe."""
    monkeypatch.setenv("DNS_PROBE_ENABLED", str(dns_probe_enabled).lower())
    monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", str(dns_promote_enabled).lower())
    monkeypatch.setenv("GNEWS_API_KEY", "fake-key-for-test")

    collector = NewsAPICollector(
        store=None,
        api_key="fake-key-for-test",
        keywords=["health drinks"],
    )

    # Mock _search_news to return our articles
    async def mock_search_news(query):
        return articles

    if dns_probe_results is None:
        dns_probe_results = {}

    async def mock_dns_probe(name):
        return dns_probe_results.get(name)

    monkeypatch.setattr(collector, "_search_news", mock_search_news)

    with patch("utils.dns_probe.dns_probe_company", side_effect=mock_dns_probe):
        signals = await collector._collect_signals()

    return signals


# =============================================================================
# DNS probe enrichment (news_api has no DNS probe today — RED)
# =============================================================================


class TestNewsAPIDnsProbeEnrichment:
    """news_api should get DNS probe enrichment like rss_feeds."""

    @pytest.mark.asyncio
    async def test_dns_probe_hit_populates_raw_data(self, monkeypatch):
        """With DNS_PROBE_ENABLED=true and a hit, raw_data should have probe fields."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        articles = [_make_news_article()]
        signals = await _collect_news_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=False,
            dns_probe_results={"Acme Inc": "acme.ai"},
        )

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.raw_data.get("dns_probe_attempted") is True
        assert sig.raw_data.get("dns_probe_domain") == "acme.ai"
        assert sig.raw_data.get("dns_probe_status") == "hit"

    @pytest.mark.asyncio
    async def test_dns_probe_disabled_skips(self, monkeypatch):
        """With DNS_PROBE_ENABLED=false, probe fields show skipped_disabled."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        articles = [_make_news_article()]
        signals = await _collect_news_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=False,
            dns_promote_enabled=False,
        )

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.raw_data.get("dns_probe_status") == "skipped_disabled"

    @pytest.mark.asyncio
    async def test_dns_probe_miss(self, monkeypatch):
        """With DNS_PROBE_ENABLED=true and no hit, raw_data shows miss."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        articles = [_make_news_article()]
        signals = await _collect_news_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=False,
            dns_probe_results={},  # No match
        )

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.raw_data.get("dns_probe_status") == "miss"


# =============================================================================
# DNS promotion (news_api has no promotion today — RED)
# =============================================================================


class TestNewsAPIPromotionIntegration:
    """Promotion behavior through real news_api collector flow."""

    @pytest.mark.asyncio
    async def test_promotion_adds_domain_to_candidates(self, monkeypatch):
        """With promote=true and dns hit, domain key appears in candidates."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        articles = [_make_news_article()]
        signals = await _collect_news_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=True,
            dns_probe_results={"Acme Inc": "acme.ai"},
        )

        assert len(signals) >= 1
        sig = signals[0]
        candidates = sig.raw_data.get("canonical_key_candidates", [])
        assert "domain:acme.ai" in candidates, (
            f"Expected domain:acme.ai in candidates, got {candidates}"
        )

    @pytest.mark.asyncio
    async def test_promotion_applies_penalty(self, monkeypatch):
        """Promoted signals should have confidence reduced."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        articles = [_make_news_article()]

        baseline = await _collect_news_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=False,
            dns_probe_results={"Acme Inc": "acme.ai"},
        )
        assert len(baseline) >= 1
        baseline_conf = baseline[0].confidence

        promoted = await _collect_news_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=True,
            dns_probe_results={"Acme Inc": "acme.ai"},
        )
        assert len(promoted) >= 1
        sig = promoted[0]

        assert sig.raw_data.get("dns_promoted") is True
        assert sig.confidence == pytest.approx(baseline_conf - 0.03)

    @pytest.mark.asyncio
    async def test_promotion_off_no_change(self, monkeypatch):
        """With promote=false, no promotion annotations."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        articles = [_make_news_article()]
        signals = await _collect_news_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=False,
            dns_probe_results={"Acme Inc": "acme.ai"},
        )

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.raw_data.get("dns_promoted") is not True
        assert sig.raw_data.get("dns_confidence_penalty") is None

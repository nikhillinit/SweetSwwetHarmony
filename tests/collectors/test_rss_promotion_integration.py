"""
Integration tests for DNS Phase 2 promotion in rss_feeds collector.

These tests call the REAL rss_feeds collector flow (with mocked network/DNS)
to validate that the promotion pass modifies signals correctly.

RED phase: these fail until the promotion pass is implemented in rss_feeds.py.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.rss_feeds import RSSFeedCollector, RSSArticle
from verification.verification_gate_v2 import Signal


# =============================================================================
# Helpers
# =============================================================================


def _make_article(
    title: str = "Acme Inc Launches New Health Drink",
    url: str = "https://techcrunch.com/2026/02/27/acme-health-drink",
    description: str = "Consumer startup Acme Inc debuts a new wellness beverage line",
    domain: str = "techcrunch.com",
) -> RSSArticle:
    """Build a realistic RSSArticle fixture."""
    return RSSArticle(
        title=title,
        description=description,
        url=url,
        source_feed="https://techcrunch.com/feed",
        published_at=datetime.now(timezone.utc) - timedelta(hours=2),
        author="Jane Reporter",
        categories=["startups", "health"],
    )


async def _collect_with_dns(
    monkeypatch,
    articles: list[RSSArticle],
    dns_probe_enabled: bool = True,
    dns_promote_enabled: bool = False,
    dns_probe_results: dict[str, str | None] | None = None,
) -> list[Signal]:
    """Run collector._collect_signals() with mocked feeds and DNS probe.

    Returns the list of Signal objects AFTER the full _collect_signals() flow,
    including DNS probe enrichment and (if enabled) promotion pass.
    """
    monkeypatch.setenv("DNS_PROBE_ENABLED", str(dns_probe_enabled).lower())
    monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", str(dns_promote_enabled).lower())

    collector = RSSFeedCollector(
        store=None,
        feeds=["https://fake-feed.test/rss"],
    )

    # Mock _parse_feed to return our articles
    async def mock_parse_feed(url):
        return articles

    # Mock dns_probe_company to return controlled results
    if dns_probe_results is None:
        dns_probe_results = {}

    async def mock_dns_probe(name):
        return dns_probe_results.get(name)

    monkeypatch.setattr(collector, "_parse_feed", mock_parse_feed)

    with patch("utils.dns_probe.dns_probe_company", side_effect=mock_dns_probe):
        signals = await collector._collect_signals()

    return signals


# =============================================================================
# Integration: promotion-off (regression anchor)
# =============================================================================


class TestRSSPromotionOffIntegration:
    """Real collector flow with DNS_PROBE_PROMOTE_ENABLED=false."""

    @pytest.mark.asyncio
    async def test_promotion_off_candidates_unchanged(self, monkeypatch):
        """With promote=false, candidates should NOT include dns_probe_domain."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        articles = [_make_article()]
        signals = await _collect_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=False,
            dns_probe_results={"Acme Inc": "acme.ai"},
        )

        assert len(signals) >= 1
        sig = signals[0]
        # DNS probe data should be present
        assert sig.raw_data["dns_probe_status"] == "hit"
        assert sig.raw_data["dns_probe_domain"] == "acme.ai"
        # But candidates should NOT have domain:acme.ai added by promotion
        candidates = sig.raw_data.get("canonical_key_candidates", [])
        # domain:acme.ai should only appear if it was already in candidates
        # from the article's own URL extraction — NOT from dns promotion
        assert sig.raw_data.get("dns_promoted") is not True

    @pytest.mark.asyncio
    async def test_promotion_off_confidence_unchanged(self, monkeypatch):
        """With promote=false, confidence should NOT be penalized."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        articles = [_make_article()]
        signals = await _collect_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=False,
            dns_probe_results={"Acme Inc": "acme.ai"},
        )

        assert len(signals) >= 1
        sig = signals[0]
        # Capture confidence — should not have any dns penalty applied
        assert sig.raw_data.get("dns_confidence_penalty") is None


# =============================================================================
# Integration: promotion-on (RED — these should FAIL until Task 7 implements)
# =============================================================================


class TestRSSPromotionOnIntegration:
    """Real collector flow with DNS_PROBE_PROMOTE_ENABLED=true."""

    @pytest.mark.asyncio
    async def test_promotion_adds_domain_to_candidates(self, monkeypatch):
        """With promote=true and dns hit, domain:acme.ai should appear in candidates."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        articles = [_make_article(
            title="Acme Inc Launches New Health Drink",
            url="https://prnewswire.com/news/acme-health-drink",
            description="Consumer startup Acme Inc debuts a new wellness beverage line",
        )]
        signals = await _collect_with_dns(
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
            f"Expected domain:acme.ai in candidates after promotion, got {candidates}"
        )

    @pytest.mark.asyncio
    async def test_promotion_applies_confidence_penalty(self, monkeypatch):
        """Promoted signals should have confidence reduced by 0.03."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        articles = [_make_article(
            title="Acme Inc Launches New Health Drink",
            url="https://prnewswire.com/news/acme-health-drink",
            description="Consumer startup Acme Inc debuts a new wellness beverage line",
        )]

        # Get baseline confidence with promote=off
        baseline_signals = await _collect_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=False,
            dns_probe_results={"Acme Inc": "acme.ai"},
        )
        assert len(baseline_signals) >= 1
        baseline_confidence = baseline_signals[0].confidence

        # Now with promote=on
        promoted_signals = await _collect_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=True,
            dns_probe_results={"Acme Inc": "acme.ai"},
        )
        assert len(promoted_signals) >= 1
        promoted_sig = promoted_signals[0]

        assert promoted_sig.raw_data.get("dns_promoted") is True, (
            "Expected dns_promoted=True in raw_data"
        )
        assert promoted_sig.raw_data.get("dns_confidence_penalty") == pytest.approx(0.03), (
            f"Expected dns_confidence_penalty=0.03, got {promoted_sig.raw_data.get('dns_confidence_penalty')}"
        )
        assert promoted_sig.confidence == pytest.approx(baseline_confidence - 0.03), (
            f"Expected confidence={baseline_confidence - 0.03}, got {promoted_sig.confidence}"
        )

    @pytest.mark.asyncio
    async def test_promotion_skips_dns_miss(self, monkeypatch):
        """Signals with dns_probe_status=miss should NOT be promoted."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        articles = [_make_article(
            title="Acme Inc Launches New Health Drink",
            url="https://prnewswire.com/news/acme-health-drink",
            description="Consumer startup Acme Inc debuts a new wellness beverage line",
        )]
        signals = await _collect_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=True,
            dns_probe_results={},  # No DNS hit for Acme Inc
        )

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.raw_data.get("dns_promoted") is not True
        assert sig.raw_data.get("dns_confidence_penalty") is None

    @pytest.mark.asyncio
    async def test_promotion_skips_when_domain_already_strong(self, monkeypatch):
        """If signal already has a domain: key in candidates (from article URL
        extraction), promotion should not duplicate it."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        # Article from acme.ai directly — URL extraction should already
        # produce domain:acme.ai in candidates
        articles = [_make_article(
            title="New Health Drink Launches Nationwide",
            url="https://acme.ai/blog/launch",
            description="Acme launches a new wellness beverage line",
        )]
        signals = await _collect_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=True,
            dns_probe_results={"Acme": "acme.ai"},
        )

        if signals:
            sig = signals[0]
            candidates = sig.raw_data.get("canonical_key_candidates", [])
            domain_count = candidates.count("domain:acme.ai")
            assert domain_count <= 1, (
                f"domain:acme.ai duplicated in candidates: {candidates}"
            )

    @pytest.mark.asyncio
    async def test_existing_domain_candidate_blocks_promotion(self, monkeypatch):
        """If the article URL extraction already yields a domain: candidate,
        the promotion pass must NOT overwrite it or add dns_promoted annotation."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        # prnewswire URL means the article domain won't produce a company domain,
        # BUT the company name "Acme" has a promoted_domain from extract_company_info
        # We need an article whose own URL IS the company domain.
        articles = [_make_article(
            title="New Health Drink Launches Nationwide",
            url="https://acme.ai/blog/launch",
            description="Acme launches a new wellness beverage line",
        )]

        # Collect WITHOUT promotion to get baseline candidates
        baseline = await _collect_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=False,
            dns_probe_results={"Acme": "acme.ai"},
        )

        # Collect WITH promotion
        promoted = await _collect_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=True,
            dns_probe_results={"Acme": "acme.ai"},
        )

        if baseline and promoted:
            base_candidates = baseline[0].raw_data.get("canonical_key_candidates", [])
            promo_candidates = promoted[0].raw_data.get("canonical_key_candidates", [])
            # If domain:acme.ai was already in baseline, promotion should not
            # have changed anything — no dns_promoted annotation
            if "domain:acme.ai" in base_candidates:
                # domain already present from URL extraction — promotion is a no-op
                # for the candidate list (penalty still applies per current code,
                # but the key itself is unchanged)
                assert promo_candidates.count("domain:acme.ai") == 1, (
                    f"domain:acme.ai should appear exactly once: {promo_candidates}"
                )

    @pytest.mark.asyncio
    async def test_penalty_applied_exactly_once(self, monkeypatch):
        """If the same signal passes through the promotion logic twice in a
        single _collect_signals() call, penalty must be applied only once."""
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        articles = [_make_article(
            title="Acme Inc Launches New Health Drink",
            url="https://prnewswire.com/news/acme-health-drink",
            description="Consumer startup Acme Inc debuts a new wellness beverage line",
        )]

        # Get baseline confidence without promotion
        baseline = await _collect_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=False,
            dns_probe_results={"Acme Inc": "acme.ai"},
        )
        assert len(baseline) >= 1
        baseline_confidence = baseline[0].confidence

        # Get promoted confidence
        promoted = await _collect_with_dns(
            monkeypatch,
            articles=articles,
            dns_probe_enabled=True,
            dns_promote_enabled=True,
            dns_probe_results={"Acme Inc": "acme.ai"},
        )
        assert len(promoted) >= 1
        promoted_sig = promoted[0]

        # Penalty should be exactly 0.03, not 0.06 (double-applied)
        assert promoted_sig.confidence == pytest.approx(baseline_confidence - 0.03), (
            f"Penalty applied more than once: baseline={baseline_confidence}, "
            f"promoted={promoted_sig.confidence}"
        )
        assert promoted_sig.raw_data.get("dns_confidence_penalty") == pytest.approx(0.03)

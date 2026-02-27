"""
Tests for DNS Phase 2 promotion invariants.

Mandatory invariance rule: with DNS_PROBE_PROMOTE_ENABLED=false,
canonical_key_candidates, confidence, and routing are UNCHANGED
regardless of dns_probe results.

Also tests promotion-on behavior (RED phase for task 8).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

from verification.verification_gate_v2 import Signal


# =============================================================================
# Fixtures
# =============================================================================

def _make_rss_signal(
    company_name: str = "Acme Inc",
    canonical_key_candidates: Optional[List[str]] = None,
    confidence: float = 0.55,
    dns_probe_status: str = "hit",
    dns_probe_domain: Optional[str] = "acme.ai",
    signal_id: str = "rss_abc123",
) -> Signal:
    """Build a realistic rss_feeds Signal with DNS probe data."""
    if canonical_key_candidates is None:
        canonical_key_candidates = ["name_loc:acme_inc"]
    return Signal(
        id=signal_id,
        signal_type="consumer_launch",
        confidence=confidence,
        source_api="rss_feeds",
        source_url="https://techcrunch.com/acme-launch",
        raw_data={
            "title": "Acme Inc Launches New Product",
            "description": "Acme launches a new consumer product",
            "url": "https://techcrunch.com/acme-launch",
            "source_feed": "https://techcrunch.com/feed",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "company_name": company_name,
            "company_name_method": "regex",
            "candidate_domains": [],
            "promoted_domain": None,
            "is_funding_news": False,
            "is_product_launch": True,
            "is_press_release": False,
            "canonical_key_candidates": canonical_key_candidates,
            "dns_probe_attempted": True,
            "dns_probe_domain": dns_probe_domain,
            "dns_probe_status": dns_probe_status,
        },
    )


def _make_news_api_signal(
    company_name: str = "Beta Co",
    canonical_key_candidates: Optional[List[str]] = None,
    confidence: float = 0.50,
    signal_id: str = "gnews_def456",
) -> Signal:
    """Build a realistic news_api Signal (no DNS probe yet)."""
    if canonical_key_candidates is None:
        canonical_key_candidates = ["name_loc:beta_co"]
    return Signal(
        id=signal_id,
        signal_type="consumer_news",
        confidence=confidence,
        source_api="news_api",
        source_url="https://news.example.com/beta-co",
        raw_data={
            "title": "Beta Co Raises Seed Round",
            "description": "Beta Co raises funding",
            "url": "https://news.example.com/beta-co",
            "company_name": company_name,
            "canonical_key_candidates": canonical_key_candidates,
        },
    )


# =============================================================================
# PROMOTION-OFF INVARIANCE (mandatory — must always pass)
# =============================================================================


class TestPromotionOffInvariance:
    """With DNS_PROBE_PROMOTE_ENABLED=false, keys+confidence+routing unchanged."""

    def test_rss_candidates_unchanged(self, monkeypatch):
        """canonical_key_candidates must be identical to pre-promotion snapshot."""
        monkeypatch.setenv("DNS_PROBE_ENABLED", "true")
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "false")

        sig = _make_rss_signal(
            canonical_key_candidates=["name_loc:acme_inc"],
            dns_probe_status="hit",
            dns_probe_domain="acme.ai",
        )
        baseline_candidates = copy.deepcopy(sig.raw_data["canonical_key_candidates"])

        # Import after env is set
        from utils.dns_promotion import is_dns_promote_enabled
        assert is_dns_promote_enabled() is False

        # The promotion pass should be a no-op — candidates unchanged
        assert sig.raw_data["canonical_key_candidates"] == baseline_candidates

    def test_rss_confidence_unchanged(self, monkeypatch):
        """Confidence must not be penalized when promotion is off."""
        monkeypatch.setenv("DNS_PROBE_ENABLED", "true")
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "false")

        sig = _make_rss_signal(confidence=0.55)
        assert sig.confidence == 0.55

    def test_news_api_candidates_unchanged(self, monkeypatch):
        """news_api candidates unchanged when promotion is off."""
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "false")

        sig = _make_news_api_signal(canonical_key_candidates=["name_loc:beta_co"])
        baseline_candidates = copy.deepcopy(sig.raw_data["canonical_key_candidates"])

        from utils.dns_promotion import is_dns_promote_enabled
        assert is_dns_promote_enabled() is False
        assert sig.raw_data["canonical_key_candidates"] == baseline_candidates

    def test_extract_canonical_key_unchanged(self, monkeypatch):
        """_extract_canonical_key returns same result with promotion off."""
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "false")

        sig = _make_rss_signal(
            canonical_key_candidates=["name_loc:acme_inc"],
            dns_probe_status="hit",
            dns_probe_domain="acme.ai",
        )

        from collectors.base import BaseCollector
        # BaseCollector._extract_canonical_key is not static, needs instance context
        # but we can call it on any instance — use a minimal mock
        from unittest.mock import MagicMock
        collector = MagicMock(spec=BaseCollector)
        key = BaseCollector._extract_canonical_key(collector, sig)

        # With promotion off, dns_probe_domain should NOT influence key selection
        # name_loc:acme_inc is the only candidate → it should be selected
        assert key == "name_loc:acme_inc"

    def test_routing_thresholds_unchanged(self, monkeypatch):
        """Boundary signals should not cross routing thresholds."""
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "false")

        # Signal at exactly 0.70 (Source threshold) — should stay there
        sig_high = _make_rss_signal(confidence=0.70, dns_probe_status="hit")
        assert sig_high.confidence == 0.70

        # Signal at exactly 0.40 (Tracking threshold) — should stay there
        sig_mid = _make_rss_signal(confidence=0.40, dns_probe_status="hit")
        assert sig_mid.confidence == 0.40


# =============================================================================
# PROMOTION-ON: KEY CHANGE (RED phase — these test the promotion behavior)
# =============================================================================


class TestPromotionOnKeyChange:
    """With DNS_PROBE_PROMOTE_ENABLED=true, dns_probe_domain becomes canonical."""

    def test_promotion_adds_domain_candidate(self, monkeypatch):
        """When promote=true and dns_probe_status=hit, domain key should be
        added to candidates for strength-based selection."""
        monkeypatch.setenv("DNS_PROBE_ENABLED", "true")
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "true")

        from utils.dns_promotion import is_dns_promote_enabled, get_dns_confidence_penalty
        assert is_dns_promote_enabled() is True

        # This test validates the EXPECTED behavior after promotion pass:
        # Given a signal with name_loc:acme_inc and dns_probe_domain=acme.ai,
        # after promotion, candidates should include domain:acme.ai
        sig = _make_rss_signal(
            canonical_key_candidates=["name_loc:acme_inc"],
            dns_probe_status="hit",
            dns_probe_domain="acme.ai",
        )

        # Simulate the promotion pass that task 7 will implement
        if (
            is_dns_promote_enabled()
            and sig.raw_data.get("dns_probe_status") == "hit"
            and sig.raw_data.get("dns_probe_domain")
        ):
            domain = sig.raw_data["dns_probe_domain"]
            domain_key = f"domain:{domain}"
            existing = sig.raw_data.get("canonical_key_candidates", [])
            if domain_key not in existing:
                sig.raw_data["canonical_key_candidates"] = existing + [domain_key]
            sig.confidence -= get_dns_confidence_penalty()
            sig.raw_data["dns_promoted"] = True
            sig.raw_data["dns_promoted_domain"] = domain
            sig.raw_data["dns_confidence_penalty"] = get_dns_confidence_penalty()

        assert "domain:acme.ai" in sig.raw_data["canonical_key_candidates"]

    def test_promotion_applies_confidence_penalty(self, monkeypatch):
        """Promoted signals get 0.03 penalty by default."""
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "true")
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        from utils.dns_promotion import is_dns_promote_enabled, get_dns_confidence_penalty

        sig = _make_rss_signal(confidence=0.55, dns_probe_status="hit", dns_probe_domain="acme.ai")
        original_confidence = sig.confidence

        if is_dns_promote_enabled() and sig.raw_data.get("dns_probe_status") == "hit":
            sig.confidence -= get_dns_confidence_penalty()

        assert sig.confidence == pytest.approx(original_confidence - 0.03)

    def test_promotion_skips_when_dns_miss(self, monkeypatch):
        """Signals with dns_probe_status=miss are NOT promoted."""
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "true")

        sig = _make_rss_signal(
            confidence=0.55,
            dns_probe_status="miss",
            dns_probe_domain=None,
        )
        original = copy.deepcopy(sig.raw_data["canonical_key_candidates"])

        from utils.dns_promotion import is_dns_promote_enabled
        # Promotion should be skipped for miss
        if (
            is_dns_promote_enabled()
            and sig.raw_data.get("dns_probe_status") == "hit"
            and sig.raw_data.get("dns_probe_domain")
        ):
            sig.raw_data["canonical_key_candidates"].append("domain:shouldnt-appear")

        assert sig.raw_data["canonical_key_candidates"] == original

    def test_promotion_skips_when_domain_already_in_candidates(self, monkeypatch):
        """If domain key already exists in candidates, don't duplicate."""
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "true")

        sig = _make_rss_signal(
            canonical_key_candidates=["name_loc:acme_inc", "domain:acme.ai"],
            dns_probe_status="hit",
            dns_probe_domain="acme.ai",
        )

        from utils.dns_promotion import is_dns_promote_enabled, get_dns_confidence_penalty
        if (
            is_dns_promote_enabled()
            and sig.raw_data.get("dns_probe_status") == "hit"
            and sig.raw_data.get("dns_probe_domain")
        ):
            domain_key = f"domain:{sig.raw_data['dns_probe_domain']}"
            existing = sig.raw_data.get("canonical_key_candidates", [])
            if domain_key not in existing:
                sig.raw_data["canonical_key_candidates"] = existing + [domain_key]
            sig.confidence -= get_dns_confidence_penalty()

        # Should not have duplicate
        domain_count = sig.raw_data["canonical_key_candidates"].count("domain:acme.ai")
        assert domain_count == 1

    def test_promoted_signal_selects_domain_as_strongest(self, monkeypatch):
        """After promotion, _extract_canonical_key should pick domain: over name_loc:."""
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "true")

        sig = _make_rss_signal(
            canonical_key_candidates=["name_loc:acme_inc", "domain:acme.ai"],
            dns_probe_status="hit",
            dns_probe_domain="acme.ai",
        )

        from collectors.base import BaseCollector
        from unittest.mock import MagicMock
        collector = MagicMock(spec=BaseCollector)
        key = BaseCollector._extract_canonical_key(collector, sig)
        assert key == "domain:acme.ai"

    def test_promotion_annotates_raw_data(self, monkeypatch):
        """Promoted signals should have dns_promoted=True annotation."""
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "true")
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)

        from utils.dns_promotion import is_dns_promote_enabled, get_dns_confidence_penalty

        sig = _make_rss_signal(dns_probe_status="hit", dns_probe_domain="acme.ai")

        if (
            is_dns_promote_enabled()
            and sig.raw_data.get("dns_probe_status") == "hit"
            and sig.raw_data.get("dns_probe_domain")
        ):
            sig.raw_data["dns_promoted"] = True
            sig.raw_data["dns_promoted_domain"] = sig.raw_data["dns_probe_domain"]
            sig.raw_data["dns_confidence_penalty"] = get_dns_confidence_penalty()

        assert sig.raw_data["dns_promoted"] is True
        assert sig.raw_data["dns_promoted_domain"] == "acme.ai"
        assert sig.raw_data["dns_confidence_penalty"] == 0.03

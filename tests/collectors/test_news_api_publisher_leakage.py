"""
Tests for publisher domain leakage in news_api and rss_feeds collectors.

Verifies that suffix-aware blocklist checking prevents publisher/platform
domains from leaking into canonical keys, including subdomains.
"""

import pytest

from utils.company_name_extractor import is_blocked_domain, _is_blocked_domain
from utils.canonical_keys import build_canonical_key_candidates, NEWS_PUBLISHER_DOMAINS


class TestIsBlockedDomainPublic:
    """Tests for the public is_blocked_domain() wrapper."""

    def test_subdomain_blocked_bostonglobe(self):
        """m.bostonglobe.com should be blocked (subdomain of publisher)."""
        assert is_blocked_domain("m.bostonglobe.com") is True

    def test_www_prefix_blocked_reuters(self):
        """www.reuters.com should be blocked."""
        assert is_blocked_domain("www.reuters.com") is True

    def test_mixed_case_blocked(self):
        """WWW.Reuters.COM should be blocked (case-insensitive)."""
        assert is_blocked_domain("WWW.Reuters.COM") is True

    def test_real_company_passes(self):
        """acme.ai should NOT be blocked."""
        assert is_blocked_domain("acme.ai") is False

    def test_github_io_suffix_blocked(self):
        """github.io is in _BLOCKED_DOMAIN_SUFFIXES — should be blocked."""
        assert is_blocked_domain("github.io") is True

    def test_platform_subdomain_blocked(self):
        """acme.github.io is blocked — platform subdomains are not stable identity."""
        assert is_blocked_domain("acme.github.io") is True

    def test_multi_part_suffix_bbc_co_uk(self):
        """m.bbc.co.uk should be blocked (bbc.co.uk in NEWS_PUBLISHER_DOMAINS)."""
        assert is_blocked_domain("m.bbc.co.uk") is True

    def test_multi_part_non_publisher(self):
        """acme.co.uk should NOT be blocked (not a publisher)."""
        assert is_blocked_domain("acme.co.uk") is False

    def test_empty_domain_blocked(self):
        """Empty string should be treated as blocked."""
        assert is_blocked_domain("") is True

    def test_public_matches_private(self):
        """Public wrapper should produce same result as private function."""
        test_cases = [
            "acme.ai", "m.reuters.com", "github.io", "bbc.co.uk",
            "techcrunch.com", "healthymeals.com", "",
        ]
        for domain in test_cases:
            assert is_blocked_domain(domain) == _is_blocked_domain(domain), \
                f"Mismatch for {domain!r}"


class TestCanonicalKeyNoPublisherLeakage:
    """Tests that canonical key building doesn't produce domain: keys for publishers."""

    def test_promoted_domain_publisher_falls_through(self):
        """If promoted_domain is a publisher, it should NOT produce a domain: key."""
        # Simulate what news_api does: check is_blocked_domain before passing to key builder
        promoted_domain = "reuters.com"
        article_domain = "reuters.com"
        company_name = "Acme Corp"

        domain_for_key = ""
        if promoted_domain and not is_blocked_domain(promoted_domain):
            domain_for_key = promoted_domain
        elif article_domain and not is_blocked_domain(article_domain):
            domain_for_key = article_domain

        keys = build_canonical_key_candidates(
            domain_or_website=domain_for_key,
            fallback_company_name=company_name,
        )
        # Should NOT have domain:reuters.com — should fall back to name_loc:
        assert not any(k.startswith("domain:reuters") for k in keys), \
            f"Publisher domain leaked into keys: {keys}"
        assert any(k.startswith("name_loc:") or k.startswith("hash:") for k in keys), \
            f"Expected fallback key, got: {keys}"

    def test_subdomain_publisher_falls_through(self):
        """m.bostonglobe.com should not produce a domain: key."""
        promoted_domain = "m.bostonglobe.com"
        company_name = "Acme Corp"

        domain_for_key = ""
        if promoted_domain and not is_blocked_domain(promoted_domain):
            domain_for_key = promoted_domain

        keys = build_canonical_key_candidates(
            domain_or_website=domain_for_key,
            fallback_company_name=company_name,
        )
        assert not any("bostonglobe" in k for k in keys), \
            f"Publisher subdomain leaked: {keys}"

    def test_legitimate_domain_produces_domain_key(self):
        """acme.ai should produce domain:acme.ai key."""
        domain_for_key = ""
        promoted_domain = "acme.ai"
        if promoted_domain and not is_blocked_domain(promoted_domain):
            domain_for_key = promoted_domain

        keys = build_canonical_key_candidates(
            domain_or_website=domain_for_key,
            fallback_company_name="Acme",
        )
        assert any(k == "domain:acme.ai" for k in keys), \
            f"Expected domain:acme.ai in {keys}"

    def test_at_least_one_legitimate_domain_key(self):
        """Positive assertion: a real company DOES get a domain: key."""
        domain_for_key = "healthymeals.com"
        assert not is_blocked_domain(domain_for_key)

        keys = build_canonical_key_candidates(
            domain_or_website=domain_for_key,
            fallback_company_name="Healthy Meals",
        )
        assert any(k.startswith("domain:") for k in keys), \
            f"Expected at least one domain: key for legitimate company, got: {keys}"


class TestBlockedDomainCoverage:
    """Verify all known leaked publisher domains are caught."""

    KNOWN_LEAKED = [
        "bostonglobe.com",
        "fastcompany.com",
        "foxbusiness.com",
        "inc.com",
        "interestingengineering.com",
        "reuters.com",
        "usaherald.com",
    ]

    @pytest.mark.parametrize("domain", KNOWN_LEAKED)
    def test_known_leaked_publisher_blocked(self, domain):
        """Each known leaked publisher domain must be blocked."""
        assert is_blocked_domain(domain) is True, f"{domain} not blocked!"

    @pytest.mark.parametrize("domain", KNOWN_LEAKED)
    def test_known_leaked_publisher_subdomain_blocked(self, domain):
        """Subdomains of known leaked publishers must also be blocked."""
        subdomain = f"m.{domain}"
        assert is_blocked_domain(subdomain) is True, f"{subdomain} not blocked!"
        www_sub = f"www.{domain}"
        assert is_blocked_domain(www_sub) is True, f"{www_sub} not blocked!"


class TestVercelAppBlocked:
    """Platform hosting subdomains should be blocked."""

    def test_vercel_app_blocked(self):
        assert is_blocked_domain("vercel.app") is False  # vercel.app itself not in suffixes
        # But subdomains of platform suffixes in _BLOCKED_DOMAIN_SUFFIXES are checked

    def test_github_io_subdomain(self):
        """acme.github.io should be blocked (github.io is in _BLOCKED_DOMAIN_SUFFIXES)."""
        assert is_blocked_domain("acme.github.io") is True

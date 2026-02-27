"""
PR10a: Collector key hygiene — canonical_key_candidates emission

Tests verify that collectors emit canonical_key_candidates in raw_data
when both domain and source-specific identifiers are available.
This enables select_strongest_candidate() (PR9) to pick the best key.
"""

import pytest
from datetime import datetime, timezone


# =============================================================================
# TEST: CrunchbaseCompany candidates
# =============================================================================

class TestCrunchbaseKeyCandidates:
    """CrunchbaseCompany should emit canonical_key_candidates."""

    def _make_company(self, **overrides):
        from collectors.crunchbase import CrunchbaseCompany
        defaults = dict(
            uuid="test-uuid-123",
            name="Acme Inc",
            permalink="acme-inc",
            website_url="https://www.acme.ai/product",
        )
        defaults.update(overrides)
        return CrunchbaseCompany(**defaults)

    def test_candidates_with_domain_and_permalink(self):
        """When domain is extractable, candidates should include both domain and crunchbase keys."""
        company = self._make_company(website_url="https://www.acme.ai")
        signal = company.to_signal()
        rd = signal.raw_data

        assert "canonical_key_candidates" in rd
        candidates = rd["canonical_key_candidates"]
        assert "domain:acme.ai" in candidates
        assert "crunchbase:acme-inc" in candidates

    def test_canonical_key_is_domain_when_available(self):
        """canonical_key should still be domain when domain exists."""
        company = self._make_company(website_url="https://acme.ai")
        signal = company.to_signal()
        assert signal.raw_data["canonical_key"] == "domain:acme.ai"

    def test_candidates_without_domain(self):
        """When no domain, candidates should include crunchbase key."""
        company = self._make_company(website_url="")
        signal = company.to_signal()
        rd = signal.raw_data

        assert "canonical_key_candidates" in rd
        candidates = rd["canonical_key_candidates"]
        assert "crunchbase:acme-inc" in candidates
        assert not any(c.startswith("domain:") for c in candidates)

    def test_candidates_order_domain_first(self):
        """Domain should appear before crunchbase in candidates."""
        company = self._make_company(website_url="https://acme.ai")
        signal = company.to_signal()
        candidates = signal.raw_data["canonical_key_candidates"]
        dom_idx = candidates.index("domain:acme.ai")
        cb_idx = candidates.index("crunchbase:acme-inc")
        assert dom_idx < cb_idx


# =============================================================================
# TEST: GitHubActivitySignal candidates
# =============================================================================

class TestGitHubActivityKeyCandidates:
    """GitHubActivitySignal should emit canonical_key_candidates."""

    def _make_signal(self, **overrides):
        from collectors.github_activity import GitHubActivitySignal
        defaults = dict(
            username="jdoe",
            signal_type="new_repo",
            website_url="https://acme.ai",
        )
        defaults.update(overrides)
        return GitHubActivitySignal(**defaults)

    def test_candidates_with_domain_and_username(self):
        """When domain is extractable, candidates should include both domain and github_user."""
        sig = self._make_signal(website_url="https://acme.ai")
        signal = sig.to_signal()
        rd = signal.raw_data

        assert "canonical_key_candidates" in rd
        candidates = rd["canonical_key_candidates"]
        assert "domain:acme.ai" in candidates
        assert "github_user:jdoe" in candidates

    def test_canonical_key_is_domain_when_available(self):
        """canonical_key should still be domain when domain exists."""
        sig = self._make_signal(website_url="https://acme.ai")
        signal = sig.to_signal()
        assert signal.raw_data["canonical_key"] == "domain:acme.ai"

    def test_candidates_without_domain(self):
        """When no website, candidates should include github_user key."""
        sig = self._make_signal(website_url=None)
        signal = sig.to_signal()
        rd = signal.raw_data

        assert "canonical_key_candidates" in rd
        candidates = rd["canonical_key_candidates"]
        assert "github_user:jdoe" in candidates
        assert not any(c.startswith("domain:") for c in candidates)

    def test_candidates_order_domain_first(self):
        """Domain should appear before github_user in candidates."""
        sig = self._make_signal(website_url="https://acme.ai")
        signal = sig.to_signal()
        candidates = signal.raw_data["canonical_key_candidates"]
        dom_idx = candidates.index("domain:acme.ai")
        gh_idx = candidates.index("github_user:jdoe")
        assert dom_idx < gh_idx


# =============================================================================
# TEST: HackerNewsPost candidates
# =============================================================================

class TestHackerNewsKeyCandidates:
    """HackerNewsPost should emit canonical_key_candidates."""

    def _make_post(self, **overrides):
        from collectors.hacker_news import HackerNewsPost
        defaults = dict(
            object_id="12345",
            title="Show HN: Acme AI - A new tool",
            url="https://acme.ai/launch",
            author="founder",
            points=50,
            num_comments=10,
            created_at=datetime.now(timezone.utc),
        )
        defaults.update(overrides)
        return HackerNewsPost(**defaults)

    def test_candidates_with_domain_and_hn_id(self):
        """When URL has domain, candidates should include both domain and hacker_news."""
        post = self._make_post(url="https://acme.ai/launch")
        signal = post.to_signal()
        rd = signal.raw_data

        assert "canonical_key_candidates" in rd
        candidates = rd["canonical_key_candidates"]
        assert "domain:acme.ai" in candidates
        assert "hacker_news:12345" in candidates

    def test_canonical_key_is_domain_when_available(self):
        """canonical_key should still be domain when domain exists."""
        post = self._make_post(url="https://acme.ai")
        signal = post.to_signal()
        assert signal.raw_data["canonical_key"] == "domain:acme.ai"

    def test_candidates_without_domain(self):
        """When URL is empty, candidates should include hacker_news key."""
        post = self._make_post(url="")
        signal = post.to_signal()
        rd = signal.raw_data

        assert "canonical_key_candidates" in rd
        candidates = rd["canonical_key_candidates"]
        assert "hacker_news:12345" in candidates
        assert not any(c.startswith("domain:") for c in candidates)

    def test_candidates_order_domain_first(self):
        """Domain should appear before hacker_news in candidates."""
        post = self._make_post(url="https://acme.ai")
        signal = post.to_signal()
        candidates = signal.raw_data["canonical_key_candidates"]
        dom_idx = candidates.index("domain:acme.ai")
        hn_idx = candidates.index("hacker_news:12345")
        assert dom_idx < hn_idx


# =============================================================================
# TEST: LinkedInCompany candidates
# =============================================================================

class TestLinkedInKeyCandidates:
    """LinkedInCompany should emit canonical_key_candidates."""

    def _make_company(self, **overrides):
        from collectors.linkedin import LinkedInCompany
        defaults = dict(
            linkedin_url="https://www.linkedin.com/company/acme-inc",
            name="Acme Inc",
            website="https://www.acme.ai",
        )
        defaults.update(overrides)
        return LinkedInCompany(**defaults)

    def test_candidates_with_domain_and_linkedin(self):
        """When website has domain, candidates should include both domain and linkedin."""
        company = self._make_company()
        signal = company.to_signal()
        rd = signal.raw_data

        assert "canonical_key_candidates" in rd
        candidates = rd["canonical_key_candidates"]
        assert "domain:acme.ai" in candidates
        assert "linkedin:acme-inc" in candidates

    def test_canonical_key_is_domain_when_available(self):
        """canonical_key should still be domain when domain exists."""
        company = self._make_company(website="https://acme.ai")
        signal = company.to_signal()
        assert signal.raw_data["canonical_key"] == "domain:acme.ai"

    def test_candidates_without_domain(self):
        """When no website, candidates should include linkedin key."""
        company = self._make_company(website="")
        signal = company.to_signal()
        rd = signal.raw_data

        assert "canonical_key_candidates" in rd
        candidates = rd["canonical_key_candidates"]
        assert "linkedin:acme-inc" in candidates
        assert not any(c.startswith("domain:") for c in candidates)

    def test_candidates_order_domain_first(self):
        """Domain should appear before linkedin in candidates."""
        company = self._make_company()
        signal = company.to_signal()
        candidates = signal.raw_data["canonical_key_candidates"]
        dom_idx = candidates.index("domain:acme.ai")
        li_idx = candidates.index("linkedin:acme-inc")
        assert dom_idx < li_idx


# =============================================================================
# TEST: ProductHuntLaunch candidates
# =============================================================================

class TestProductHuntKeyCandidates:
    """ProductHuntLaunch should emit canonical_key_candidates."""

    def _make_launch(self, **overrides):
        from collectors.product_hunt import ProductHuntLaunch
        defaults = dict(
            product_id="ph-123",
            name="Acme AI",
            tagline="AI for everyone",
            description="A tool for everything",
            url="https://www.producthunt.com/posts/acme-ai",
            website="https://acme.ai",
            votes_count=100,
            comments_count=20,
            launched_at=datetime.now(timezone.utc),
        )
        defaults.update(overrides)
        return ProductHuntLaunch(**defaults)

    def test_candidates_with_domain_and_ph_id(self):
        """When website has domain, candidates should include both domain and product_hunt."""
        launch = self._make_launch()
        signal = launch.to_signal()
        rd = signal.raw_data

        assert "canonical_key_candidates" in rd
        candidates = rd["canonical_key_candidates"]
        assert "domain:acme.ai" in candidates
        assert "product_hunt:ph-123" in candidates

    def test_canonical_key_is_domain_when_available(self):
        """canonical_key should still be domain when domain exists."""
        launch = self._make_launch(website="https://acme.ai")
        signal = launch.to_signal()
        assert signal.raw_data["canonical_key"] == "domain:acme.ai"

    def test_candidates_without_domain(self):
        """When no website, candidates should include product_hunt key."""
        launch = self._make_launch(website="")
        signal = launch.to_signal()
        rd = signal.raw_data

        assert "canonical_key_candidates" in rd
        candidates = rd["canonical_key_candidates"]
        assert "product_hunt:ph-123" in candidates
        assert not any(c.startswith("domain:") for c in candidates)

    def test_candidates_order_domain_first(self):
        """Domain should appear before product_hunt in candidates."""
        launch = self._make_launch()
        signal = launch.to_signal()
        candidates = signal.raw_data["canonical_key_candidates"]
        dom_idx = candidates.index("domain:acme.ai")
        ph_idx = candidates.index("product_hunt:ph-123")
        assert dom_idx < ph_idx

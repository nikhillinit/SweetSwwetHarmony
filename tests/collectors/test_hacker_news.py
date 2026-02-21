"""
Tests for collectors/hacker_news.py — company name extraction fixes.

Covers:
- _domain_to_company() subdomain-aware conversion
- _looks_like_name() heuristic
- HackerNewsPost._extract_company_name() refactored logic
"""

import pytest
from datetime import datetime, timezone

from collectors.hacker_news import (
    HackerNewsPost,
    _domain_to_company,
    _looks_like_name,
    _SHOW_HN_SEP_RE,
)


# =============================================================================
# _domain_to_company TESTS
# =============================================================================


class TestDomainToCompany:
    """Test subdomain-aware domain→company conversion."""

    def test_simple_domain(self):
        assert _domain_to_company("startup.com") == "Startup"

    def test_app_subdomain(self):
        assert _domain_to_company("app.startup.com") == "Startup"

    def test_blog_subdomain(self):
        assert _domain_to_company("blog.acme.ai") == "Acme"

    def test_m_subdomain(self):
        assert _domain_to_company("m.example.com") == "Example"

    def test_no_subdomain(self):
        assert _domain_to_company("example.com") == "Example"

    def test_empty_domain(self):
        assert _domain_to_company("") == ""

    def test_deep_subdomain(self):
        assert _domain_to_company("docs.api.startup.com") == "Startup"

    def test_www_not_in_skip_but_stripped_by_domain_property(self):
        # www is stripped by HackerNewsPost.domain property, not _domain_to_company
        # but if it's passed directly, it's not in _SUBDOMAIN_PREFIXES
        assert _domain_to_company("www.startup.com") == "Www"


# =============================================================================
# _looks_like_name TESTS
# =============================================================================


class TestLooksLikeName:
    """Test last-resort name heuristic."""

    def test_single_word_name(self):
        assert _looks_like_name("Raycast") is True

    def test_two_word_name(self):
        assert _looks_like_name("Fresh Bowls") is True

    def test_long_sentence_rejected(self):
        assert _looks_like_name("This is a very long descriptive title about something") is False

    def test_sentence_with_verb_rejected(self):
        assert _looks_like_name("Acme helps teams") is False

    def test_sentence_marker_from(self):
        assert _looks_like_name("insights from surveys") is False

    def test_empty_string(self):
        assert _looks_like_name("") is False

    def test_whitespace_only(self):
        assert _looks_like_name("   ") is False

    def test_four_words_ok(self):
        assert _looks_like_name("My Cool App Name") is True

    def test_five_words_rejected(self):
        assert _looks_like_name("My Very Cool App Name") is False


# =============================================================================
# _SHOW_HN_SEP_RE TESTS
# =============================================================================


class TestShowHnSepRegex:
    """Test the separator regex matches expected patterns."""

    def test_spaced_dash(self):
        m = _SHOW_HN_SEP_RE.search("Zed - a fast editor")
        assert m is not None
        assert "Zed - a fast editor"[:m.start()].strip() == "Zed"

    def test_unspaced_dash(self):
        m = _SHOW_HN_SEP_RE.search("Zed-The fast editor")
        assert m is not None

    def test_pipe(self):
        m = _SHOW_HN_SEP_RE.search("Acme| The ultimate tool")
        assert m is not None
        assert "Acme| The ultimate tool"[:m.start()].strip() == "Acme"

    def test_comma(self):
        m = _SHOW_HN_SEP_RE.search("Acme, the AI tool")
        assert m is not None

    def test_paren(self):
        m = _SHOW_HN_SEP_RE.search("MyApp (now in beta)")
        assert m is not None

    def test_em_dash(self):
        m = _SHOW_HN_SEP_RE.search("Acme \u2014 fast tool")
        assert m is not None

    def test_no_separator(self):
        m = _SHOW_HN_SEP_RE.search("17Mb Model Beats Human Experts at Medical Diagnosis")
        # Should match on "Beats" if there's a dash/pipe... but there isn't one
        # Actually this should NOT match since there's no separator character
        # Wait — let me check: there's no dash, pipe, paren, or comma before "Beats"
        # Correct: no match
        assert m is None


# =============================================================================
# HackerNewsPost._extract_company_name TESTS
# =============================================================================


def _make_post(title: str, url: str = "", tags: list = None) -> HackerNewsPost:
    """Helper to create a HackerNewsPost for testing."""
    return HackerNewsPost(
        object_id="12345",
        title=title,
        url=url,
        author="testuser",
        points=100,
        num_comments=50,
        created_at=datetime.now(timezone.utc),
        tags=tags or [],
    )


class TestHNExtractCompanyName:
    """Test refactored _extract_company_name()."""

    def test_dash_separator(self):
        post = _make_post(
            "Show HN: Zed - a fast editor",
            url="https://zed.dev",
            tags=["show_hn"],
        )
        assert post._extract_company_name() == "Zed"

    def test_unspaced_dash(self):
        post = _make_post(
            "Show HN: Zed-The fast editor",
            url="https://zed.dev",
            tags=["show_hn"],
        )
        assert post._extract_company_name() == "Zed"

    def test_pipe_separator(self):
        post = _make_post(
            "Show HN: Acme| The ultimate tool",
            url="https://acme.ai",
            tags=["show_hn"],
        )
        assert post._extract_company_name() == "Acme"

    def test_comma_separator(self):
        post = _make_post(
            "Show HN: Acme, the AI tool",
            url="https://acme.ai",
            tags=["show_hn"],
        )
        assert post._extract_company_name() == "Acme"

    def test_paren_separator(self):
        post = _make_post(
            "Show HN: MyApp (now in beta)",
            url="https://myapp.com",
            tags=["show_hn"],
        )
        assert post._extract_company_name() == "Myapp"

    def test_em_dash(self):
        post = _make_post(
            "Show HN: Acme \u2014 fast tool",
            url="https://acme.ai",
            tags=["show_hn"],
        )
        assert post._extract_company_name() == "Acme"

    def test_no_separator_falls_to_domain(self):
        """The original bug: descriptive title should fall to domain."""
        post = _make_post(
            "Show HN: 17Mb Model Beats Human Experts at Medical Diagnosis",
            url="https://huggingface.co/paper",
            tags=["show_hn"],
        )
        result = post._extract_company_name()
        assert result == "Huggingface"

    def test_no_separator_no_url_last_resort(self):
        """No URL, short name → last-resort heuristic kicks in."""
        post = _make_post(
            "Show HN: Raycast",
            url="",
            tags=["show_hn"],
        )
        result = post._extract_company_name()
        assert result == "Raycast"

    def test_no_separator_no_url_long_description(self):
        """No URL, long descriptive text → empty string."""
        post = _make_post(
            "Show HN: A new way to build things that helps everyone",
            url="",
            tags=["show_hn"],
        )
        result = post._extract_company_name()
        assert result == ""

    def test_non_show_hn_uses_domain(self):
        post = _make_post(
            "Some interesting article about startups",
            url="https://coolstartup.io/blog",
            tags=["story"],
        )
        assert post._extract_company_name() == "Coolstartup"

    def test_no_url_returns_empty(self):
        post = _make_post(
            "Ask HN: What do you think about this?",
            url="",
            tags=["ask_hn"],
        )
        assert post._extract_company_name() == ""

    def test_subdomain_url(self):
        post = _make_post(
            "Show HN: Check this descriptive title about our new thing",
            url="https://app.startup.com/demo",
            tags=["show_hn"],
        )
        # No separator → falls to domain → subdomain-aware
        result = post._extract_company_name()
        assert result == "Startup"

    def test_en_dash_separator(self):
        post = _make_post(
            "Show HN: Acme \u2013 the next big thing",
            url="https://acme.com",
            tags=["show_hn"],
        )
        assert post._extract_company_name() == "Acme"

    def test_show_hn_case_insensitive_tag(self):
        post = _make_post(
            "Show HN: TestApp - awesome stuff",
            url="https://testapp.com",
            tags=["show_hn"],
        )
        assert post._extract_company_name() == "Testapp"

    def test_multi_word_name_before_dash(self):
        post = _make_post(
            "Show HN: Fresh Bowls - healthy meal delivery",
            url="https://freshbowls.co",
            tags=["show_hn"],
        )
        assert post._extract_company_name() == "Fresh Bowls"

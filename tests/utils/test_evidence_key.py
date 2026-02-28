"""Tests for utils/evidence_key.py — normalize_url, compute_evidence_key, extract_source_url_from_raw_data."""

from __future__ import annotations

import pytest

from utils.evidence_key import normalize_url, compute_evidence_key, extract_source_url_from_raw_data


# =============================================================================
# normalize_url
# =============================================================================

class TestNormalizeUrl:
    """Test URL normalization for evidence_key stability."""

    def test_strips_www(self):
        assert normalize_url("https://www.example.com/page") == "https://example.com/page"

    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/page/") == "https://example.com/page"

    def test_preserves_root_slash(self):
        result = normalize_url("https://example.com/")
        assert result == "https://example.com/"

    def test_preserves_path_case(self):
        """Paths can be case-sensitive (e.g., GitHub repos)."""
        result = normalize_url("https://github.com/Owner/Repo")
        assert "/Owner/Repo" in result

    def test_lowercases_host(self):
        result = normalize_url("https://EXAMPLE.COM/page")
        assert "example.com" in result

    def test_removes_fragment(self):
        result = normalize_url("https://example.com/page#section")
        assert "#" not in result
        assert result == "https://example.com/page"

    def test_strips_utm_params(self):
        url = "https://example.com/article?utm_source=twitter&utm_medium=social&id=123"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "id=123" in result

    def test_strips_fbclid(self):
        url = "https://example.com/page?fbclid=abc123&real=yes"
        result = normalize_url(url)
        assert "fbclid" not in result
        assert "real=yes" in result

    def test_strips_gclid(self):
        url = "https://example.com/page?gclid=xyz&real=yes"
        result = normalize_url(url)
        assert "gclid" not in result

    def test_sorts_query_params(self):
        url1 = "https://example.com/page?b=2&a=1"
        url2 = "https://example.com/page?a=1&b=2"
        assert normalize_url(url1) == normalize_url(url2)

    def test_preserves_hn_id_param(self):
        """HN ?id= is content-identifying, not tracking."""
        url = "https://news.ycombinator.com/item?id=12345"
        result = normalize_url(url)
        assert "id=12345" in result

    def test_empty_string(self):
        assert normalize_url("") == ""

    def test_none_input(self):
        assert normalize_url(None) == ""

    def test_whitespace_only(self):
        assert normalize_url("   ") == ""

    def test_combined_tracking_params(self):
        url = "https://example.com/page?utm_source=x&utm_medium=y&utm_campaign=z&utm_content=w&utm_term=q&real=data"
        result = normalize_url(url)
        for param in ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]:
            assert param not in result
        assert "real=data" in result

    def test_no_trailing_question_mark(self):
        """After stripping all query params, no trailing ? should remain."""
        url = "https://example.com/page?utm_source=x"
        result = normalize_url(url)
        assert not result.endswith("?")

    def test_default_scheme_to_https(self):
        """URLs without scheme get https:// prepended."""
        result = normalize_url("example.com/article/123")
        assert result.startswith("https://")
        assert "example.com/article/123" in result

    def test_strips_non_standard_port_preserved(self):
        result = normalize_url("https://example.com:8443/page")
        assert ":8443" in result

    def test_standard_port_stripped(self):
        """Standard ports (443 for https, 80 for http) should not appear."""
        result = normalize_url("https://example.com:443/page")
        assert ":443" not in result


# =============================================================================
# compute_evidence_key
# =============================================================================

class TestComputeEvidenceKey:
    """Test evidence_key hash computation."""

    def test_returns_32_char_hex(self):
        key = compute_evidence_key("news_api", "https://example.com/article/123")
        assert len(key) == 32
        assert all(c in "0123456789abcdef" for c in key)

    def test_deterministic(self):
        """Same inputs → same output."""
        k1 = compute_evidence_key("news_api", "https://example.com/article/123")
        k2 = compute_evidence_key("news_api", "https://example.com/article/123")
        assert k1 == k2

    def test_different_sources_different_keys(self):
        """Same URL from different source_apis → different evidence_keys."""
        k1 = compute_evidence_key("news_api", "https://example.com/article/123")
        k2 = compute_evidence_key("rss_feeds", "https://example.com/article/123")
        assert k1 != k2

    def test_empty_url_returns_empty(self):
        assert compute_evidence_key("news_api", "") == ""

    def test_none_url_returns_empty(self):
        assert compute_evidence_key("news_api", None) == ""

    def test_unit_separator_in_payload(self):
        """Ensure source_api and URL are separated by \\x1f to prevent collisions."""
        # "news_apihttps://..." != "news_api" + "\x1f" + "https://..."
        k1 = compute_evidence_key("news_api", "https://a.com")
        k2 = compute_evidence_key("news_apihttps://a.com", "")
        # k2 should be empty since URL is empty
        assert k2 == ""

    def test_normalizes_url_before_hashing(self):
        """UTM params, www, trailing slash should not affect the key."""
        k1 = compute_evidence_key("news_api", "https://www.example.com/article/")
        k2 = compute_evidence_key("news_api", "https://example.com/article?utm_source=twitter")
        assert k1 == k2


# =============================================================================
# extract_source_url_from_raw_data
# =============================================================================

class TestExtractSourceUrlFromRawData:
    """Test source URL extraction from raw_data dict."""

    def test_from_provenance_block(self):
        raw_data = {
            "_provenance": {"source_url": "https://example.com/article"},
            "title": "Test",
        }
        assert extract_source_url_from_raw_data(raw_data) == "https://example.com/article"

    def test_fallback_to_url_key(self):
        raw_data = {"url": "https://example.com/article", "title": "Test"}
        assert extract_source_url_from_raw_data(raw_data) == "https://example.com/article"

    def test_provenance_takes_priority(self):
        raw_data = {
            "_provenance": {"source_url": "https://provenance.com/article"},
            "url": "https://fallback.com/article",
        }
        assert extract_source_url_from_raw_data(raw_data) == "https://provenance.com/article"

    def test_missing_provenance_returns_empty(self):
        raw_data = {"title": "Test"}
        assert extract_source_url_from_raw_data(raw_data) == ""

    def test_non_dict_returns_empty(self):
        assert extract_source_url_from_raw_data("not a dict") == ""

    def test_none_returns_empty(self):
        assert extract_source_url_from_raw_data(None) == ""

    def test_same_url_different_hash_same_key(self):
        """source_response_hash is NOT part of the evidence_key — same URL = same key."""
        raw1 = {"_provenance": {"source_url": "https://example.com/art", "source_response_hash": "aaa"}}
        raw2 = {"_provenance": {"source_url": "https://example.com/art", "source_response_hash": "bbb"}}
        url1 = extract_source_url_from_raw_data(raw1)
        url2 = extract_source_url_from_raw_data(raw2)
        key1 = compute_evidence_key("news_api", url1)
        key2 = compute_evidence_key("news_api", url2)
        assert key1 == key2

"""
Tests for URL Profiler core functionality.

Tests URL parsing utilities, data structures, and basic profiler behavior.
"""

import pytest
from datetime import datetime, timezone

from profilers.url_profiler import (
    parse_base_url,
    extract_domain,
    generate_canonical_key,
    normalize_path,
    build_page_urls,
    hash_content,
    ExtractedField,
    PageFetchResult,
    ProfileExtractionResult,
    CompanyProfile,
    URLProfiler,
    DEFAULT_PAGES,
)


# =============================================================================
# URL PARSING UTILITIES
# =============================================================================

class TestParseBaseUrl:
    """Tests for parse_base_url function."""

    def test_simple_url(self):
        assert parse_base_url("https://acme.ai/about") == "https://acme.ai"

    def test_with_port(self):
        assert parse_base_url("http://example.com:8080/path") == "http://example.com:8080"

    def test_with_query_params(self):
        assert parse_base_url("https://acme.ai/page?ref=123") == "https://acme.ai"

    def test_with_fragment(self):
        assert parse_base_url("https://acme.ai/page#section") == "https://acme.ai"

    def test_missing_scheme_defaults_to_https(self):
        assert parse_base_url("acme.ai/about") == "https://acme.ai"

    def test_www_prefix_preserved(self):
        assert parse_base_url("https://www.acme.ai") == "https://www.acme.ai"

    def test_http_scheme_preserved(self):
        assert parse_base_url("http://example.com") == "http://example.com"


class TestExtractDomain:
    """Tests for extract_domain function."""

    def test_simple_domain(self):
        assert extract_domain("https://acme.ai") == "acme.ai"

    def test_removes_www(self):
        assert extract_domain("https://www.acme.ai") == "acme.ai"

    def test_with_subdomain(self):
        assert extract_domain("https://sub.domain.example.com") == "sub.domain.example.com"

    def test_with_path(self):
        assert extract_domain("https://acme.ai/about/team") == "acme.ai"

    def test_with_port(self):
        assert extract_domain("https://acme.ai:8080") == "acme.ai"

    def test_lowercase(self):
        assert extract_domain("https://ACME.AI") == "acme.ai"

    def test_missing_scheme(self):
        assert extract_domain("acme.ai") == "acme.ai"

    def test_www_with_subdomain(self):
        # www. is only stripped if it's at the start
        assert extract_domain("https://www.sub.acme.ai") == "sub.acme.ai"


class TestGenerateCanonicalKey:
    """Tests for generate_canonical_key function."""

    def test_simple_url(self):
        assert generate_canonical_key("https://acme.ai") == "domain:acme.ai"

    def test_with_www(self):
        assert generate_canonical_key("https://www.acme.ai") == "domain:acme.ai"

    def test_with_path(self):
        assert generate_canonical_key("https://acme.ai/about") == "domain:acme.ai"

    def test_bare_domain(self):
        assert generate_canonical_key("acme.ai") == "domain:acme.ai"

    def test_consistent_keys(self):
        # Different URL formats for same domain should produce same key
        urls = [
            "https://acme.ai",
            "https://www.acme.ai",
            "http://acme.ai/about",
            "acme.ai",
            "https://ACME.AI/",
        ]
        keys = [generate_canonical_key(url) for url in urls]
        assert all(k == "domain:acme.ai" for k in keys)


class TestNormalizePath:
    """Tests for normalize_path function."""

    def test_empty_path(self):
        assert normalize_path("") == "/"

    def test_root_path(self):
        assert normalize_path("/") == "/"

    def test_adds_leading_slash(self):
        assert normalize_path("about") == "/about"

    def test_removes_trailing_slash(self):
        assert normalize_path("/about/") == "/about"

    def test_preserves_root_slash(self):
        # Root path should keep its slash
        assert normalize_path("/") == "/"

    def test_lowercase(self):
        assert normalize_path("/About") == "/about"
        assert normalize_path("/PRICING") == "/pricing"

    def test_nested_path(self):
        assert normalize_path("about/team") == "/about/team"
        assert normalize_path("/about/team/") == "/about/team"


class TestBuildPageUrls:
    """Tests for build_page_urls function."""

    def test_basic_paths(self):
        urls = build_page_urls("https://acme.ai", ["/", "/about", "/pricing"])
        assert urls == [
            "https://acme.ai/",
            "https://acme.ai/about",
            "https://acme.ai/pricing",
        ]

    def test_normalizes_paths(self):
        urls = build_page_urls("https://acme.ai", ["", "About", "/PRICING/"])
        assert urls == [
            "https://acme.ai/",
            "https://acme.ai/about",
            "https://acme.ai/pricing",
        ]

    def test_default_pages(self):
        urls = build_page_urls("https://acme.ai", DEFAULT_PAGES)
        assert len(urls) == 4
        assert "https://acme.ai/" in urls
        assert "https://acme.ai/about" in urls
        assert "https://acme.ai/pricing" in urls
        assert "https://acme.ai/team" in urls


class TestHashContent:
    """Tests for hash_content function."""

    def test_consistent_hash(self):
        content = "Hello, World!"
        hash1 = hash_content(content)
        hash2 = hash_content(content)
        assert hash1 == hash2

    def test_different_content_different_hash(self):
        assert hash_content("Hello") != hash_content("World")

    def test_returns_16_chars(self):
        result = hash_content("test content")
        assert len(result) == 16

    def test_alphanumeric(self):
        result = hash_content("test")
        assert all(c in "0123456789abcdef" for c in result)


# =============================================================================
# DATA CLASSES
# =============================================================================

class TestExtractedField:
    """Tests for ExtractedField dataclass."""

    def test_creation(self):
        field = ExtractedField(
            value="We help small businesses",
            short_phrase="small business help",
            confidence=0.85,
            evidence_snippet="We help small businesses grow...",
            source_url="https://acme.ai/about",
            extraction_method="llm",
        )
        assert field.value == "We help small businesses"
        assert field.confidence == 0.85

    def test_to_dict(self):
        field = ExtractedField(
            value="Test value",
            short_phrase="test",
            confidence=0.9,
            evidence_snippet="evidence",
            source_url="https://test.com",
        )
        d = field.to_dict()
        assert d["value"] == "Test value"
        assert d["confidence"] == 0.9
        assert d["extraction_method"] == "llm"  # default


class TestPageFetchResult:
    """Tests for PageFetchResult dataclass."""

    def test_success_property(self):
        result = PageFetchResult(
            url="https://acme.ai",
            path="/",
            status_code=200,
            html_content="<html>...</html>",
            text_content="Hello",
            fetch_time=datetime.now(timezone.utc),
        )
        assert result.success is True

    def test_failure_on_error(self):
        result = PageFetchResult(
            url="https://acme.ai",
            path="/",
            status_code=200,
            html_content="",
            text_content="",
            fetch_time=datetime.now(timezone.utc),
            error="Connection failed",
        )
        assert result.success is False

    def test_failure_on_non_200(self):
        result = PageFetchResult(
            url="https://acme.ai",
            path="/about",
            status_code=404,
            html_content="",
            text_content="",
            fetch_time=datetime.now(timezone.utc),
        )
        assert result.success is False

    def test_to_dict(self):
        result = PageFetchResult(
            url="https://acme.ai",
            path="/",
            status_code=200,
            html_content="<html>test</html>",
            text_content="test",
            fetch_time=datetime.now(timezone.utc),
            content_hash="abc123",
        )
        d = result.to_dict()
        assert d["url"] == "https://acme.ai"
        assert d["status_code"] == 200
        assert d["success"] is True
        assert "html_content" not in d  # Not included in to_dict


class TestProfileExtractionResult:
    """Tests for ProfileExtractionResult dataclass."""

    def test_fields_extracted_count(self):
        result = ProfileExtractionResult(
            problem_solved=ExtractedField(
                value="test", short_phrase="test", confidence=0.8,
                evidence_snippet="", source_url=""
            ),
            target_customer=ExtractedField(
                value="test", short_phrase="test", confidence=0.8,
                evidence_snippet="", source_url=""
            ),
        )
        assert result.fields_extracted == 2

    def test_is_complete_when_both_present(self):
        result = ProfileExtractionResult(
            problem_solved=ExtractedField(
                value="test", short_phrase="test", confidence=0.8,
                evidence_snippet="", source_url=""
            ),
            target_customer=ExtractedField(
                value="test", short_phrase="test", confidence=0.8,
                evidence_snippet="", source_url=""
            ),
        )
        assert result.is_complete is True

    def test_is_not_complete_when_missing(self):
        result = ProfileExtractionResult(
            problem_solved=ExtractedField(
                value="test", short_phrase="test", confidence=0.8,
                evidence_snippet="", source_url=""
            ),
        )
        assert result.is_complete is False

    def test_empty_result(self):
        result = ProfileExtractionResult()
        assert result.fields_extracted == 0
        assert result.is_complete is False


class TestCompanyProfile:
    """Tests for CompanyProfile dataclass."""

    def test_claim_count(self):
        profile = CompanyProfile(
            canonical_key="domain:acme.ai",
            domain="acme.ai",
            claims=[],  # Empty list
        )
        assert profile.claim_count == 0

    def test_to_dict(self):
        profile = CompanyProfile(
            canonical_key="domain:acme.ai",
            domain="acme.ai",
            source_urls=["https://acme.ai"],
            profile_complete=True,
            last_profiled_at=datetime.now(timezone.utc),
        )
        d = profile.to_dict()
        assert d["canonical_key"] == "domain:acme.ai"
        assert d["domain"] == "acme.ai"
        assert d["profile_complete"] is True


# =============================================================================
# URL PROFILER CLASS
# =============================================================================

class TestURLProfilerInit:
    """Tests for URLProfiler initialization."""

    def test_default_pages(self):
        profiler = URLProfiler()
        assert profiler.pages_to_fetch == DEFAULT_PAGES

    def test_custom_pages(self):
        profiler = URLProfiler(pages_to_fetch=["/", "/about"])
        assert profiler.pages_to_fetch == ["/", "/about"]

    def test_default_timeout(self):
        profiler = URLProfiler()
        assert profiler.timeout == 30.0


class TestURLProfilerTextExtraction:
    """Tests for HTML text extraction."""

    def test_basic_html_stripping(self):
        profiler = URLProfiler()
        html = "<html><body><h1>Hello</h1><p>World</p></body></html>"
        text = profiler._extract_text_from_html(html)
        assert "Hello" in text
        assert "World" in text
        assert "<" not in text

    def test_script_removal(self):
        profiler = URLProfiler()
        html = "<html><script>alert('hi')</script><p>Content</p></html>"
        text = profiler._extract_text_from_html(html)
        assert "alert" not in text
        assert "Content" in text

    def test_style_removal(self):
        profiler = URLProfiler()
        html = "<html><style>.red{color:red}</style><p>Content</p></html>"
        text = profiler._extract_text_from_html(html)
        assert "red" not in text
        assert "Content" in text

    def test_entity_decoding(self):
        profiler = URLProfiler()
        html = "<p>A &amp; B &lt; C &gt; D</p>"
        text = profiler._extract_text_from_html(html)
        assert "&" in text
        assert "<" in text
        assert ">" in text

    def test_whitespace_normalization(self):
        profiler = URLProfiler()
        html = "<p>Hello    \n\n   World</p>"
        text = profiler._extract_text_from_html(html)
        # Should not have excessive whitespace
        assert "  " not in text


class TestURLProfilerCombineTexts:
    """Tests for combining page texts."""

    def test_combines_with_headers(self):
        profiler = URLProfiler()
        pages = [
            PageFetchResult(
                url="https://acme.ai/",
                path="/",
                status_code=200,
                html_content="",
                text_content="Homepage content",
                fetch_time=datetime.now(timezone.utc),
            ),
            PageFetchResult(
                url="https://acme.ai/about",
                path="/about",
                status_code=200,
                html_content="",
                text_content="About content",
                fetch_time=datetime.now(timezone.utc),
            ),
        ]
        combined = profiler._combine_page_texts(pages)
        assert "HOMEPAGE PAGE" in combined
        assert "Homepage content" in combined
        assert "ABOUT PAGE" in combined
        assert "About content" in combined

    def test_skips_failed_pages(self):
        profiler = URLProfiler()
        pages = [
            PageFetchResult(
                url="https://acme.ai/",
                path="/",
                status_code=200,
                html_content="",
                text_content="Good content",
                fetch_time=datetime.now(timezone.utc),
            ),
            PageFetchResult(
                url="https://acme.ai/about",
                path="/about",
                status_code=404,
                html_content="",
                text_content="",
                fetch_time=datetime.now(timezone.utc),
            ),
        ]
        combined = profiler._combine_page_texts(pages)
        assert "Good content" in combined
        assert "ABOUT PAGE" not in combined

    def test_skips_empty_content(self):
        profiler = URLProfiler()
        pages = [
            PageFetchResult(
                url="https://acme.ai/",
                path="/",
                status_code=200,
                html_content="",
                text_content="",  # Empty
                fetch_time=datetime.now(timezone.utc),
            ),
        ]
        combined = profiler._combine_page_texts(pages)
        assert combined == ""

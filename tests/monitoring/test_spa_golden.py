"""
Golden-file tests for SPA hydration extraction.

Tests the full extraction pipeline with realistic SPA HTML fixtures
including Next.js, Nuxt.js, and edge cases.
"""

import json
from pathlib import Path

import pytest

from monitoring.content_pipeline.extract_hydration import HydrationExtractor, HydrationSource
from monitoring.content_pipeline.json_canonical import canonicalize_json, CanonicalizeOptions
from monitoring.content_pipeline.models import RepresentationType


# Path to SPA fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "content_pipeline" / "spa"


def load_fixture(name: str) -> str:
    """Load HTML fixture file."""
    path = FIXTURES_DIR / f"{name}.html"
    if not path.exists():
        raise FileNotFoundError(f"Fixture not found: {path}")
    return path.read_text(encoding="utf-8")


def load_expected(name: str) -> dict:
    """Load expected JSON file."""
    path = FIXTURES_DIR / f"{name}.expected.json"
    if not path.exists():
        raise FileNotFoundError(f"Expected file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


class TestNextJsExtraction:
    """Tests for Next.js __NEXT_DATA__ extraction."""

    @pytest.fixture
    def extractor(self) -> HydrationExtractor:
        """Create HydrationExtractor instance."""
        return HydrationExtractor()

    def test_nextjs_products_extraction(self, extractor: HydrationExtractor) -> None:
        """Test extraction of Next.js products page."""
        html = load_fixture("nextjs_products")
        expected = load_expected("nextjs_products")

        result = extractor.extract(html)

        assert result is not None
        assert result.representation_type == RepresentationType.JSON
        assert result.metadata["source"] == HydrationSource.NEXT_DATA.value

        # Parse extracted content
        parsed = json.loads(result.content)

        # Check key fields are present
        assert parsed["page"] == "/products"
        assert "props" in parsed
        assert "pageProps" in parsed["props"]

        # Check products extracted correctly
        products = parsed["props"]["pageProps"]["products"]
        assert len(products) == expected["assertions"]["product_count"]

        # Check specific product data
        assert products[0]["name"] == "Premium Widget"
        assert products[0]["price"] == 29.99
        assert products[1]["name"] == "Deluxe Gadget"
        assert products[2]["inStock"] is False

        # Check pagination
        assert "pagination" in parsed["props"]["pageProps"]
        pagination = parsed["props"]["pageProps"]["pagination"]
        assert pagination["total"] == 3

        # Check filters
        assert "filters" in parsed["props"]["pageProps"]
        filters = parsed["props"]["pageProps"]["filters"]
        assert "Electronics" in filters["categories"]

    def test_nextjs_volatile_keys_can_be_removed(self, extractor: HydrationExtractor) -> None:
        """Test that volatile keys are removed during canonicalization."""
        html = load_fixture("nextjs_products")

        result = extractor.extract(html)
        assert result is not None

        # Canonicalize to remove volatile keys
        canonical = canonicalize_json(result.content)
        parsed = json.loads(canonical)

        # buildId should be removed (volatile)
        assert "buildId" not in parsed

        # __N_SSG should be removed (volatile)
        if "props" in parsed:
            assert "__N_SSG" not in parsed.get("props", {})

    def test_nextjs_high_confidence(self, extractor: HydrationExtractor) -> None:
        """Test that Next.js extraction has high confidence."""
        html = load_fixture("nextjs_products")

        result = extractor.extract(html)

        assert result is not None
        # Next.js __NEXT_DATA__ should have high confidence (0.9)
        assert result.confidence >= 0.8


class TestNuxtJsExtraction:
    """Tests for Nuxt.js __NUXT__ extraction."""

    @pytest.fixture
    def extractor(self) -> HydrationExtractor:
        """Create HydrationExtractor instance."""
        return HydrationExtractor()

    def test_nuxtjs_blog_extraction(self, extractor: HydrationExtractor) -> None:
        """Test extraction of Nuxt.js blog page."""
        html = load_fixture("nuxtjs_blog")
        expected = load_expected("nuxtjs_blog")

        result = extractor.extract(html)

        assert result is not None
        assert result.representation_type == RepresentationType.JSON
        assert result.metadata["source"] == HydrationSource.NUXT.value

        # Parse extracted content
        parsed = json.loads(result.content)

        # Check data structure
        assert "data" in parsed
        assert len(parsed["data"]) > 0

        # Check posts
        posts = parsed["data"][0]["posts"]
        assert len(posts) == expected["assertions"]["post_count"]

        # Check specific post data
        assert posts[0]["title"] == "Introduction to Vue 3"
        assert posts[0]["author"]["name"] == "Jane Doe"
        assert "vue" in posts[0]["tags"]

        # Check meta
        meta = parsed["data"][0]["meta"]
        assert meta["total"] == 42

        # Check state
        assert "state" in parsed
        assert parsed["state"]["theme"] == "light"

    def test_nuxtjs_handles_js_syntax(self, extractor: HydrationExtractor) -> None:
        """Test that Nuxt.js JS object literal syntax is handled."""
        html = load_fixture("nuxtjs_blog")

        result = extractor.extract(html)

        # Should successfully parse despite JS syntax (unquoted keys)
        assert result is not None

        # Content should be valid JSON after extraction
        parsed = json.loads(result.content)
        assert isinstance(parsed, dict)


class TestEdgeCases:
    """Tests for edge cases in SPA extraction."""

    @pytest.fixture
    def extractor(self) -> HydrationExtractor:
        """Create HydrationExtractor instance."""
        return HydrationExtractor()

    def test_no_hydration_data_returns_none(self, extractor: HydrationExtractor) -> None:
        """Test that pages without hydration data return None."""
        html = load_fixture("no_hydration")

        result = extractor.extract(html)

        assert result is None

    def test_empty_html_returns_none(self, extractor: HydrationExtractor) -> None:
        """Test that empty HTML returns None."""
        result = extractor.extract("")
        assert result is None

    def test_malformed_json_handled_gracefully(self, extractor: HydrationExtractor) -> None:
        """Test that malformed JSON is handled gracefully."""
        html = """
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">
                {this is not valid json at all
            </script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        # Should return None (can't parse)
        assert result is None

    def test_truncated_json_handled_gracefully(self, extractor: HydrationExtractor) -> None:
        """Test that truncated JSON is handled gracefully."""
        html = """
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">
                {"props":{"pageProps":{"title":"Hello"
            </script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        # Should return None (truncated)
        assert result is None

    def test_size_limit_enforced(self) -> None:
        """Test that size limit is enforced."""
        # Create extractor with 1KB limit
        extractor = HydrationExtractor(max_size_bytes=1024)

        # Create data that exceeds 1KB
        large_data = {"data": "x" * 2000}
        html = f"""
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">
                {json.dumps(large_data)}
            </script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        # Should return None due to size limit
        assert result is None


class TestCanonicalOutput:
    """Tests for canonical JSON output from SPA extraction."""

    @pytest.fixture
    def extractor(self) -> HydrationExtractor:
        """Create HydrationExtractor instance."""
        return HydrationExtractor()

    def test_output_is_deterministic(self, extractor: HydrationExtractor) -> None:
        """Test that extraction output is deterministic."""
        html = load_fixture("nextjs_products")

        result1 = extractor.extract(html)
        result2 = extractor.extract(html)

        assert result1 is not None
        assert result2 is not None
        # Content should be identical (keys sorted)
        assert result1.content == result2.content

    def test_keys_are_sorted(self, extractor: HydrationExtractor) -> None:
        """Test that JSON keys are sorted in output."""
        html = load_fixture("nextjs_products")

        result = extractor.extract(html)
        assert result is not None

        # Parse and check key order
        # First-level keys should be sorted
        parsed = json.loads(result.content)
        keys = list(parsed.keys())

        # Keys should be in alphabetical order
        assert keys == sorted(keys)


class TestMetadata:
    """Tests for extraction metadata."""

    @pytest.fixture
    def extractor(self) -> HydrationExtractor:
        """Create HydrationExtractor instance."""
        return HydrationExtractor()

    def test_metadata_includes_source(self, extractor: HydrationExtractor) -> None:
        """Test that metadata includes the hydration source."""
        html = load_fixture("nextjs_products")

        result = extractor.extract(html)

        assert result is not None
        assert result.metadata is not None
        assert "source" in result.metadata
        assert result.metadata["source"] in [s.value for s in HydrationSource]

    def test_metadata_includes_sizes(self, extractor: HydrationExtractor) -> None:
        """Test that metadata includes size information."""
        html = load_fixture("nextjs_products")

        result = extractor.extract(html)

        assert result is not None
        assert result.metadata is not None
        assert "original_size" in result.metadata
        assert "parsed_size" in result.metadata
        assert result.metadata["original_size"] > 0
        assert result.metadata["parsed_size"] > 0

    def test_extraction_time_recorded(self, extractor: HydrationExtractor) -> None:
        """Test that extraction time is recorded."""
        html = load_fixture("nextjs_products")

        result = extractor.extract(html)

        assert result is not None
        # extraction_time_ms should be set (may be 0 for fast extractions)
        assert result.extraction_time_ms >= 0

"""
Tests for HydrationExtractor - SPA hydration data extraction.

Tests extraction of __NEXT_DATA__, __NUXT__, and other SPA hydration JSON
from script tags in HTML.
"""

import json
import pytest

from monitoring.content_pipeline.extract_hydration import (
    HydrationExtractor,
    HydrationSource,
)
from monitoring.content_pipeline.models import ExtractedContent, RepresentationType


class TestHydrationExtractor:
    """Tests for HydrationExtractor class."""

    @pytest.fixture
    def extractor(self) -> HydrationExtractor:
        """Create a HydrationExtractor instance."""
        return HydrationExtractor()

    # === __NEXT_DATA__ extraction tests ===

    def test_extract_next_data_basic(self, extractor: HydrationExtractor) -> None:
        """Test basic __NEXT_DATA__ extraction."""
        html = """
        <html>
        <head><title>Test</title></head>
        <body>
            <script id="__NEXT_DATA__" type="application/json">
                {"props":{"pageProps":{"title":"Hello World"}},"page":"/"}
            </script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        assert result.representation_type == RepresentationType.JSON
        parsed = json.loads(result.content)
        assert parsed["props"]["pageProps"]["title"] == "Hello World"
        assert result.metadata is not None
        assert result.metadata.get("source") == HydrationSource.NEXT_DATA.value

    def test_extract_next_data_nested(self, extractor: HydrationExtractor) -> None:
        """Test __NEXT_DATA__ with deeply nested structure."""
        data = {
            "props": {
                "pageProps": {
                    "products": [
                        {"id": 1, "name": "Product A", "price": 10.99},
                        {"id": 2, "name": "Product B", "price": 20.99},
                    ],
                    "pagination": {"page": 1, "total": 100},
                }
            },
            "page": "/products",
            "query": {"category": "electronics"},
        }
        html = f"""
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        parsed = json.loads(result.content)
        assert parsed["props"]["pageProps"]["products"][0]["name"] == "Product A"
        assert parsed["page"] == "/products"

    def test_extract_next_data_with_whitespace(self, extractor: HydrationExtractor) -> None:
        """Test __NEXT_DATA__ extraction with extra whitespace."""
        html = """
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">

                {
                    "props": {
                        "pageProps": {}
                    },
                    "page": "/"
                }

            </script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        parsed = json.loads(result.content)
        assert parsed["page"] == "/"

    # === __NUXT__ extraction tests ===

    def test_extract_nuxt_data(self, extractor: HydrationExtractor) -> None:
        """Test __NUXT__ extraction from Nuxt.js apps."""
        html = """
        <html>
        <body>
            <script>window.__NUXT__={data:[{title:"Nuxt App"}],state:{user:null}}</script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        assert result.metadata is not None
        assert result.metadata.get("source") == HydrationSource.NUXT.value
        # Content should be parseable as JSON (chompjs converts JS to JSON)
        assert "Nuxt App" in result.content

    def test_extract_nuxt_with_function_call(self, extractor: HydrationExtractor) -> None:
        """Test __NUXT__ extraction with function wrapper (common pattern)."""
        html = """
        <html>
        <body>
            <script>window.__NUXT__=(function(a,b){return {data:[{title:a}],fetch:{},state:b}})("Hello","world")</script>
        </body>
        </html>
        """
        # This pattern is too complex for chompjs - should return None or partial
        result = extractor.extract(html)
        # We expect this to fail gracefully (function calls aren't extractable)
        # The extractor should skip complex patterns

    # === Generic script[type=application/json] tests ===

    def test_extract_generic_json_script(self, extractor: HydrationExtractor) -> None:
        """Test extraction from generic JSON script tags."""
        html = """
        <html>
        <body>
            <script type="application/json" data-hydration="true">
                {"config":{"apiUrl":"https://api.example.com"},"user":null}
            </script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        parsed = json.loads(result.content)
        assert parsed["config"]["apiUrl"] == "https://api.example.com"

    # === Priority tests ===

    def test_priority_next_data_over_nuxt(self, extractor: HydrationExtractor) -> None:
        """Test that __NEXT_DATA__ takes priority over __NUXT__."""
        html = """
        <html>
        <body>
            <script>window.__NUXT__={data:[{source:"nuxt"}]}</script>
            <script id="__NEXT_DATA__" type="application/json">
                {"props":{"source":"next"}}
            </script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        assert result.metadata["source"] == HydrationSource.NEXT_DATA.value
        parsed = json.loads(result.content)
        assert parsed["props"]["source"] == "next"

    # === Edge cases ===

    def test_extract_no_hydration_data(self, extractor: HydrationExtractor) -> None:
        """Test extraction when no hydration data exists."""
        html = """
        <html>
        <body>
            <script>console.log("Hello World");</script>
            <p>Regular content</p>
        </body>
        </html>
        """
        result = extractor.extract(html)
        assert result is None

    def test_extract_empty_html(self, extractor: HydrationExtractor) -> None:
        """Test extraction from empty HTML."""
        result = extractor.extract("")
        assert result is None

    def test_extract_invalid_json_in_next_data(self, extractor: HydrationExtractor) -> None:
        """Test handling of invalid JSON in __NEXT_DATA__."""
        html = """
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">
                {invalid json here}
            </script>
        </body>
        </html>
        """
        result = extractor.extract(html)
        # Should return None or try chompjs fallback
        # Invalid JSON without JS syntax should fail

    def test_extract_truncated_json(self, extractor: HydrationExtractor) -> None:
        """Test handling of truncated JSON."""
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
        # Truncated JSON should fail gracefully
        assert result is None

    def test_extract_with_html_entities(self, extractor: HydrationExtractor) -> None:
        """Test extraction when JSON contains HTML entities."""
        html = """
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">
                {"props":{"text":"Hello &amp; World"}}
            </script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        # HTML entities should be decoded
        assert "Hello & World" in result.content or "Hello &amp; World" in result.content

    def test_extract_with_unicode(self, extractor: HydrationExtractor) -> None:
        """Test extraction with unicode characters."""
        html = """
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">
                {"props":{"greeting":"Hello \u4e16\u754c"}}
            </script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        parsed = json.loads(result.content)
        # Should handle unicode escapes
        assert "Hello" in parsed["props"]["greeting"]

    # === Metadata tests ===

    def test_metadata_includes_source(self, extractor: HydrationExtractor) -> None:
        """Test that metadata includes the hydration source."""
        html = """
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">{"page":"/"}</script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        assert result.metadata is not None
        assert "source" in result.metadata
        assert result.metadata["source"] == HydrationSource.NEXT_DATA.value

    def test_metadata_includes_size(self, extractor: HydrationExtractor) -> None:
        """Test that metadata includes content size."""
        data = {"large": "x" * 1000}
        html = f"""
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        assert result.metadata is not None
        assert "original_size" in result.metadata
        assert result.metadata["original_size"] >= 1000

    def test_confidence_score(self, extractor: HydrationExtractor) -> None:
        """Test that confidence score is set appropriately."""
        html = """
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">
                {"props":{"pageProps":{}},"page":"/"}
            </script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        # High confidence for well-formed __NEXT_DATA__
        assert result.confidence >= 0.8


class TestHydrationSource:
    """Tests for HydrationSource enum."""

    def test_source_values(self) -> None:
        """Test that source enum has expected values."""
        assert HydrationSource.NEXT_DATA.value == "next_data"
        assert HydrationSource.NUXT.value == "nuxt"
        assert HydrationSource.GENERIC_JSON.value == "generic_json"


class TestSizeLimits:
    """Tests for size limit functionality (W2 mitigation)."""

    def test_size_limit_enforced(self) -> None:
        """Test that size limit is enforced."""
        # Create extractor with 1KB limit
        extractor = HydrationExtractor(max_size_bytes=1024)

        # Create data that exceeds 1KB
        large_data = {"data": "x" * 2000}
        html = f"""
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">{json.dumps(large_data)}</script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        # Should return None due to size limit
        assert result is None

    def test_size_limit_not_exceeded(self) -> None:
        """Test that extraction works when under size limit."""
        extractor = HydrationExtractor(max_size_bytes=10240)  # 10KB

        small_data = {"data": "x" * 100}
        html = f"""
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">{json.dumps(small_data)}</script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        parsed = json.loads(result.content)
        assert len(parsed["data"]) == 100

    def test_default_size_limit_is_100kb(self) -> None:
        """Test that default size limit is 100KB."""
        extractor = HydrationExtractor()
        assert extractor.max_size_bytes == 102400  # 100KB


class TestChompjsTimeout:
    """Tests for chompjs timeout functionality (W2 mitigation)."""

    def test_default_timeout_is_5_seconds(self) -> None:
        """Test that default chompjs timeout is 5 seconds."""
        extractor = HydrationExtractor()
        assert extractor.chompjs_timeout_ms == 5000

    def test_timeout_configurable(self) -> None:
        """Test that timeout is configurable."""
        extractor = HydrationExtractor(chompjs_timeout_ms=1000)
        assert extractor.chompjs_timeout_ms == 1000

    def test_chompjs_parses_js_object_literals(self) -> None:
        """Test that chompjs can parse JS object literals (not valid JSON)."""
        extractor = HydrationExtractor()

        # This is JS syntax (unquoted keys, trailing comma) - not valid JSON
        html = """
        <html>
        <body>
            <script>window.__NUXT__={data:[{name:'Test'}],state:{active:true,}}</script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        parsed = json.loads(result.content)
        assert parsed["data"][0]["name"] == "Test"
        assert parsed["state"]["active"] is True

    def test_chompjs_handles_single_quotes(self) -> None:
        """Test that chompjs handles single-quoted strings."""
        extractor = HydrationExtractor()

        html = """
        <html>
        <body>
            <script>window.__NUXT__={title:'Hello World',count:42}</script>
        </body>
        </html>
        """
        result = extractor.extract(html)

        assert result is not None
        parsed = json.loads(result.content)
        assert parsed["title"] == "Hello World"
        assert parsed["count"] == 42

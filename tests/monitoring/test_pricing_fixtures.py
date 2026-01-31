"""
Fixture-based tests for Pricing Page Extraction.

Tests InscriptisExtractor and StructuredDataExtractor against
pricing page HTML fixtures with expected output files.

Tests cover:
- SaaS pricing page with HTML tables and JSON-LD Product schemas
- E-commerce product page with microdata markup
- Table alignment preservation in text extraction
- JSON-LD and microdata extraction accuracy
- ContentPipeline with prefer_structured=True

Fixtures located in: tests/monitoring/fixtures/content_pipeline/pricing/
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monitoring.content_pipeline.extract_inscriptis import InscriptisExtractor
from monitoring.content_pipeline.extract_structured import StructuredDataExtractor
from monitoring.content_pipeline.models import FetchArtifact, RepresentationType
from monitoring.content_pipeline.orchestrator import ContentPipeline


# Path to pricing fixtures
PRICING_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "content_pipeline" / "pricing"


def load_html_fixture(name: str) -> str:
    """Load an HTML fixture file from the pricing fixtures directory."""
    path = PRICING_FIXTURES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Fixture not found: {path}")
    return path.read_text(encoding="utf-8")


def load_text_fixture(name: str) -> str:
    """Load a text expected output file from the pricing fixtures directory."""
    path = PRICING_FIXTURES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Expected output not found: {path}")
    return path.read_text(encoding="utf-8")


def load_json_fixture(name: str) -> Dict[str, Any]:
    """Load a JSON expected output file from the pricing fixtures directory."""
    path = PRICING_FIXTURES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Expected output not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def create_mock_fetch_artifact(url: str, content: str, status_code: int = 200) -> FetchArtifact:
    """Create a FetchArtifact for testing."""
    return FetchArtifact(
        url=url,
        status_code=status_code,
        headers={"content-type": "text/html"},
        content=content,
        fetch_time_ms=50,
        fetched_at=datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    )


def normalize_whitespace(text: str) -> str:
    """
    Normalize whitespace in text for comparison.

    Strips trailing whitespace from lines but preserves structure.
    This handles platform differences in line endings and trailing spaces.
    """
    lines = text.strip().split("\n")
    return "\n".join(line.rstrip() for line in lines)


class TestInscriptisExtractorTablePreservation:
    """Tests for InscriptisExtractor table alignment preservation."""

    def test_saas_pricing_table_alignment(self):
        """
        SaaS pricing page should preserve table column alignment.

        The pricing table has columns: Feature, Free, Pro, Enterprise
        Column alignment should be maintained in the text output.
        """
        html = load_html_fixture("pricing_saas.html")
        expected = load_text_fixture("pricing_saas_text.txt")

        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        # Normalize whitespace for comparison
        actual_normalized = normalize_whitespace(result.content)
        expected_normalized = normalize_whitespace(expected)

        assert actual_normalized == expected_normalized

    def test_saas_pricing_table_contains_all_tiers(self):
        """All pricing tiers should be present in extracted text."""
        html = load_html_fixture("pricing_saas.html")

        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        content_lower = result.content.lower()

        # Check tier names are present
        assert "free" in content_lower
        assert "pro" in content_lower
        assert "enterprise" in content_lower

        # Check prices are present
        assert "$0/mo" in result.content
        assert "$9/mo" in result.content
        assert "$29/mo" in result.content

    def test_saas_pricing_table_has_feature_rows(self):
        """All feature rows should be extracted from pricing table."""
        html = load_html_fixture("pricing_saas.html")

        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        content_lower = result.content.lower()

        # Check feature rows
        assert "workout tracking" in content_lower
        assert "ai coach" in content_lower
        assert "team members" in content_lower
        assert "analytics" in content_lower
        assert "support" in content_lower

    def test_ecommerce_product_list_extraction(self):
        """E-commerce product list should be fully extracted."""
        html = load_html_fixture("pricing_ecommerce.html")
        expected = load_text_fixture("pricing_ecommerce_text.txt")

        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        actual_normalized = normalize_whitespace(result.content)
        expected_normalized = normalize_whitespace(expected)

        assert actual_normalized == expected_normalized

    def test_ecommerce_products_with_prices(self):
        """All products with prices should be present in extracted text."""
        html = load_html_fixture("pricing_ecommerce.html")

        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        # Check product names
        assert "Vitamin C Serum" in result.content
        assert "Hydrating Moisturizer" in result.content
        assert "Retinol Night Cream" in result.content

        # Check prices
        assert "$24.99" in result.content
        assert "$32.00" in result.content
        assert "$45.00" in result.content

    def test_inscriptis_confidence_for_saas_page(self):
        """SaaS pricing page should have high confidence due to substantial content."""
        html = load_html_fixture("pricing_saas.html")

        extractor = InscriptisExtractor()
        result = extractor.extract(html)

        # Content is well over 200 chars, should be high confidence
        assert result.confidence == 1.0
        assert result.extractor_name == "inscriptis_v1"


class TestStructuredDataExtractorPricing:
    """Tests for StructuredDataExtractor on pricing pages."""

    def test_saas_pricing_jsonld_extraction(self):
        """
        SaaS pricing page JSON-LD extraction.

        Should extract 3 Product schemas (Free, Pro, Enterprise plans)
        with prices, descriptions, and availability.
        """
        html = load_html_fixture("pricing_saas.html")
        expected = load_json_fixture("pricing_saas_json.json")

        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        actual = json.loads(result.content)

        # Check JSON-LD products
        assert len(actual.get("json-ld", [])) == 3
        assert len(expected.get("json-ld", [])) == 3

        # Check product names match
        actual_names = sorted([p["name"] for p in actual["json-ld"]])
        expected_names = sorted([p["name"] for p in expected["json-ld"]])
        assert actual_names == expected_names

        # Check prices match
        actual_prices = sorted([p["offers"]["price"] for p in actual["json-ld"]])
        expected_prices = sorted([p["offers"]["price"] for p in expected["json-ld"]])
        assert actual_prices == expected_prices

    def test_saas_pricing_opengraph(self):
        """SaaS pricing page should extract OpenGraph metadata."""
        html = load_html_fixture("pricing_saas.html")

        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        data = json.loads(result.content)

        assert len(data.get("opengraph", [])) > 0
        og = data["opengraph"][0]
        assert og.get("og:title") == "FitTrack Pro Pricing"

    def test_ecommerce_microdata_extraction(self):
        """
        E-commerce page microdata extraction.

        Should extract 3 Product schemas from HTML5 microdata
        with prices, ratings, and availability.
        """
        html = load_html_fixture("pricing_ecommerce.html")
        expected = load_json_fixture("pricing_ecommerce_json.json")

        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        actual = json.loads(result.content)

        # Check microdata products
        assert len(actual.get("microdata", [])) == 3
        assert len(expected.get("microdata", [])) == 3

        # Check product names match
        actual_names = sorted([p["name"] for p in actual["microdata"]])
        expected_names = sorted([p["name"] for p in expected["microdata"]])
        assert actual_names == expected_names

    def test_ecommerce_microdata_prices_and_ratings(self):
        """E-commerce microdata should include prices and ratings."""
        html = load_html_fixture("pricing_ecommerce.html")

        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        data = json.loads(result.content)

        for product in data["microdata"]:
            # Each product should have offers with price
            assert "offers" in product
            assert "price" in product["offers"]

            # Each product should have aggregate rating
            assert "aggregateRating" in product
            assert "ratingValue" in product["aggregateRating"]
            assert "reviewCount" in product["aggregateRating"]

    def test_structured_extractor_confidence(self):
        """
        Extractor should have high confidence for rich structured data.

        SaaS page has 3 JSON-LD + OpenGraph + RDFa = multiple types
        """
        html = load_html_fixture("pricing_saas.html")

        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        # Multiple types found -> high confidence
        assert result.confidence == 1.0
        assert result.extractor_name == "extruct_v1"

    def test_structured_extractor_metadata_counts(self):
        """Extractor metadata should have accurate counts."""
        html = load_html_fixture("pricing_saas.html")

        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        assert result.metadata is not None
        assert result.metadata["json_ld_count"] == 3
        assert result.metadata["opengraph_count"] >= 1
        assert "json-ld" in result.metadata["types_found"]


class TestContentPipelineWithStructuredData:
    """Tests for ContentPipeline with prefer_structured=True."""

    @pytest.mark.asyncio
    async def test_saas_pricing_with_prefer_structured(self):
        """
        ContentPipeline with prefer_structured=True should return JSON as primary.

        When structured data has sufficient confidence (>=0.5),
        JSON should be the primary representation with text as secondary.
        """
        html = load_html_fixture("pricing_saas.html")
        url = "https://fittrack.pro/pricing"

        mock_artifact = create_mock_fetch_artifact(url, html)

        # Create config with prefer_structured=True
        config_json = json.dumps({
            "preset": "pricing",
            "extractor": {
                "selectors": ["#pricing"],
                "prefer_structured": True,
            }
        })

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process_url(
                watch_id=100,
                url=url,
                config_json=config_json,
            )

            assert result.success is True

            # Should have multiple representations
            assert len(result.representations) >= 2

            # Primary should be JSON when structured data is rich
            assert result.primary_representation == RepresentationType.JSON

            # Should have both JSON and TEXT representations
            rep_types = [r.representation_type for r in result.representations]
            assert RepresentationType.JSON in rep_types
            assert RepresentationType.TEXT in rep_types

    @pytest.mark.asyncio
    async def test_ecommerce_pricing_with_prefer_structured(self):
        """
        E-commerce page with microdata should return JSON as primary.
        """
        html = load_html_fixture("pricing_ecommerce.html")
        url = "https://glowupbeauty.com/shop"

        mock_artifact = create_mock_fetch_artifact(url, html)

        config_json = json.dumps({
            "preset": "default",
            "extractor": {
                "selectors": ["#products"],
                "prefer_structured": True,
            }
        })

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process_url(
                watch_id=101,
                url=url,
                config_json=config_json,
            )

            assert result.success is True

            # Primary should be JSON (microdata is rich)
            assert result.primary_representation == RepresentationType.JSON

    @pytest.mark.asyncio
    async def test_prefer_structured_false_returns_text_primary(self):
        """
        With prefer_structured=False (default), text should be primary.
        """
        html = load_html_fixture("pricing_saas.html")
        url = "https://fittrack.pro/pricing"

        mock_artifact = create_mock_fetch_artifact(url, html)

        # Default config (prefer_structured=False)
        config_json = json.dumps({
            "preset": "pricing",
            "extractor": {
                "selectors": ["#pricing"],
            }
        })

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process_url(
                watch_id=102,
                url=url,
                config_json=config_json,
            )

            assert result.success is True

            # Primary should be TEXT when prefer_structured is False
            assert result.primary_representation == RepresentationType.TEXT

            # Should only have text representation
            assert len(result.representations) == 1


class TestPricingExtractorMetadata:
    """Tests for extractor metadata on pricing pages."""

    def test_inscriptis_metadata_contains_config(self):
        """InscriptisExtractor metadata should contain configuration."""
        html = load_html_fixture("pricing_saas.html")

        extractor = InscriptisExtractor(
            table_cell_separator="  ",
            display_links=False,
        )
        result = extractor.extract(html)

        assert result.metadata is not None
        assert result.metadata["table_cell_separator"] == "  "
        assert result.metadata["display_links"] is False

    def test_structured_extractor_metadata_contains_syntaxes(self):
        """StructuredDataExtractor metadata should list syntaxes."""
        html = load_html_fixture("pricing_ecommerce.html")

        extractor = StructuredDataExtractor()
        result = extractor.extract(html)

        assert result.metadata is not None
        assert "syntaxes_requested" in result.metadata
        assert "json-ld" in result.metadata["syntaxes_requested"]
        assert "microdata" in result.metadata["syntaxes_requested"]


class TestPricingFixtureIntegrity:
    """Tests to verify fixture files are complete and consistent."""

    def test_all_saas_fixtures_exist(self):
        """All SaaS pricing fixtures should exist."""
        assert (PRICING_FIXTURES_DIR / "pricing_saas.html").exists()
        assert (PRICING_FIXTURES_DIR / "pricing_saas_text.txt").exists()
        assert (PRICING_FIXTURES_DIR / "pricing_saas_json.json").exists()

    def test_all_ecommerce_fixtures_exist(self):
        """All e-commerce pricing fixtures should exist."""
        assert (PRICING_FIXTURES_DIR / "pricing_ecommerce.html").exists()
        assert (PRICING_FIXTURES_DIR / "pricing_ecommerce_text.txt").exists()
        assert (PRICING_FIXTURES_DIR / "pricing_ecommerce_json.json").exists()

    def test_saas_json_fixture_is_valid(self):
        """SaaS JSON fixture should be valid JSON with expected structure."""
        data = load_json_fixture("pricing_saas_json.json")

        assert "json-ld" in data
        assert "microdata" in data
        assert "opengraph" in data
        assert "rdfa" in data

    def test_ecommerce_json_fixture_is_valid(self):
        """E-commerce JSON fixture should be valid JSON with expected structure."""
        data = load_json_fixture("pricing_ecommerce_json.json")

        assert "json-ld" in data
        assert "microdata" in data
        assert "opengraph" in data
        assert "rdfa" in data

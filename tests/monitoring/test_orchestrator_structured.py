"""Tests for ContentPipeline structured data extraction integration.

Tests cover:
- prefer_structured=False (default) returns only text representation
- prefer_structured=True with rich JSON-LD returns JSON as primary
- prefer_structured=True with sparse structured data returns text as primary
- Multiple representations returned when both are available
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from monitoring.content_pipeline.config import ExtractorConfig, WatchConfig
from monitoring.content_pipeline.models import (
    FetchArtifact,
    ExtractedContent,
    PipelineResult,
    RepresentationType,
)
from monitoring.models import Watch


class TestExtractorConfigPreferStructured:
    """Test ExtractorConfig prefer_structured option."""

    def test_default_prefer_structured_is_false(self):
        """Default prefer_structured should be False."""
        config = ExtractorConfig()
        assert config.prefer_structured is False

    def test_prefer_structured_can_be_set_to_true(self):
        """prefer_structured can be set to True."""
        config = ExtractorConfig(prefer_structured=True)
        assert config.prefer_structured is True

    def test_to_dict_includes_prefer_structured_when_true(self):
        """to_dict() should include prefer_structured when True."""
        config = ExtractorConfig(prefer_structured=True)
        result = config.to_dict()
        assert "prefer_structured" in result
        assert result["prefer_structured"] is True

    def test_to_dict_excludes_prefer_structured_when_false(self):
        """to_dict() should exclude prefer_structured when False (default)."""
        config = ExtractorConfig(prefer_structured=False)
        result = config.to_dict()
        # False is default, should be omitted for brevity
        assert "prefer_structured" not in result

    def test_from_dict_with_prefer_structured_true(self):
        """from_dict() should parse prefer_structured=True."""
        data = {"preset": "default", "prefer_structured": True}
        config = ExtractorConfig.from_dict(data)
        assert config.prefer_structured is True

    def test_from_dict_without_prefer_structured_defaults_to_false(self):
        """from_dict() without prefer_structured defaults to False."""
        data = {"preset": "default"}
        config = ExtractorConfig.from_dict(data)
        assert config.prefer_structured is False


class TestContentPipelinePreferStructuredFalse:
    """Test ContentPipeline with prefer_structured=False (default)."""

    @pytest.mark.asyncio
    async def test_default_returns_only_text_representation(self):
        """With prefer_structured=False (default), should return only text."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com/pricing",
            canonical_key="domain:example.com",
        )

        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Pro Plan",
                "offers": {"@type": "Offer", "price": "29.99", "priceCurrency": "USD"}
            }
            </script>
        </head>
        <body><article>Pricing content here</article></body>
        </html>
        """

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com/pricing",
            status_code=200,
            headers={"content-type": "text/html"},
            content=html_content,
            fetch_time_ms=50,
        )

        # Use default config (prefer_structured=False)
        mock_config = WatchConfig(
            preset="pricing_table_v1",
            extractor=ExtractorConfig(
                preset="pricing_table_v1",
                selectors=["article"],
                prefer_structured=False,
            ),
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.PipelinePlanner"
        ) as MockPlanner:
            mock_planner = MagicMock()
            mock_planner.plan.return_value = mock_config
            MockPlanner.return_value = mock_planner

            with patch.object(
                ContentPipeline, "_fetch", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = mock_fetch_artifact

                pipeline = ContentPipeline()
                result = await pipeline.process(watch)

                # Should only have text representation
                assert len(result.representations) == 1
                assert result.representations[0].representation_type == RepresentationType.TEXT
                assert result.primary_representation == RepresentationType.TEXT


class TestContentPipelinePreferStructuredTrue:
    """Test ContentPipeline with prefer_structured=True."""

    @pytest.mark.asyncio
    async def test_rich_json_ld_returns_json_as_primary(self):
        """With rich JSON-LD (confidence >= 0.5), JSON should be primary."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com/pricing",
            canonical_key="domain:example.com",
        )

        # Rich JSON-LD with multiple products
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta property="og:title" content="Pricing Plans">
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Basic Plan",
                "offers": {"@type": "Offer", "price": "19.00", "priceCurrency": "USD"}
            }
            </script>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Pro Plan",
                "offers": {"@type": "Offer", "price": "49.00", "priceCurrency": "USD"}
            }
            </script>
        </head>
        <body><article>Pricing content here</article></body>
        </html>
        """

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com/pricing",
            status_code=200,
            headers={"content-type": "text/html"},
            content=html_content,
            fetch_time_ms=50,
        )

        mock_config = WatchConfig(
            preset="pricing_table_v1",
            extractor=ExtractorConfig(
                preset="pricing_table_v1",
                selectors=["article"],
                prefer_structured=True,
            ),
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.PipelinePlanner"
        ) as MockPlanner:
            mock_planner = MagicMock()
            mock_planner.plan.return_value = mock_config
            MockPlanner.return_value = mock_planner

            with patch.object(
                ContentPipeline, "_fetch", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = mock_fetch_artifact

                pipeline = ContentPipeline()
                result = await pipeline.process(watch)

                # Should have JSON as primary representation
                assert result.primary_representation == RepresentationType.JSON

                # Should have at least the JSON representation
                json_reps = [r for r in result.representations
                             if r.representation_type == RepresentationType.JSON]
                assert len(json_reps) >= 1

                # JSON content should contain product data
                json_data = json.loads(json_reps[0].content)
                assert "json-ld" in json_data
                assert len(json_data["json-ld"]) >= 2

    @pytest.mark.asyncio
    async def test_sparse_structured_data_returns_text_as_primary(self):
        """With sparse structured data (confidence < 0.5), text should be primary."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com/about",
            canonical_key="domain:example.com",
        )

        # Sparse structured data - only basic OpenGraph
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>About Us</title>
        </head>
        <body><article>Detailed about page content with lots of text.</article></body>
        </html>
        """

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com/about",
            status_code=200,
            headers={"content-type": "text/html"},
            content=html_content,
            fetch_time_ms=50,
        )

        mock_config = WatchConfig(
            preset="default",
            extractor=ExtractorConfig(
                preset="default",
                selectors=["article"],
                prefer_structured=True,
            ),
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.PipelinePlanner"
        ) as MockPlanner:
            mock_planner = MagicMock()
            mock_planner.plan.return_value = mock_config
            MockPlanner.return_value = mock_planner

            with patch.object(
                ContentPipeline, "_fetch", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = mock_fetch_artifact

                pipeline = ContentPipeline()
                result = await pipeline.process(watch)

                # Should have text as primary (sparse/no structured data)
                assert result.primary_representation == RepresentationType.TEXT

                # Should still have text representation
                text_reps = [r for r in result.representations
                             if r.representation_type == RepresentationType.TEXT]
                assert len(text_reps) >= 1


class TestContentPipelineMultipleRepresentations:
    """Test multiple representations returned when both are available."""

    @pytest.mark.asyncio
    async def test_both_representations_returned_when_prefer_structured(self):
        """With prefer_structured=True, should return both JSON and text reps."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com/pricing",
            canonical_key="domain:example.com",
        )

        # Rich HTML with both good structured data and text content
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta property="og:title" content="Pricing Plans">
            <meta property="og:type" content="website">
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Pro Plan",
                "offers": {"@type": "Offer", "price": "29.99", "priceCurrency": "USD"}
            }
            </script>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Enterprise Plan",
                "offers": {"@type": "Offer", "price": "99.99", "priceCurrency": "USD"}
            }
            </script>
        </head>
        <body><article>This is the pricing page with detailed text content.</article></body>
        </html>
        """

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com/pricing",
            status_code=200,
            headers={"content-type": "text/html"},
            content=html_content,
            fetch_time_ms=50,
        )

        mock_config = WatchConfig(
            preset="pricing_table_v1",
            extractor=ExtractorConfig(
                preset="pricing_table_v1",
                selectors=["article"],
                prefer_structured=True,
            ),
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.PipelinePlanner"
        ) as MockPlanner:
            mock_planner = MagicMock()
            mock_planner.plan.return_value = mock_config
            MockPlanner.return_value = mock_planner

            with patch.object(
                ContentPipeline, "_fetch", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = mock_fetch_artifact

                pipeline = ContentPipeline()
                result = await pipeline.process(watch)

                # Should have both representations
                rep_types = [r.representation_type for r in result.representations]
                assert RepresentationType.JSON in rep_types
                assert RepresentationType.TEXT in rep_types

                # Should have at least 2 representations
                assert len(result.representations) >= 2

    @pytest.mark.asyncio
    async def test_json_representation_comes_before_text_when_primary(self):
        """When JSON is primary, it should be first in the list."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com/pricing",
            canonical_key="domain:example.com",
        )

        # Rich structured data
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta property="og:title" content="Pricing Plans">
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Basic Plan",
                "offers": {"@type": "Offer", "price": "19.00", "priceCurrency": "USD"}
            }
            </script>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Pro Plan",
                "offers": {"@type": "Offer", "price": "49.00", "priceCurrency": "USD"}
            }
            </script>
        </head>
        <body><article>Content</article></body>
        </html>
        """

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com/pricing",
            status_code=200,
            headers={"content-type": "text/html"},
            content=html_content,
            fetch_time_ms=50,
        )

        mock_config = WatchConfig(
            preset="pricing_table_v1",
            extractor=ExtractorConfig(
                preset="pricing_table_v1",
                selectors=["article"],
                prefer_structured=True,
            ),
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.PipelinePlanner"
        ) as MockPlanner:
            mock_planner = MagicMock()
            mock_planner.plan.return_value = mock_config
            MockPlanner.return_value = mock_planner

            with patch.object(
                ContentPipeline, "_fetch", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = mock_fetch_artifact

                pipeline = ContentPipeline()
                result = await pipeline.process(watch)

                # If JSON is primary, it should be first
                if result.primary_representation == RepresentationType.JSON:
                    assert len(result.representations) >= 1
                    assert result.representations[0].representation_type == RepresentationType.JSON


class TestContentPipelineStructuredExtractorInitialization:
    """Test StructuredDataExtractor initialization in ContentPipeline."""

    def test_structured_extractor_not_initialized_by_default(self):
        """StructuredDataExtractor should not be initialized by default."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        pipeline = ContentPipeline()
        # Should have _structured_extractor attribute but it should be None
        assert hasattr(pipeline, "_structured_extractor")
        assert pipeline._structured_extractor is None

    def test_structured_extractor_can_be_injected(self):
        """StructuredDataExtractor can be injected via constructor."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline
        from monitoring.content_pipeline.extract_structured import StructuredDataExtractor

        structured_extractor = StructuredDataExtractor()
        pipeline = ContentPipeline(structured_extractor=structured_extractor)

        assert pipeline._structured_extractor is structured_extractor


class TestContentPipelineGetPrimaryContent:
    """Test PipelineResult.get_primary_content() with multiple representations."""

    @pytest.mark.asyncio
    async def test_get_primary_content_returns_json_when_json_is_primary(self):
        """get_primary_content() should return JSON content when JSON is primary."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com/pricing",
            canonical_key="domain:example.com",
        )

        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta property="og:title" content="Pricing">
            <script type="application/ld+json">
            {"@context": "https://schema.org", "@type": "Product", "name": "Plan A"}
            </script>
            <script type="application/ld+json">
            {"@context": "https://schema.org", "@type": "Product", "name": "Plan B"}
            </script>
        </head>
        <body><article>Text content</article></body>
        </html>
        """

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com/pricing",
            status_code=200,
            headers={"content-type": "text/html"},
            content=html_content,
            fetch_time_ms=50,
        )

        mock_config = WatchConfig(
            preset="pricing_table_v1",
            extractor=ExtractorConfig(
                preset="pricing_table_v1",
                selectors=["article"],
                prefer_structured=True,
            ),
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.PipelinePlanner"
        ) as MockPlanner:
            mock_planner = MagicMock()
            mock_planner.plan.return_value = mock_config
            MockPlanner.return_value = mock_planner

            with patch.object(
                ContentPipeline, "_fetch", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = mock_fetch_artifact

                pipeline = ContentPipeline()
                result = await pipeline.process(watch)

                # When JSON is primary, get_primary_content should return JSON
                if result.primary_representation == RepresentationType.JSON:
                    primary_content = result.get_primary_content()
                    assert primary_content is not None
                    # Should be valid JSON
                    data = json.loads(primary_content)
                    assert "json-ld" in data

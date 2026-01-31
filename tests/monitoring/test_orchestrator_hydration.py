"""
Tests for ContentPipeline integration with HydrationExtractor.

Tests the spa_hydration_v1 preset and SPA hydration extraction flow.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from monitoring.content_pipeline.config import ExtractorConfig, TransportConfig, WatchConfig
from monitoring.content_pipeline.extract_hydration import HydrationExtractor
from monitoring.content_pipeline.models import (
    ExtractedContent,
    FetchArtifact,
    PipelineResult,
    RepresentationType,
)
from monitoring.content_pipeline.orchestrator import ContentPipeline


class TestSpaHydrationPreset:
    """Tests for spa_hydration_v1 preset integration."""

    @pytest.fixture
    def next_data_html(self) -> str:
        """HTML with __NEXT_DATA__ hydration."""
        data = {
            "props": {
                "pageProps": {
                    "products": [
                        {"id": 1, "name": "Product A", "price": 10.99},
                        {"id": 2, "name": "Product B", "price": 20.99},
                    ]
                }
            },
            "page": "/products",
            "buildId": "abc123",  # Volatile - should be removed by canonicalization
        }
        return f"""
        <!DOCTYPE html>
        <html>
        <head><title>Products</title></head>
        <body>
            <div id="__next">Loading...</div>
            <script id="__NEXT_DATA__" type="application/json">
                {json.dumps(data)}
            </script>
        </body>
        </html>
        """

    @pytest.fixture
    def nuxt_html(self) -> str:
        """HTML with __NUXT__ hydration."""
        return """
        <!DOCTYPE html>
        <html>
        <head><title>Nuxt App</title></head>
        <body>
            <div id="__nuxt"></div>
            <script>window.__NUXT__={data:[{title:'Nuxt App',items:['a','b','c']}],state:{user:null}}</script>
        </body>
        </html>
        """

    @pytest.fixture
    def spa_config(self) -> WatchConfig:
        """WatchConfig for spa_hydration_v1 preset."""
        return WatchConfig(
            preset="spa_hydration_v1",
            extractor=ExtractorConfig(
                preset="spa",
                selectors=["#__NEXT_DATA__", "#__NUXT__"],
                fallback_on_empty=False,
                prefer_structured=True,  # SPA preset prefers JSON
            ),
            transport=TransportConfig(
                initial="httpx",
                max_json_bytes=102400,  # 100KB
            ),
        )

    @pytest.mark.asyncio
    async def test_extracts_next_data_with_spa_preset(
        self, next_data_html: str, spa_config: WatchConfig
    ) -> None:
        """Test that spa_hydration_v1 preset extracts __NEXT_DATA__."""
        mock_artifact = FetchArtifact(
            url="https://example.com/products",
            status_code=200,
            headers={"content-type": "text/html"},
            content=next_data_html,
        )

        # Mock planner to return spa config
        mock_planner = MagicMock()
        mock_planner.plan.return_value = spa_config

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            # Create pipeline with hydration extractor
            pipeline = ContentPipeline(
                planner=mock_planner,
                hydration_extractor=HydrationExtractor(),
            )

            result = await pipeline.process_url(
                watch_id=1,
                url="https://example.com/products",
            )

        assert result.success is True
        assert result.preset_used == "spa_hydration_v1"

        # Should have JSON representation
        json_rep = next(
            (r for r in result.representations if r.representation_type == RepresentationType.JSON),
            None,
        )
        assert json_rep is not None

        parsed = json.loads(json_rep.content)
        assert parsed["page"] == "/products"
        assert len(parsed["props"]["pageProps"]["products"]) == 2

    @pytest.mark.asyncio
    async def test_extracts_nuxt_data_with_spa_preset(
        self, nuxt_html: str, spa_config: WatchConfig
    ) -> None:
        """Test that spa_hydration_v1 preset extracts __NUXT__."""
        mock_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={"content-type": "text/html"},
            content=nuxt_html,
        )

        mock_planner = MagicMock()
        mock_planner.plan.return_value = spa_config

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline(
                planner=mock_planner,
                hydration_extractor=HydrationExtractor(),
            )

            result = await pipeline.process_url(
                watch_id=1,
                url="https://example.com",
            )

        assert result.success is True

        # Should have JSON representation from __NUXT__
        json_rep = next(
            (r for r in result.representations if r.representation_type == RepresentationType.JSON),
            None,
        )
        assert json_rep is not None

        parsed = json.loads(json_rep.content)
        assert parsed["data"][0]["title"] == "Nuxt App"

    @pytest.mark.asyncio
    async def test_spa_preset_uses_json_as_primary(
        self, next_data_html: str, spa_config: WatchConfig
    ) -> None:
        """Test that spa_hydration_v1 preset sets JSON as primary representation."""
        mock_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={"content-type": "text/html"},
            content=next_data_html,
        )

        mock_planner = MagicMock()
        mock_planner.plan.return_value = spa_config

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline(
                planner=mock_planner,
                hydration_extractor=HydrationExtractor(),
            )

            result = await pipeline.process_url(
                watch_id=1,
                url="https://example.com",
            )

        assert result.success is True
        assert result.primary_representation == RepresentationType.JSON

    @pytest.mark.asyncio
    async def test_fallback_to_text_when_no_hydration_data(self) -> None:
        """Test fallback to text when no hydration data found."""
        plain_html = """
        <html>
        <body>
            <h1>Plain Page</h1>
            <p>No SPA here.</p>
        </body>
        </html>
        """

        spa_config = WatchConfig(
            preset="spa_hydration_v1",
            extractor=ExtractorConfig(
                preset="spa",
                selectors=["#__NEXT_DATA__"],
                fallback_on_empty=True,  # Fall back to text extraction
                prefer_structured=True,
            ),
        )

        mock_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={"content-type": "text/html"},
            content=plain_html,
        )

        mock_planner = MagicMock()
        mock_planner.plan.return_value = spa_config

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline(
                planner=mock_planner,
                hydration_extractor=HydrationExtractor(),
            )

            result = await pipeline.process_url(
                watch_id=1,
                url="https://example.com",
            )

        assert result.success is True
        # Should fall back to TEXT since no hydration data
        assert result.primary_representation == RepresentationType.TEXT


class TestHydrationExtractorMetadata:
    """Tests for hydration extraction metadata."""

    @pytest.mark.asyncio
    async def test_metadata_includes_hydration_source(self) -> None:
        """Test that metadata includes the hydration source."""
        next_data_html = """
        <html>
        <body>
            <script id="__NEXT_DATA__" type="application/json">{"page":"/"}</script>
        </body>
        </html>
        """

        spa_config = WatchConfig(
            preset="spa_hydration_v1",
            extractor=ExtractorConfig(
                preset="spa",
                selectors=["#__NEXT_DATA__"],
                prefer_structured=True,
            ),
        )

        mock_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={"content-type": "text/html"},
            content=next_data_html,
        )

        mock_planner = MagicMock()
        mock_planner.plan.return_value = spa_config

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline(
                planner=mock_planner,
                hydration_extractor=HydrationExtractor(),
            )

            result = await pipeline.process_url(
                watch_id=1,
                url="https://example.com",
            )

        # Find JSON representation
        json_rep = next(
            (r for r in result.representations if r.representation_type == RepresentationType.JSON),
            None,
        )
        assert json_rep is not None
        assert json_rep.metadata is not None
        assert json_rep.metadata.get("source") == "next_data"

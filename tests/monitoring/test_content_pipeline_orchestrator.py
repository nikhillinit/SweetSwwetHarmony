"""Tests for ContentPipeline orchestrator.

Tests cover:
- Processing a Watch object to produce PipelineResult
- Processing a URL directly with process_url()
- Using PipelinePlanner to determine WatchConfig
- Using HttpxTransport for fetching with conditional requests
- Handling 304 Not Modified responses
- Content extraction with SelectorExtractor
- Error handling (timeout, connection error, content size exceeded)
- Timing calculation (fetch + extraction = total_time_ms)
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import httpx

from monitoring.models import Watch
from monitoring.content_pipeline.models import (
    FetchArtifact,
    PipelineResult,
    ExtractedContent,
    RepresentationType,
)
from monitoring.content_pipeline.config import WatchConfig, ExtractorConfig, TransportConfig
from monitoring.content_pipeline.exceptions import ContentSizeExceededError


class TestContentPipelineProcessWatch:
    """Test processing a Watch object."""

    @pytest.mark.asyncio
    async def test_process_returns_pipeline_result(self):
        """process() should return a PipelineResult."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        # Mock the transport and extractor
        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={"content-type": "text/html"},
            content="<html><body><article>Content</article></body></html>",
            fetch_time_ms=50,
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_fetch_artifact

            pipeline = ContentPipeline()
            result = await pipeline.process(watch)

            assert isinstance(result, PipelineResult)
            assert result.watch_id == 1
            assert result.success is True

    @pytest.mark.asyncio
    async def test_process_uses_watch_etag_and_last_modified(self):
        """process() should pass watch.last_etag and last_modified to transport."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
            last_etag='"abc123"',
            last_modified="Wed, 21 Oct 2025 07:28:00 GMT",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            fetch_time_ms=50,
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_fetch_artifact

            pipeline = ContentPipeline()
            await pipeline.process(watch)

            # Verify etag and last_modified were passed
            mock_fetch.assert_called_once()
            call_kwargs = mock_fetch.call_args.kwargs
            assert call_kwargs.get("etag") == '"abc123"'
            assert call_kwargs.get("last_modified") == "Wed, 21 Oct 2025 07:28:00 GMT"

    @pytest.mark.asyncio
    async def test_process_uses_planner_for_config(self):
        """process() should use PipelinePlanner to get WatchConfig."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com/pricing",
            canonical_key="domain:example.com",
            config_json='{"preset": "custom"}',
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com/pricing",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            fetch_time_ms=50,
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.PipelinePlanner"
        ) as MockPlanner:
            mock_planner = MagicMock()
            mock_planner.plan.return_value = WatchConfig(preset="custom")
            MockPlanner.return_value = mock_planner

            with patch.object(
                ContentPipeline, "_fetch", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = mock_fetch_artifact

                pipeline = ContentPipeline()
                result = await pipeline.process(watch)

                # Verify planner was used with URL and config_json
                mock_planner.plan.assert_called_once_with(
                    "https://example.com/pricing",
                    config_json='{"preset": "custom"}',
                )
                assert result.preset_used == "custom"


class TestContentPipelineProcessUrl:
    """Test processing a URL directly."""

    @pytest.mark.asyncio
    async def test_process_url_returns_pipeline_result(self):
        """process_url() should return a PipelineResult."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            fetch_time_ms=50,
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_fetch_artifact

            pipeline = ContentPipeline()
            result = await pipeline.process_url(
                watch_id=42,
                url="https://example.com",
            )

            assert isinstance(result, PipelineResult)
            assert result.watch_id == 42

    @pytest.mark.asyncio
    async def test_process_url_with_conditional_headers(self):
        """process_url() should accept etag and last_modified parameters."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            fetch_time_ms=50,
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_fetch_artifact

            pipeline = ContentPipeline()
            await pipeline.process_url(
                watch_id=42,
                url="https://example.com",
                etag='"xyz789"',
                last_modified="Thu, 22 Oct 2025 08:00:00 GMT",
            )

            call_kwargs = mock_fetch.call_args.kwargs
            assert call_kwargs.get("etag") == '"xyz789"'
            assert call_kwargs.get("last_modified") == "Thu, 22 Oct 2025 08:00:00 GMT"

    @pytest.mark.asyncio
    async def test_process_url_with_config_json(self):
        """process_url() should accept config_json parameter."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            fetch_time_ms=50,
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.PipelinePlanner"
        ) as MockPlanner:
            mock_planner = MagicMock()
            mock_planner.plan.return_value = WatchConfig(preset="spa")
            MockPlanner.return_value = mock_planner

            with patch.object(
                ContentPipeline, "_fetch", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = mock_fetch_artifact

                pipeline = ContentPipeline()
                result = await pipeline.process_url(
                    watch_id=42,
                    url="https://example.com",
                    config_json='{"preset": "spa"}',
                )

                mock_planner.plan.assert_called_once_with(
                    "https://example.com",
                    config_json='{"preset": "spa"}',
                )
                assert result.preset_used == "spa"


class TestContentPipeline304NotModified:
    """Test handling of 304 Not Modified responses."""

    @pytest.mark.asyncio
    async def test_304_returns_success_with_empty_representations(self):
        """304 response should return success=True with empty representations."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
            last_etag='"existing"',
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=304,
            headers={},
            content="",
            fetch_time_ms=20,
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_fetch_artifact

            pipeline = ContentPipeline()
            result = await pipeline.process(watch)

            assert result.success is True
            assert result.representations == []
            assert result.fetch_artifact.is_not_modified is True

    @pytest.mark.asyncio
    async def test_304_sets_primary_representation_to_text(self):
        """304 response should still set primary_representation to TEXT."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=304,
            headers={},
            content="",
            fetch_time_ms=20,
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_fetch_artifact

            pipeline = ContentPipeline()
            result = await pipeline.process(watch)

            assert result.primary_representation == RepresentationType.TEXT


class TestContentPipelineExtraction:
    """Test content extraction."""

    @pytest.mark.asyncio
    async def test_extraction_uses_selectors_from_config(self):
        """Extraction should use selectors from WatchConfig."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body><article>Article content</article></body></html>",
            fetch_time_ms=50,
        )

        mock_config = WatchConfig(
            preset="article",
            extractor=ExtractorConfig(
                preset="article",
                selectors=["article", "main"],
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

                # Should have extracted content
                assert len(result.representations) == 1
                assert "Article content" in result.representations[0].content

    @pytest.mark.asyncio
    async def test_selectors_tried_recorded_in_result(self):
        """selectors_tried should be recorded from extraction metadata."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body><main>Main content</main></body></html>",
            fetch_time_ms=50,
        )

        mock_config = WatchConfig(
            preset="default",
            extractor=ExtractorConfig(
                selectors=["article", "main"],
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

                # selectors_tried should be populated
                assert result.selectors_tried is not None
                assert "article" in result.selectors_tried or "main" in result.selectors_tried

    @pytest.mark.asyncio
    async def test_primary_representation_is_text(self):
        """primary_representation should be TEXT."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            fetch_time_ms=50,
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_fetch_artifact

            pipeline = ContentPipeline()
            result = await pipeline.process(watch)

            assert result.primary_representation == RepresentationType.TEXT


class TestContentPipelineErrorHandling:
    """Test error handling scenarios."""

    @pytest.mark.asyncio
    async def test_timeout_error_sets_success_false(self):
        """Timeout error should set success=False with error message."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.side_effect = httpx.TimeoutException("Request timed out")

            pipeline = ContentPipeline()
            result = await pipeline.process(watch)

            assert result.success is False
            assert "timeout" in result.error.lower()

    @pytest.mark.asyncio
    async def test_connection_error_sets_success_false(self):
        """Connection error should set success=False with error message."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.side_effect = httpx.ConnectError("Connection refused")

            pipeline = ContentPipeline()
            result = await pipeline.process(watch)

            assert result.success is False
            assert "connection" in result.error.lower()

    @pytest.mark.asyncio
    async def test_content_size_exceeded_sets_success_false(self):
        """ContentSizeExceededError should set success=False with specific error."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.side_effect = ContentSizeExceededError(
                url="https://example.com",
                max_size=5_000_000,
                actual_size=10_000_000,
            )

            pipeline = ContentPipeline()
            result = await pipeline.process(watch)

            assert result.success is False
            assert "size" in result.error.lower()

    @pytest.mark.asyncio
    async def test_extraction_error_sets_success_false(self):
        """Extraction error should set success=False with error message."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            fetch_time_ms=50,
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_fetch_artifact

            with patch(
                "monitoring.content_pipeline.orchestrator.SelectorExtractor"
            ) as MockExtractor:
                mock_extractor = MagicMock()
                mock_extractor.extract.side_effect = Exception("Parse error")
                MockExtractor.return_value = mock_extractor

                pipeline = ContentPipeline()
                result = await pipeline.process(watch)

                assert result.success is False
                assert "extraction" in result.error.lower() or "error" in result.error.lower()

    @pytest.mark.asyncio
    async def test_error_result_still_has_fetch_artifact(self):
        """Error result should still have fetch_artifact if fetch succeeded."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            fetch_time_ms=50,
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_fetch_artifact

            with patch(
                "monitoring.content_pipeline.orchestrator.SelectorExtractor"
            ) as MockExtractor:
                mock_extractor = MagicMock()
                mock_extractor.extract.side_effect = Exception("Parse error")
                MockExtractor.return_value = mock_extractor

                pipeline = ContentPipeline()
                result = await pipeline.process(watch)

                # Fetch artifact should still be present
                assert result.fetch_artifact is not None
                assert result.fetch_artifact.url == "https://example.com"


class TestContentPipelineTiming:
    """Test timing calculation."""

    @pytest.mark.asyncio
    async def test_total_time_includes_fetch_and_extraction(self):
        """total_time_ms should include both fetch and extraction time."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            fetch_time_ms=100,  # 100ms fetch time
        )

        # Mock extracted content with extraction time
        mock_extracted = ExtractedContent(
            representation_type=RepresentationType.TEXT,
            content="Content",
            extraction_time_ms=50,  # 50ms extraction time
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_fetch_artifact

            with patch(
                "monitoring.content_pipeline.orchestrator.SelectorExtractor"
            ) as MockExtractor:
                mock_extractor = MagicMock()
                mock_extractor.extract.return_value = mock_extracted
                MockExtractor.return_value = mock_extractor

                pipeline = ContentPipeline()
                result = await pipeline.process(watch)

                # total_time_ms should be at least fetch + extraction
                assert result.total_time_ms >= 150  # 100 + 50

    @pytest.mark.asyncio
    async def test_total_time_for_304_response(self):
        """total_time_ms should be just fetch time for 304 (no extraction)."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=304,
            headers={},
            content="",
            fetch_time_ms=30,
        )

        with patch.object(
            ContentPipeline, "_fetch", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_fetch_artifact

            pipeline = ContentPipeline()
            result = await pipeline.process(watch)

            # total_time_ms should be approximately fetch_time_ms for 304
            assert result.total_time_ms >= 30


class TestContentPipelineTransportConfig:
    """Test transport configuration."""

    @pytest.mark.asyncio
    async def test_transport_uses_size_limits_from_config(self):
        """Transport should use max_html_bytes and max_json_bytes from config."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_config = WatchConfig(
            preset="default",
            transport=TransportConfig(
                max_html_bytes=1_000_000,
                max_json_bytes=500_000,
            ),
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            fetch_time_ms=50,
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.PipelinePlanner"
        ) as MockPlanner:
            mock_planner = MagicMock()
            mock_planner.plan.return_value = mock_config
            MockPlanner.return_value = mock_planner

            with patch(
                "monitoring.content_pipeline.orchestrator.TransportEscalator"
            ) as MockEscalator:
                mock_escalator = AsyncMock()
                mock_escalator.fetch = AsyncMock(return_value=mock_fetch_artifact)
                MockEscalator.return_value = mock_escalator

                pipeline = ContentPipeline()
                await pipeline.process(watch)

                # Verify size limits were passed to transport
                call_kwargs = mock_escalator.fetch.call_args.kwargs
                assert call_kwargs.get("max_html_bytes") == 1_000_000
                assert call_kwargs.get("max_json_bytes") == 500_000


class TestContentPipelinePresetMetadata:
    """Test preset metadata in result."""

    @pytest.mark.asyncio
    async def test_preset_used_from_config(self):
        """preset_used should be taken from WatchConfig."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com/pricing",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com/pricing",
            status_code=200,
            headers={},
            content="<html><body>Pricing</body></html>",
            fetch_time_ms=50,
        )

        mock_config = WatchConfig(
            preset="pricing_table_v1",
            extractor=ExtractorConfig(
                selectors=[".pricing", "#pricing"],
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

                assert result.preset_used == "pricing_table_v1"


class TestContentPipelineRemoveSelectors:
    """Test remove_selectors functionality."""

    @pytest.mark.asyncio
    async def test_remove_selectors_passed_to_extractor(self):
        """remove_selectors should be passed to SelectorExtractor."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body><article>Content<nav>Nav</nav></article></body></html>",
            fetch_time_ms=50,
        )

        # Create config with remove_selectors in metadata
        mock_config = WatchConfig(
            preset="default",
            extractor=ExtractorConfig(
                selectors=["article"],
            ),
            metadata={"remove_selectors": ["nav", "footer"]},
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

                with patch(
                    "monitoring.content_pipeline.orchestrator.SelectorExtractor"
                ) as MockExtractor:
                    mock_extractor = MagicMock()
                    mock_extractor.extract.return_value = ExtractedContent(
                        representation_type=RepresentationType.TEXT,
                        content="Content",
                        metadata={"selectors_tried": ["article"]},
                    )
                    MockExtractor.return_value = mock_extractor

                    pipeline = ContentPipeline()
                    await pipeline.process(watch)

                    # Verify remove_selectors was passed
                    call_kwargs = mock_extractor.extract.call_args.kwargs
                    assert call_kwargs.get("remove_selectors") == ["nav", "footer"]


class TestContentPipelineIntegration:
    """Integration tests with real components (mocked transport only)."""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_simple_html(self):
        """Full pipeline test with simple HTML (mocked fetch only)."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={"content-type": "text/html"},
            content="""
            <html>
                <body>
                    <article>
                        <h1>Test Article</h1>
                        <p>This is the article content with enough text to pass extraction.</p>
                    </article>
                </body>
            </html>
            """,
            fetch_time_ms=50,
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_fetch_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process(watch)

            # Full pipeline should work
            assert result.success is True
            assert result.watch_id == 1
            assert len(result.representations) == 1
            assert "Test Article" in result.representations[0].content or "article content" in result.representations[0].content
            assert result.primary_representation == RepresentationType.TEXT
            assert result.total_time_ms > 0

    @pytest.mark.asyncio
    async def test_full_pipeline_pricing_url_auto_selects_preset(self):
        """Pricing URL should auto-select pricing preset."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://startup.io/pricing",
            canonical_key="domain:startup.io",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://startup.io/pricing",
            status_code=200,
            headers={"content-type": "text/html"},
            content="""
            <html>
                <body>
                    <div class="pricing">
                        <h2>Pricing Plans</h2>
                        <p>Basic: $10/mo, Pro: $30/mo</p>
                    </div>
                </body>
            </html>
            """,
            fetch_time_ms=50,
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_fetch_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process(watch)

            # Should auto-select pricing preset
            assert result.preset_used == "pricing_table_v1"
            assert result.success is True


class TestContentPipelineTransportEscalation:
    """Test transport escalation integration."""

    @pytest.mark.asyncio
    async def test_pipeline_uses_transport_escalator(self):
        """ContentPipeline should use TransportEscalator for fetching."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline
        from monitoring.content_pipeline.transport_escalator import TransportEscalator

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            transport_used="httpx",
            fetch_time_ms=50,
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = AsyncMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_fetch_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process(watch)

            # Verify TransportEscalator was used
            MockEscalator.assert_called_once()
            mock_escalator.fetch.assert_called_once()
            assert result.success is True

    @pytest.mark.asyncio
    async def test_pipeline_passes_transport_config_to_escalator(self):
        """ContentPipeline should pass TransportConfig to escalator."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            transport_used="httpx",
            fetch_time_ms=50,
        )

        # Create config with escalation settings
        mock_config = WatchConfig(
            preset="default",
            transport=TransportConfig(
                on_403="curl_cffi",
                user_agent_profile="chrome",
            ),
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.PipelinePlanner"
        ) as MockPlanner:
            mock_planner = MagicMock()
            mock_planner.plan.return_value = mock_config
            MockPlanner.return_value = mock_planner

            with patch(
                "monitoring.content_pipeline.orchestrator.TransportEscalator"
            ) as MockEscalator:
                mock_escalator = AsyncMock()
                mock_escalator.fetch = AsyncMock(return_value=mock_fetch_artifact)
                MockEscalator.return_value = mock_escalator

                pipeline = ContentPipeline()
                await pipeline.process(watch)

                # Verify TransportConfig was passed
                call_kwargs = MockEscalator.call_args.kwargs
                config = call_kwargs.get("config")
                assert config is not None
                assert config.on_403 == "curl_cffi"
                assert config.user_agent_profile == "chrome"

    @pytest.mark.asyncio
    async def test_pipeline_reports_transport_used_in_artifact(self):
        """PipelineResult should report which transport was used."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        watch = Watch(
            id=1,
            url="https://example.com",
            canonical_key="domain:example.com",
        )

        # Simulate escalation - curl_cffi was used
        mock_fetch_artifact = FetchArtifact(
            url="https://example.com",
            status_code=200,
            headers={},
            content="<html><body>Content</body></html>",
            transport_used="curl_cffi",  # Escalated to curl_cffi
            fetch_time_ms=100,
        )

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = AsyncMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_fetch_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process(watch)

            # fetch_artifact should show curl_cffi was used
            assert result.fetch_artifact.transport_used == "curl_cffi"

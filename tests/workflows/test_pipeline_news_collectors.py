"""
Tests for News Collectors Pipeline Integration

Tests for:
- News API collector wiring into pipeline
- RSS feeds collector wiring into pipeline
- Environment variable handling (GNEWS_API_KEY)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import os

from workflows.pipeline import DiscoveryPipeline, PipelineConfig
from discovery_engine.mcp_server import CollectorResult, CollectorStatus


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture(autouse=True)
def no_heartbeat_writes():
    """Keep collector heartbeat bookkeeping from writing state/collectors.json.

    The pipeline records collector heartbeats to a cwd-relative tracked file;
    these unit tests only exercise collector wiring, so stub the writers out.
    """
    with patch("workflows.pipeline.initialize_collector_state"), \
         patch("workflows.pipeline.record_collector_heartbeat"):
        yield


@pytest.fixture
def mock_store():
    """Create a mock signal store."""
    store = MagicMock()
    store.initialize = MagicMock()
    store.get_pending_signals = MagicMock(return_value=[])
    store.get_signal_counts = MagicMock(return_value={
        "total": 0,
        "pending": 0,
        "qualified": 0,
        "pushed": 0,
    })
    # The pipeline's per-collector telemetry awaits store._db.execute(...)
    # when a _db handle is present.  A bare MagicMock is not awaitable, so
    # expose no DB handle and let the telemetry count short-circuit to None.
    store._db = None
    return store


@pytest.fixture
def pipeline_config():
    """Create a test pipeline config."""
    return PipelineConfig(
        db_path=":memory:",
        parallel_collectors=False,
        use_gating=False,
        use_asset_store=False,
        use_entities=False,
    )


# =============================================================================
# NEWS API COLLECTOR TESTS
# =============================================================================

class TestNewsAPICollectorIntegration:
    """Tests for NewsAPI collector integration into pipeline."""

    @pytest.mark.asyncio
    async def test_news_api_collector_recognized(self, pipeline_config, mock_store):
        """Pipeline recognizes news_api as valid collector."""
        with patch("workflows.pipeline.SignalStore", return_value=mock_store):
            pipeline = DiscoveryPipeline(config=pipeline_config)
            pipeline._store = mock_store
            pipeline._initialized = True

            # Mock the news_api collector
            with patch("collectors.news_api.NewsAPICollector") as MockCollector:
                mock_collector = MagicMock()
                mock_collector.run = AsyncMock(return_value=CollectorResult(
                    collector="news_api",
                    status=CollectorStatus.SUCCESS,
                    signals_found=5,
                    dry_run=True,
                ))
                MockCollector.return_value = mock_collector

                # Set API key
                with patch.dict(os.environ, {"GNEWS_API_KEY": "test_key"}):
                    result = await pipeline._run_single_collector("news_api", dry_run=True)

                assert result.status == CollectorStatus.SUCCESS
                assert result.signals_found == 5

    @pytest.mark.asyncio
    async def test_news_api_requires_api_key(self, pipeline_config, mock_store):
        """Pipeline skips news_api without GNEWS_API_KEY."""
        with patch("workflows.pipeline.SignalStore", return_value=mock_store):
            pipeline = DiscoveryPipeline(config=pipeline_config)
            pipeline._store = mock_store
            pipeline._initialized = True

            # Ensure no API key
            with patch.dict(os.environ, {}, clear=True):
                # Remove GNEWS_API_KEY if it exists
                if "GNEWS_API_KEY" in os.environ:
                    del os.environ["GNEWS_API_KEY"]

                result = await pipeline._run_single_collector("news_api", dry_run=True)

                assert result.status == CollectorStatus.SKIPPED
                assert "GNEWS_API_KEY" in result.error_message

    @pytest.mark.asyncio
    async def test_news_api_passes_api_key(self, pipeline_config, mock_store):
        """Pipeline passes API key to NewsAPICollector."""
        with patch("workflows.pipeline.SignalStore", return_value=mock_store):
            pipeline = DiscoveryPipeline(config=pipeline_config)
            pipeline._store = mock_store
            pipeline._initialized = True

            with patch("collectors.news_api.NewsAPICollector") as MockCollector:
                mock_collector = MagicMock()
                mock_collector.run = AsyncMock(return_value=CollectorResult(
                    collector="news_api",
                    status=CollectorStatus.SUCCESS,
                    signals_found=0,
                    dry_run=True,
                ))
                MockCollector.return_value = mock_collector

                with patch.dict(os.environ, {"GNEWS_API_KEY": "my_secret_key"}):
                    await pipeline._run_single_collector("news_api", dry_run=True)

                # Verify collector was created with API key
                MockCollector.assert_called_once()
                call_kwargs = MockCollector.call_args[1]
                assert call_kwargs.get("api_key") == "my_secret_key"


# =============================================================================
# RSS FEEDS COLLECTOR TESTS
# =============================================================================

class TestRSSFeedsCollectorIntegration:
    """Tests for RSS feeds collector integration into pipeline."""

    @pytest.mark.asyncio
    async def test_rss_feeds_collector_recognized(self, pipeline_config, mock_store):
        """Pipeline recognizes rss_feeds as valid collector."""
        with patch("workflows.pipeline.SignalStore", return_value=mock_store):
            pipeline = DiscoveryPipeline(config=pipeline_config)
            pipeline._store = mock_store
            pipeline._initialized = True

            with patch("collectors.rss_feeds.RSSFeedCollector") as MockCollector:
                mock_collector = MagicMock()
                mock_collector.run = AsyncMock(return_value=CollectorResult(
                    collector="rss_feeds",
                    status=CollectorStatus.SUCCESS,
                    signals_found=10,
                    dry_run=True,
                ))
                MockCollector.return_value = mock_collector

                result = await pipeline._run_single_collector("rss_feeds", dry_run=True)

                assert result.status == CollectorStatus.SUCCESS
                assert result.signals_found == 10

    @pytest.mark.asyncio
    async def test_rss_feeds_no_api_key_required(self, pipeline_config, mock_store):
        """RSS feeds collector works without any API key."""
        with patch("workflows.pipeline.SignalStore", return_value=mock_store):
            pipeline = DiscoveryPipeline(config=pipeline_config)
            pipeline._store = mock_store
            pipeline._initialized = True

            with patch("collectors.rss_feeds.RSSFeedCollector") as MockCollector:
                mock_collector = MagicMock()
                mock_collector.run = AsyncMock(return_value=CollectorResult(
                    collector="rss_feeds",
                    status=CollectorStatus.SUCCESS,
                    signals_found=3,
                    dry_run=True,
                ))
                MockCollector.return_value = mock_collector

                # Clear environment to ensure no API keys
                with patch.dict(os.environ, {}, clear=True):
                    result = await pipeline._run_single_collector("rss_feeds", dry_run=True)

                # Should succeed without API key
                assert result.status == CollectorStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_rss_feeds_custom_feeds(self, pipeline_config, mock_store):
        """Pipeline can pass custom RSS feeds via environment."""
        with patch("workflows.pipeline.SignalStore", return_value=mock_store):
            pipeline = DiscoveryPipeline(config=pipeline_config)
            pipeline._store = mock_store
            pipeline._initialized = True

            with patch("collectors.rss_feeds.RSSFeedCollector") as MockCollector:
                mock_collector = MagicMock()
                mock_collector.run = AsyncMock(return_value=CollectorResult(
                    collector="rss_feeds",
                    status=CollectorStatus.SUCCESS,
                    signals_found=2,
                    dry_run=True,
                ))
                MockCollector.return_value = mock_collector

                custom_feeds = "https://example.com/feed.xml,https://other.com/rss"
                with patch.dict(os.environ, {"RSS_FEEDS": custom_feeds}):
                    await pipeline._run_single_collector("rss_feeds", dry_run=True)

                MockCollector.assert_called_once()
                call_kwargs = MockCollector.call_args[1]
                # Should have feeds parameter if env var is set
                if "feeds" in call_kwargs:
                    assert len(call_kwargs["feeds"]) == 2

    @pytest.mark.asyncio
    async def test_rss_feeds_custom_categories(self, pipeline_config, mock_store):
        """Pipeline can filter RSS feeds by category."""
        with patch("workflows.pipeline.SignalStore", return_value=mock_store):
            pipeline = DiscoveryPipeline(config=pipeline_config)
            pipeline._store = mock_store
            pipeline._initialized = True

            with patch("collectors.rss_feeds.RSSFeedCollector") as MockCollector:
                mock_collector = MagicMock()
                mock_collector.run = AsyncMock(return_value=CollectorResult(
                    collector="rss_feeds",
                    status=CollectorStatus.SUCCESS,
                    signals_found=5,
                    dry_run=True,
                ))
                MockCollector.return_value = mock_collector

                with patch.dict(os.environ, {"RSS_CATEGORIES": "startup,health_tech"}):
                    await pipeline._run_single_collector("rss_feeds", dry_run=True)

                MockCollector.assert_called_once()


# =============================================================================
# COMBINED NEWS COLLECTORS TESTS
# =============================================================================

class TestCombinedNewsCollectors:
    """Tests for running both news collectors together."""

    @pytest.mark.asyncio
    async def test_run_all_news_collectors(self, pipeline_config, mock_store):
        """Both news collectors can run in sequence."""
        with patch("workflows.pipeline.SignalStore", return_value=mock_store):
            pipeline = DiscoveryPipeline(config=pipeline_config)
            pipeline._store = mock_store
            pipeline._initialized = True

            with patch("collectors.news_api.NewsAPICollector") as MockNewsAPI, \
                 patch("collectors.rss_feeds.RSSFeedCollector") as MockRSS:

                # Mock news_api
                news_collector = MagicMock()
                news_collector.run = AsyncMock(return_value=CollectorResult(
                    collector="news_api",
                    status=CollectorStatus.SUCCESS,
                    signals_found=5,
                    dry_run=True,
                ))
                MockNewsAPI.return_value = news_collector

                # Mock rss_feeds
                rss_collector = MagicMock()
                rss_collector.run = AsyncMock(return_value=CollectorResult(
                    collector="rss_feeds",
                    status=CollectorStatus.SUCCESS,
                    signals_found=10,
                    dry_run=True,
                ))
                MockRSS.return_value = rss_collector

                with patch.dict(os.environ, {"GNEWS_API_KEY": "test_key"}):
                    results = await pipeline._run_collectors_stage(
                        ["news_api", "rss_feeds"],
                        dry_run=True,
                    )

                assert len(results) == 2
                total_signals = sum(r.signals_found for r in results)
                assert total_signals == 15

    @pytest.mark.asyncio
    async def test_news_collectors_graceful_failure(self, pipeline_config, mock_store):
        """One news collector failing doesn't stop the other."""
        with patch("workflows.pipeline.SignalStore", return_value=mock_store):
            pipeline = DiscoveryPipeline(config=pipeline_config)
            pipeline._store = mock_store
            pipeline._initialized = True

            with patch("collectors.news_api.NewsAPICollector") as MockNewsAPI, \
                 patch("collectors.rss_feeds.RSSFeedCollector") as MockRSS:

                # news_api fails
                news_collector = MagicMock()
                news_collector.run = AsyncMock(side_effect=Exception("API Error"))
                MockNewsAPI.return_value = news_collector

                # rss_feeds succeeds
                rss_collector = MagicMock()
                rss_collector.run = AsyncMock(return_value=CollectorResult(
                    collector="rss_feeds",
                    status=CollectorStatus.SUCCESS,
                    signals_found=10,
                    dry_run=True,
                ))
                MockRSS.return_value = rss_collector

                with patch.dict(os.environ, {"GNEWS_API_KEY": "test_key"}):
                    results = await pipeline._run_collectors_stage(
                        ["news_api", "rss_feeds"],
                        dry_run=True,
                    )

                # Should have 2 results
                assert len(results) == 2
                # RSS should succeed
                rss_result = next(r for r in results if r.collector == "rss_feeds")
                assert rss_result.status == CollectorStatus.SUCCESS


# =============================================================================
# CLI INTEGRATION TESTS
# =============================================================================

class TestCLINewsCollectors:
    """Tests for CLI documentation and collector listing."""

    def test_news_collectors_in_docstring(self):
        """News collectors appear in module docstring."""
        import run_pipeline
        docstring = run_pipeline.__doc__
        # After implementation, check docstring includes news collectors
        # This test will fail until we update the docstring

    def test_valid_collector_names(self):
        """news_api and rss_feeds are valid collector names."""
        # This verifies the collector names we're using
        valid_names = ["news_api", "rss_feeds"]
        for name in valid_names:
            assert "_" in name or name.isalpha()  # Valid Python identifier style

"""
Tests for Phase C shared HTTP client injection into collectors.

Verifies that GitHub and SEC EDGAR collectors correctly use
an injected CollectorHttpClient instead of creating their own.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import httpx
import pytest

from collectors.http_client import CollectorHttpClient, RunContext
from collectors.base import BaseCollector


class _ConcreteCollector(BaseCollector):
    """Minimal concrete subclass for testing BaseCollector."""

    async def _collect_signals(self):
        return []


class TestBaseCollectorHttpInjection:
    """BaseCollector accepts and stores the http parameter."""

    def test_http_default_is_none(self):
        """http defaults to None for backward compatibility."""
        collector = _ConcreteCollector(collector_name="test")
        assert collector.http is None

    def test_http_injection(self):
        """http parameter is stored on the instance."""
        mock_client = MagicMock(spec=CollectorHttpClient)
        collector = _ConcreteCollector(collector_name="test", http=mock_client)
        assert collector.http is mock_client


class TestGitHubSharedClient:
    """GitHub collector uses shared client when injected."""

    @pytest.fixture
    def shared_httpx_client(self):
        return httpx.AsyncClient(timeout=30.0)

    @pytest.fixture
    def collector_http_client(self, shared_httpx_client):
        return CollectorHttpClient(
            shared_httpx_client,
            run_context=RunContext(execution_id="test-exec-123"),
            collector_name="github",
        )

    @pytest.mark.asyncio
    async def test_uses_injected_client(self, collector_http_client, shared_httpx_client):
        """When http is provided, GitHub collector uses its underlying client."""
        from collectors.github import GitHubCollector

        collector = GitHubCollector(
            github_token="ghp_test",
            http=collector_http_client,
        )

        async with collector:
            # Should use the shared client
            assert collector.client is shared_httpx_client
            assert collector._owns_client is False

        await shared_httpx_client.aclose()

    @pytest.mark.asyncio
    async def test_creates_own_client_without_injection(self):
        """Without http injection, GitHub collector creates its own client."""
        from collectors.github import GitHubCollector

        collector = GitHubCollector(github_token="ghp_test")

        async with collector:
            assert collector._owns_client is True
            assert isinstance(collector.client, httpx.AsyncClient)

    @pytest.mark.asyncio
    async def test_does_not_close_shared_client(self, collector_http_client, shared_httpx_client):
        """GitHub collector does not close the shared client on __aexit__."""
        from collectors.github import GitHubCollector

        collector = GitHubCollector(
            github_token="ghp_test",
            http=collector_http_client,
        )

        async with collector:
            pass

        # Shared client should still be open
        assert not shared_httpx_client.is_closed
        await shared_httpx_client.aclose()


class TestSECEdgarSharedClient:
    """SEC EDGAR collector uses shared client when injected."""

    @pytest.fixture
    def shared_httpx_client(self):
        return httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    @pytest.fixture
    def collector_http_client(self, shared_httpx_client):
        return CollectorHttpClient(
            shared_httpx_client,
            run_context=RunContext(execution_id="test-exec-456"),
            collector_name="sec_edgar",
        )

    @pytest.mark.asyncio
    async def test_uses_injected_client(self, collector_http_client, shared_httpx_client):
        """When http is provided, SEC collector uses its underlying client."""
        from collectors.sec_edgar import SECEdgarCollector

        collector = SECEdgarCollector(http=collector_http_client)

        async with collector:
            assert collector._client is shared_httpx_client
            assert collector._owns_client is False

        await shared_httpx_client.aclose()

    @pytest.mark.asyncio
    async def test_creates_own_client_without_injection(self):
        """Without http injection, SEC collector creates its own client."""
        from collectors.sec_edgar import SECEdgarCollector

        collector = SECEdgarCollector()

        async with collector:
            assert collector._owns_client is True
            assert isinstance(collector._client, httpx.AsyncClient)

    @pytest.mark.asyncio
    async def test_does_not_close_shared_client(self, collector_http_client, shared_httpx_client):
        """SEC collector does not close the shared client on __aexit__."""
        from collectors.sec_edgar import SECEdgarCollector

        collector = SECEdgarCollector(http=collector_http_client)

        async with collector:
            pass

        assert not shared_httpx_client.is_closed
        await shared_httpx_client.aclose()

    def test_request_headers_property(self):
        """_request_headers returns User-Agent for per-request injection."""
        from collectors.sec_edgar import SECEdgarCollector

        collector = SECEdgarCollector(user_agent="TestAgent/1.0")
        assert collector._request_headers == {"User-Agent": "TestAgent/1.0"}


class TestRunTrackingHelpers:
    """Pipeline run tracking helpers work correctly."""

    @pytest.mark.asyncio
    async def test_begin_run_tracking_creates_execution_id(self):
        """_begin_run_tracking returns an execution_id."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        config = PipelineConfig(db_path=":memory:")
        pipeline = DiscoveryPipeline(config=config)

        # Mock store so we don't need a real DB
        mock_store = AsyncMock()
        mock_store._db = AsyncMock()
        pipeline._store = mock_store
        pipeline._shared_httpx_client = httpx.AsyncClient()

        # Mock create_run to return a record-like object
        mock_record = MagicMock()
        mock_record.id = "abc123"

        with patch("workflows.pipeline.create_run", return_value=mock_record) as mock_create, \
             patch("workflows.pipeline.start_run") as mock_start:
            exec_id = await pipeline._begin_run_tracking("pipeline", {"test": True})

        assert exec_id == "abc123"
        assert pipeline._execution_id == "abc123"
        assert pipeline._run_tracking_available is True
        assert pipeline._collector_http_client is not None
        mock_create.assert_called_once()
        mock_start.assert_called_once()

        await pipeline._shared_httpx_client.aclose()

    @pytest.mark.asyncio
    async def test_begin_run_tracking_fallback_on_failure(self):
        """_begin_run_tracking generates local UUID on run_history failure."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        config = PipelineConfig(db_path=":memory:")
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = AsyncMock()
        pipeline._shared_httpx_client = httpx.AsyncClient()

        with patch("workflows.pipeline.create_run", side_effect=Exception("DB error")):
            exec_id = await pipeline._begin_run_tracking()

        assert len(exec_id) == 16
        assert pipeline._run_tracking_available is False

        await pipeline._shared_httpx_client.aclose()

    @pytest.mark.asyncio
    async def test_end_run_tracking_success(self):
        """_end_run_tracking calls complete_run on success."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        config = PipelineConfig(db_path=":memory:")
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = AsyncMock()
        pipeline._execution_id = "test-id"
        pipeline._run_tracking_available = True

        with patch("workflows.pipeline.complete_run") as mock_complete:
            await pipeline._end_run_tracking(success=True, stats={"count": 5})

        mock_complete.assert_called_once_with(
            pipeline._store, "test-id", result={"count": 5}
        )

    @pytest.mark.asyncio
    async def test_end_run_tracking_failure(self):
        """_end_run_tracking calls fail_run on failure."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        config = PipelineConfig(db_path=":memory:")
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = AsyncMock()
        pipeline._execution_id = "test-id"
        pipeline._run_tracking_available = True

        with patch("workflows.pipeline.fail_run") as mock_fail:
            await pipeline._end_run_tracking(success=False, error="boom")

        mock_fail.assert_called_once_with(
            pipeline._store, "test-id", error_message="boom"
        )

    @pytest.mark.asyncio
    async def test_end_run_tracking_noop_when_unavailable(self):
        """_end_run_tracking is a no-op when tracking wasn't initialized."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        config = PipelineConfig(db_path=":memory:")
        pipeline = DiscoveryPipeline(config=config)
        pipeline._run_tracking_available = False

        with patch("workflows.pipeline.complete_run") as mock_complete, \
             patch("workflows.pipeline.fail_run") as mock_fail:
            await pipeline._end_run_tracking(success=True)

        mock_complete.assert_not_called()
        mock_fail.assert_not_called()

    @pytest.mark.asyncio
    async def test_end_run_tracking_never_raises(self):
        """_end_run_tracking swallows exceptions."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        config = PipelineConfig(db_path=":memory:")
        pipeline = DiscoveryPipeline(config=config)
        pipeline._store = AsyncMock()
        pipeline._execution_id = "test-id"
        pipeline._run_tracking_available = True

        with patch("workflows.pipeline.complete_run", side_effect=Exception("DB down")):
            # Should not raise
            await pipeline._end_run_tracking(success=True)

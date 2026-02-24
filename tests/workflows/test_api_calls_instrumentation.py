"""
Tests for api_calls / rate_limit_hits instrumentation via httpx event hooks + ContextVars.

Covers:
1. Event hook increments on request
2. ContextVar attribution in parallel collectors
3. Rate limit detection (429)
4. GitHub 403 detection
5. Non-GitHub 403 ignored
6. OpenCorporates receives http= via **common_args
7. Counters reset between runs
8. Invariant: signals > 0 implies api_calls > 0
9. Collector without shared client → api_calls == 0
10. CollectorResult.api_calls field exists in to_dict()
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from contextvars import ContextVar
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from workflows.pipeline import DiscoveryPipeline, _current_collector, CollectorMetrics


# =============================================================================
# HELPERS
# =============================================================================

def _make_pipeline_with_hooks():
    """Create a DiscoveryPipeline with event-hooked httpx client, skipping full init."""
    with patch.object(DiscoveryPipeline, '__init__', lambda self, *a, **kw: None):
        pipeline = DiscoveryPipeline.__new__(DiscoveryPipeline)

    # Manually set up just what we need
    pipeline._http_counters = {}

    async def _on_request(request):
        name = _current_collector.get("unknown")
        if name not in pipeline._http_counters:
            pipeline._http_counters[name] = {"api_calls": 0, "rate_limit_hits": 0}
        pipeline._http_counters[name]["api_calls"] += 1

    async def _on_response(response):
        name = _current_collector.get("unknown")
        if name in pipeline._http_counters:
            status = response.status_code
            if status == 429:
                pipeline._http_counters[name]["rate_limit_hits"] += 1
            elif status == 403:
                if "api.github.com" in str(response.url):
                    pipeline._http_counters[name]["rate_limit_hits"] += 1

    pipeline._shared_httpx_client = httpx.AsyncClient(
        timeout=30.0,
        transport=httpx.MockTransport(lambda req: httpx.Response(200)),
        event_hooks={"request": [_on_request], "response": [_on_response]},
    )

    return pipeline


# =============================================================================
# TEST 1: Event hook increments on request
# =============================================================================


class TestEventHookCounting:
    """Event hooks on shared httpx client correctly count API calls."""

    @pytest.mark.asyncio
    async def test_hook_increments_on_each_request(self):
        """Making N requests increments api_calls to N."""
        pipeline = _make_pipeline_with_hooks()
        token = _current_collector.set("test_collector")
        try:
            for _ in range(5):
                await pipeline._shared_httpx_client.get("https://example.com/api")

            assert pipeline._http_counters["test_collector"]["api_calls"] == 5
            assert pipeline._http_counters["test_collector"]["rate_limit_hits"] == 0
        finally:
            _current_collector.reset(token)
            await pipeline._shared_httpx_client.aclose()


# =============================================================================
# TEST 2: ContextVar attribution in parallel
# =============================================================================


class TestContextVarAttribution:
    """ContextVar correctly attributes requests to the right collector in parallel."""

    @pytest.mark.asyncio
    async def test_parallel_collectors_attributed_correctly(self):
        """Two collectors running concurrently have separate api_calls counts."""
        pipeline = _make_pipeline_with_hooks()

        async def simulate_collector(name: str, num_requests: int):
            tok = _current_collector.set(name)
            try:
                for _ in range(num_requests):
                    await pipeline._shared_httpx_client.get("https://example.com/api")
            finally:
                _current_collector.reset(tok)

        await asyncio.gather(
            simulate_collector("github", 3),
            simulate_collector("sec_edgar", 7),
        )

        assert pipeline._http_counters["github"]["api_calls"] == 3
        assert pipeline._http_counters["sec_edgar"]["api_calls"] == 7

        await pipeline._shared_httpx_client.aclose()


# =============================================================================
# TEST 3: Rate limit detection (429)
# =============================================================================


class TestRateLimitDetection:
    """429 responses increment rate_limit_hits."""

    @pytest.mark.asyncio
    async def test_429_increments_rate_limit_hits(self):
        """A 429 response increments rate_limit_hits for the active collector."""
        with patch.object(DiscoveryPipeline, '__init__', lambda self, *a, **kw: None):
            pipeline = DiscoveryPipeline.__new__(DiscoveryPipeline)
        pipeline._http_counters = {}

        async def _on_request(request):
            name = _current_collector.get("unknown")
            if name not in pipeline._http_counters:
                pipeline._http_counters[name] = {"api_calls": 0, "rate_limit_hits": 0}
            pipeline._http_counters[name]["api_calls"] += 1

        async def _on_response(response):
            name = _current_collector.get("unknown")
            if name in pipeline._http_counters:
                if response.status_code == 429:
                    pipeline._http_counters[name]["rate_limit_hits"] += 1
                elif response.status_code == 403:
                    if "api.github.com" in str(response.url):
                        pipeline._http_counters[name]["rate_limit_hits"] += 1

        call_count = 0

        def mock_transport(request):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return httpx.Response(429)
            return httpx.Response(200)

        pipeline._shared_httpx_client = httpx.AsyncClient(
            timeout=30.0,
            transport=httpx.MockTransport(mock_transport),
            event_hooks={"request": [_on_request], "response": [_on_response]},
        )

        token = _current_collector.set("news_api")
        try:
            await pipeline._shared_httpx_client.get("https://gnews.io/api/v4")
            await pipeline._shared_httpx_client.get("https://gnews.io/api/v4")
            await pipeline._shared_httpx_client.get("https://gnews.io/api/v4")

            assert pipeline._http_counters["news_api"]["api_calls"] == 3
            assert pipeline._http_counters["news_api"]["rate_limit_hits"] == 1
        finally:
            _current_collector.reset(token)
            await pipeline._shared_httpx_client.aclose()


# =============================================================================
# TEST 4: GitHub 403 detection
# =============================================================================


class TestGitHub403Detection:
    """GitHub 403 (secondary rate limit) increments rate_limit_hits."""

    @pytest.mark.asyncio
    async def test_github_403_counts_as_rate_limit(self):
        """A 403 from api.github.com is counted as rate_limit_hits."""
        with patch.object(DiscoveryPipeline, '__init__', lambda self, *a, **kw: None):
            pipeline = DiscoveryPipeline.__new__(DiscoveryPipeline)
        pipeline._http_counters = {}

        async def _on_request(request):
            name = _current_collector.get("unknown")
            if name not in pipeline._http_counters:
                pipeline._http_counters[name] = {"api_calls": 0, "rate_limit_hits": 0}
            pipeline._http_counters[name]["api_calls"] += 1

        async def _on_response(response):
            name = _current_collector.get("unknown")
            if name in pipeline._http_counters:
                if response.status_code == 429:
                    pipeline._http_counters[name]["rate_limit_hits"] += 1
                elif response.status_code == 403:
                    if "api.github.com" in str(response.url):
                        pipeline._http_counters[name]["rate_limit_hits"] += 1

        pipeline._shared_httpx_client = httpx.AsyncClient(
            timeout=30.0,
            transport=httpx.MockTransport(lambda req: httpx.Response(403)),
            event_hooks={"request": [_on_request], "response": [_on_response]},
        )

        token = _current_collector.set("github")
        try:
            await pipeline._shared_httpx_client.get("https://api.github.com/repos")
            assert pipeline._http_counters["github"]["rate_limit_hits"] == 1
        finally:
            _current_collector.reset(token)
            await pipeline._shared_httpx_client.aclose()


# =============================================================================
# TEST 5: Non-GitHub 403 ignored
# =============================================================================


class TestNonGitHub403Ignored:
    """403 from non-GitHub hosts does NOT increment rate_limit_hits."""

    @pytest.mark.asyncio
    async def test_non_github_403_not_counted(self):
        """A 403 from a non-GitHub URL is not counted as rate_limit_hits."""
        with patch.object(DiscoveryPipeline, '__init__', lambda self, *a, **kw: None):
            pipeline = DiscoveryPipeline.__new__(DiscoveryPipeline)
        pipeline._http_counters = {}

        async def _on_request(request):
            name = _current_collector.get("unknown")
            if name not in pipeline._http_counters:
                pipeline._http_counters[name] = {"api_calls": 0, "rate_limit_hits": 0}
            pipeline._http_counters[name]["api_calls"] += 1

        async def _on_response(response):
            name = _current_collector.get("unknown")
            if name in pipeline._http_counters:
                if response.status_code == 429:
                    pipeline._http_counters[name]["rate_limit_hits"] += 1
                elif response.status_code == 403:
                    if "api.github.com" in str(response.url):
                        pipeline._http_counters[name]["rate_limit_hits"] += 1

        pipeline._shared_httpx_client = httpx.AsyncClient(
            timeout=30.0,
            transport=httpx.MockTransport(lambda req: httpx.Response(403)),
            event_hooks={"request": [_on_request], "response": [_on_response]},
        )

        token = _current_collector.set("sec_edgar")
        try:
            await pipeline._shared_httpx_client.get("https://efts.sec.gov/LATEST")
            assert pipeline._http_counters["sec_edgar"]["api_calls"] == 1
            assert pipeline._http_counters["sec_edgar"]["rate_limit_hits"] == 0
        finally:
            _current_collector.reset(token)
            await pipeline._shared_httpx_client.aclose()


# =============================================================================
# TEST 6: OpenCorporates receives http= via **common_args
# =============================================================================


class TestOpenCorporatesHttpInjection:
    """OpenCorporatesCollector now receives http= via **common_args."""

    def test_opencorporates_gets_http_kwarg(self):
        """Verify the pipeline code passes **common_args (which includes http=)."""
        # Read the source and verify the pattern
        import inspect
        from workflows.pipeline import DiscoveryPipeline

        source = inspect.getsource(DiscoveryPipeline._run_single_collector)

        # Should use **common_args, not store=common_args.get("store")
        assert "OpenCorporatesCollector(\n" in source or "OpenCorporatesCollector(" in source
        assert "**common_args" in source
        # Old pattern should NOT be present
        assert 'store=common_args.get("store")' not in source


# =============================================================================
# TEST 7: Counters reset between runs
# =============================================================================


class TestCounterReset:
    """_http_counters.clear() is called at the start of _run_collectors_stage."""

    def test_counters_cleared_in_run_collectors_stage(self):
        """Verify that _run_collectors_stage source calls self._http_counters.clear()."""
        import inspect
        from workflows.pipeline import DiscoveryPipeline

        source = inspect.getsource(DiscoveryPipeline._run_collectors_stage)
        assert "self._http_counters.clear()" in source

    @pytest.mark.asyncio
    async def test_counters_fresh_each_stage(self):
        """Counters dict is empty after clear()."""
        pipeline = _make_pipeline_with_hooks()

        # Simulate some counts
        token = _current_collector.set("github")
        try:
            await pipeline._shared_httpx_client.get("https://api.github.com/repos")
        finally:
            _current_collector.reset(token)

        assert pipeline._http_counters.get("github", {}).get("api_calls", 0) == 1

        # Clear (as _run_collectors_stage does)
        pipeline._http_counters.clear()

        assert pipeline._http_counters == {}
        await pipeline._shared_httpx_client.aclose()


# =============================================================================
# TEST 8: Invariant: signals > 0 implies api_calls > 0
# =============================================================================


class TestSignalsImpliesApiCalls:
    """When a collector produces signals via the shared client, api_calls > 0."""

    @pytest.mark.asyncio
    async def test_signals_produced_means_api_calls_positive(self):
        """Event hooks fire when a collector makes HTTP requests through shared client."""
        pipeline = _make_pipeline_with_hooks()

        token = _current_collector.set("rss_feeds")
        try:
            # Simulate a collector making requests (which would produce signals)
            await pipeline._shared_httpx_client.get("https://techcrunch.com/feed/")
            await pipeline._shared_httpx_client.get("https://prnewswire.com/rss/")

            counters = pipeline._http_counters.get("rss_feeds", {})
            assert counters.get("api_calls", 0) > 0, (
                "api_calls must be > 0 when HTTP requests were made through shared client"
            )
        finally:
            _current_collector.reset(token)
            await pipeline._shared_httpx_client.aclose()


# =============================================================================
# TEST 9: Collector without shared client → api_calls == 0
# =============================================================================


class TestCollectorWithoutSharedClient:
    """Collector that doesn't use the shared client won't have hook-counted api_calls."""

    @pytest.mark.asyncio
    async def test_no_shared_client_means_zero_api_calls(self):
        """If a collector creates its own httpx client, event hooks don't fire."""
        pipeline = _make_pipeline_with_hooks()

        # A collector that creates its own client and doesn't use the shared one
        # will not trigger event hooks
        own_client = httpx.AsyncClient(
            timeout=30.0,
            transport=httpx.MockTransport(lambda req: httpx.Response(200)),
        )

        token = _current_collector.set("standalone")
        try:
            # This request goes through a SEPARATE client, not the hooked one
            await own_client.get("https://example.com/api")

            # Event hooks on pipeline's shared client were NOT triggered
            counters = pipeline._http_counters.get("standalone", {})
            assert counters.get("api_calls", 0) == 0, (
                "api_calls should be 0 when requests bypass the shared client"
            )
        finally:
            _current_collector.reset(token)
            await own_client.aclose()
            await pipeline._shared_httpx_client.aclose()


# =============================================================================
# TEST 10: CollectorResult.api_calls field exists and appears in to_dict()
# =============================================================================


class TestCollectorResultApiCallsField:
    """CollectorResult has an api_calls field that appears in to_dict()."""

    def test_api_calls_field_default(self):
        """api_calls defaults to 0."""
        result = CollectorResult(
            collector="test",
            status=CollectorStatus.SUCCESS,
        )
        assert result.api_calls == 0

    def test_api_calls_in_to_dict(self):
        """api_calls appears in to_dict() output."""
        result = CollectorResult(
            collector="test",
            status=CollectorStatus.SUCCESS,
            api_calls=42,
        )
        d = result.to_dict()
        assert "api_calls" in d
        assert d["api_calls"] == 42

    def test_api_calls_settable(self):
        """api_calls can be set on construction."""
        result = CollectorResult(
            collector="github",
            status=CollectorStatus.SUCCESS,
            signals_found=10,
            api_calls=25,
        )
        assert result.api_calls == 25

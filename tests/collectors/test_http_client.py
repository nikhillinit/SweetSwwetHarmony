"""
Tests for CollectorHttpClient — centralized HTTP primitive.

Covers:
1. Retry on 5xx (delegates to with_retry)
2. 429 rate-limit handling
3. 4xx non-retryable errors propagate
4. POST method with JSON body
5. Telemetry events emitted with execution_id
6. request_json returns parsed JSON
7. request_text returns text
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
import respx

from collectors.http_client import CollectorHttpClient, RunContext, TelemetryLogger
from collectors.retry_strategy import RetryConfig


@pytest.fixture
def run_context():
    return RunContext(execution_id="test-exec-001", dry_run=False)


@pytest.fixture
def fast_retry():
    """Retry config with minimal delays for testing."""
    return RetryConfig(max_retries=2, backoff_base=0.01, backoff_max=0.02, jitter=False)


@pytest.fixture
def semaphore():
    return asyncio.Semaphore(5)


def _make_client(
    httpx_client: httpx.AsyncClient,
    run_context: RunContext,
    retry_config: RetryConfig,
    semaphore: asyncio.Semaphore,
    collector_name: str = "test_collector",
) -> CollectorHttpClient:
    return CollectorHttpClient(
        httpx_client,
        run_context=run_context,
        retry_config=retry_config,
        semaphore=semaphore,
        collector_name=collector_name,
    )


class TestRetryOn5xx:
    """5xx errors should be retried up to max_retries."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_retries_on_500_then_succeeds(self, run_context, fast_retry, semaphore):
        route = respx.get("https://api.example.com/data").mock(
            side_effect=[
                httpx.Response(500),
                httpx.Response(200, json={"ok": True}),
            ]
        )

        async with httpx.AsyncClient() as httpx_client:
            client = _make_client(httpx_client, run_context, fast_retry, semaphore)
            resp = await client.request("GET", "https://api.example.com/data")

        assert resp.status_code == 200
        assert route.call_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_exhausts_retries_on_persistent_5xx(self, run_context, fast_retry, semaphore):
        respx.get("https://api.example.com/data").mock(
            return_value=httpx.Response(503)
        )

        async with httpx.AsyncClient() as httpx_client:
            client = _make_client(httpx_client, run_context, fast_retry, semaphore)
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.request("GET", "https://api.example.com/data")

        assert exc_info.value.response.status_code == 503


class TestRateLimitHandling:
    """429 responses should be retried."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_retries_on_429_then_succeeds(self, run_context, fast_retry, semaphore):
        route = respx.get("https://api.example.com/items").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(200, text="ok"),
            ]
        )

        async with httpx.AsyncClient() as httpx_client:
            client = _make_client(httpx_client, run_context, fast_retry, semaphore)
            resp = await client.request("GET", "https://api.example.com/items")

        assert resp.status_code == 200
        assert route.call_count == 2


class TestNonRetryable4xx:
    """4xx errors (except 429) should propagate immediately."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_404_not_retried(self, run_context, fast_retry, semaphore):
        route = respx.get("https://api.example.com/missing").mock(
            return_value=httpx.Response(404)
        )

        async with httpx.AsyncClient() as httpx_client:
            client = _make_client(httpx_client, run_context, fast_retry, semaphore)
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.request("GET", "https://api.example.com/missing")

        assert exc_info.value.response.status_code == 404
        assert route.call_count == 1  # No retries

    @respx.mock
    @pytest.mark.asyncio
    async def test_403_not_retried(self, run_context, fast_retry, semaphore):
        route = respx.get("https://api.example.com/forbidden").mock(
            return_value=httpx.Response(403)
        )

        async with httpx.AsyncClient() as httpx_client:
            client = _make_client(httpx_client, run_context, fast_retry, semaphore)
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.request("GET", "https://api.example.com/forbidden")

        assert exc_info.value.response.status_code == 403
        assert route.call_count == 1


class TestPostMethod:
    """POST requests with JSON body should work."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_post_with_json_body(self, run_context, fast_retry, semaphore):
        route = respx.post("https://api.example.com/submit").mock(
            return_value=httpx.Response(201, json={"id": "new-123"})
        )

        async with httpx.AsyncClient() as httpx_client:
            client = _make_client(httpx_client, run_context, fast_retry, semaphore)
            resp = await client.request(
                "POST",
                "https://api.example.com/submit",
                json={"name": "test"},
            )

        assert resp.status_code == 201
        assert resp.json() == {"id": "new-123"}
        assert route.call_count == 1


class TestTelemetry:
    """Telemetry events should include execution_id."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_telemetry_logged_with_execution_id(
        self, run_context, fast_retry, semaphore, caplog
    ):
        respx.get("https://api.example.com/data").mock(
            return_value=httpx.Response(200, json={})
        )

        with caplog.at_level(logging.DEBUG, logger="collectors.http_client.telemetry"):
            async with httpx.AsyncClient() as httpx_client:
                client = _make_client(httpx_client, run_context, fast_retry, semaphore)
                await client.request("GET", "https://api.example.com/data")

        # Check that execution_id appears in log records
        assert any("test-exec-001" in record.message for record in caplog.records)

        # Check attempt log (DEBUG)
        attempt_logs = [r for r in caplog.records if "http_attempt" in r.message]
        assert len(attempt_logs) >= 1

        # Check request completion log (INFO)
        request_logs = [r for r in caplog.records if "http_request" in r.message]
        assert len(request_logs) == 1
        assert "status=200" in request_logs[0].message

    def test_telemetry_logger_execution_id(self):
        """TelemetryLogger stores execution_id."""
        tl = TelemetryLogger(execution_id="exec-42")
        assert tl.execution_id == "exec-42"


class TestRequestJson:
    """request_json should return parsed JSON."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_request_json_returns_dict(self, run_context, fast_retry, semaphore):
        respx.get("https://api.example.com/json").mock(
            return_value=httpx.Response(200, json={"items": [1, 2, 3]})
        )

        async with httpx.AsyncClient() as httpx_client:
            client = _make_client(httpx_client, run_context, fast_retry, semaphore)
            result = await client.request_json("GET", "https://api.example.com/json")

        assert result == {"items": [1, 2, 3]}


class TestRequestText:
    """request_text should return text content."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_request_text_returns_string(self, run_context, fast_retry, semaphore):
        respx.get("https://api.example.com/text").mock(
            return_value=httpx.Response(200, text="Hello, world!")
        )

        async with httpx.AsyncClient() as httpx_client:
            client = _make_client(httpx_client, run_context, fast_retry, semaphore)
            result = await client.request_text("GET", "https://api.example.com/text")

        assert result == "Hello, world!"


class TestRunContext:
    """RunContext dataclass tests."""

    def test_defaults(self):
        ctx = RunContext(execution_id="abc")
        assert ctx.execution_id == "abc"
        assert ctx.dry_run is False

    def test_dry_run(self):
        ctx = RunContext(execution_id="abc", dry_run=True)
        assert ctx.dry_run is True


class TestExecutionIdProperty:
    """CollectorHttpClient.execution_id exposes the context value."""

    def test_execution_id_property(self, run_context):
        client_obj = CollectorHttpClient(
            httpx.AsyncClient(),
            run_context=run_context,
            collector_name="test",
        )
        assert client_obj.execution_id == "test-exec-001"

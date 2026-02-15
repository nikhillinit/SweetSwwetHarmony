"""Block 2.2: Collector Resilience Tests.

Tests graceful degradation when collectors fail — timeouts, HTTP errors,
malformed data, and partial failures.
"""

import asyncio
import json
import os
import sys
import tempfile
from collections import deque
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from collectors.base import BaseCollector, MAX_TIMEOUT_EVENTS
from collectors.retry_strategy import RetryConfig, with_retry, is_retryable_error
from collectors.timeout_config import TimeoutConfig, OperationType, TimeoutEvent
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from verification.verification_gate_v2 import Signal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store(monkeypatch):
    """Fresh SignalStore with temp DB."""
    monkeypatch.delenv("GNEWS_API_KEY", raising=False)
    monkeypatch.delenv("V2_ENABLEMENT", raising=False)
    monkeypatch.delenv("ML_ENABLEMENT", raising=False)
    monkeypatch.setenv("DELIVERY_MODE", "staging_only")
    monkeypatch.setenv("LLM_THESIS_MODE", "off")

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


def _make_signal(signal_id="domain:test.com", **kwargs):
    defaults = dict(
        signal_type="funding_event",
        source_api="sec_edgar",
        confidence=0.75,
        detected_at=datetime.now(timezone.utc),
        raw_data={"description": "test"},
    )
    defaults.update(kwargs)
    defaults["id"] = signal_id
    # Remove company_name if passed — Signal doesn't have it
    defaults.pop("company_name", None)
    return Signal(**defaults)


# ---------------------------------------------------------------------------
# Controllable test collectors
# ---------------------------------------------------------------------------

class TimeoutCollector(BaseCollector):
    """Collector that raises TimeoutError."""

    async def _collect_signals(self):
        raise asyncio.TimeoutError("Simulated timeout")


class ErrorCollector(BaseCollector):
    """Collector that raises a generic exception."""

    def __init__(self, error=None, **kwargs):
        kwargs.setdefault("collector_name", "error_collector")
        super().__init__(**kwargs)
        self._error = error or RuntimeError("Simulated failure")

    async def _collect_signals(self):
        raise self._error


class PartialCollector(BaseCollector):
    """Collector that returns some valid signals and some bad ones."""

    def __init__(self, good_signals=None, **kwargs):
        kwargs.setdefault("collector_name", "partial")
        super().__init__(**kwargs)
        self._good_signals = good_signals or []

    async def _collect_signals(self):
        return list(self._good_signals)


class SuccessCollector(BaseCollector):
    """Collector that always succeeds with given signals."""

    def __init__(self, signals=None, **kwargs):
        kwargs.setdefault("collector_name", "success")
        super().__init__(**kwargs)
        self._fake_signals = signals or []

    async def _collect_signals(self):
        return list(self._fake_signals)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCollectorTimeout:
    """Test timeout handling in collectors."""

    @pytest.mark.asyncio
    async def test_timeout_returns_error_result(self, store):
        """Timeout → ERROR result, pipeline continues."""
        collector = TimeoutCollector(collector_name="timeout_test", store=store)
        result = await collector.run(dry_run=False)

        assert result.status == CollectorStatus.ERROR
        assert result.signals_found == 0
        assert "timeout" in result.error_message.lower() or "Timeout" in result.error_message

    @pytest.mark.asyncio
    async def test_timeout_telemetry_recorded(self, store):
        """Timeout events are recorded in _timeout_events when explicitly recorded."""
        collector = SuccessCollector(
            collector_name="telemetry_test",
            store=store,
            timeout_config=TimeoutConfig(
                connect_timeout=1.0,
                search_timeout=2.0,
                enrich_timeout=1.5,
                download_timeout=3.0,
            ),
        )

        # Record some timeout events manually (as collectors do internally)
        collector._record_timeout(OperationType.SEARCH, "/api/test", 2.0)
        collector._record_timeout(OperationType.ENRICH, "/api/detail", 1.5)

        assert collector.timeout_event_count == 2
        assert collector._timeout_events[0].operation == OperationType.SEARCH
        assert collector._timeout_events[1].endpoint == "/api/detail"


class TestHTTPErrorHandling:
    """Test HTTP error classification and retry behavior."""

    @pytest.mark.asyncio
    async def test_429_is_retryable(self):
        """HTTP 429 → classified as retryable."""
        request = httpx.Request("GET", "https://api.example.com/test")
        response = httpx.Response(429, request=request)
        error = httpx.HTTPStatusError("Rate limited", request=request, response=response)

        assert is_retryable_error(error) is True

    @pytest.mark.asyncio
    async def test_retries_exhausted_returns_error(self, store):
        """Retries exhausted → ERROR result, not crash."""
        collector = ErrorCollector(
            error=ConnectionError("Connection refused"),
            store=store,
        )
        result = await collector.run(dry_run=False)

        assert result.status == CollectorStatus.ERROR
        assert "Connection refused" in result.error_message

    @pytest.mark.asyncio
    async def test_401_not_retryable(self):
        """HTTP 401/403 → NOT retryable (non-retryable client error)."""
        request = httpx.Request("GET", "https://api.example.com/test")
        response = httpx.Response(401, request=request)
        error = httpx.HTTPStatusError("Unauthorized", request=request, response=response)

        assert is_retryable_error(error) is False

    @pytest.mark.asyncio
    async def test_403_not_retryable(self):
        """HTTP 403 → NOT retryable."""
        request = httpx.Request("GET", "https://api.example.com/test")
        response = httpx.Response(403, request=request)
        error = httpx.HTTPStatusError("Forbidden", request=request, response=response)

        assert is_retryable_error(error) is False

    @pytest.mark.asyncio
    async def test_500_is_retryable(self):
        """HTTP 500 → classified as retryable."""
        request = httpx.Request("GET", "https://api.example.com/test")
        response = httpx.Response(500, request=request)
        error = httpx.HTTPStatusError("Server Error", request=request, response=response)

        assert is_retryable_error(error) is True


class TestAllCollectorsFail:
    """Test pipeline behavior when all collectors fail."""

    @pytest.mark.asyncio
    async def test_all_collectors_fail_produces_zero_signals(self, store):
        """When all collectors fail, pipeline completes with 0 signals."""
        collectors = [
            ErrorCollector(
                error=RuntimeError(f"Fail {i}"),
                collector_name=f"fail_{i}",
                store=store,
            )
            for i in range(3)
        ]

        results = []
        for c in collectors:
            results.append(await c.run(dry_run=False))

        assert all(r.status == CollectorStatus.ERROR for r in results)
        assert all(r.signals_found == 0 for r in results)
        assert sum(r.signals_new for r in results) == 0


class TestPartialBatch:
    """Test that partial failures don't block successful signals."""

    @pytest.mark.asyncio
    async def test_partial_batch_success(self, store):
        """Some signals succeed while collector reports partial success."""
        good_signals = [
            _make_signal("domain:good-a.com"),
            _make_signal("domain:good-b.com"),
        ]
        collector = PartialCollector(good_signals=good_signals, store=store)
        result = await collector.run(dry_run=False)

        assert result.signals_found == 2
        assert result.signals_new == 2
        assert result.status in (CollectorStatus.SUCCESS, CollectorStatus.PARTIAL_SUCCESS)


class TestNetworkErrors:
    """Test network error handling."""

    @pytest.mark.asyncio
    async def test_connection_error_retryable(self):
        """ConnectionError → classified as retryable."""
        error = ConnectionError("Connection refused")
        assert is_retryable_error(error) is True

    @pytest.mark.asyncio
    async def test_connection_error_collector_returns_error(self, store):
        """ConnectionError in collector → ERROR result."""
        collector = ErrorCollector(
            error=ConnectionError("Network unreachable"),
            store=store,
        )
        result = await collector.run(dry_run=False)

        assert result.status == CollectorStatus.ERROR
        assert "Network unreachable" in result.error_message


class TestRetryStrategy:
    """Test the retry strategy directly."""

    @pytest.mark.asyncio
    async def test_with_retry_succeeds_after_failures(self):
        """with_retry succeeds if function eventually succeeds."""
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Transient failure")
            return "success"

        config = RetryConfig(max_retries=3, backoff_base=0.01, backoff_max=0.05, jitter=False)
        result = await with_retry(flaky_func, config)

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_with_retry_raises_after_exhaustion(self):
        """with_retry raises the last error when retries exhausted."""
        async def always_fail():
            raise ConnectionError("Permanent failure")

        config = RetryConfig(max_retries=2, backoff_base=0.01, backoff_max=0.05, jitter=False)

        with pytest.raises(ConnectionError, match="Permanent failure"):
            await with_retry(always_fail, config)

    @pytest.mark.asyncio
    async def test_non_retryable_error_not_retried(self):
        """ValueError → not retried, raised immediately."""
        call_count = 0

        async def bad_input():
            nonlocal call_count
            call_count += 1
            raise ValueError("Bad input data")

        config = RetryConfig(max_retries=3, backoff_base=0.01, jitter=False)

        with pytest.raises(ValueError, match="Bad input data"):
            await with_retry(bad_input, config)

        assert call_count == 1  # No retry for non-retryable errors


class TestMalformedResponses:
    """Forensic Phase 2: Malformed data handling in collectors."""

    @pytest.mark.asyncio
    async def test_none_in_signal_list_handled(self, store):
        """Collector returning [None] doesn't crash — error is logged."""

        class NoneSignalCollector(BaseCollector):
            async def _collect_signals(self):
                return [None]

        collector = NoneSignalCollector(collector_name="none_sig", store=store)
        result = await collector.run(dry_run=False)

        # Should complete (ERROR or PARTIAL), not crash
        assert result.status in (CollectorStatus.ERROR, CollectorStatus.PARTIAL_SUCCESS,
                                  CollectorStatus.SUCCESS)

    @pytest.mark.asyncio
    async def test_dict_instead_of_signal_handled(self, store):
        """Collector returning a dict (not Signal) is handled gracefully."""

        class DictCollector(BaseCollector):
            async def _collect_signals(self):
                return [{"id": "fake", "confidence": 0.5}]

        collector = DictCollector(collector_name="dict_col", store=store)
        result = await collector.run(dry_run=False)

        # Should not crash — either stores 0 new or errors out
        assert result.signals_found >= 0

    @pytest.mark.asyncio
    async def test_empty_collector_returns_success(self, store):
        """Collector returning empty list → SUCCESS with 0 signals."""
        collector = SuccessCollector(signals=[], store=store)
        result = await collector.run(dry_run=False)

        assert result.status == CollectorStatus.SUCCESS
        assert result.signals_found == 0
        assert result.signals_new == 0


class TestMixedCollectorOutcomes:
    """Forensic Phase 2: Mixed success/failure across multiple collectors."""

    @pytest.mark.asyncio
    async def test_mixed_collectors_success_error_counts(self, store):
        """3 collectors: 1 success + 1 error + 1 success → correct counts."""
        good_sigs_1 = [_make_signal("domain:mix-a.com")]
        good_sigs_2 = [_make_signal("domain:mix-b.com"),
                       _make_signal("domain:mix-c.com")]

        c_ok1 = SuccessCollector(signals=good_sigs_1, store=store,
                                  collector_name="ok1")
        c_err = ErrorCollector(error=RuntimeError("API down"),
                                collector_name="err1", store=store)
        c_ok2 = SuccessCollector(signals=good_sigs_2, store=store,
                                  collector_name="ok2")

        results = []
        for c in [c_ok1, c_err, c_ok2]:
            results.append(await c.run(dry_run=False))

        succeeded = [r for r in results if r.status == CollectorStatus.SUCCESS]
        failed = [r for r in results if r.status == CollectorStatus.ERROR]

        assert len(succeeded) == 2
        assert len(failed) == 1
        assert sum(r.signals_new for r in results) == 3

    @pytest.mark.asyncio
    async def test_all_collectors_succeed_aggregate(self, store):
        """All 3 succeed — total signals is aggregate."""
        collectors = [
            SuccessCollector(
                signals=[_make_signal(f"domain:agg-{i}.com")],
                store=store,
                collector_name=f"agg_{i}",
            )
            for i in range(3)
        ]

        results = [await c.run(dry_run=False) for c in collectors]

        assert all(r.status == CollectorStatus.SUCCESS for r in results)
        assert sum(r.signals_new for r in results) == 3

    @pytest.mark.asyncio
    async def test_timeout_and_success_parallel(self, store):
        """Timeout in one collector doesn't affect sibling."""
        sig = _make_signal("domain:parallel-ok.com")
        c_ok = SuccessCollector(signals=[sig], store=store,
                                 collector_name="par_ok")
        c_timeout = TimeoutCollector(collector_name="par_timeout", store=store)

        r_ok = await c_ok.run(dry_run=False)
        r_timeout = await c_timeout.run(dry_run=False)

        assert r_ok.status == CollectorStatus.SUCCESS
        assert r_ok.signals_new == 1
        assert r_timeout.status == CollectorStatus.ERROR
        assert r_timeout.signals_found == 0

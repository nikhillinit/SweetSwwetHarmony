"""
Integration tests for Phase C: Pipeline Operations.

Tests:
- C6.1: httpx.RequestError triggers retry
- C6.2: timeout config flows from pipeline to collectors
- C6.3: timeout telemetry captures operation + endpoint
- C6.4: thesis accuracy regression
"""

import asyncio
from collections import deque
from datetime import datetime, timezone
from unittest import mock

import httpx
import pytest

from collectors.base import BaseCollector
from collectors.retry_strategy import with_retry, RetryConfig, is_retryable_error
from collectors.timeout_config import TimeoutConfig, OperationType, TimeoutEvent
from workflows.pipeline import PipelineConfig
from verification.verification_gate_v2 import Signal


class MockCollector(BaseCollector):
    """Mock collector for integration testing."""

    async def _collect_signals(self):
        return []


class TestRetryIntegration:
    """C6.1: httpx.RequestError triggers retry integration tests."""

    @pytest.mark.asyncio
    async def test_connect_error_triggers_retry(self):
        """ConnectError should trigger retry and eventually succeed."""
        call_count = 0

        async def flaky_connect():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.ConnectError("Connection refused")
            return {"status": "success"}

        config = RetryConfig(max_retries=3, jitter=False, backoff_base=0.01)
        result = await with_retry(flaky_connect, config)

        assert result == {"status": "success"}
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_read_timeout_triggers_retry(self):
        """ReadTimeout should trigger retry and eventually succeed."""
        call_count = 0

        async def flaky_read():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ReadTimeout("Read timed out")
            return {"data": "loaded"}

        config = RetryConfig(max_retries=3, jitter=False, backoff_base=0.01)
        result = await with_retry(flaky_read, config)

        assert result == {"data": "loaded"}
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_all_httpx_transport_errors_retryable(self):
        """All httpx transport errors should be classified as retryable."""
        transport_errors = [
            httpx.ConnectError("Connection refused"),
            httpx.ConnectTimeout("Connect timed out"),
            httpx.ReadTimeout("Read timed out"),
            httpx.WriteTimeout("Write timed out"),
            httpx.PoolTimeout("Pool timeout"),
            httpx.ReadError("Read failed"),
        ]

        for error in transport_errors:
            assert is_retryable_error(error) is True, (
                f"{type(error).__name__} should be retryable"
            )


class TestTimeoutConfigFlow:
    """C6.2: Timeout config flows from pipeline to collectors."""

    def test_timeout_config_from_pipeline_config(self):
        """TimeoutConfig correctly extracts values from PipelineConfig."""
        pipeline_config = PipelineConfig(
            collector_connect_timeout=15.0,
            collector_search_timeout=90.0,
            collector_enrich_timeout=60.0,
            collector_download_timeout=180.0,
        )

        timeout_config = TimeoutConfig.from_pipeline_config(pipeline_config)

        assert timeout_config.connect_timeout == 15.0
        assert timeout_config.search_timeout == 90.0
        assert timeout_config.enrich_timeout == 60.0
        assert timeout_config.download_timeout == 180.0

    def test_timeout_config_passed_to_collector(self):
        """Collector receives and uses TimeoutConfig."""
        timeout_config = TimeoutConfig(
            connect_timeout=10.0,
            search_timeout=60.0,
            enrich_timeout=45.0,
            download_timeout=90.0,
        )

        collector = MockCollector(
            collector_name="test",
            timeout_config=timeout_config,
        )

        assert collector.timeout_config is timeout_config

        # Verify timeout selection works
        search_timeout = collector._get_timeout_for_operation(OperationType.SEARCH)
        assert search_timeout.read == 60.0

        enrich_timeout = collector._get_timeout_for_operation(OperationType.ENRICH)
        assert enrich_timeout.read == 45.0

    def test_pipeline_config_env_integration(self):
        """PipelineConfig loads timeout values from environment."""
        import os

        env = {
            "COLLECTOR_CONNECT_TIMEOUT": "20.0",
            "COLLECTOR_SEARCH_TIMEOUT": "120.0",
        }

        with mock.patch.dict(os.environ, env, clear=False):
            config = PipelineConfig.from_env()

            assert config.collector_connect_timeout == 20.0
            assert config.collector_search_timeout == 120.0


class TestTimeoutTelemetry:
    """C6.3: Timeout telemetry captures operation + endpoint."""

    def test_telemetry_event_captures_all_fields(self):
        """TimeoutEvent captures collector, operation, endpoint, timeout."""
        event = TimeoutEvent(
            collector="github",
            operation=OperationType.SEARCH,
            endpoint="/search/repositories",
            timeout_seconds=60.0,
            occurred_at=datetime.now(timezone.utc),
        )

        assert event.collector == "github"
        assert event.operation == OperationType.SEARCH
        assert event.endpoint == "/search/repositories"
        assert event.timeout_seconds == 60.0

    def test_collector_records_timeout_event(self):
        """Collector's _record_timeout adds event to deque."""
        collector = MockCollector(collector_name="test_collector")

        collector._record_timeout(
            operation=OperationType.ENRICH,
            endpoint="/api/v1/company/123",
            timeout_seconds=45.0,
        )

        assert len(collector._timeout_events) == 1
        event = collector._timeout_events[0]
        assert event.collector == "test_collector"
        assert event.operation == OperationType.ENRICH
        assert event.endpoint == "/api/v1/company/123"

    def test_telemetry_buffer_bounded(self):
        """Telemetry buffer doesn't exceed maxlen."""
        collector = MockCollector(collector_name="test")

        # Add more events than buffer size
        for i in range(150):
            collector._record_timeout(
                operation=OperationType.SEARCH,
                endpoint=f"/endpoint/{i}",
                timeout_seconds=30.0,
            )

        # Buffer should be bounded at 100
        assert len(collector._timeout_events) == 100

        # Oldest events dropped (FIFO)
        first_event = collector._timeout_events[0]
        assert first_event.endpoint == "/endpoint/50"


class TestThesisAccuracyRegression:
    """C6.4: Thesis accuracy regression test."""

    @pytest.mark.asyncio
    async def test_thesis_accuracy_regression(self):
        """Keyword matcher maintains >= 88% accuracy on thesis_sample.jsonl."""
        from utils.thesis_evaluator import KeywordEvaluator

        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate("datasets/thesis_sample.jsonl")

        assert result.accuracy >= 0.88, (
            f"Accuracy {result.accuracy:.1%} below 88% regression threshold"
        )

        # Verify no errors during evaluation
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_thesis_no_rejected_qualified_confusion(self):
        """Never reject a truly qualified prospect (zero FN for QUALIFIED→REJECTED)."""
        from utils.thesis_evaluator import KeywordEvaluator

        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate("datasets/thesis_sample.jsonl")

        confusion = result.confusion_matrix
        qualified_rejected = confusion.get("QUALIFIED", {}).get("REJECTED", 0)

        assert qualified_rejected == 0, (
            f"Critical: {qualified_rejected} qualified prospects were rejected"
        )

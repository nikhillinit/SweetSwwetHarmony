"""
Tests for BaseCollector timeout configuration and telemetry.

Phase C3-C4: Timeout configuration wiring and telemetry instrumentation.
"""

from collections import deque
from datetime import datetime, timezone
from unittest import mock

import httpx
import pytest

from collectors.base import BaseCollector
from collectors.timeout_config import TimeoutConfig, OperationType, TimeoutEvent
from verification.verification_gate_v2 import Signal


class MockCollector(BaseCollector):
    """Mock collector for testing timeout behavior."""

    async def _collect_signals(self):
        return []


class TestBaseCollectorTimeoutConfig:
    """Tests for timeout_config parameter in BaseCollector."""

    def test_timeout_config_default_none(self):
        """C3.2: BaseCollector accepts timeout_config, defaults to None."""
        collector = MockCollector(collector_name="test")
        # Default is None (uses default httpx timeout behavior)
        assert collector.timeout_config is None

    def test_timeout_config_custom_value(self):
        """C3.2: BaseCollector stores custom timeout_config."""
        config = TimeoutConfig(
            connect_timeout=5.0,
            search_timeout=30.0,
            enrich_timeout=20.0,
            download_timeout=60.0,
        )
        collector = MockCollector(collector_name="test", timeout_config=config)

        assert collector.timeout_config is config
        assert collector.timeout_config.connect_timeout == 5.0

    def test_get_timeout_for_operation_with_config(self):
        """C3.3: _get_timeout_for_operation returns httpx.Timeout for operation."""
        config = TimeoutConfig(
            connect_timeout=10.0,
            search_timeout=60.0,
            enrich_timeout=45.0,
            download_timeout=90.0,
        )
        collector = MockCollector(collector_name="test", timeout_config=config)

        timeout = collector._get_timeout_for_operation(OperationType.SEARCH)
        assert timeout.read == 60.0

        timeout = collector._get_timeout_for_operation(OperationType.ENRICH)
        assert timeout.read == 45.0

    def test_get_timeout_for_operation_no_config_returns_default(self):
        """_get_timeout_for_operation returns default when no config."""
        collector = MockCollector(collector_name="test")

        timeout = collector._get_timeout_for_operation(OperationType.SEARCH)
        assert timeout == 30.0  # Default float timeout


class TestBaseCollectorTimeoutTelemetry:
    """Tests for timeout event tracking in BaseCollector."""

    def test_timeout_events_deque_initialized(self):
        """C4.2: BaseCollector initializes bounded timeout_events deque."""
        collector = MockCollector(collector_name="test")

        assert hasattr(collector, "_timeout_events")
        assert isinstance(collector._timeout_events, deque)
        assert collector._timeout_events.maxlen == 100  # Bounded

    def test_timeout_events_bounded_size(self):
        """C4.2: timeout_events deque doesn't exceed maxlen."""
        collector = MockCollector(collector_name="test")

        # Add more events than maxlen
        for i in range(150):
            collector._timeout_events.append(
                TimeoutEvent(
                    collector="test",
                    operation=OperationType.SEARCH,
                    endpoint=f"/endpoint/{i}",
                    timeout_seconds=30.0,
                    occurred_at=datetime.now(timezone.utc),
                )
            )

        assert len(collector._timeout_events) == 100

    def test_record_timeout_adds_event(self):
        """C4.3: _record_timeout adds TimeoutEvent to deque."""
        collector = MockCollector(collector_name="test")

        collector._record_timeout(
            operation=OperationType.ENRICH,
            endpoint="/api/data",
            timeout_seconds=45.0,
        )

        assert len(collector._timeout_events) == 1
        event = collector._timeout_events[0]
        assert event.collector == "test"
        assert event.operation == OperationType.ENRICH
        assert event.endpoint == "/api/data"
        assert event.timeout_seconds == 45.0

    def test_timeout_event_count_property(self):
        """C4.6: timeout_event_count returns number of recorded events."""
        collector = MockCollector(collector_name="test")

        assert collector.timeout_event_count == 0

        collector._record_timeout(OperationType.SEARCH, "/search", 60.0)
        collector._record_timeout(OperationType.ENRICH, "/detail", 45.0)

        assert collector.timeout_event_count == 2


class TestBaseCollectorHttpGetWithOperation:
    """Tests for _http_get with operation parameter."""

    @pytest.mark.asyncio
    async def test_http_get_accepts_operation(self):
        """C3.3: _http_get accepts operation parameter for timeout selection."""
        config = TimeoutConfig(
            connect_timeout=10.0,
            search_timeout=60.0,
            enrich_timeout=45.0,
        )
        collector = MockCollector(collector_name="test", timeout_config=config)

        # Mock httpx.AsyncClient to capture timeout
        captured_timeout = None

        class MockClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, **kwargs):
                nonlocal captured_timeout
                captured_timeout = kwargs.get("timeout")
                # Return mock response
                response = mock.Mock()
                response.raise_for_status = mock.Mock()
                response.json = mock.Mock(return_value={"status": "ok"})
                return response

        with mock.patch("httpx.AsyncClient", return_value=MockClient()):
            result = await collector._http_get(
                "https://api.example.com/search",
                operation=OperationType.SEARCH,
            )

        assert result == {"status": "ok"}
        # Should have used search timeout
        assert captured_timeout is not None
        assert captured_timeout.read == 60.0

    @pytest.mark.asyncio
    async def test_http_get_default_timeout_without_config(self):
        """_http_get uses default timeout when no timeout_config."""
        collector = MockCollector(collector_name="test")

        captured_timeout = None

        class MockClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, **kwargs):
                nonlocal captured_timeout
                captured_timeout = kwargs.get("timeout")
                response = mock.Mock()
                response.raise_for_status = mock.Mock()
                response.json = mock.Mock(return_value={})
                return response

        with mock.patch("httpx.AsyncClient", return_value=MockClient()):
            await collector._http_get("https://api.example.com/data")

        # Default float timeout when no config
        assert captured_timeout == 30.0

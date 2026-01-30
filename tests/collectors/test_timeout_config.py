"""
Tests for timeout configuration.

Phase C2-C4: TimeoutConfig, OperationType enum, TimeoutEvent, and telemetry.
"""

from collections import deque
from datetime import datetime, timezone

import httpx
import pytest

from collectors.timeout_config import (
    TimeoutConfig,
    OperationType,
    TimeoutEvent,
)
from workflows.pipeline import PipelineConfig


class TestOperationType:
    """Tests for OperationType enum."""

    def test_operation_type_values(self):
        """C3.1: OperationType has expected values."""
        assert OperationType.SEARCH == "search"
        assert OperationType.ENRICH == "enrich"
        assert OperationType.DOWNLOAD == "download"

    def test_operation_type_is_string(self):
        """OperationType values can be used as strings."""
        op = OperationType.SEARCH
        # Enum value is a string and can be compared
        assert op.value == "search"
        assert op == "search"  # str Enum allows direct comparison


class TestTimeoutConfig:
    """Tests for TimeoutConfig dataclass."""

    def test_default_values(self):
        """C2.1: Default timeout values are sensible."""
        config = TimeoutConfig()

        assert config.connect_timeout == 10.0
        assert config.search_timeout == 60.0
        assert config.enrich_timeout == 45.0
        assert config.download_timeout == 90.0

    def test_custom_values(self):
        """Custom timeout values are respected."""
        config = TimeoutConfig(
            connect_timeout=5.0,
            search_timeout=30.0,
            enrich_timeout=20.0,
            download_timeout=120.0,
        )

        assert config.connect_timeout == 5.0
        assert config.search_timeout == 30.0
        assert config.enrich_timeout == 20.0
        assert config.download_timeout == 120.0

    def test_to_httpx_timeout_default(self):
        """C2.2: to_httpx_timeout returns appropriate timeout for default operation."""
        config = TimeoutConfig()
        timeout = config.to_httpx_timeout()

        assert timeout.connect == 10.0
        assert timeout.read == 60.0  # Default uses search_timeout
        assert timeout.write == 30.0
        assert timeout.pool == 10.0

    def test_to_httpx_timeout_search(self):
        """C2.2: to_httpx_timeout uses search_timeout for search operation."""
        config = TimeoutConfig(search_timeout=120.0)
        timeout = config.to_httpx_timeout(OperationType.SEARCH)

        assert timeout.read == 120.0

    def test_to_httpx_timeout_enrich(self):
        """C2.2: to_httpx_timeout uses enrich_timeout for enrich operation."""
        config = TimeoutConfig(enrich_timeout=45.0)
        timeout = config.to_httpx_timeout(OperationType.ENRICH)

        assert timeout.read == 45.0

    def test_to_httpx_timeout_download(self):
        """C2.2: to_httpx_timeout uses download_timeout for download operation."""
        config = TimeoutConfig(download_timeout=180.0)
        timeout = config.to_httpx_timeout(OperationType.DOWNLOAD)

        assert timeout.read == 180.0

    def test_to_httpx_timeout_unknown_uses_search(self):
        """Unknown operation type defaults to search_timeout."""
        config = TimeoutConfig(search_timeout=60.0)
        # Pass a string that's not a recognized operation
        timeout = config.to_httpx_timeout("unknown")

        assert timeout.read == 60.0

    def test_from_pipeline_config(self):
        """C2.3: from_pipeline_config extracts timeout values."""
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


class TestTimeoutEvent:
    """Tests for TimeoutEvent dataclass."""

    def test_timeout_event_creation(self):
        """C4.1: TimeoutEvent captures all required fields."""
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
        assert event.occurred_at is not None

    def test_timeout_event_with_enrich_operation(self):
        """TimeoutEvent works with enrich operation."""
        event = TimeoutEvent(
            collector="sec_edgar",
            operation=OperationType.ENRICH,
            endpoint="/cgi-bin/browse-edgar",
            timeout_seconds=45.0,
            occurred_at=datetime.now(timezone.utc),
        )

        assert event.operation == OperationType.ENRICH


class TestTimeoutEventBuffer:
    """Tests for bounded timeout event buffer."""

    def test_bounded_deque_limits_size(self):
        """C4.2: Bounded deque prevents memory blowup."""
        MAX_EVENTS = 100
        events: deque[TimeoutEvent] = deque(maxlen=MAX_EVENTS)

        # Add more events than the limit
        for i in range(150):
            events.append(
                TimeoutEvent(
                    collector="test",
                    operation=OperationType.SEARCH,
                    endpoint=f"/endpoint/{i}",
                    timeout_seconds=30.0,
                    occurred_at=datetime.now(timezone.utc),
                )
            )

        # Should only have MAX_EVENTS
        assert len(events) == MAX_EVENTS

        # Oldest events should be dropped (FIFO)
        assert events[0].endpoint == "/endpoint/50"
        assert events[-1].endpoint == "/endpoint/149"

    def test_bounded_deque_preserves_recent(self):
        """Bounded deque preserves most recent events."""
        events: deque[TimeoutEvent] = deque(maxlen=10)

        for i in range(20):
            events.append(
                TimeoutEvent(
                    collector="test",
                    operation=OperationType.ENRICH,
                    endpoint=f"/endpoint/{i}",
                    timeout_seconds=30.0,
                    occurred_at=datetime.now(timezone.utc),
                )
            )

        # Most recent 10 events (10-19)
        assert len(events) == 10
        endpoints = [e.endpoint for e in events]
        assert endpoints[0] == "/endpoint/10"
        assert endpoints[-1] == "/endpoint/19"

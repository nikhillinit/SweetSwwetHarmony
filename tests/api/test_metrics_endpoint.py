"""M2.3 — Tests for the OpenMetrics /metrics endpoint.

Tests cover:
1. GET /api/v1/health/metrics returns 200 with OpenMetrics content type
2. Counter lines match OpenMetrics format
3. Timer lines include count, total_ms, avg_ms
4. Empty metrics returns valid (minimal) OpenMetrics response
5. Ops gauges included when ops collector is available
"""

import sqlite3
import pytest
from unittest.mock import patch, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.health import router, OPENMETRICS_CONTENT_TYPE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    """Minimal FastAPI app with health router mounted."""
    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def client(app):
    """TestClient with instrumentation metrics reset and ops collector disabled."""
    from utils.instrumentation import metrics
    metrics.reset()
    with patch("api.routers.health._get_ops_collector", return_value=None):
        yield TestClient(app)
    metrics.reset()


@pytest.fixture
def client_with_ops(app):
    """TestClient with a mock OpsMetricsCollector returning known values."""
    from utils.instrumentation import metrics
    metrics.reset()

    mock_snapshot = MagicMock()
    mock_snapshot.overall_health_pct = 95.5
    mock_snapshot.extractions_24h = 42
    mock_snapshot.open_incidents = 1

    mock_collector = MagicMock()
    mock_collector.collect.return_value = mock_snapshot

    with patch("api.routers.health._get_ops_collector", return_value=mock_collector):
        yield TestClient(app)
    metrics.reset()


# ---------------------------------------------------------------------------
# Basic response tests
# ---------------------------------------------------------------------------


class TestOpenMetricsBasic:
    """Verify content type, status code, and minimal structure."""

    def test_returns_200(self, client):
        resp = client.get("/health/metrics")
        assert resp.status_code == 200

    def test_content_type_is_openmetrics(self, client):
        resp = client.get("/health/metrics")
        assert resp.headers["content-type"] == OPENMETRICS_CONTENT_TYPE

    def test_empty_metrics_returns_valid_response(self, client):
        resp = client.get("/health/metrics")
        body = resp.text
        assert body.strip().endswith("# EOF")

    def test_empty_metrics_has_no_counter_lines(self, client):
        resp = client.get("/health/metrics")
        body = resp.text
        # Only # EOF (and possibly blank lines) when no metrics recorded
        lines = [l for l in body.strip().splitlines() if not l.startswith("#")]
        assert lines == []


# ---------------------------------------------------------------------------
# Counter format tests
# ---------------------------------------------------------------------------


class TestCounterFormat:
    """Verify counter lines match OpenMetrics exposition format."""

    def test_counter_line_format(self, client):
        from utils.instrumentation import metrics
        metrics.increment("triage.approve.success", 5)

        resp = client.get("/health/metrics")
        body = resp.text
        assert '# TYPE discovery_counter counter' in body
        assert 'discovery_counter{name="triage.approve.success"} 5' in body

    def test_multiple_counters_sorted(self, client):
        from utils.instrumentation import metrics
        metrics.increment("z_counter", 1)
        metrics.increment("a_counter", 2)

        resp = client.get("/health/metrics")
        body = resp.text
        a_pos = body.index('name="a_counter"')
        z_pos = body.index('name="z_counter"')
        assert a_pos < z_pos, "Counters should be sorted by name"

    def test_counter_value_is_integer(self, client):
        from utils.instrumentation import metrics
        metrics.increment("test.counter", 42)

        resp = client.get("/health/metrics")
        body = resp.text
        assert 'discovery_counter{name="test.counter"} 42' in body


# ---------------------------------------------------------------------------
# Timer format tests
# ---------------------------------------------------------------------------


class TestTimerFormat:
    """Verify timer lines include count, total_ms, avg_ms."""

    def test_timer_emits_count_total_avg(self, client):
        from utils.instrumentation import metrics
        metrics.record_timing("db.query", 10.5)
        metrics.record_timing("db.query", 20.5)

        resp = client.get("/health/metrics")
        body = resp.text
        assert '# TYPE discovery_timer_count gauge' in body
        assert 'discovery_timer_count{name="db.query"} 2' in body
        assert 'discovery_timer_total_ms{name="db.query"} 31.0' in body
        assert 'discovery_timer_avg_ms{name="db.query"} 15.5' in body

    def test_multiple_timers_sorted(self, client):
        from utils.instrumentation import metrics
        metrics.record_timing("z_timer", 1.0)
        metrics.record_timing("a_timer", 2.0)

        resp = client.get("/health/metrics")
        body = resp.text
        a_pos = body.index('name="a_timer"')
        z_pos = body.index('name="z_timer"')
        assert a_pos < z_pos, "Timers should be sorted by name"

    def test_timer_type_declarations(self, client):
        from utils.instrumentation import metrics
        metrics.record_timing("test.timer", 5.0)

        resp = client.get("/health/metrics")
        body = resp.text
        assert '# TYPE discovery_timer_total_ms gauge' in body
        assert '# HELP discovery_timer_total_ms' in body


# ---------------------------------------------------------------------------
# Ops gauge tests
# ---------------------------------------------------------------------------


class TestOpsGauges:
    """Verify ops metrics are included when OpsMetricsCollector is available."""

    def test_ops_gauges_present(self, client_with_ops):
        resp = client_with_ops.get("/health/metrics")
        body = resp.text
        assert "# TYPE discovery_health_pct gauge" in body
        assert "discovery_health_pct 95.5" in body
        assert "discovery_extractions_24h 42" in body
        assert "discovery_open_incidents 1" in body

    def test_ops_gauges_absent_when_collector_unavailable(self, client):
        resp = client.get("/health/metrics")
        body = resp.text
        assert "discovery_health_pct" not in body
        assert "discovery_extractions_24h" not in body
        assert "discovery_open_incidents" not in body

    def test_ops_collector_error_does_not_break_endpoint(self, app):
        from utils.instrumentation import metrics
        metrics.reset()

        mock_collector = MagicMock()
        mock_collector.collect.side_effect = RuntimeError("DB locked")

        with patch("api.routers.health._get_ops_collector", return_value=mock_collector):
            client = TestClient(app)
            resp = client.get("/health/metrics")

        assert resp.status_code == 200
        body = resp.text
        assert body.strip().endswith("# EOF")
        assert "discovery_health_pct" not in body
        metrics.reset()


# ---------------------------------------------------------------------------
# Combined metrics test
# ---------------------------------------------------------------------------


class TestCombinedMetrics:
    """Verify all metric types coexist in a single response."""

    def test_counters_and_timers_together(self, client):
        from utils.instrumentation import metrics
        metrics.increment("requests.total", 100)
        metrics.record_timing("requests.latency", 50.0)

        resp = client.get("/health/metrics")
        body = resp.text
        assert 'discovery_counter{name="requests.total"} 100' in body
        assert 'discovery_timer_count{name="requests.latency"} 1' in body
        assert body.strip().endswith("# EOF")

    def test_all_three_sections(self, client_with_ops):
        from utils.instrumentation import metrics
        metrics.increment("test.counter", 1)
        metrics.record_timing("test.timer", 10.0)

        resp = client_with_ops.get("/health/metrics")
        body = resp.text
        # Counters
        assert "discovery_counter" in body
        # Timers
        assert "discovery_timer_count" in body
        # Ops gauges
        assert "discovery_health_pct" in body
        # EOF
        assert body.strip().endswith("# EOF")

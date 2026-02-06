"""Phase 2.4 — FastAPI ops health endpoint tests."""

import pytest
from unittest.mock import patch, MagicMock
from decimal import Decimal

from fastapi.testclient import TestClient
from fastapi import FastAPI

from ops.monitoring.metrics import OpsMetricsSnapshot


def _make_snapshot(**overrides) -> OpsMetricsSnapshot:
    defaults = dict(
        timestamp="2026-02-05T00:00:00+00:00",
        health_summary={},
        overall_health_pct=100.0,
        last_extraction=None,
        extractions_24h=1,
        total_cost_24h=Decimal("0.10"),
        avg_extraction_duration=3.0,
        total_extractions_all_time=5,
        facts_by_status={"active": 10, "pending": 2},
        total_facts=12,
        avg_fact_confidence=0.85,
        unused_high_confidence_facts=2,
        open_incidents=0,
        recent_incidents_24h=0,
        audit_entries_24h=3,
    )
    defaults.update(overrides)
    return OpsMetricsSnapshot(**defaults)


@pytest.fixture
def app_with_ops():
    """Create a test FastAPI app with ops health router."""
    from api.routers.health import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app_with_ops):
    return TestClient(app_with_ops)


class TestOpsHealthEndpoint:
    def test_ops_health_returns_200(self, client):
        """GET /health/ops returns 200 when ops tables exist."""
        mock_snap = _make_snapshot()
        with patch("api.routers.health._get_ops_collector") as mock_get:
            mock_collector = MagicMock()
            mock_collector.collect.return_value = mock_snap
            mock_get.return_value = mock_collector

            with patch("api.routers.health._get_ops_alert_engine") as mock_eng:
                mock_engine = MagicMock()
                mock_engine.evaluate.return_value = []
                mock_eng.return_value = mock_engine

                resp = client.get("/health/ops")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["overall_health_pct"] == 100.0

    def test_ops_health_status_degraded(self, client):
        """GET /health/ops returns degraded when alerts fire."""
        mock_snap = _make_snapshot(
            health_summary={"db": {"health_percent": 40.0, "total_checks": 10, "avg_latency_ms": 5.0}},
            overall_health_pct=40.0,
        )
        from ops.monitoring.alerts import Alert

        mock_alert = Alert(
            rule_name="health_degraded",
            severity="critical",
            message="Component health below 70%",
            fired_at="2026-02-05T00:00:00+00:00",
            snapshot_value=None,
            fingerprint="health_degraded:critical:health",
        )

        with patch("api.routers.health._get_ops_collector") as mock_get:
            mock_collector = MagicMock()
            mock_collector.collect.return_value = mock_snap
            mock_get.return_value = mock_collector

            with patch("api.routers.health._get_ops_alert_engine") as mock_eng:
                mock_engine = MagicMock()
                mock_engine.evaluate.return_value = [mock_alert]
                mock_eng.return_value = mock_engine

                resp = client.get("/health/ops")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("degraded", "unhealthy")
        assert len(data["active_alerts"]) == 1


class TestOpsMetricsEndpoint:
    def test_ops_metrics_full_snapshot(self, client):
        """GET /health/ops/metrics returns full snapshot."""
        mock_snap = _make_snapshot()
        with patch("api.routers.health._get_ops_collector") as mock_get:
            mock_collector = MagicMock()
            mock_collector.collect.return_value = mock_snap
            mock_collector.get_daily_history.return_value = []
            mock_get.return_value = mock_collector

            resp = client.get("/health/ops/metrics")

        assert resp.status_code == 200
        data = resp.json()
        assert "timestamp" in data
        assert data["total_facts"] == 12

    def test_ops_metrics_with_history(self, client):
        """GET /health/ops/metrics?history_days=7 includes history."""
        mock_snap = _make_snapshot()
        mock_history = [{"date": "2026-02-05", "runs": 2, "cost": "0.15", "avg_duration_s": 3.0}]

        with patch("api.routers.health._get_ops_collector") as mock_get:
            mock_collector = MagicMock()
            mock_collector.collect.return_value = mock_snap
            mock_collector.get_daily_history.return_value = mock_history
            mock_get.return_value = mock_collector

            resp = client.get("/health/ops/metrics?history_days=7")

        assert resp.status_code == 200
        data = resp.json()
        assert "daily_history" in data
        assert len(data["daily_history"]) == 1


class TestGracefulDegradation:
    def test_ops_endpoints_graceful_no_tables(self, client):
        """Returns 503 when ops tables don't exist."""
        with patch("api.routers.health._get_ops_collector") as mock_get:
            mock_get.return_value = None

            resp = client.get("/health/ops")

        assert resp.status_code == 503
        data = resp.json()
        assert "error" in data.get("detail", data)


class TestThreadpool:
    def test_ops_endpoints_use_threadpool(self):
        """Verify the endpoint uses run_in_threadpool."""
        import inspect
        from api.routers import health as health_module
        source = inspect.getsource(health_module)
        assert "run_in_threadpool" in source

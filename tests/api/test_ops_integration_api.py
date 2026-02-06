"""Phase 6 — API integration tests with real OpsStorage.

Tests FastAPI endpoints backed by real SQLite databases to verify
the full stack: HTTP request → router → OpsStorage → response.

Note: FastAPI's run_in_threadpool() creates worker threads, so we can't
share OpsStorage instances across threads. Instead, we seed data via
a temporary OpsStorage, close it, and let _get_ops_storage() create
fresh instances per-call (in the correct worker thread).
"""

import json
import sqlite3
import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi import FastAPI

from ops.storage import OpsStorage

# ---------------------------------------------------------------------------
# Thread-safe SQLite for FastAPI integration tests
#
# FastAPI's run_in_threadpool() creates worker threads. OpsStorage's
# ConnectionPool creates connections with check_same_thread=True (default),
# which then fails when accessed from a different thread. For tests only,
# we patch sqlite3.connect to allow cross-thread usage.
# ---------------------------------------------------------------------------

_original_connect = sqlite3.connect


def _threadsafe_connect(*args, **kwargs):
    kwargs["check_same_thread"] = False
    return _original_connect(*args, **kwargs)


@pytest.fixture
def db_path(tmp_path):
    """Create a real SQLite DB with thread-safe connections, return path."""
    path = str(tmp_path / "api_integration.db")
    with patch("ops.storage.sqlite3.connect", side_effect=_threadsafe_connect):
        storage = OpsStorage(path)
        del storage
    return path


@pytest.fixture
def client_and_path(db_path):
    """FastAPI TestClient wired to real DB via per-call OpsStorage factory."""
    from api.routers.health import router
    from ops.monitoring.metrics import OpsMetricsCollector
    from ops.monitoring.alerts import AlertEngine

    app = FastAPI()
    app.include_router(router)

    def get_storage():
        return OpsStorage(db_path)

    def get_collector():
        return OpsMetricsCollector(OpsStorage(db_path))

    def get_engine():
        return AlertEngine()

    with patch("ops.storage.sqlite3.connect", side_effect=_threadsafe_connect), \
         patch("api.routers.health._get_ops_storage", side_effect=get_storage), \
         patch("api.routers.health._get_ops_collector", side_effect=get_collector), \
         patch("api.routers.health._get_ops_alert_engine", side_effect=get_engine):
        yield TestClient(app), db_path


def _seed(db_path):
    """Create a temporary OpsStorage for seeding, return it. Caller must delete."""
    with patch("ops.storage.sqlite3.connect", side_effect=_threadsafe_connect):
        return OpsStorage(db_path)


# =============================================================================
# Rule CRUD Round-Trip via API
# =============================================================================


class TestRuleCRUDRoundTrip:
    """Full create → read → update → delete lifecycle via HTTP endpoints."""

    def test_full_crud_lifecycle(self, client_and_path):
        """POST → GET → PUT → GET → DELETE → GET 404."""
        client, db_path = client_and_path

        # CREATE
        resp = client.post("/health/ops/rules", json={
            "name": "api_crud_test",
            "condition": {"field": "total_cost_24h", "op": ">", "value": 10},
            "severity": "warning",
            "message_template": "API CRUD test rule",
        })
        assert resp.status_code == 201
        rule = resp.json()
        rule_id = rule["id"]
        assert rule["name"] == "api_crud_test"
        assert rule["severity"] == "warning"

        # READ
        resp = client.get(f"/health/ops/rules/{rule_id}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["rule"]["name"] == "api_crud_test"
        assert detail["evaluations"] == []

        # UPDATE severity
        resp = client.put(f"/health/ops/rules/{rule_id}", json={
            "severity": "critical",
        })
        assert resp.status_code == 200
        assert resp.json()["severity"] == "critical"

        # VERIFY update
        resp = client.get(f"/health/ops/rules/{rule_id}")
        assert resp.json()["rule"]["severity"] == "critical"

        # DELETE
        resp = client.delete(f"/health/ops/rules/{rule_id}")
        assert resp.status_code == 200

        # VERIFY deleted
        resp = client.get(f"/health/ops/rules/{rule_id}")
        assert resp.status_code == 404


# =============================================================================
# Metric History via API
# =============================================================================


class TestMetricHistoryAPI:
    """Test /health/ops/history returns real snapshot data."""

    def test_history_returns_persisted_snapshots(self, client_and_path):
        """Snapshots saved to DB should appear in API response."""
        client, db_path = client_and_path

        # Seed snapshots via temporary storage
        storage = _seed(db_path)
        storage.save_metric_snapshot({
            "overall_health_pct": 95.0,
            "total_cost_24h": "0.50",
        })
        storage.save_metric_snapshot({
            "overall_health_pct": 85.0,
            "total_cost_24h": "1.20",
        })
        del storage

        resp = client.get("/health/ops/history", params={"hours": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2
        # Most recent first
        assert data[0]["snapshot"]["overall_health_pct"] == 85.0


# =============================================================================
# Rule with Evaluations via API
# =============================================================================


class TestRuleWithEvaluationsAPI:
    """Test GET /health/ops/rules/{id} returns associated evaluations."""

    def test_rule_detail_includes_evaluations(self, client_and_path):
        """Creating evaluations in DB should appear in rule detail response."""
        client, db_path = client_and_path

        # Create rule via API
        resp = client.post("/health/ops/rules", json={
            "name": "eval_api_test",
            "condition": {"field": "open_incidents", "op": ">", "value": 5},
            "severity": "warning",
            "message_template": "Eval API test",
        })
        rule_id = resp.json()["id"]

        # Record evaluation directly in DB (simulating evaluate_all)
        storage = _seed(db_path)
        snapshot_id = storage.save_metric_snapshot({"open_incidents": 10})
        storage.record_alert_evaluation(
            rule_name="eval_api_test",
            fingerprint="eval_api_test:warning:global",
            severity="warning",
            message="Eval API test",
            snapshot_id=snapshot_id,
        )
        del storage

        # GET rule detail
        resp = client.get(f"/health/ops/rules/{rule_id}")
        assert resp.status_code == 200
        detail = resp.json()
        assert len(detail["evaluations"]) >= 1
        assert detail["evaluations"][0]["rule_name"] == "eval_api_test"


# =============================================================================
# Ops Health Endpoint with Real Alerts
# =============================================================================


class TestOpsHealthWithRealData:
    """Test GET /health/ops with seeded unhealthy data."""

    def test_ops_health_shows_active_alerts(self, client_and_path):
        """Unhealthy data should produce active_alerts in /health/ops response."""
        client, db_path = client_and_path

        # Seed unhealthy component data
        storage = _seed(db_path)
        for _ in range(5):
            storage.log_health("test_component", "unhealthy", latency_ms=5000.0)
        del storage

        resp = client.get("/health/ops")
        assert resp.status_code == 200
        data = resp.json()

        # Should be degraded or unhealthy
        assert data["status"] in ("degraded", "unhealthy")
        assert data["overall_health_pct"] < 50
        assert len(data["active_alerts"]) > 0

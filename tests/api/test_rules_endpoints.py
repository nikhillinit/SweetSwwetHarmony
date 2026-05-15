"""Phase 5.5 — FastAPI alert rules + metric history endpoint tests (TDD RED)."""

import json
import pytest
from unittest.mock import patch, MagicMock
from decimal import Decimal

from fastapi.testclient import TestClient
from fastapi import FastAPI

from api.auth.jwt_auth import Role, create_access_token
from ops.monitoring.metrics import OpsMetricsSnapshot


def _make_snapshot(**overrides) -> OpsMetricsSnapshot:
    defaults = dict(
        timestamp="2026-02-06T00:00:00+00:00",
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
    from api.routers.health import router
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app_with_ops):
    return TestClient(app_with_ops)


def _auth_header(role: Role, email: str | None = None) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=f"{role.value}-rules-test",
        email=email or f"{role.value}@example.com",
        role=role,
        name=f"{role.value} Rules Test",
    )
    return {"Authorization": f"Bearer {token}"}


def _view_headers() -> dict[str, str]:
    return _auth_header(Role.READONLY)


def _gp_headers() -> dict[str, str]:
    return _auth_header(Role.GP)


VALID_RULE_REQUEST = {
    "name": "auth_matrix_rule",
    "condition": {"field": "total_cost_24h", "op": ">", "value": 10},
    "severity": "warning",
    "message_template": "Auth matrix rule",
}


RULES_AUTH_MATRIX = [
    ("GET", "/health/ops/rules", {}),
    ("POST", "/health/ops/rules", {"json": VALID_RULE_REQUEST}),
    ("GET", "/health/ops/rules/1", {}),
    ("PUT", "/health/ops/rules/1", {"json": {"severity": "critical"}}),
    ("DELETE", "/health/ops/rules/1", {}),
]


RULE_WRITE_AUTH_MATRIX = [
    ("POST", "/health/ops/rules", {"json": VALID_RULE_REQUEST}),
    ("PUT", "/health/ops/rules/1", {"json": {"severity": "critical"}}),
    ("DELETE", "/health/ops/rules/1", {}),
]


def _read_storage() -> MagicMock:
    storage = MagicMock()
    storage.list_alert_rules.return_value = []
    storage.get_alert_rule.return_value = {
        "id": 1,
        "name": "auth_matrix_rule",
        "severity": "warning",
        "condition_json": '{"field":"total_cost_24h","op":">","value":10}',
        "component": None,
        "message_template": "Auth matrix rule",
        "enabled": 1,
        "is_builtin": 0,
        "created_at": "2026-02-06T00:00:00",
        "updated_at": None,
    }
    storage.get_alert_evaluations.return_value = []
    return storage


# =============================================================================
# AUTHORIZATION
# =============================================================================


class TestRulesAuth:
    @pytest.mark.parametrize("method,path,request_kwargs", RULES_AUTH_MATRIX)
    def test_ops_rule_routes_reject_unauthenticated(
        self, client, method, path, request_kwargs
    ):
        """Every ops-rule CRUD route requires authentication."""
        resp = client.request(method, path, **request_kwargs)
        assert resp.status_code == 401

    @pytest.mark.parametrize("role", [Role.READONLY, Role.ANALYST])
    @pytest.mark.parametrize("path", ["/health/ops/rules", "/health/ops/rules/1"])
    def test_ops_rule_read_routes_allow_authenticated_viewers(
        self, client, role, path
    ):
        """Authenticated viewers can read ops-rule list and detail routes."""
        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = _read_storage()
            resp = client.get(path, headers=_auth_header(role))

        assert resp.status_code == 200

    @pytest.mark.parametrize("role", [Role.READONLY, Role.ANALYST])
    @pytest.mark.parametrize("method,path,request_kwargs", RULE_WRITE_AUTH_MATRIX)
    def test_ops_rule_mutations_reject_non_gp_roles(
        self, client, role, method, path, request_kwargs
    ):
        """Ops-rule mutation routes require ops-admin permission."""
        resp = client.request(
            method,
            path,
            headers=_auth_header(role),
            **request_kwargs,
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "INSUFFICIENT_PERMISSION"


# =============================================================================
# GET /health/ops/rules
# =============================================================================

class TestListRules:
    def test_list_rules_empty(self, client):
        """GET /health/ops/rules returns empty list when no rules exist."""
        mock_storage = MagicMock()
        mock_storage.list_alert_rules.return_value = []

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.get("/health/ops/rules", headers=_view_headers())

        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_rules_with_data(self, client):
        """GET /health/ops/rules returns all rules."""
        mock_storage = MagicMock()
        mock_storage.list_alert_rules.return_value = [
            {
                "id": 1, "name": "cost_alert", "severity": "warning",
                "condition_json": '{"field":"total_cost_24h","op":">","value":5}',
                "component": "cost", "message_template": "Cost exceeds $5",
                "enabled": 1, "is_builtin": 0,
                "created_at": "2026-02-06T00:00:00", "updated_at": None,
            },
            {
                "id": 2, "name": "health_check", "severity": "critical",
                "condition_json": '{"field":"overall_health_pct","op":"<","value":50}',
                "component": "health", "message_template": "Health below 50%",
                "enabled": 1, "is_builtin": 1,
                "created_at": "2026-02-06T00:00:00", "updated_at": None,
            },
        ]

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.get("/health/ops/rules", headers=_view_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "cost_alert"
        assert data[1]["is_builtin"] is True

    def test_list_rules_503_no_storage(self, client):
        """GET /health/ops/rules returns 503 when ops tables missing."""
        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = None
            resp = client.get("/health/ops/rules", headers=_view_headers())

        assert resp.status_code == 503


# =============================================================================
# POST /health/ops/rules
# =============================================================================

class TestCreateRule:
    def test_create_rule_success(self, client):
        """POST /health/ops/rules creates a custom rule."""
        mock_storage = MagicMock()
        mock_storage.create_alert_rule.return_value = 5
        mock_storage.get_alert_rule.return_value = {
            "id": 5, "name": "my_rule", "severity": "warning",
            "condition_json": '{"field":"total_cost_24h","op":">","value":10}',
            "component": None, "message_template": "Cost high",
            "enabled": 1, "is_builtin": 0,
            "created_at": "2026-02-06T00:00:00", "updated_at": None,
        }

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.post("/health/ops/rules", json={
                "name": "my_rule",
                "condition": {"field": "total_cost_24h", "op": ">", "value": 10},
                "severity": "warning",
                "message_template": "Cost high",
            }, headers=_gp_headers())

        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == 5
        assert data["name"] == "my_rule"

    def test_create_rule_with_component(self, client):
        """POST /health/ops/rules accepts optional component."""
        mock_storage = MagicMock()
        mock_storage.create_alert_rule.return_value = 6
        mock_storage.get_alert_rule.return_value = {
            "id": 6, "name": "comp_rule", "severity": "critical",
            "condition_json": '{"field":"open_incidents","op":">","value":2}',
            "component": "incidents", "message_template": "Too many incidents",
            "enabled": 1, "is_builtin": 0,
            "created_at": "2026-02-06T00:00:00", "updated_at": None,
        }

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.post("/health/ops/rules", json={
                "name": "comp_rule",
                "condition": {"field": "open_incidents", "op": ">", "value": 2},
                "severity": "critical",
                "message_template": "Too many incidents",
                "component": "incidents",
            }, headers=_gp_headers())

        assert resp.status_code == 201
        assert resp.json()["component"] == "incidents"

    def test_create_rule_invalid_condition(self, client):
        """POST /health/ops/rules rejects invalid condition JSON."""
        mock_storage = MagicMock()

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.post("/health/ops/rules", json={
                "name": "bad_rule",
                "condition": {"nonsense": True},
                "severity": "warning",
                "message_template": "Nope",
            }, headers=_gp_headers())

        assert resp.status_code == 422

    def test_create_rule_invalid_severity(self, client):
        """POST /health/ops/rules rejects invalid severity."""
        mock_storage = MagicMock()

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.post("/health/ops/rules", json={
                "name": "bad_sev",
                "condition": {"field": "open_incidents", "op": ">", "value": 1},
                "severity": "deadly",
                "message_template": "Nope",
            }, headers=_gp_headers())

        assert resp.status_code == 422

    def test_create_rule_composite_condition(self, client):
        """POST /health/ops/rules accepts composite (all/any) conditions."""
        mock_storage = MagicMock()
        mock_storage.create_alert_rule.return_value = 7
        mock_storage.get_alert_rule.return_value = {
            "id": 7, "name": "composite", "severity": "warning",
            "condition_json": json.dumps({"all": [
                {"field": "total_cost_24h", "op": ">", "value": 3},
                {"field": "extractions_24h", "op": ">", "value": 0},
            ]}),
            "component": None, "message_template": "Composite fired",
            "enabled": 1, "is_builtin": 0,
            "created_at": "2026-02-06T00:00:00", "updated_at": None,
        }

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.post("/health/ops/rules", json={
                "name": "composite",
                "condition": {"all": [
                    {"field": "total_cost_24h", "op": ">", "value": 3},
                    {"field": "extractions_24h", "op": ">", "value": 0},
                ]},
                "severity": "warning",
                "message_template": "Composite fired",
            }, headers=_gp_headers())

        assert resp.status_code == 201


# =============================================================================
# GET /health/ops/rules/{id}
# =============================================================================

class TestGetRule:
    def test_get_rule_found(self, client):
        """GET /health/ops/rules/{id} returns rule with evaluation history."""
        mock_storage = MagicMock()
        mock_storage.get_alert_rule.return_value = {
            "id": 1, "name": "cost_alert", "severity": "warning",
            "condition_json": '{"field":"total_cost_24h","op":">","value":5}',
            "component": "cost", "message_template": "Cost exceeds $5",
            "enabled": 1, "is_builtin": 0,
            "created_at": "2026-02-06T00:00:00", "updated_at": None,
        }
        mock_storage.get_alert_evaluations.return_value = [
            {
                "id": 10, "rule_name": "cost_alert", "fired_at": "2026-02-06T01:00:00",
                "fingerprint": "cost_alert:warning:cost", "severity": "warning",
                "message": "Cost exceeds $5", "resolved_at": None, "snapshot_id": 1,
            },
        ]

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.get("/health/ops/rules/1", headers=_view_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert data["rule"]["name"] == "cost_alert"
        assert len(data["evaluations"]) == 1

    def test_get_rule_not_found(self, client):
        """GET /health/ops/rules/{id} returns 404 when rule doesn't exist."""
        mock_storage = MagicMock()
        mock_storage.get_alert_rule.return_value = None

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.get("/health/ops/rules/999", headers=_view_headers())

        assert resp.status_code == 404


# =============================================================================
# PUT /health/ops/rules/{id}
# =============================================================================

class TestUpdateRule:
    def test_update_rule_severity(self, client):
        """PUT /health/ops/rules/{id} updates severity."""
        mock_storage = MagicMock()
        mock_storage.update_alert_rule.return_value = True
        mock_storage.get_alert_rule.return_value = {
            "id": 1, "name": "cost_alert", "severity": "critical",
            "condition_json": '{"field":"total_cost_24h","op":">","value":5}',
            "component": "cost", "message_template": "Cost exceeds $5",
            "enabled": 1, "is_builtin": 0,
            "created_at": "2026-02-06T00:00:00", "updated_at": "2026-02-06T01:00:00",
        }

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.put(
                "/health/ops/rules/1",
                json={"severity": "critical"},
                headers=_gp_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["severity"] == "critical"

    def test_update_rule_condition(self, client):
        """PUT /health/ops/rules/{id} updates condition."""
        new_cond = {"field": "total_cost_24h", "op": ">", "value": 20}
        mock_storage = MagicMock()
        mock_storage.update_alert_rule.return_value = True
        mock_storage.get_alert_rule.return_value = {
            "id": 1, "name": "cost_alert", "severity": "warning",
            "condition_json": json.dumps(new_cond),
            "component": "cost", "message_template": "Cost exceeds $20",
            "enabled": 1, "is_builtin": 0,
            "created_at": "2026-02-06T00:00:00", "updated_at": "2026-02-06T01:00:00",
        }

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.put(
                "/health/ops/rules/1",
                json={"condition": new_cond},
                headers=_gp_headers(),
            )

        assert resp.status_code == 200

    def test_update_rule_enable_disable(self, client):
        """PUT /health/ops/rules/{id} can toggle enabled."""
        mock_storage = MagicMock()
        mock_storage.update_alert_rule.return_value = True
        mock_storage.get_alert_rule.return_value = {
            "id": 1, "name": "cost_alert", "severity": "warning",
            "condition_json": '{"field":"total_cost_24h","op":">","value":5}',
            "component": "cost", "message_template": "Cost exceeds $5",
            "enabled": 0, "is_builtin": 0,
            "created_at": "2026-02-06T00:00:00", "updated_at": "2026-02-06T01:00:00",
        }

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.put(
                "/health/ops/rules/1",
                json={"enabled": False},
                headers=_gp_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_update_rule_not_found(self, client):
        """PUT /health/ops/rules/{id} returns 404 when rule doesn't exist."""
        mock_storage = MagicMock()
        mock_storage.update_alert_rule.return_value = False

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.put(
                "/health/ops/rules/999",
                json={"severity": "info"},
                headers=_gp_headers(),
            )

        assert resp.status_code == 404

    def test_update_rule_invalid_severity(self, client):
        """PUT /health/ops/rules/{id} rejects invalid severity."""
        mock_storage = MagicMock()

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.put(
                "/health/ops/rules/1",
                json={"severity": "extreme"},
                headers=_gp_headers(),
            )

        assert resp.status_code == 422


# =============================================================================
# DELETE /health/ops/rules/{id}
# =============================================================================

class TestDeleteRule:
    def test_delete_custom_rule(self, client):
        """DELETE /health/ops/rules/{id} deletes a custom rule."""
        mock_storage = MagicMock()
        mock_storage.delete_alert_rule.return_value = True

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.delete("/health/ops/rules/1", headers=_gp_headers())

        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"].lower()

    def test_delete_builtin_rule_rejected(self, client):
        """DELETE /health/ops/rules/{id} rejects deletion of builtin rules."""
        mock_storage = MagicMock()
        mock_storage.delete_alert_rule.return_value = False
        mock_storage.get_alert_rule.return_value = {
            "id": 1, "name": "builtin", "is_builtin": 1,
        }

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.delete("/health/ops/rules/1", headers=_gp_headers())

        assert resp.status_code == 403

    def test_delete_rule_not_found(self, client):
        """DELETE /health/ops/rules/{id} returns 404 when rule doesn't exist."""
        mock_storage = MagicMock()
        mock_storage.delete_alert_rule.return_value = False
        mock_storage.get_alert_rule.return_value = None

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.delete("/health/ops/rules/999", headers=_gp_headers())

        assert resp.status_code == 404


# =============================================================================
# GET /health/ops/history
# =============================================================================

class TestMetricHistory:
    def test_history_default(self, client):
        """GET /health/ops/history returns snapshots from last 24h."""
        mock_storage = MagicMock()
        mock_storage.get_metric_snapshots.return_value = [
            {
                "id": 1,
                "timestamp": "2026-02-06T01:00:00",
                "snapshot": {"overall_health_pct": 100.0, "total_cost_24h": "0.10"},
            },
            {
                "id": 2,
                "timestamp": "2026-02-06T02:00:00",
                "snapshot": {"overall_health_pct": 95.0, "total_cost_24h": "0.20"},
            },
        ]

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.get("/health/ops/history")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        mock_storage.get_metric_snapshots.assert_called_once_with(hours=24, limit=100)

    def test_history_custom_range(self, client):
        """GET /health/ops/history?hours=168 supports custom time range."""
        mock_storage = MagicMock()
        mock_storage.get_metric_snapshots.return_value = []

        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = mock_storage
            resp = client.get("/health/ops/history?hours=168&limit=50")

        assert resp.status_code == 200
        mock_storage.get_metric_snapshots.assert_called_once_with(hours=168, limit=50)

    def test_history_503_no_storage(self, client):
        """GET /health/ops/history returns 503 when ops tables missing."""
        with patch("api.routers.health._get_ops_storage") as mock_get:
            mock_get.return_value = None
            resp = client.get("/health/ops/history")

        assert resp.status_code == 503

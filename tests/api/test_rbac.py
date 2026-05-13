"""Tests for RBAC — permission model, decorator enforcement, operator context."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from api.auth.jwt_auth import Role, User, create_access_token
from api.auth.rbac import (
    OperatorContext,
    Permission,
    get_permissions,
    has_permission,
    require_any_permission,
    require_permission,
)


# ============================================================================
# Permission model
# ============================================================================


class TestPermissionModel:
    def test_readonly_has_view(self):
        assert has_permission(Role.READONLY, Permission.VIEW)
        assert has_permission(Role.READONLY, Permission.SEARCH)
        assert has_permission(Role.READONLY, Permission.EXPORT)

    def test_readonly_cannot_approve(self):
        assert not has_permission(Role.READONLY, Permission.TRIAGE_APPROVE)
        assert not has_permission(Role.READONLY, Permission.COMPANY_ACTION)
        assert not has_permission(Role.READONLY, Permission.ENTITY_MERGE)
        assert not has_permission(Role.READONLY, Permission.BATCH_COMMIT)

    def test_analyst_can_triage(self):
        assert has_permission(Role.ANALYST, Permission.TRIAGE_APPROVE)
        assert has_permission(Role.ANALYST, Permission.TRIAGE_REJECT)
        assert has_permission(Role.ANALYST, Permission.TRIAGE_DEFER)
        assert has_permission(Role.ANALYST, Permission.COMPANY_ACTION)
        assert has_permission(Role.ANALYST, Permission.HUNTER_RUN)
        assert has_permission(Role.ANALYST, Permission.HUNTER_PROMOTE)

    def test_analyst_cannot_merge(self):
        assert not has_permission(Role.ANALYST, Permission.ENTITY_MERGE)
        assert not has_permission(Role.ANALYST, Permission.BATCH_COMMIT)
        assert not has_permission(Role.ANALYST, Permission.BULK_TRIAGE)

    def test_gp_has_all(self):
        for perm in Permission:
            assert has_permission(Role.GP, perm), f"GP should have {perm}"

    def test_get_permissions_readonly(self):
        perms = get_permissions(Role.READONLY)
        assert Permission.VIEW in perms
        assert Permission.TRIAGE_APPROVE not in perms

    def test_get_permissions_returns_copy(self):
        p1 = get_permissions(Role.GP)
        p2 = get_permissions(Role.GP)
        assert p1 is not p2


# ============================================================================
# Operator context
# ============================================================================


class TestOperatorContext:
    def test_from_user_and_request(self):
        user = User(id="u1", email="test@example.com", role=Role.ANALYST, name="Test")
        request = MagicMock()
        request.state.request_id = "req-123"

        ctx = OperatorContext.from_request(user, request)
        assert ctx.user_id == "u1"
        assert ctx.email == "test@example.com"
        assert ctx.role == Role.ANALYST
        assert ctx.request_id == "req-123"

    def test_system_context(self):
        ctx = OperatorContext.system("canary")
        assert ctx.user_id == "system:canary"
        assert ctx.role == Role.GP

    def test_actor_label(self):
        ctx = OperatorContext(
            user_id="u1", email="gp@press.com", role=Role.GP
        )
        assert ctx.actor_label == "gp@press.com"


# ============================================================================
# FastAPI integration — require_permission
# ============================================================================


def _make_test_app():
    """Build a minimal FastAPI app with protected endpoints."""
    app = FastAPI()

    @app.get("/view-only")
    async def view_only(
        op: OperatorContext = Depends(require_permission(Permission.VIEW)),
    ):
        return {"user": op.email}

    @app.post("/approve")
    async def approve(
        op: OperatorContext = Depends(
            require_permission(Permission.TRIAGE_APPROVE)
        ),
    ):
        return {"approved_by": op.email}

    @app.post("/merge")
    async def merge(
        op: OperatorContext = Depends(
            require_permission(Permission.ENTITY_MERGE)
        ),
    ):
        return {"merged_by": op.email}

    @app.post("/any-triage")
    async def any_triage(
        op: OperatorContext = Depends(
            require_any_permission(
                Permission.TRIAGE_APPROVE,
                Permission.TRIAGE_REJECT,
            )
        ),
    ):
        return {"op": op.email}

    return app


def _auth_header(role: Role, email: str = "test@example.com") -> dict:
    """Generate Authorization header with JWT for given role."""
    token, _ = create_access_token(
        user_id="test-user",
        email=email,
        role=role,
        name="Test",
    )
    return {"Authorization": f"Bearer {token}"}


class TestRequirePermission:
    def setup_method(self):
        self.app = _make_test_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_unauthenticated_returns_401(self):
        resp = self.client.get("/view-only")
        assert resp.status_code == 401

    def test_readonly_can_view(self):
        resp = self.client.get(
            "/view-only", headers=_auth_header(Role.READONLY)
        )
        assert resp.status_code == 200
        assert resp.json()["user"] == "test@example.com"

    def test_readonly_cannot_approve(self):
        resp = self.client.post(
            "/approve", headers=_auth_header(Role.READONLY)
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["detail"]["code"] == "INSUFFICIENT_PERMISSION"

    def test_analyst_can_approve(self):
        resp = self.client.post(
            "/approve", headers=_auth_header(Role.ANALYST)
        )
        assert resp.status_code == 200

    def test_analyst_cannot_merge(self):
        resp = self.client.post(
            "/merge", headers=_auth_header(Role.ANALYST)
        )
        assert resp.status_code == 403

    def test_gp_can_merge(self):
        resp = self.client.post(
            "/merge", headers=_auth_header(Role.GP)
        )
        assert resp.status_code == 200

    def test_gp_can_everything(self):
        for endpoint in ["/view-only", "/approve", "/merge"]:
            method = self.client.get if endpoint == "/view-only" else self.client.post
            resp = method(endpoint, headers=_auth_header(Role.GP))
            assert resp.status_code == 200, f"GP should access {endpoint}"


class TestRequireAnyPermission:
    def setup_method(self):
        self.app = _make_test_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_analyst_can_any_triage(self):
        resp = self.client.post(
            "/any-triage", headers=_auth_header(Role.ANALYST)
        )
        assert resp.status_code == 200

    def test_readonly_cannot_any_triage(self):
        resp = self.client.post(
            "/any-triage", headers=_auth_header(Role.READONLY)
        )
        assert resp.status_code == 403

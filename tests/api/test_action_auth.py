"""Auth regression tests for inbox action routes."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from api.auth.jwt_auth import Role, create_access_token
from api.routers import actions as actions_mod
from api.services.action_handler import ActionResult


ACTION_CASES = (
    (
        "/api/v1/actions/track",
        {"canonical_key": "domain:test.com"},
        "track",
    ),
    (
        "/api/v1/actions/pass",
        {"canonical_key": "domain:test.com", "reason": "out of thesis"},
        "pass",
    ),
    (
        "/api/v1/actions/pipeline",
        {"canonical_key": "domain:test.com"},
        "pipeline",
    ),
    (
        "/api/v1/actions/snooze",
        {
            "canonical_key": "domain:test.com",
            "until": "2026-05-13T00:00:00+00:00",
        },
        "snooze",
    ),
)


class FakeActionHandler:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def track(self, canonical_key: str, actor: str | None = None) -> ActionResult:
        self.calls.append(
            {"action": "track", "canonical_key": canonical_key, "actor": actor}
        )
        return ActionResult(
            success=True,
            canonical_key=canonical_key,
            action="track",
            message="tracked",
            new_status="tracking",
        )

    async def pass_company(
        self,
        canonical_key: str,
        reason: str,
        actor: str | None = None,
    ) -> ActionResult:
        self.calls.append(
            {
                "action": "pass",
                "canonical_key": canonical_key,
                "reason": reason,
                "actor": actor,
            }
        )
        return ActionResult(
            success=True,
            canonical_key=canonical_key,
            action="pass",
            message="passed",
            new_status="passed",
        )

    async def add_to_pipeline(
        self,
        canonical_key: str,
        actor: str | None = None,
    ) -> ActionResult:
        self.calls.append(
            {"action": "pipeline", "canonical_key": canonical_key, "actor": actor}
        )
        return ActionResult(
            success=True,
            canonical_key=canonical_key,
            action="pipeline",
            message="queued",
            new_status="pipeline_requested",
        )

    async def snooze(
        self,
        canonical_key: str,
        until: object,
        actor: str | None = None,
    ) -> ActionResult:
        self.calls.append(
            {
                "action": "snooze",
                "canonical_key": canonical_key,
                "until": until,
                "actor": actor,
            }
        )
        return ActionResult(
            success=True,
            canonical_key=canonical_key,
            action="snooze",
            message="snoozed",
        )


class FakeStore:
    async def get_company_by_key(self, canonical_key: str) -> None:
        return None


def _auth_header(role: Role, email: str = "operator@example.com") -> dict[str, str]:
    token, _ = create_access_token(
        user_id="test-user",
        email=email,
        role=role,
        name="Test Operator",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake_handler() -> FakeActionHandler:
    return FakeActionHandler()


@pytest.fixture
def app(fake_handler: FakeActionHandler) -> FastAPI:
    app = FastAPI()

    async def override_handler() -> FakeActionHandler:
        return fake_handler

    async def override_store() -> FakeStore:
        return FakeStore()

    app.dependency_overrides[actions_mod.get_handler] = override_handler
    app.dependency_overrides[actions_mod.get_store] = override_store
    app.include_router(actions_mod.router, prefix="/api/v1")
    return app


async def _post_json(
    app: FastAPI,
    path: str,
    body: dict[str, object],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=body, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("path,body,_action", ACTION_CASES)
async def test_mutating_actions_require_authentication(
    app: FastAPI,
    fake_handler: FakeActionHandler,
    path: str,
    body: dict[str, object],
    _action: str,
) -> None:
    response = await _post_json(app, path, body)

    assert response.status_code == 401
    assert fake_handler.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path,body,_action", ACTION_CASES)
async def test_readonly_cannot_mutate_actions(
    app: FastAPI,
    fake_handler: FakeActionHandler,
    path: str,
    body: dict[str, object],
    _action: str,
) -> None:
    response = await _post_json(app, path, body, headers=_auth_header(Role.READONLY))

    assert response.status_code == 403
    assert fake_handler.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path,body,action", ACTION_CASES)
async def test_mutating_actions_use_operator_actor_not_request_body_actor(
    app: FastAPI,
    fake_handler: FakeActionHandler,
    path: str,
    body: dict[str, object],
    action: str,
) -> None:
    response = await _post_json(
        app,
        path,
        {**body, "actor": "spoofed-body-actor"},
        headers=_auth_header(Role.ANALYST, email="analyst@press.example"),
    )

    assert response.status_code == 200
    assert fake_handler.calls[-1]["action"] == action
    assert fake_handler.calls[-1]["actor"] == "analyst@press.example"


@pytest.mark.asyncio
async def test_magic_link_execute_remains_public(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/actions/execute",
            data={"token": "not-a-valid-token"},
        )

    assert response.status_code == 200
    assert "Unable to Process Request" in response.text

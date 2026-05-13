from __future__ import annotations

import asyncio
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from api.auth.jwt_auth import Role, create_access_token
from api.main import app as main_app
from api.routers import merge_review
from storage.signal_store import SignalStore


TEST_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TEST_ROOT.parent
FIXTURE_PATH = TEST_ROOT / "fixtures" / "v1_response_snapshots.json"

PRESERVED_LITERAL_KEYS = {"code", "error"}
CURSOR_META_TARGETS = {
    "triage_list",
    "batches_list",
    "hunter_runs",
    "merge_suggestions_router",
    "merge_suggestions_router_paginated",
}


@dataclass(frozen=True)
class SnapshotTarget:
    target_id: str
    app_name: str
    method: str
    path: str
    expected_status: int
    role: Role | None = None
    json_body: dict[str, Any] | None = None
    seed: str | None = None


SNAPSHOT_TARGETS = (
    SnapshotTarget("root_health", "main", "GET", "/health", 200),
    SnapshotTarget("auth_roles", "main", "GET", "/api/v1/auth/roles", 200),
    SnapshotTarget("jobs_types", "main", "GET", "/api/v1/jobs/types", 200),
    SnapshotTarget("health_detailed", "main", "GET", "/api/v1/health/detailed", 200),
    SnapshotTarget("health_collectors", "main", "GET", "/api/v1/health/collectors", 200),
    SnapshotTarget("companies_inbox", "main", "GET", "/api/v1/companies/inbox", 200),
    SnapshotTarget("entities_list", "main", "GET", "/api/v1/entities", 200, Role.READONLY),
    SnapshotTarget("triage_list", "main", "GET", "/api/v1/triage", 200, Role.ANALYST),
    SnapshotTarget("batches_list", "main", "GET", "/api/v1/batches", 200, Role.GP),
    SnapshotTarget(
        "batch_create_no_approved_reviews",
        "main",
        "POST",
        "/api/v1/batches",
        400,
        Role.GP,
        {"limit": 10},
    ),
    SnapshotTarget("hunter_runs", "main", "GET", "/api/v1/hunter/runs", 200, Role.GP),
    SnapshotTarget("canary_status", "main", "GET", "/api/v1/canary/status", 200, Role.READONLY),
    SnapshotTarget(
        "merge_suggestions_main_shadowed",
        "main",
        "GET",
        "/api/v1/entities/merge-suggestions",
        404,
        Role.GP,
    ),
    SnapshotTarget(
        "merge_suggestions_router",
        "merge_review",
        "GET",
        "/api/v1/entities/merge-suggestions",
        200,
        Role.GP,
    ),
    SnapshotTarget(
        "merge_suggestions_router_paginated",
        "merge_review",
        "GET",
        "/api/v1/entities/merge-suggestions?limit=1",
        200,
        Role.GP,
        seed="merge_suggestions",
    ),
)


class SnapshotSignalStore(SignalStore):
    async def _get_db(self):
        return self._db


@pytest_asyncio.fixture
async def snapshot_store(tmp_path):
    store = SnapshotSignalStore(db_path=str(tmp_path / "v1-snapshots.db"))
    await store.initialize()
    yield store
    await store.close()


@pytest_asyncio.fixture
async def snapshot_apps(snapshot_store):
    main_app.state.store = snapshot_store
    main_app.state.write_lock = asyncio.Lock()
    main_app.state.notion_connector = None
    main_app.state.notion_transport = None

    merge_review_app = FastAPI()
    merge_review_app.state.store = snapshot_store
    merge_review_app.state.write_lock = asyncio.Lock()
    merge_review_app.include_router(merge_review.router, prefix="/api/v1")

    return {
        "main": main_app,
        "merge_review": merge_review_app,
    }


def _auth_headers(role: Role | None) -> dict[str, str]:
    if role is None:
        return {}

    token, _ = create_access_token(
        user_id="snapshot-user",
        email="snapshot@example.com",
        role=role,
        name="Snapshot User",
    )
    return {"Authorization": f"Bearer {token}"}


async def _fetch_snapshot(
    target: SnapshotTarget,
    snapshot_apps: dict[str, FastAPI],
    snapshot_store: SnapshotSignalStore,
) -> dict[str, Any]:
    await _seed_snapshot_target(target, snapshot_store)

    app = snapshot_apps[target.app_name]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(
            target.method,
            target.path,
            headers=_auth_headers(target.role),
            json=target.json_body,
        )

    assert response.status_code == target.expected_status
    body = response.json()
    _assert_cursor_meta_shape(target, body)
    return {
        "method": target.method,
        "path": target.path,
        "status_code": response.status_code,
        "body": _normalize_json(body),
    }


async def _seed_snapshot_target(
    target: SnapshotTarget,
    snapshot_store: SnapshotSignalStore,
) -> None:
    if target.seed is None:
        return

    if target.seed == "merge_suggestions":
        await _seed_merge_suggestion(snapshot_store, suggestion_id=1, pair_key="pair-1")
        await _seed_merge_suggestion(snapshot_store, suggestion_id=2, pair_key="pair-2")
        return

    raise AssertionError(f"Unknown snapshot seed fixture: {target.seed}")


async def _seed_merge_suggestion(
    store: SnapshotSignalStore,
    suggestion_id: int,
    pair_key: str,
) -> None:
    await store._db.execute("PRAGMA foreign_keys = OFF")
    await store._db.execute(
        """INSERT INTO merge_suggestions
           (id, pair_key, entity_a_company_id, entity_b_company_id,
            entity_a_canonical_key, entity_b_canonical_key,
            entity_a_company_name, entity_b_company_name,
            match_type, similarity_score, scoring_version,
            evidence_json, status, blast_radius_json, created_at)
           VALUES (?, ?, ?, ?, 'domain:a.com', 'domain:b.com',
                   'Company A', 'Company B', 'fuzzy_name', 0.85, '1.0.0',
                   '{}', 'pending', NULL, datetime('now'))""",
        (
            suggestion_id,
            pair_key,
            f"company-a-{suggestion_id}",
            f"company-b-{suggestion_id}",
        ),
    )
    await store._db.commit()


def _assert_cursor_meta_shape(target: SnapshotTarget, body: Any) -> None:
    if target.target_id not in CURSOR_META_TARGETS:
        return

    assert isinstance(body, dict)
    assert isinstance(body.get("meta"), dict)
    assert "next_cursor" in body["meta"]
    assert "cursor" not in body["meta"]


def _normalize_json(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            item_key: _normalize_json(item_value, item_key)
            for item_key, item_value in sorted(value.items())
        }

    if isinstance(value, list):
        return _unique_list_shapes(value, key)

    if value is None:
        return None
    if isinstance(value, bool):
        return "<bool>"
    if isinstance(value, int):
        return "<int>"
    if isinstance(value, float):
        return "<float>"
    if isinstance(value, str):
        if key in PRESERVED_LITERAL_KEYS:
            return value
        return "<str>"

    return f"<{type(value).__name__}>"


def _unique_list_shapes(values: list[Any], key: str | None = None) -> list[Any]:
    normalized_values: list[Any] = []
    seen: set[str] = set()

    for value in values:
        normalized = _normalize_json(value, key)
        fingerprint = json.dumps(normalized, sort_keys=True)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        normalized_values.append(normalized)

    return normalized_values


def _load_expected_snapshots() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def test_v1_response_snapshot_fixture_covers_all_targets() -> None:
    expected_ids = set(_load_expected_snapshots())
    target_ids = {target.target_id for target in SNAPSHOT_TARGETS}

    assert expected_ids == target_ids


def test_list_meta_does_not_use_legacy_cursor_keyword() -> None:
    errors: list[str] = []

    for path in (REPO_ROOT / "api").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr
            else:
                continue

            if func_name != "ListMeta":
                continue

            for keyword in node.keywords:
                if keyword.arg == "cursor":
                    relpath = path.relative_to(REPO_ROOT)
                    errors.append(f"{relpath}:{node.lineno}")

    message = "Use ListMeta(next_cursor=...) instead of cursor=:\n"
    assert not errors, message + "\n".join(errors)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", SNAPSHOT_TARGETS, ids=lambda target: target.target_id)
async def test_v1_response_snapshot(
    target: SnapshotTarget,
    snapshot_apps: dict[str, FastAPI],
    snapshot_store: SnapshotSignalStore,
) -> None:
    expected = _load_expected_snapshots()[target.target_id]
    actual = await _fetch_snapshot(target, snapshot_apps, snapshot_store)

    assert actual == expected

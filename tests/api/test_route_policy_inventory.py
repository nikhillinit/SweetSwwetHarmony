from __future__ import annotations

import csv
import inspect
from enum import Enum
from pathlib import Path

from fastapi.routing import APIRoute

from api.main import app


API_PREFIX = "/api/v1"
FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "api_route_policy_inventory.csv"
)

ALLOWED_POLICIES = {
    "PUBLIC_HEALTH",
    "PUBLIC_AUTH",
    "PUBLIC_SHARE",
    "AUTH_VIEW",
    "AUTH_OPERATOR",
    "AUTH_ADMIN",
    "SCHEDULER_ADMIN",
    "OPS_ADMIN",
    "BLOCKED_UNCLASSIFIED",
}
ROUTE_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
NON_AUTH_DEPENDENCIES = {
    ("api.contracts", "get_idempotency_key"),
    ("api.db", "get_store"),
    ("api.routers.actions", "get_handler"),
    ("api.routers.jobs", "get_job_service"),
    ("api.routers.scheduler", "get_scheduler"),
    ("fastapi.security.oauth2", "OAuth2PasswordRequestForm"),
}


def _is_api_v1_path(path: str) -> bool:
    return path == API_PREFIX or path.startswith(f"{API_PREFIX}/")


def _load_inventory_rows() -> list[dict[str, str]]:
    with FIXTURE_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows, f"{FIXTURE_PATH} must contain route policy rows"
    return rows


def _route_key(row: dict[str, str]) -> tuple[str, str]:
    return row["method"], row["path"]


def _api_v1_routes_by_key() -> dict[tuple[str, str], APIRoute]:
    routes: dict[tuple[str, str], APIRoute] = {}
    duplicates: list[tuple[str, str]] = []

    for route in app.routes:
        if not isinstance(route, APIRoute) or not _is_api_v1_path(route.path):
            continue

        for method in sorted(route.methods & ROUTE_METHODS):
            key = (method, route.path)
            if key in routes:
                duplicates.append(key)
            routes[key] = route

    assert not duplicates, "Duplicate /api/v1 route registrations:\n" + _format_keys(duplicates)
    return routes


def _dependency_closure_values(call: object) -> list[object]:
    values: list[object] = []
    for cell in getattr(call, "__closure__", None) or []:
        try:
            values.append(cell.cell_contents)
        except ValueError:
            continue
    return values


def _current_auth_marker(route: APIRoute) -> str:
    unclassified_dependencies: list[str] = []

    for dependency in route.dependant.dependencies:
        call = dependency.call
        name = getattr(call, "__name__", "")
        qualname = getattr(call, "__qualname__", "")
        module = getattr(call, "__module__", "")

        if name == "get_current_user":
            return "get_current_user"
        if name == "get_current_user_optional":
            return "optional/current user"
        if qualname == "require_permission.<locals>.checker":
            for value in _dependency_closure_values(call):
                if isinstance(value, Enum):
                    return f"require_permission({value.name})"
        if qualname == "require_role.<locals>.role_checker":
            for value in _dependency_closure_values(call):
                if isinstance(value, list):
                    roles = ", ".join(role.name for role in value)
                    return f"require_role({roles})"

        if (module, name) in NON_AUTH_DEPENDENCIES:
            continue

        unclassified_dependencies.append(f"{module}.{qualname or name}")

    source = inspect.getsource(route.endpoint)
    if "peek_token(" in source:
        return "magic token peek"
    if "consume_token(" in source:
        return "magic token consume"
    if _has_body_actor_authority(route):
        return "body actor only"
    if unclassified_dependencies:
        return "unknown dependency: " + ", ".join(sorted(unclassified_dependencies))

    return "none"


def _has_body_actor_authority(route: APIRoute) -> bool:
    for parameter in inspect.signature(route.endpoint).parameters.values():
        annotation = parameter.annotation
        field_names: set[str] = set()

        if hasattr(annotation, "model_fields"):
            field_names = set(annotation.model_fields)
        elif hasattr(annotation, "__fields__"):
            field_names = set(annotation.__fields__)

        if "actor" in field_names:
            return True

    return False


def _format_keys(keys: set[tuple[str, str]] | list[tuple[str, str]]) -> str:
    return "\n".join(f"{method} {path}" for method, path in sorted(keys))


def test_route_policy_inventory_covers_registered_api_v1_routes() -> None:
    inventory_keys = {_route_key(row) for row in _load_inventory_rows()}
    registered_keys = set(_api_v1_routes_by_key())

    missing_from_inventory = registered_keys - inventory_keys
    stale_inventory_entries = inventory_keys - registered_keys

    assert not missing_from_inventory, (
        "Every /api/v1 route must have an explicit route policy inventory entry. "
        "Missing entries:\n"
        + _format_keys(missing_from_inventory)
    )
    assert not stale_inventory_entries, (
        "Route policy inventory contains entries that are not registered by api.main. "
        "Stale entries:\n"
        + _format_keys(stale_inventory_entries)
    )


def test_route_policy_inventory_entries_are_explicit() -> None:
    rows = _load_inventory_rows()
    seen: set[tuple[str, str]] = set()
    errors: list[str] = []

    for row in rows:
        key = _route_key(row)
        method, path = key
        policy = row["intended_policy"]

        if key in seen:
            errors.append(f"{method} {path}: duplicate inventory row")
        seen.add(key)

        if method not in ROUTE_METHODS:
            errors.append(f"{method} {path}: unsupported method")
        if not _is_api_v1_path(path):
            errors.append(f"{method} {path}: path must start with {API_PREFIX}")
        if policy not in ALLOWED_POLICIES:
            errors.append(f"{method} {path}: unknown intended_policy {policy!r}")
        if not row["current_auth_marker"].strip():
            errors.append(f"{method} {path}: current_auth_marker is required")
        if not row["sprint_0_note"].strip():
            errors.append(f"{method} {path}: sprint_0_note is required")
        if (
            method in MUTATING_METHODS
            and policy.startswith("PUBLIC_")
            and not row["public_rationale"].strip()
        ):
            errors.append(
                f"{method} {path}: public mutating routes require public_rationale"
            )

    assert not errors, "Invalid route policy inventory rows:\n" + "\n".join(errors)


def test_route_policy_inventory_current_auth_markers_match_code() -> None:
    routes_by_key = _api_v1_routes_by_key()
    errors: list[str] = []

    for row in _load_inventory_rows():
        key = _route_key(row)
        route = routes_by_key.get(key)
        if route is None:
            continue

        actual_marker = _current_auth_marker(route)
        expected_marker = row["current_auth_marker"]
        if actual_marker != expected_marker:
            method, path = key
            errors.append(
                f"{method} {path}: fixture current_auth_marker={expected_marker!r}, "
                f"code={actual_marker!r}"
            )

    assert not errors, (
        "Route policy current_auth_marker values must match FastAPI route code. "
        "Update the fixture when dependencies or authority semantics change:\n"
        + "\n".join(errors)
    )

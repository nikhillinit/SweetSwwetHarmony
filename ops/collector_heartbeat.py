"""Collector heartbeat state persistence.

Maintains ``state/collectors.json`` as a compact, atomic, runner-neutral
snapshot of collector configuration and the most recent runtime outcome for
each collector.  Runtime heartbeat fields are deliberately separate from
operator configuration fields:

* ``configured_status`` — static/operator state from ``config/collectors.yaml``
* ``last_run_status`` — latest collector runtime result
* ``effective_status`` — health-facing status derived from configuration,
  runtime result, and cadence/staleness

The heartbeat writer must never convert an intentionally disabled or blocked
collector into an enabled/failing collector merely because a run attempted it.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from ops.collector_config import (
    CONFIGURED_BLOCKED_ACCESS,
    CONFIGURED_DISABLED_INTENTIONAL,
    CONFIGURED_DISABLED_MISSING_KEY,
    CONFIGURED_ENABLED,
    CONFIGURED_STATUS_VALUES,
    DEFAULT_CONFIG_PATH,
    INTENTIONAL_CONFIGURED_STATUSES,
    CollectorConfig,
    load_collector_config,
)

SCHEMA_VERSION = 2
DEFAULT_STATE_PATH = Path("state") / "collectors.json"
STATE_PATH_ENV = "COLLECTOR_STATE_PATH"

LAST_RUN_NOT_RUN = "not_run"

_SUCCESS_STATUSES = {"success", "dry_run"}
_PARTIAL_SUCCESS_STATUSES = {"partial_success"}
_FAILURE_STATUSES = {"error", "not_found"}
_SKIP_STATUSES = {"skipped"}
_RUNTIME_STATUSES = (
    _SUCCESS_STATUSES
    | _PARTIAL_SUCCESS_STATUSES
    | _FAILURE_STATUSES
    | _SKIP_STATUSES
    | {LAST_RUN_NOT_RUN}
)

EFFECTIVE_DISABLED_MISSING_KEY = CONFIGURED_DISABLED_MISSING_KEY
EFFECTIVE_DISABLED_INTENTIONAL = CONFIGURED_DISABLED_INTENTIONAL
EFFECTIVE_BLOCKED_ACCESS = CONFIGURED_BLOCKED_ACCESS
EFFECTIVE_HEALTHY = "healthy"
EFFECTIVE_DEGRADED = "degraded"
EFFECTIVE_FAILING = "failing"
EFFECTIVE_SKIPPED = "skipped"
EFFECTIVE_STALE = "stale"
EFFECTIVE_NOT_RUN = LAST_RUN_NOT_RUN
EFFECTIVE_UNKNOWN = "unknown"

_LOCK = threading.RLock()


def utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def isoformat(value: Optional[datetime | str]) -> Optional[str]:
    """Normalize datetimes / strings to ISO-8601 strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return str(value)


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_state_path(state_path: Optional[str | os.PathLike[str]] = None) -> Path:
    """Resolve collector heartbeat state path."""
    if state_path is not None:
        return Path(state_path)
    env_path = os.getenv(STATE_PATH_ENV)
    if env_path:
        return Path(env_path)
    return DEFAULT_STATE_PATH


def empty_state() -> dict[str, Any]:
    """Return an empty schema-v2 state document."""
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": None,
        "collectors": {},
    }


def _runtime_status_from_entry(entry: Mapping[str, Any]) -> str:
    # Accept legacy ``status`` as runtime status during in-memory migration.
    status = str(entry.get("last_run_status") or entry.get("status") or LAST_RUN_NOT_RUN)
    if status not in _RUNTIME_STATUSES:
        return EFFECTIVE_UNKNOWN
    return status


def _is_stale(entry: Mapping[str, Any], now: datetime) -> bool:
    try:
        cadence = float(entry.get("expected_cadence_hours") or 0)
    except (TypeError, ValueError):
        cadence = 0.0
    if cadence <= 0:
        return False

    last_success = _parse_iso_datetime(entry.get("last_success_at"))
    last_finished = _parse_iso_datetime(entry.get("last_finished_at"))
    # Prefer the last successful run.  If the collector has never succeeded but
    # has run before, the runtime status should describe the issue instead of
    # returning a misleading stale result.
    last_reference = last_success or last_finished
    if last_reference is None:
        return False
    return now - last_reference > timedelta(hours=cadence)


def compute_effective_status(
    entry: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
) -> str:
    """Compute status exposed to health tooling.

    Configuration wins over runtime.  In particular, intentional disablement and
    blocked-access states are sticky until an operator changes configuration or
    state explicitly.
    """
    checked_at = now or utc_now()
    configured_status = str(entry.get("configured_status") or CONFIGURED_ENABLED)
    if configured_status == CONFIGURED_DISABLED_MISSING_KEY:
        return EFFECTIVE_DISABLED_MISSING_KEY
    if configured_status == CONFIGURED_DISABLED_INTENTIONAL:
        return EFFECTIVE_DISABLED_INTENTIONAL
    if configured_status == CONFIGURED_BLOCKED_ACCESS:
        return EFFECTIVE_BLOCKED_ACCESS
    if configured_status != CONFIGURED_ENABLED:
        return EFFECTIVE_UNKNOWN

    last_run_status = _runtime_status_from_entry(entry)
    if last_run_status == LAST_RUN_NOT_RUN:
        return EFFECTIVE_NOT_RUN
    if last_run_status in _SUCCESS_STATUSES:
        return EFFECTIVE_STALE if _is_stale(entry, checked_at) else EFFECTIVE_HEALTHY
    if last_run_status in _PARTIAL_SUCCESS_STATUSES:
        return EFFECTIVE_STALE if _is_stale(entry, checked_at) else EFFECTIVE_DEGRADED
    if last_run_status in _FAILURE_STATUSES:
        return EFFECTIVE_FAILING
    if last_run_status in _SKIP_STATUSES:
        return EFFECTIVE_SKIPPED
    return EFFECTIVE_UNKNOWN


def health_for_effective_status(effective_status: str) -> str:
    """Map effective status to a coarse health bucket."""
    if effective_status == EFFECTIVE_HEALTHY:
        return "ok"
    if effective_status in {EFFECTIVE_DEGRADED, EFFECTIVE_STALE}:
        return "degraded"
    if effective_status == EFFECTIVE_FAILING:
        return "failing"
    if effective_status == EFFECTIVE_SKIPPED:
        return "skipped"
    if effective_status in {
        EFFECTIVE_DISABLED_MISSING_KEY,
        EFFECTIVE_DISABLED_INTENTIONAL,
        EFFECTIVE_BLOCKED_ACCESS,
    }:
        return "disabled"
    if effective_status == EFFECTIVE_NOT_RUN:
        return "not_run"
    return "unknown"


def health_for_status(status: str) -> str:
    """Backward-compatible runtime-status health mapping.

    New consumers should prefer ``effective_status`` and
    ``health_for_effective_status``.
    """
    if status == "partial_success":
        return "degraded"
    if status in _SUCCESS_STATUSES:
        return "ok"
    if status in _SKIP_STATUSES:
        return "skipped"
    if status in _FAILURE_STATUSES:
        return "failing"
    if status == LAST_RUN_NOT_RUN:
        return "not_run"
    return "unknown"


def _coerce_configured_status(value: Any) -> str:
    status = str(value or CONFIGURED_ENABLED)
    if status in CONFIGURED_STATUS_VALUES:
        return status
    return CONFIGURED_ENABLED


def _configured_fields_for(
    collector_name: str,
    config: Optional[CollectorConfig],
    previous: Mapping[str, Any],
) -> dict[str, Any]:
    """Return configuration fields for a collector without clobbering intent."""
    previous_configured_status = _coerce_configured_status(previous.get("configured_status"))

    if previous_configured_status in INTENTIONAL_CONFIGURED_STATUSES:
        configured_status = previous_configured_status
        reason = previous.get("configured_status_reason") or previous.get("disabled_reason")
    elif config is not None:
        configured_status, reason = config.resolved_configured_status()
    else:
        configured_status = previous_configured_status
        reason = previous.get("configured_status_reason")

    if config is not None:
        expected_cadence_hours = config.expected_cadence_hours
        description = config.description
        required_env = config.required_env.as_dict()
    else:
        expected_cadence_hours = previous.get("expected_cadence_hours", 24.0)
        description = previous.get("description")
        required_env = previous.get("required_env", {})

    fields: dict[str, Any] = {
        "collector": collector_name,
        "configured_status": configured_status,
        "expected_cadence_hours": expected_cadence_hours,
    }
    if reason:
        fields["configured_status_reason"] = reason
    if description:
        fields["description"] = description
    if required_env:
        fields["required_env"] = required_env
    return fields


def _normalize_entry(
    collector_name: str,
    previous: Mapping[str, Any],
    config: Optional[CollectorConfig],
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Normalize/upgrade a collector entry to schema v2."""
    checked_at = now or utc_now()
    config_fields = _configured_fields_for(collector_name, config, previous)
    runtime_status = _runtime_status_from_entry(previous)

    entry: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        **config_fields,
        "last_run_status": runtime_status,
        "dry_run": bool(previous.get("dry_run", False)),
        "last_started_at": previous.get("last_started_at"),
        "last_finished_at": previous.get("last_finished_at"),
        "last_duration_seconds": previous.get("last_duration_seconds"),
        "last_success_at": previous.get("last_success_at"),
        "last_error_at": previous.get("last_error_at"),
        "last_skip_at": previous.get("last_skip_at"),
        "consecutive_failures": int(previous.get("consecutive_failures") or 0),
        "consecutive_skips": int(previous.get("consecutive_skips") or 0),
        "signals_found": int(previous.get("signals_found") or 0),
        "signals_new": int(previous.get("signals_new") or 0),
        "signals_suppressed": int(previous.get("signals_suppressed") or 0),
        "data_version_before": _optional_int(previous.get("data_version_before")),
        "data_version_after": _optional_int(previous.get("data_version_after")),
        "rows_inserted_this_iter": _optional_int(
            previous.get("rows_inserted_this_iter")
        ),
        "rows_total_last_24h": _optional_int(previous.get("rows_total_last_24h")),
        "collector_class": previous.get("collector_class") or collector_name,
        "api_calls": int(previous.get("api_calls") or 0),
        "rate_limit_hits": int(previous.get("rate_limit_hits") or 0),
        "retries": int(previous.get("retries") or 0),
        "errors": int(previous.get("errors") or 0),
        "error_message": previous.get("error_message"),
        "error_messages": list(previous.get("error_messages") or []),
        "runner": previous.get("runner", "unknown"),
        "updated_at": previous.get("updated_at"),
    }

    # Preserve future/unknown fields, except legacy ambiguous ``status`` which is
    # replaced by ``last_run_status``.
    owned = set(entry) | {"status", "health", "effective_status"}
    for key, value in previous.items():
        if key not in owned:
            entry[key] = value

    entry["effective_status"] = compute_effective_status(entry, now=checked_at)
    entry["health"] = health_for_effective_status(entry["effective_status"])
    return entry


def _configured_state(
    state: Mapping[str, Any],
    *,
    config_path: Optional[str | os.PathLike[str]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Merge loaded state with the collector config registry."""
    checked_at = now or utc_now()
    config = load_collector_config(config_path)

    raw_collectors = state.get("collectors") if isinstance(state, Mapping) else {}
    if not isinstance(raw_collectors, Mapping):
        raw_collectors = {}

    collector_names = set(str(name) for name in raw_collectors) | set(config)
    collectors: dict[str, Any] = {}
    for collector_name in sorted(collector_names):
        previous = raw_collectors.get(collector_name, {})
        if not isinstance(previous, Mapping):
            previous = {}
        collectors[collector_name] = _normalize_entry(
            collector_name,
            previous,
            config.get(collector_name),
            now=checked_at,
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": state.get("updated_at") if isinstance(state, Mapping) else None,
        "collectors": collectors,
    }


def load_collector_state(
    state_path: Optional[str | os.PathLike[str]] = None,
    *,
    config_path: Optional[str | os.PathLike[str]] = None,
    include_configured: bool = True,
) -> dict[str, Any]:
    """Load collector state, tolerating missing or malformed files.

    A malformed existing file is treated as an empty v2 document so the next
    successful heartbeat can self-heal it.  By default, configured collectors
    are materialized in memory even before they have ever run.
    """
    path = get_state_path(state_path)
    if not path.exists():
        state = empty_state()
    else:
        try:
            with path.open("r", encoding="utf-8") as fh:
                raw_state = json.load(fh)
        except (json.JSONDecodeError, OSError):
            raw_state = empty_state()

        if not isinstance(raw_state, Mapping):
            state = empty_state()
        else:
            collectors = raw_state.get("collectors")
            if not isinstance(collectors, Mapping):
                collectors = {}
            state = {
                "schema_version": SCHEMA_VERSION,
                "updated_at": raw_state.get("updated_at"),
                "collectors": collectors,
            }

    if not include_configured:
        return state
    return _configured_state(state, config_path=config_path)


def atomic_write_collector_state(
    state: Mapping[str, Any],
    state_path: Optional[str | os.PathLike[str]] = None,
) -> Path:
    """Atomically write collector state as pretty JSON."""
    path = get_state_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return path


def initialize_collector_state(
    state_path: Optional[str | os.PathLike[str]] = None,
    *,
    config_path: Optional[str | os.PathLike[str]] = None,
    runner: str = "bootstrap",
) -> dict[str, Any]:
    """Write a state file containing all configured collectors.

    This is the bootstrap primitive the Day 2 health CLI can call before
    reading health.  It preserves runtime history and intentional disabled /
    blocked states.
    """
    now = utc_now()
    with _LOCK:
        state = load_collector_state(state_path, config_path=config_path)
        for entry in state.get("collectors", {}).values():
            if isinstance(entry, dict):
                if entry.get("runner") in (None, "unknown"):
                    entry["runner"] = runner
                entry["effective_status"] = compute_effective_status(entry, now=now)
                entry["health"] = health_for_effective_status(entry["effective_status"])
        state["schema_version"] = SCHEMA_VERSION
        state["updated_at"] = isoformat(now)
        atomic_write_collector_state(state, state_path)
        return state


def _as_status_value(result: Any) -> str:
    status = result.status
    return getattr(status, "value", str(status))


def record_collector_heartbeat(
    *,
    result: Any,
    started_at: Optional[datetime | str] = None,
    finished_at: Optional[datetime | str] = None,
    duration_seconds: Optional[float] = None,
    api_calls: Optional[int] = None,
    rate_limit_hits: Optional[int] = None,
    retries: Optional[int] = None,
    errors: Optional[int] = None,
    error_messages: Optional[list[str]] = None,
    data_version_before: Optional[int] = None,
    data_version_after: Optional[int] = None,
    rows_inserted_this_iter: Optional[int] = None,
    rows_total_last_24h: Optional[int] = None,
    collector_class: Optional[str] = None,
    runner: str = "unknown",
    state_path: Optional[str | os.PathLike[str]] = None,
    config_path: Optional[str | os.PathLike[str]] = None,
) -> dict[str, Any]:
    """Record the latest heartbeat for a collector and return the entry.

    The heartbeat updates runtime fields only.  ``configured_status`` is loaded
    from configuration or preserved from the previous entry, with
    ``disabled_intentional`` and ``blocked_access`` treated as sticky operator
    intent.
    """
    status = _as_status_value(result)
    if status not in _RUNTIME_STATUSES:
        status = EFFECTIVE_UNKNOWN

    now = utc_now()
    finished_iso = (
        isoformat(finished_at)
        or isoformat(getattr(result, "timestamp", None))
        or isoformat(now)
    )
    started_iso = isoformat(started_at)

    with _LOCK:
        state = load_collector_state(state_path, config_path=config_path)
        collectors = state.setdefault("collectors", {})
        previous = collectors.get(result.collector, {})
        if not isinstance(previous, Mapping):
            previous = {}

        previous_failures = int(previous.get("consecutive_failures") or 0)
        previous_skips = int(previous.get("consecutive_skips") or 0)

        if status in _SUCCESS_STATUSES or status in _PARTIAL_SUCCESS_STATUSES:
            consecutive_failures = 0
            consecutive_skips = 0
            last_success_at = finished_iso
            last_error_at = previous.get("last_error_at")
            last_skip_at = previous.get("last_skip_at")
        elif status in _SKIP_STATUSES:
            consecutive_failures = 0
            consecutive_skips = previous_skips + 1
            last_success_at = previous.get("last_success_at")
            last_error_at = previous.get("last_error_at")
            last_skip_at = finished_iso
        else:
            consecutive_failures = previous_failures + 1
            consecutive_skips = 0
            last_success_at = previous.get("last_success_at")
            last_error_at = finished_iso
            last_skip_at = previous.get("last_skip_at")

        config = load_collector_config(config_path).get(result.collector)
        configured_fields = _configured_fields_for(result.collector, config, previous)
        proof_rows_inserted = rows_inserted_this_iter
        if proof_rows_inserted is None:
            proof_rows_inserted = getattr(result, "rows_inserted_this_iter", None)
        if proof_rows_inserted is None:
            proof_rows_inserted = getattr(result, "signals_new", None)

        entry: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            **configured_fields,
            "last_run_status": status,
            "dry_run": bool(result.dry_run),
            "last_started_at": started_iso,
            "last_finished_at": finished_iso,
            "last_duration_seconds": duration_seconds,
            "last_success_at": last_success_at,
            "last_error_at": last_error_at,
            "last_skip_at": last_skip_at,
            "consecutive_failures": consecutive_failures,
            "consecutive_skips": consecutive_skips,
            "signals_found": int(getattr(result, "signals_found", 0) or 0),
            "signals_new": int(getattr(result, "signals_new", 0) or 0),
            "signals_suppressed": int(getattr(result, "signals_suppressed", 0) or 0),
            "data_version_before": _optional_int(
                data_version_before
                if data_version_before is not None
                else getattr(result, "data_version_before", None)
            ),
            "data_version_after": _optional_int(
                data_version_after
                if data_version_after is not None
                else getattr(result, "data_version_after", None)
            ),
            "rows_inserted_this_iter": _optional_int(proof_rows_inserted),
            "rows_total_last_24h": _optional_int(
                rows_total_last_24h
                if rows_total_last_24h is not None
                else getattr(result, "rows_total_last_24h", None)
            ),
            "collector_class": (
                collector_class
                or getattr(result, "collector_class", None)
                or str(result.collector)
            ),
            "api_calls": int(
                api_calls
                if api_calls is not None
                else (getattr(result, "api_calls", 0) or 0)
            ),
            "rate_limit_hits": int(rate_limit_hits or 0),
            "retries": int(retries or 0),
            "errors": int(errors or 0),
            "error_message": getattr(result, "error_message", None),
            "error_messages": list(
                error_messages
                or (
                    []
                    if not getattr(result, "error_message", None)
                    else [getattr(result, "error_message")]
                )
            ),
            "runner": runner,
            "updated_at": isoformat(now),
        }

        preserved = {
            k: v
            for k, v in previous.items()
            if k
            not in (
                set(entry)
                | {"status", "health", "effective_status"}
            )
        }
        entry = {**preserved, **entry}
        entry["effective_status"] = compute_effective_status(entry, now=now)
        entry["health"] = health_for_effective_status(entry["effective_status"])

        collectors[result.collector] = entry

        # Recompute effective/health for all configured entries in case cadence
        # or env-derived configured_status changed since the previous write.
        for name, current in list(collectors.items()):
            if not isinstance(current, Mapping):
                continue
            normalized = _normalize_entry(name, current, load_collector_config(config_path).get(name), now=now)
            if name == result.collector:
                normalized = {**normalized, **entry}
                normalized["effective_status"] = compute_effective_status(normalized, now=now)
                normalized["health"] = health_for_effective_status(normalized["effective_status"])
            collectors[name] = normalized

        state["schema_version"] = SCHEMA_VERSION
        state["updated_at"] = entry["updated_at"]
        state["collectors"] = collectors

        atomic_write_collector_state(state, state_path)
        return collectors[result.collector]

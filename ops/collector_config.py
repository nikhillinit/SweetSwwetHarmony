"""Collector configuration source for heartbeat and health tooling.

The heartbeat writer owns runtime fields such as ``last_run_status`` and
``last_success_at``.  This module owns static / operator-controlled fields such
as ``configured_status`` and ``expected_cadence_hours`` so a collector that is
intentionally disabled or blocked is never misreported as a failed runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

import yaml

CONFIG_SCHEMA_VERSION = 1
DEFAULT_CONFIG_PATH = Path("config") / "collectors.yaml"
CONFIG_PATH_ENV = "COLLECTOR_CONFIG_PATH"

CONFIGURED_ENABLED = "enabled"
CONFIGURED_DISABLED_MISSING_KEY = "disabled_missing_key"
CONFIGURED_DISABLED_INTENTIONAL = "disabled_intentional"
CONFIGURED_BLOCKED_ACCESS = "blocked_access"

CONFIGURED_STATUS_VALUES = frozenset(
    {
        CONFIGURED_ENABLED,
        CONFIGURED_DISABLED_MISSING_KEY,
        CONFIGURED_DISABLED_INTENTIONAL,
        CONFIGURED_BLOCKED_ACCESS,
    }
)

INTENTIONAL_CONFIGURED_STATUSES = frozenset(
    {
        CONFIGURED_DISABLED_INTENTIONAL,
        CONFIGURED_BLOCKED_ACCESS,
    }
)


@dataclass(frozen=True)
class EnvRequirement:
    """Environment variables required for a collector to be considered enabled."""

    all_of: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()

    def missing(self, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
        values = env if env is not None else os.environ
        missing: list[str] = []
        for key in self.all_of:
            if not values.get(key):
                missing.append(key)
        if self.any_of and not any(values.get(key) for key in self.any_of):
            missing.append(" or ".join(self.any_of))
        return tuple(missing)

    def as_dict(self) -> dict[str, list[str]]:
        data: dict[str, list[str]] = {}
        if self.all_of:
            data["all_of"] = list(self.all_of)
        if self.any_of:
            data["any_of"] = list(self.any_of)
        return data


@dataclass(frozen=True)
class CollectorConfig:
    """Static collector configuration consumed by heartbeat / health tooling."""

    name: str
    configured_status: str = CONFIGURED_ENABLED
    expected_cadence_hours: float = 24.0
    required_env: EnvRequirement = field(default_factory=EnvRequirement)
    disabled_reason: Optional[str] = None
    description: Optional[str] = None

    def resolved_configured_status(
        self,
        env: Mapping[str, str] | None = None,
    ) -> tuple[str, Optional[str]]:
        """Return effective configured status after env-key checks.

        Non-enabled statuses in the config are operator intent and are returned
        as-is.  Enabled collectors with missing required env vars become
        ``disabled_missing_key``.
        """
        if self.configured_status != CONFIGURED_ENABLED:
            return self.configured_status, self.disabled_reason

        missing = self.required_env.missing(env)
        if missing:
            return (
                CONFIGURED_DISABLED_MISSING_KEY,
                "Missing required environment: " + ", ".join(missing),
            )
        return CONFIGURED_ENABLED, None


def get_config_path(config_path: Optional[str | os.PathLike[str]] = None) -> Path:
    """Resolve collector config path."""
    if config_path is not None:
        return Path(config_path)
    env_path = os.getenv(CONFIG_PATH_ENV)
    if env_path:
        return Path(env_path)
    return DEFAULT_CONFIG_PATH


def _coerce_status(value: object, *, collector_name: str) -> str:
    status = str(value or CONFIGURED_ENABLED)
    if status not in CONFIGURED_STATUS_VALUES:
        raise ValueError(
            f"Invalid configured_status for collector {collector_name!r}: {status!r}"
        )
    return status


def _coerce_cadence(value: object, *, collector_name: str) -> float:
    try:
        cadence = float(value if value is not None else 24.0)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid expected_cadence_hours for collector {collector_name!r}: {value!r}"
        ) from exc
    if cadence < 0:
        raise ValueError(
            f"expected_cadence_hours must be >= 0 for collector {collector_name!r}"
        )
    return cadence


def _as_str_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item) for item in value if str(item))
    return (str(value),)


def _parse_env_requirement(raw: object) -> EnvRequirement:
    if raw is None:
        return EnvRequirement()
    if isinstance(raw, Mapping):
        return EnvRequirement(
            all_of=_as_str_tuple(raw.get("all_of")),
            any_of=_as_str_tuple(raw.get("any_of")),
        )
    return EnvRequirement(all_of=_as_str_tuple(raw))


def _parse_collector_config(name: str, raw: object) -> CollectorConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"Collector config for {name!r} must be a mapping")
    return CollectorConfig(
        name=name,
        configured_status=_coerce_status(raw.get("configured_status"), collector_name=name),
        expected_cadence_hours=_coerce_cadence(
            raw.get("expected_cadence_hours"), collector_name=name
        ),
        required_env=_parse_env_requirement(raw.get("required_env")),
        disabled_reason=(
            None if raw.get("disabled_reason") is None else str(raw.get("disabled_reason"))
        ),
        description=None if raw.get("description") is None else str(raw.get("description")),
    )


def load_collector_config(
    config_path: Optional[str | os.PathLike[str]] = None,
) -> dict[str, CollectorConfig]:
    """Load collector configuration from YAML.

    Missing files are tolerated and return an empty config mapping.  Malformed
    YAML or invalid statuses are surfaced to callers because configuration
    mistakes should be fixed before health checks are trusted.
    """
    path = get_config_path(config_path)
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as fh:
        raw_doc = yaml.safe_load(fh) or {}

    if not isinstance(raw_doc, Mapping):
        raise ValueError(f"Collector config {path} must be a mapping")

    raw_collectors = raw_doc.get("collectors", {})
    if not isinstance(raw_collectors, Mapping):
        raise ValueError(f"Collector config {path} field 'collectors' must be a mapping")

    configs: dict[str, CollectorConfig] = {}
    for name, raw in raw_collectors.items():
        collector_name = str(name)
        configs[collector_name] = _parse_collector_config(collector_name, raw)
    return configs

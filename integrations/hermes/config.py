from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = Path(".claude/hermes/model-routing.json")

RiskLevel = Literal["low", "medium", "high"]
HermesMode = Literal["plan-only", "dry-run", "preflight-only", "execute"]


class HermesBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ExecutorConfig(HermesBaseModel):
    provider: str
    display_name: str = Field(alias="displayName")
    enabled: bool = True
    required: bool = False
    binary: str | None = None
    env: list[str] = Field(default_factory=list)
    sunset_date: date | None = Field(default=None, alias="sunsetDate")
    supports_execute: bool = Field(default=True, alias="supportsExecute")


class DeferredExecutorConfig(HermesBaseModel):
    provider: str
    reason: str
    sunset_date: date | None = Field(default=None, alias="sunsetDate")


class PhaseConfig(HermesBaseModel):
    risk_order: list[RiskLevel] = Field(alias="riskOrder", min_length=1)
    preferred_executors: list[str] = Field(alias="preferredExecutors", min_length=1)
    fallback_executors: list[str] = Field(default_factory=list, alias="fallbackExecutors")


class SpecialistConfig(HermesBaseModel):
    keywords: list[str] = Field(min_length=1)
    risk: RiskLevel
    preferred_executors: list[str] = Field(alias="preferredExecutors", min_length=1)
    fallback_executors: list[str] = Field(default_factory=list, alias="fallbackExecutors")


class RiskDefaultsConfig(HermesBaseModel):
    no_specialist: RiskLevel = Field(alias="noSpecialist")
    high_risk_keywords: list[str] = Field(default_factory=list, alias="highRiskKeywords")


class RoutingRulesConfig(HermesBaseModel):
    manual_override_allowed: bool = Field(default=True, alias="manualOverrideAllowed")
    fallback_order: list[str] = Field(alias="fallbackOrder", min_length=1)
    unknown_task_executor: str = Field(alias="unknownTaskExecutor")


class GateSpec(HermesBaseModel):
    name: str
    command: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=120, alias="timeoutSeconds", ge=1)


class GatesConfig(HermesBaseModel):
    preflight: list[GateSpec] = Field(default_factory=list)
    postflight: list[GateSpec] = Field(default_factory=list)


class LedgerConfig(HermesBaseModel):
    root: str = "ai-logs/hermes"
    redaction_patterns: list[str] = Field(default_factory=list, alias="redactionPatterns")
    lock_path: str = Field(default="ai-logs/hermes/hermes.lock", alias="lockPath")


class RoutingConfig(HermesBaseModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    executors: dict[str, ExecutorConfig]
    deferred_executors: dict[str, DeferredExecutorConfig] = Field(
        default_factory=dict,
        alias="deferredExecutors",
    )
    phases: dict[str, PhaseConfig]
    specialists: dict[str, SpecialistConfig]
    risk_defaults: RiskDefaultsConfig = Field(alias="riskDefaults")
    routing: RoutingRulesConfig
    gates: GatesConfig
    ledger: LedgerConfig
    modes: list[HermesMode] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> "RoutingConfig":
        if not self.executors:
            raise ValueError("executors must not be empty")

        known = set(self.executors)
        deferred = set(self.deferred_executors)

        def check_refs(owner: str, refs: list[str], *, fallback: bool = False) -> None:
            for ref in refs:
                if ref in deferred:
                    message = f"{owner} references deferred executor {ref!r}"
                    if fallback:
                        message = f"{owner} has deferred executor {ref!r} in fallback path"
                    raise ValueError(message)
                if ref not in known:
                    raise ValueError(f"{owner} has unknown executor reference {ref!r}")

        for phase_name, phase in self.phases.items():
            check_refs(f"phase {phase_name}.preferredExecutors", phase.preferred_executors)
            check_refs(
                f"phase {phase_name}.fallbackExecutors",
                phase.fallback_executors,
                fallback=True,
            )

        for specialist_name, specialist in self.specialists.items():
            check_refs(
                f"specialist {specialist_name}.preferredExecutors",
                specialist.preferred_executors,
            )
            check_refs(
                f"specialist {specialist_name}.fallbackExecutors",
                specialist.fallback_executors,
                fallback=True,
            )

        check_refs("routing.fallbackOrder", self.routing.fallback_order, fallback=True)
        check_refs("routing.unknownTaskExecutor", [self.routing.unknown_task_executor])
        return self


def resolve_config_path(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path)

    env_path = os.environ.get("HERMES_CONFIG")
    if env_path:
        return Path(env_path)

    return PROJECT_ROOT / DEFAULT_CONFIG_PATH


def load_config(path: Path | str | None = None) -> RoutingConfig:
    config_path = resolve_config_path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return RoutingConfig.model_validate(data)

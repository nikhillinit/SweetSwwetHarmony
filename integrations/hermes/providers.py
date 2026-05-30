from __future__ import annotations

import importlib
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import DeferredExecutorConfig, ExecutorConfig, RoutingConfig

WRAPPER_IMPORTS = {
    "codex": "integrations.codex_wrapper",
    "gemini": "integrations.gemini_antigravity_client",
    "antigravity": "integrations.gemini_antigravity_client",
    "kimi": "integrations.llm_cli.kimi",
}


@dataclass(frozen=True)
class ProviderCheck:
    name: str
    ok: bool
    required: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "required": self.required,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    provider: str
    required: bool
    enabled: bool
    checks: tuple[ProviderCheck, ...]

    @property
    def checks_by_name(self) -> dict[str, ProviderCheck]:
        return {check.name: check for check in self.checks}

    @property
    def success(self) -> bool:
        return all(check.ok or not check.required for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "required": self.required,
            "enabled": self.enabled,
            "success": self.success,
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True)
class DeferredProviderStatus:
    name: str
    provider: str
    reason: str
    sunset_date: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "reason": self.reason,
            "sunsetDate": self.sunset_date,
        }


@dataclass(frozen=True)
class ProviderReport:
    providers: dict[str, ProviderStatus]
    deferred: dict[str, DeferredProviderStatus]
    system_checks: tuple[ProviderCheck, ...]

    @property
    def success(self) -> bool:
        return all(provider.success for provider in self.providers.values()) and all(
            check.ok or not check.required for check in self.system_checks
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "providers": {
                name: status.to_dict()
                for name, status in sorted(self.providers.items())
            },
            "deferred": {
                name: status.to_dict()
                for name, status in sorted(self.deferred.items())
            },
            "systemChecks": [check.to_dict() for check in self.system_checks],
        }

    def to_text(self) -> str:
        lines = ["Hermes Provider Doctor", "=" * 40, f"Success: {self.success}"]
        for name, status in sorted(self.providers.items()):
            lines.append("")
            lines.append(f"{name} ({status.provider})")
            for check in status.checks:
                marker = "OK" if check.ok else "WARN"
                if check.required and not check.ok:
                    marker = "FAIL"
                lines.append(f"  [{marker}] {check.name}: {check.detail}")
        if self.deferred:
            lines.append("")
            lines.append("Deferred providers")
            for name, status in sorted(self.deferred.items()):
                lines.append(f"  - {name}: {status.reason}")
        return "\n".join(lines)


def doctor(config: RoutingConfig, strict: bool = False) -> ProviderReport:
    del strict  # Strict affects CLI exit behavior; report contents stay identical.
    providers = {
        name: _provider_status(name, executor)
        for name, executor in config.executors.items()
    }
    deferred = {
        name: _deferred_status(name, executor)
        for name, executor in config.deferred_executors.items()
    }
    system_checks = (_fallback_check(config),)
    return ProviderReport(
        providers=providers,
        deferred=deferred,
        system_checks=system_checks,
    )


def _provider_status(name: str, executor: ExecutorConfig) -> ProviderStatus:
    checks: list[ProviderCheck] = []
    checks.append(_wrapper_import_check(name, executor))
    if executor.binary:
        checks.append(_binary_check(executor.binary, executor.required))
    for env_name in executor.env:
        checks.append(_env_check(env_name, executor.required))
    checks.append(_sunset_check(executor))
    return ProviderStatus(
        name=name,
        provider=executor.provider,
        required=executor.required,
        enabled=executor.enabled,
        checks=tuple(checks),
    )


def _wrapper_import_check(name: str, executor: ExecutorConfig) -> ProviderCheck:
    module_name = WRAPPER_IMPORTS.get(executor.provider)
    if module_name is None:
        return ProviderCheck(
            name="wrapper_import",
            ok=True,
            required=False,
            detail="no wrapper required for doctor",
        )
    try:
        importlib.import_module(module_name)
    except ImportError as exc:
        return ProviderCheck(
            name="wrapper_import",
            ok=False,
            required=executor.required,
            detail=str(exc),
        )
    return ProviderCheck(
        name="wrapper_import",
        ok=True,
        required=executor.required,
        detail=module_name,
    )


def _binary_check(binary: str, required: bool) -> ProviderCheck:
    resolved = shutil.which(binary)
    return ProviderCheck(
        name="binary",
        ok=resolved is not None,
        required=required,
        detail=resolved or f"{binary} not found on PATH",
    )


def _env_check(env_name: str, required: bool) -> ProviderCheck:
    present = bool(os.environ.get(env_name))
    return ProviderCheck(
        name=f"env:{env_name}",
        ok=present,
        required=required,
        detail="present" if present else "missing",
    )


def _sunset_check(executor: ExecutorConfig) -> ProviderCheck:
    if executor.sunset_date is None:
        return ProviderCheck(
            name="sunset",
            ok=True,
            required=executor.required,
            detail="no sunset date configured",
        )
    today = datetime.now(timezone.utc).date()
    ok = executor.sunset_date >= today
    return ProviderCheck(
        name="sunset",
        ok=ok,
        required=executor.required,
        detail=executor.sunset_date.isoformat(),
    )


def _deferred_status(
    name: str,
    executor: DeferredExecutorConfig,
) -> DeferredProviderStatus:
    return DeferredProviderStatus(
        name=name,
        provider=executor.provider,
        reason=executor.reason,
        sunset_date=executor.sunset_date.isoformat() if executor.sunset_date else None,
    )


def _fallback_check(config: RoutingConfig) -> ProviderCheck:
    known = set(config.executors)
    deferred = set(config.deferred_executors)
    refs = set(config.routing.fallback_order)
    ok = refs <= known and not (refs & deferred)
    detail = "fallback targets defined" if ok else "fallback targets invalid"
    return ProviderCheck(
        name="fallback_consistency",
        ok=ok,
        required=True,
        detail=detail,
    )

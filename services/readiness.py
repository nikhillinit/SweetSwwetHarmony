"""Readiness / health evaluation primitives.

This is intentionally small and dependency-light.

Goal: provide a stable contract for representing health checks across
CLI entrypoints (human output + strict JSON output) without forcing a
full services refactor.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional


class CheckScope(str, Enum):
    """High-level ownership of a check."""

    CORE = "core"
    EXTERNAL = "external"


class CheckStatus(str, Enum):
    """Normalized check status."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    """A single health/readiness check result."""

    name: str
    scope: CheckScope
    status: CheckStatus
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    @property
    def passed(self) -> bool:
        return self.status != CheckStatus.FAIL

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "scope": self.scope.value,
            "status": self.status.value,
            "passed": self.passed,
            "message": self.message,
        }
        if self.details:
            out["details"] = self.details
        return out


class ReadinessReport:
    """A collection of checks with an overall status."""

    def __init__(self, checks: Iterable[CheckResult]):
        self.checks: List[CheckResult] = list(checks)

    def _has(self, *, scope: Optional[CheckScope] = None, status: Optional[CheckStatus] = None) -> bool:
        for c in self.checks:
            if scope is not None and c.scope != scope:
                continue
            if status is not None and c.status != status:
                continue
            return True
        return False

    @property
    def core_status(self) -> str:
        if self._has(scope=CheckScope.CORE, status=CheckStatus.FAIL):
            return "UNHEALTHY"
        if self._has(scope=CheckScope.CORE, status=CheckStatus.WARN):
            return "DEGRADED"
        return "HEALTHY"

    @property
    def integration_status(self) -> str:
        if self._has(scope=CheckScope.EXTERNAL, status=CheckStatus.FAIL):
            return "UNHEALTHY"
        if self._has(scope=CheckScope.EXTERNAL, status=CheckStatus.WARN):
            return "DEGRADED"
        return "HEALTHY"

    @property
    def overall_status(self) -> str:
        if self._has(status=CheckStatus.FAIL):
            return "UNHEALTHY"
        if self._has(status=CheckStatus.WARN):
            return "DEGRADED"
        return "HEALTHY"

    def exit_code(self, *, fail_on_degraded: bool = False) -> int:
        """Compute a process exit code.

        Default:
          - HEALTHY or DEGRADED => 0
          - UNHEALTHY           => 1
        """

        if self.overall_status == "UNHEALTHY":
            return 1
        if fail_on_degraded and self.overall_status == "DEGRADED":
            return 1
        return 0

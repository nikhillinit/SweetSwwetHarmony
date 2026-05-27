"""Minimal Hermes Track A task contract.

This first slice defines the task interface and non-mutating result shape only.
State-changing runners add their safety mechanics in follow-up PRs with their
own fixtures.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Iterable, Literal

from integrations.hermes.config import PROJECT_ROOT

TaskMode = Literal["plan-only", "preflight-only", "dry-run", "execute"]
TaskRisk = Literal["low", "medium", "high", "critical"]

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_GATE_FAILURE = 4


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class TaskResult:
    task: str
    mode: TaskMode
    exit_code: int
    status: str
    plan: dict[str, Any]
    checks: tuple[CheckResult, ...] = ()
    outputs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "mode": self.mode,
            "exitCode": self.exit_code,
            "status": self.status,
            "plan": self.plan,
            "checks": [check.to_dict() for check in self.checks],
            "outputs": self.outputs,
        }


@dataclass(frozen=True)
class TaskContext:
    task: "HermesTask"
    mode: TaskMode
    args: argparse.Namespace
    root: Path = PROJECT_ROOT

    def resolve(self, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        path = Path(value)
        if path.is_absolute():
            return path
        return self.root / path


class HermesTask:
    """Base interface for non-mutating Track A task planning and review."""

    name: ClassVar[str]
    description: ClassVar[str] = "Hermes Track A task"
    risk_level: ClassVar[TaskRisk] = "medium"
    supported_modes: ClassVar[tuple[TaskMode, ...]] = (
        "plan-only",
        "preflight-only",
        "dry-run",
    )
    mutates_external_systems: ClassVar[bool] = False

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Add task-specific CLI arguments."""

    def supports_mode(self, mode: TaskMode) -> bool:
        return mode in self.supported_modes

    def build_context(self, args: argparse.Namespace, *, mode: TaskMode) -> TaskContext:
        return TaskContext(task=self, mode=mode, args=args)

    def plan(self, context: TaskContext) -> dict[str, Any]:
        return self._base_plan(context)

    def preflight(self, context: TaskContext, plan: dict[str, Any]) -> Iterable[CheckResult]:
        return ()

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        return {"dryRun": True, "mutationCommitted": False}

    def _base_plan(self, context: TaskContext) -> dict[str, Any]:
        return {
            "task": self.name,
            "mode": context.mode,
            "description": self.description,
            "risk_level": self.risk_level,
            "supported_modes": list(self.supported_modes),
            "mutation": {
                "allowed": False,
                "external_systems": [],
            },
        }

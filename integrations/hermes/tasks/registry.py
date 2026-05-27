from __future__ import annotations

import argparse
from typing import Any

from .base import (
    EXIT_GATE_FAILURE,
    EXIT_INVALID,
    EXIT_OK,
    CheckResult,
    HermesTask,
    TaskContext,
    TaskMode,
    TaskResult,
)


class ContractCheckTask(HermesTask):
    """Non-mutating placeholder that proves the Track A task contract."""

    name = "contract-check"
    description = "Plan-only/dry-run contract check for Hermes Track A tasks."
    risk_level = "low"
    supported_modes = ("plan-only", "preflight-only", "dry-run")

    def plan(self, context: TaskContext) -> dict[str, Any]:
        plan = self._base_plan(context)
        plan.update(
            {
                "contract_version": 1,
                "preflight_gates": ["contract_loaded"],
                "postflight_gates": [],
            }
        )
        return plan

    def preflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> list[CheckResult]:
        return [
            CheckResult(
                "contract_loaded",
                True,
                "Hermes Track A task contract is importable.",
            )
        ]

    def dry_run(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        return {"dryRun": True, "mutationCommitted": False}


_TASKS: dict[str, type[HermesTask]] = {
    ContractCheckTask.name: ContractCheckTask,
}

TASK_REGISTRY = _TASKS


def registered_task_names() -> list[str]:
    return sorted(_TASKS)


def get_task(name: str) -> HermesTask:
    try:
        return _TASKS[name]()
    except KeyError as exc:
        expected = ", ".join(registered_task_names())
        raise ValueError(
            f"unknown Hermes task {name!r}; expected one of {expected}"
        ) from exc


def add_task_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("task_name", choices=registered_task_names())
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--plan-only", action="store_true")
    mode_group.add_argument("--preflight-only", action="store_true")
    mode_group.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")


def mode_from_args(args: argparse.Namespace) -> TaskMode:
    if getattr(args, "dry_run", False):
        return "dry-run"
    if getattr(args, "preflight_only", False):
        return "preflight-only"
    return "plan-only"


def run_registered_task(args: argparse.Namespace) -> TaskResult:
    task = get_task(args.task_name)
    mode = mode_from_args(args)
    if not task.supports_mode(mode):
        return TaskResult(
            task=task.name,
            mode=mode,
            exit_code=EXIT_INVALID,
            status="unsupported_mode",
            plan={},
        )

    context = task.build_context(args, mode=mode)
    plan = task.plan(context)
    checks = tuple(task.preflight(context, plan)) if mode != "plan-only" else ()
    if checks and not all(check.passed for check in checks):
        return TaskResult(
            task=task.name,
            mode=mode,
            exit_code=EXIT_GATE_FAILURE,
            status="preflight_failed",
            plan=plan,
            checks=checks,
        )

    outputs = task.dry_run(context, plan) if mode == "dry-run" else {}
    status_by_mode = {
        "plan-only": "planned",
        "preflight-only": "preflight_passed",
        "dry-run": "dry_run_passed",
    }
    return TaskResult(
        task=task.name,
        mode=mode,
        exit_code=EXIT_OK,
        status=status_by_mode[mode],
        plan=plan,
        checks=checks,
        outputs=outputs,
    )

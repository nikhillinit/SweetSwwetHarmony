from __future__ import annotations

import argparse
from typing import Any

from .base import (
    CheckResult,
    HermesTask,
    TaskContext,
    TaskMode,
    TaskResult,
)
from .restore_db import RestoreDbTask
from .suppression_sync import SuppressionSyncTask


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
    RestoreDbTask.name: RestoreDbTask,
    SuppressionSyncTask.name: SuppressionSyncTask,
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
    parser.add_argument("--config", default=None, help="Path to Hermes routing config")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--plan-only", action="store_true")
    mode_group.add_argument("--preflight-only", action="store_true")
    mode_group.add_argument("--dry-run", action="store_true")
    mode_group.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--ack-risk",
        default=None,
        help="Task-specific risk acknowledgement token",
    )
    parser.add_argument("--lock-ttl-seconds", type=int, default=900)
    parser.add_argument("--actor-type", default="operator")
    parser.add_argument("--actor-id", default=None)
    parser.add_argument("--json", action="store_true", dest="json_output")

    parser.add_argument("--backup")
    parser.add_argument("--target", default="signals.db")
    parser.add_argument("--allow-target-create", action="store_true")
    parser.add_argument("--handle-sidecars", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--api-url")
    parser.add_argument("--expected-schema-version", type=int)
    parser.add_argument("--min-row-count", type=int, default=0)
    parser.add_argument("--db-path", default="signals.db")
    parser.add_argument("--ttl-days", type=int, default=7)
    parser.add_argument("--delete-stale", action="store_true")
    parser.add_argument("--max-removals", type=int, default=25)


def mode_from_args(args: argparse.Namespace) -> TaskMode:
    if getattr(args, "execute", False):
        return "execute"
    if getattr(args, "dry_run", False):
        return "dry-run"
    if getattr(args, "preflight_only", False):
        return "preflight-only"
    return "plan-only"


def run_registered_task(args: argparse.Namespace) -> TaskResult:
    task = get_task(args.task_name)
    return task.run(
        args,
        mode=mode_from_args(args),
        config_path=getattr(args, "config", None),
        ack_risk=getattr(args, "ack_risk", None),
    )

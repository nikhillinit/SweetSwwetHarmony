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
from .collector_promote import CollectorPromoteTask
from .config_promote import ConfigPromoteTask
from .deliberation import DeliberationTask
from .governance import GovernanceTask
from .incident_response import INCIDENT_PHASES, IncidentResponseTask
from .ledger_audit import LedgerAuditTask
from .outbox_purge import OutboxPurgeTask
from .restore_db import RestoreDbTask
from .shadow_validate import ShadowValidateTask
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
    CollectorPromoteTask.name: CollectorPromoteTask,
    ConfigPromoteTask.name: ConfigPromoteTask,
    ContractCheckTask.name: ContractCheckTask,
    DeliberationTask.name: DeliberationTask,
    GovernanceTask.name: GovernanceTask,
    IncidentResponseTask.name: IncidentResponseTask,
    LedgerAuditTask.name: LedgerAuditTask,
    OutboxPurgeTask.name: OutboxPurgeTask,
    RestoreDbTask.name: RestoreDbTask,
    ShadowValidateTask.name: ShadowValidateTask,
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
    cleanup_group = parser.add_mutually_exclusive_group()
    cleanup_group.add_argument(
        "--delete-stale",
        dest="delete_stale",
        action="store_true",
        default=None,
    )
    cleanup_group.add_argument(
        "--skip-clean-expired",
        dest="delete_stale",
        action="store_false",
    )
    parser.add_argument("--max-removals", type=int, default=25)
    parser.add_argument("--feature")
    parser.add_argument("--from-state")
    parser.add_argument("--target-state")
    parser.add_argument("--reason")
    parser.add_argument("--regret-check-date")
    parser.add_argument("--effective-at")
    parser.add_argument("--repair-source")
    parser.add_argument("--rollback-ticket")
    parser.add_argument("--incident-id")
    parser.add_argument("--direct-db")
    parser.add_argument("--state-source")
    parser.add_argument("--state-verify-attempts", type=int, default=3)
    parser.add_argument("--state-verify-delay-seconds", type=float, default=1.0)
    DeliberationTask.add_arguments(parser)
    parser.add_argument(
        "--phase-name",
        dest="incident_phase",
        choices=INCIDENT_PHASES,
        default="freeze",
    )
    parser.add_argument("--artifact-root", default="ops/artifacts/maintenance")
    ShadowValidateTask.add_arguments(parser)
    CollectorPromoteTask.add_arguments(parser)
    ConfigPromoteTask.add_arguments(parser)
    OutboxPurgeTask.add_arguments(parser)
    LedgerAuditTask.add_arguments(parser)


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

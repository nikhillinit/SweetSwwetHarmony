"""Hermes Track A task contract.

The base contract keeps PR1's non-mutating placeholder path intact while adding
the narrow runtime primitives needed by the restore-db runner: ledger artifacts,
task locks, explicit execute acknowledgement, and repair prompts.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Coroutine, Iterable, Literal, TypeVar

from integrations.hermes.config import PROJECT_ROOT, RoutingConfig, load_config
from integrations.hermes.ledger import HermesLedger, HermesRun
from integrations.hermes.locks import (
    HermesLock,
    HermesLockError,
    assert_canonical_lock_order,
)
from integrations.hermes.plan_contract import (
    CURRENT_CONTRACT_VERSION,
    attach_plan_contract,
)
from storage.db_paths import guard_db_path, resolve_canonical_db_path

TaskMode = Literal["plan-only", "preflight-only", "dry-run", "execute"]
TaskRisk = Literal["low", "medium", "high", "critical"]

EXIT_OK = 0
EXIT_TASK_FAILURE = 1
EXIT_INVALID = 2
EXIT_GATE_FAILURE = 4
EXIT_LOCK_HELD = 6
EXIT_LEDGER_FAILURE = 7
EXIT_ACK_REQUIRED = 75
T = TypeVar("T")
SQLITE_COUNT_TABLES = {"signals": '"signals"'}


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
    run_id: str | None = None
    run_dir: str | None = None
    checks: tuple[CheckResult, ...] = ()
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "mode": self.mode,
            "exitCode": self.exit_code,
            "runId": self.run_id,
            "runDir": self.run_dir,
            "status": self.status,
            "plan": self.plan,
            "checks": [check.to_dict() for check in self.checks],
            "outputs": self.outputs,
            "error": self.error,
        }


class TaskFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        exit_code: int = EXIT_TASK_FAILURE,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.evidence = evidence or {}


@dataclass
class TaskContext:
    task: "HermesTask"
    mode: TaskMode
    args: argparse.Namespace
    root: Path = PROJECT_ROOT
    config: RoutingConfig | None = None
    ledger: HermesLedger | None = None
    run: HermesRun | None = None
    acquired_locks: list[HermesLock] = field(default_factory=list)

    @property
    def run_dir(self) -> Path:
        if self.run is None:
            raise RuntimeError("task context has no ledger run")
        return self.run.run_dir

    def artifact_path(self, relative_path: str) -> Path:
        path = self.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, relative_path: str, payload: dict[str, Any]) -> Path:
        if self.ledger is None or self.run is None:
            raise RuntimeError("task context has no ledger")
        return self.ledger.write_json_artifact(self.run, relative_path, payload)

    def write_text(self, relative_path: str, text: str) -> Path:
        if self.ledger is None or self.run is None:
            raise RuntimeError("task context has no ledger")
        return self.ledger.write_text_artifact(self.run, relative_path, text)

    def resolve(self, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        path = Path(value)
        if path.is_absolute():
            return path
        return self.root / path


def resolve_task_db_path(context: TaskContext, value: str | Path | None) -> Path:
    """Resolve a Hermes task DB target through the canonical in-tree guard."""
    if value in (None, ""):
        return resolve_canonical_db_path()
    resolved = context.resolve(value)
    if resolved is None:
        return resolve_canonical_db_path()
    return guard_db_path(resolved.expanduser().resolve())


class HermesTask:
    """Base interface for Hermes Track A tasks."""

    name: ClassVar[str]
    description: ClassVar[str] = "Hermes Track A task"
    risk_level: ClassVar[TaskRisk] = "medium"
    ack_risk_token: ClassVar[str | None] = None
    supported_modes: ClassVar[tuple[TaskMode, ...]] = (
        "plan-only",
        "preflight-only",
        "dry-run",
    )
    required_locks: ClassVar[tuple[str, ...]] = ()
    mutates_external_systems: ClassVar[bool] = False
    ledger_backed: ClassVar[bool] = False

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Add task-specific CLI arguments."""

    def supports_mode(self, mode: TaskMode) -> bool:
        return mode in self.supported_modes

    def build_context(self, args: argparse.Namespace, *, mode: TaskMode) -> TaskContext:
        return TaskContext(task=self, mode=mode, args=args)

    def run(
        self,
        args: argparse.Namespace,
        *,
        mode: TaskMode,
        config_path: str | Path | None = None,
        ack_risk: str | None = None,
    ) -> TaskResult:
        if not self.ledger_backed:
            return self._run_basic(args, mode=mode)
        return self._run_ledger_backed(
            args,
            mode=mode,
            config_path=config_path,
            ack_risk=ack_risk,
        )

    def plan(self, context: TaskContext) -> dict[str, Any]:
        return self._base_plan(context)

    def preflight(self, context: TaskContext, plan: dict[str, Any]) -> Iterable[CheckResult]:
        return ()

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        return {"dryRun": True, "mutationCommitted": False}

    def execute(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        raise TaskFailure(f"{self.name} has no execute implementation")

    def required_ack_token(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> str | None:
        return self.ack_risk_token

    def postflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        outputs: dict[str, Any],
    ) -> Iterable[CheckResult]:
        return ()

    def _base_plan(self, context: TaskContext) -> dict[str, Any]:
        try:
            assert_canonical_lock_order(self.required_locks, task_name=self.name)
        except HermesLockError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "contractVersion": CURRENT_CONTRACT_VERSION,
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

    def _run_basic(self, args: argparse.Namespace, *, mode: TaskMode) -> TaskResult:
        if not self.supports_mode(mode):
            return TaskResult(
                task=self.name,
                mode=mode,
                exit_code=EXIT_INVALID,
                status="unsupported_mode",
                plan={},
            )

        context = self.build_context(args, mode=mode)
        plan = attach_plan_contract(self.plan(context))
        checks = tuple(self.preflight(context, plan)) if mode != "plan-only" else ()
        if checks and not all(check.passed for check in checks):
            return TaskResult(
                task=self.name,
                mode=mode,
                exit_code=EXIT_GATE_FAILURE,
                status="preflight_failed",
                plan=plan,
                checks=checks,
            )

        outputs = self.dry_run(context, plan) if mode == "dry-run" else {}
        status_by_mode = {
            "plan-only": "planned",
            "preflight-only": "preflight_passed",
            "dry-run": "dry_run_passed",
            "execute": "unsupported_mode",
        }
        return TaskResult(
            task=self.name,
            mode=mode,
            exit_code=EXIT_OK,
            status=status_by_mode[mode],
            plan=plan,
            checks=checks,
            outputs=outputs,
        )

    def _run_ledger_backed(
        self,
        args: argparse.Namespace,
        *,
        mode: TaskMode,
        config_path: str | Path | None,
        ack_risk: str | None,
    ) -> TaskResult:
        if not self.supports_mode(mode):
            return TaskResult(
                task=self.name,
                mode=mode,
                exit_code=EXIT_INVALID,
                status="unsupported_mode",
                plan={},
            )

        try:
            config = load_config(config_path)
            ledger = HermesLedger(config.ledger, root=_resolve_repo_path(config.ledger.root))
            run = ledger.create_run(
                plan={
                    "contractVersion": CURRENT_CONTRACT_VERSION,
                    "task": self.name,
                    "mode": mode,
                    "risk": self.risk_level,
                    "supportedModes": list(self.supported_modes),
                },
                prompt=f"Hermes Track A task {self.name} ({mode})",
                metadata={
                    "mode": mode,
                    "taskType": "track-a",
                    "risk": self.risk_level,
                },
            )
        except OSError as exc:
            return TaskResult(
                task=self.name,
                mode=mode,
                exit_code=EXIT_LEDGER_FAILURE,
                status="ledger_failed",
                plan={},
                error=str(exc),
            )

        context = TaskContext(
            task=self,
            mode=mode,
            args=args,
            config=config,
            ledger=ledger,
            run=run,
        )
        plan = self.plan(context)
        plan.setdefault("task", self.name)
        plan.setdefault("mode", mode)
        plan.setdefault("risk_level", self.risk_level)
        plan.setdefault("locks_required", list(self.required_locks))
        plan.setdefault("ack_risk_required", self.ack_risk_token is not None)
        plan.setdefault("ack_risk_token", self.ack_risk_token)
        plan = attach_plan_contract(plan)
        context.write_json("task_plan.json", plan)
        self._write_record(context, plan, status="planned", checks=(), outputs={})

        if mode == "plan-only":
            self._write_markdown_plan(context, plan)
            return TaskResult(
                task=self.name,
                mode=mode,
                exit_code=EXIT_OK,
                status="planned",
                plan=plan,
                run_id=run.run_id,
                run_dir=str(run.run_dir),
            )

        if not self._acquire_locks(context):
            holders = _lock_holders(context)
            context.write_json("lock_conflict.json", {"holders": holders})
            self._write_record(
                context,
                plan,
                status="lock_held",
                checks=(),
                outputs={"holders": holders},
            )
            return TaskResult(
                task=self.name,
                mode=mode,
                exit_code=EXIT_LOCK_HELD,
                status="lock_held",
                plan=plan,
                run_id=run.run_id,
                run_dir=str(run.run_dir),
                outputs={"holders": holders},
            )

        try:
            lock_failure = self._lock_health_failure_result(
                context,
                plan,
                stage="after_acquisition",
            )
            if lock_failure is not None:
                return lock_failure

            lock_failure = self._lock_health_failure_result(
                context,
                plan,
                stage="before_preflight",
            )
            if lock_failure is not None:
                return lock_failure

            checks = tuple(self.preflight(context, plan))
            context.write_json(
                "preflight.json",
                {
                    "success": all(check.passed for check in checks),
                    "checks": [check.to_dict() for check in checks],
                },
            )
            if not all(check.passed for check in checks):
                self._repair_prompt(
                    context,
                    plan,
                    failure_type="preflight",
                    exit_code=EXIT_GATE_FAILURE,
                    checks=checks,
                    next_action="Fix the failing preflight check and rerun this Hermes task.",
                )
                self._write_record(
                    context,
                    plan,
                    status="preflight_failed",
                    checks=checks,
                    outputs={},
                )
                return TaskResult(
                    task=self.name,
                    mode=mode,
                    exit_code=EXIT_GATE_FAILURE,
                    status="preflight_failed",
                    plan=plan,
                    run_id=run.run_id,
                    run_dir=str(run.run_dir),
                    checks=checks,
                )

            if mode == "preflight-only":
                self._write_record(
                    context,
                    plan,
                    status="preflight_passed",
                    checks=checks,
                    outputs={},
                )
                return TaskResult(
                    task=self.name,
                    mode=mode,
                    exit_code=EXIT_OK,
                    status="preflight_passed",
                    plan=plan,
                    run_id=run.run_id,
                    run_dir=str(run.run_dir),
                    checks=checks,
                )

            if mode == "dry-run":
                outputs = self.dry_run(context, plan)
                context.write_json("dry_run.json", outputs)
                postflight = tuple(self.postflight(context, plan, outputs))
                all_checks = checks + postflight
                exit_code = (
                    EXIT_OK
                    if all(check.passed for check in all_checks)
                    else EXIT_GATE_FAILURE
                )
                status = "dry_run_passed" if exit_code == EXIT_OK else "dry_run_failed"
                if exit_code != EXIT_OK:
                    self._repair_prompt(
                        context,
                        plan,
                        failure_type="dry-run-postflight",
                        exit_code=exit_code,
                        checks=postflight,
                        next_action="Inspect dry-run outputs before allowing execute mode.",
                    )
                self._write_record(
                    context,
                    plan,
                    status=status,
                    checks=all_checks,
                    outputs=outputs,
                )
                return TaskResult(
                    task=self.name,
                    mode=mode,
                    exit_code=exit_code,
                    status=status,
                    plan=plan,
                    run_id=run.run_id,
                    run_dir=str(run.run_dir),
                    checks=all_checks,
                    outputs=outputs,
                )

            required_ack = self.required_ack_token(context, plan)
            lock_failure = self._lock_health_failure_result(
                context,
                plan,
                stage="before_ack_gated_execute",
                checks=checks,
            )
            if lock_failure is not None:
                return lock_failure

            if required_ack and ack_risk != required_ack:
                outputs = {"requiredAck": required_ack, "providedAck": ack_risk}
                context.write_json("approval_required.json", outputs)
                self._write_record(
                    context,
                    plan,
                    status="approval_required",
                    checks=checks,
                    outputs=outputs,
                )
                return TaskResult(
                    task=self.name,
                    mode=mode,
                    exit_code=EXIT_ACK_REQUIRED,
                    status="approval_required",
                    plan=plan,
                    run_id=run.run_id,
                    run_dir=str(run.run_dir),
                    checks=checks,
                    outputs=outputs,
                )

            lock_failure = self._lock_health_failure_result(
                context,
                plan,
                stage="before_execute",
                checks=checks,
            )
            if lock_failure is not None:
                return lock_failure

            try:
                outputs = self.execute(context, plan)
            except TaskFailure as exc:
                outputs = {"error": str(exc), "evidence": exc.evidence}
                context.write_json("execute_failure.json", outputs)
                self._repair_prompt(
                    context,
                    plan,
                    failure_type="execute",
                    exit_code=exc.exit_code,
                    checks=checks,
                    next_action="Review execute_failure.json, restore any snapshots listed in the plan, and rerun from plan-only.",
                    arguments=outputs,
                )
                self._write_record(
                    context,
                    plan,
                    status="failed",
                    checks=checks,
                    outputs=outputs,
                )
                return TaskResult(
                    task=self.name,
                    mode=mode,
                    exit_code=exc.exit_code,
                    status="failed",
                    plan=plan,
                    run_id=run.run_id,
                    run_dir=str(run.run_dir),
                    checks=checks,
                    outputs=outputs,
                    error=str(exc),
                )

            lock_failure = self._lock_health_failure_result(
                context,
                plan,
                stage="after_execute",
                checks=checks,
                outputs=outputs,
            )
            if lock_failure is not None:
                return lock_failure

            context.write_json("execute.json", outputs)
            postflight = tuple(self.postflight(context, plan, outputs))
            all_checks = checks + postflight
            exit_code = (
                EXIT_OK
                if all(check.passed for check in all_checks)
                else EXIT_GATE_FAILURE
            )
            status = "executed" if exit_code == EXIT_OK else "postflight_failed"
            if exit_code != EXIT_OK:
                self._repair_prompt(
                    context,
                    plan,
                    failure_type="postflight",
                    exit_code=exit_code,
                    checks=postflight,
                    next_action="Postflight failed after mutation. Follow rollback recipe before retrying.",
                )
            self._write_record(
                context,
                plan,
                status=status,
                checks=all_checks,
                outputs=outputs,
            )
            return TaskResult(
                task=self.name,
                mode=mode,
                exit_code=exit_code,
                status=status,
                plan=plan,
                run_id=run.run_id,
                run_dir=str(run.run_dir),
                checks=all_checks,
                outputs=outputs,
            )
        finally:
            for lock in reversed(context.acquired_locks):
                lock.release()

    def _acquire_locks(self, context: TaskContext) -> bool:
        if context.config is None or context.run is None:
            raise RuntimeError("task context has no lock configuration")
        assert_canonical_lock_order(self.required_locks, task_name=self.name)
        for lock_name in self.required_locks:
            lock = HermesLock(
                _task_lock_path(context.config, lock_name),
                ttl_seconds=int(getattr(context.args, "lock_ttl_seconds", 900)),
                mode=context.mode,
                run_id=context.run.run_id,
            )
            if not lock.acquire(timeout_seconds=0):
                for acquired in reversed(context.acquired_locks):
                    acquired.release()
                context.acquired_locks.clear()
                return False
            context.acquired_locks.append(lock)
        return True

    def _lock_health_failure_result(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        *,
        stage: str,
        checks: Iterable[CheckResult] = (),
        outputs: dict[str, Any] | None = None,
    ) -> TaskResult | None:
        try:
            for lock in context.acquired_locks:
                lock.assert_healthy()
        except HermesLockError as exc:
            checks_tuple = tuple(checks)
            payload: dict[str, Any] = {
                "stage": stage,
                "error": str(exc),
                "locks": [
                    {
                        "path": str(lock.lock_path),
                        "healthy": lock.is_healthy(),
                        "heartbeatError": lock.heartbeat_error(),
                    }
                    for lock in context.acquired_locks
                ],
            }
            if outputs is not None:
                payload["outputs"] = outputs
            context.write_json("lock_health_failure.json", payload)
            self._repair_prompt(
                context,
                plan,
                failure_type="lock-health",
                exit_code=EXIT_LOCK_HELD,
                checks=checks_tuple,
                next_action=(
                    "Lock ownership was lost during this Hermes task. "
                    "Inspect lock_health_failure.json, verify no competing "
                    "writer is active, and rerun from plan-only."
                ),
                arguments=payload,
            )
            self._write_record(
                context,
                plan,
                status="lock_unhealthy",
                checks=checks_tuple,
                outputs=payload,
            )
            return TaskResult(
                task=self.name,
                mode=context.mode,
                exit_code=EXIT_LOCK_HELD,
                status="lock_unhealthy",
                plan=plan,
                run_id=context.run.run_id if context.run else None,
                run_dir=str(context.run_dir),
                checks=checks_tuple,
                outputs=payload,
                error=str(exc),
            )
        return None

    def _write_record(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        *,
        status: str,
        checks: Iterable[CheckResult],
        outputs: dict[str, Any],
    ) -> None:
        if context.ledger is None or context.run is None:
            raise RuntimeError("task context has no ledger")
        checks_tuple = tuple(checks)
        contract_version = int(plan.get("contractVersion", CURRENT_CONTRACT_VERSION))
        record = {
            "run_id": context.run.run_id,
            "contract_version": contract_version,
            "task": self.name,
            "mode": context.mode,
            "risk_level": self.risk_level,
            "actor": {
                "type": getattr(context.args, "actor_type", "operator"),
                "id": getattr(context.args, "actor_id", None)
                or os.environ.get("USER")
                or os.environ.get("USERNAME")
                or "unknown",
            },
            "started_at": context.run.created_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "inputs": _namespace_inputs(context.args),
            "locks": {
                "required": list(self.required_locks),
                "acquired": [str(lock.lock_path) for lock in context.acquired_locks],
                "ttl_seconds": int(getattr(context.args, "lock_ttl_seconds", 900)),
            },
            "ack_risk_token": plan.get("ack_risk_token", self.ack_risk_token),
            "preflight": {
                "checks": [check.to_dict() for check in checks_tuple],
                "passed": all(check.passed for check in checks_tuple),
            },
            "outputs": outputs,
            "ledger": {
                "index_ref": str(context.ledger.index_path),
                "run_dir": str(context.run_dir),
            },
            "plan_ref": "task_plan.json",
        }
        context.write_json("run_record.json", record)
        context.ledger.append_index(
            {
                "runId": context.run.run_id,
                "createdAt": context.run.created_at,
                "task": self.name,
                "mode": context.mode,
                "status": status,
                "risk": self.risk_level,
                "runDir": str(context.run_dir),
            }
        )

    def _write_markdown_plan(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> Path:
        lines = [
            f"# Hermes task plan: {self.name}",
            "",
            f"- Run ID: {context.run.run_id if context.run else 'none'}",
            f"- Mode: {context.mode}",
            f"- Risk: {self.risk_level}",
            f"- Required locks: {', '.join(self.required_locks) if self.required_locks else 'none'}",
            f"- Ack risk token: {plan.get('ack_risk_token') or 'not required'}",
            "",
            "```json",
            json.dumps(plan, indent=2, sort_keys=True),
            "```",
            "",
        ]
        return context.write_text("plan.md", "\n".join(lines))

    def _repair_prompt(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        *,
        failure_type: str,
        exit_code: int,
        checks: Iterable[CheckResult],
        next_action: str,
        arguments: dict[str, Any] | None = None,
    ) -> Path:
        if context.ledger is None or context.run is None:
            raise RuntimeError("task context has no ledger")
        state_paths = sorted(context.run_dir.glob("*.json")) + sorted(
            (context.run_dir / "state").glob("*.json")
        )
        return context.ledger.write_repair_prompt(
            context.run,
            failure_type=failure_type,
            exit_code=exit_code,
            routing_plan=plan,
            state_paths=state_paths,
            next_action=next_action,
            arguments={
                "task": self.name,
                "checks": [check.to_dict() for check in checks],
                **(arguments or {}),
            },
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_payload_fingerprint(path: Path) -> dict[str, Any]:
    main_sha256 = sha256_file(path)
    sidecars = [
        _sqlite_fingerprint_component(sidecar)
        for sidecar in _sqlite_sidecars(path)
        if sidecar.exists()
    ]
    wal_sidecars = [
        sidecar for sidecar in sidecars if sidecar["name"].endswith("-wal")
    ]
    for sidecar in sidecars:
        sidecar["included_in_sha256"] = sidecar in wal_sidecars
    if not wal_sidecars:
        return {
            "sha256": main_sha256,
            "main_sha256": main_sha256,
            "sha256_algorithm": "sha256:file-v1",
            "sidecars": sidecars,
        }

    digest = hashlib.sha256()
    digest.update(b"sqlite-main-and-wal-v1\0")
    _update_sqlite_fingerprint_digest(
        digest,
        {
            "name": path.name,
            "sha256": main_sha256,
            "size_bytes": path.stat().st_size,
        },
    )
    for sidecar in wal_sidecars:
        _update_sqlite_fingerprint_digest(digest, sidecar)

    return {
        "sha256": digest.hexdigest(),
        "main_sha256": main_sha256,
        "sha256_algorithm": "sha256:sqlite-main-and-wal-v1",
        "sidecars": sidecars,
    }


def sqlite_integrity(path: Path) -> tuple[bool, dict[str, Any]]:
    evidence: dict[str, Any] = {
        "path": str(path),
        "integrity_check": None,
        "schema_version": None,
        "tables": [],
    }
    if not path.exists():
        evidence["error"] = "missing"
        return False, evidence
    try:
        conn = sqlite3.connect(_sqlite_readonly_uri(path), uri=True, timeout=1)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            evidence["integrity_check"] = row[0] if row else "missing"
            evidence["tables"] = [
                result[0]
                for result in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            try:
                version_row = conn.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()
                evidence["schema_version"] = version_row[0] if version_row else None
            except sqlite3.OperationalError:
                evidence["schema_version"] = None
        finally:
            conn.close()
    except Exception as exc:
        evidence["error"] = str(exc)
        return False, evidence
    return evidence.get("integrity_check") == "ok", evidence


def sqlite_count(path: Path, table: str) -> tuple[int | None, str | None]:
    table_name = _sqlite_count_table(table)
    try:
        conn = sqlite3.connect(_sqlite_readonly_uri(path), uri=True, timeout=1)
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            return int(row[0]), None
        finally:
            conn.close()
    except Exception as exc:
        return None, str(exc)


def _sqlite_count_table(table: str) -> str:
    try:
        return SQLITE_COUNT_TABLES[table]
    except KeyError as exc:
        expected = ", ".join(sorted(SQLITE_COUNT_TABLES))
        raise ValueError(
            f"unsupported sqlite count table {table!r}; expected one of {expected}"
        ) from exc


def _sqlite_readonly_uri(path: Path) -> str:
    resolved = path.resolve()
    # immutable=1 avoids materializing WAL/SHM sidecars for sidecar-free WAL-mode
    # databases, but it must not be used when committed frames already exist in WAL.
    immutable = (
        ""
        if any(sidecar.exists() for sidecar in _sqlite_sidecars(resolved))
        else "&immutable=1"
    )
    return f"file:{resolved}?mode=ro{immutable}"


def _sqlite_sidecars(path: Path) -> tuple[Path, Path]:
    return (
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
    )


def _sqlite_fingerprint_component(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "path": str(path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _update_sqlite_fingerprint_digest(
    digest: Any,
    component: dict[str, Any],
) -> None:
    digest.update(str(component["name"]).encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(component["size_bytes"]).encode("ascii"))
    digest.update(b"\0")
    digest.update(str(component["sha256"]).encode("ascii"))
    digest.update(b"\0")


def run_async_blocking(coro: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, T] = {}
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(
        target=runner,
        name="hermes-async-blocking",
        daemon=True,
    )
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    if "value" not in result:
        raise RuntimeError("async runner exited without a result")
    return result["value"]


def copy_snapshot(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def run_command(
    command: list[str],
    *,
    cwd: Path = PROJECT_ROOT,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returnCode": -1,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"timed out after {timeout_seconds}s",
            "timedOut": True,
        }
    return {
        "command": command,
        "returnCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "timedOut": False,
    }


def _resolve_repo_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _task_lock_path(config: RoutingConfig, lock_name: str) -> Path:
    base = _resolve_repo_path(config.ledger.lock_path).parent / "task-locks"
    safe = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "-"
        for char in lock_name
    )
    return base / f"{safe}.lock"


def _lock_holders(context: TaskContext) -> dict[str, Any]:
    if context.config is None:
        raise RuntimeError("task context has no lock configuration")
    holders: dict[str, Any] = {}
    for lock_name in context.task.required_locks:
        lock = HermesLock(_task_lock_path(context.config, lock_name))
        holders[lock_name] = lock.get_holder_info()
    return holders


def _namespace_inputs(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in vars(args).items():
        if key == "func" or callable(value):
            continue
        if isinstance(value, Path):
            payload[key] = str(value)
        else:
            payload[key] = value
    return payload

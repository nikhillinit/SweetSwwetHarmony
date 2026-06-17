from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .base import (
    CheckResult,
    HermesTask,
    TaskContext,
    TaskFailure,
    copy_snapshot,
    resolve_task_db_path,
    sha256_file,
    sqlite_count,
    sqlite_integrity,
    sqlite_payload_fingerprint,
)

RESTORE_SIDECAR_HANDLER = "scripts.restore_db._ensure_no_target_sidecars"
RESTORE_READINESS_ARTIFACT = "restore_readiness.json"
RESTORE_GLOBAL_LOCK_REASON = (
    "restore-db uses the shared signals.db task lock to serialize all SQLite "
    "restore operations, including canary targets"
)


class RestoreDbTask(HermesTask):
    name = "restore-db"
    description = "Locked, hash-checked, ledger-backed SQLite restore wrapper."
    risk_level = "critical"
    ack_risk_token = "RESTORE_DB"
    supported_modes = ("plan-only", "preflight-only", "dry-run", "execute")
    required_locks = ("signals.db",)
    ledger_backed = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--backup", required=True)
        parser.add_argument("--target", default=None)
        parser.add_argument("--allow-target-create", action="store_true")
        parser.add_argument("--handle-sidecars", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--api-url")
        parser.add_argument("--expected-schema-version", type=int)
        parser.add_argument("--min-row-count", type=int, default=0)

    def plan(self, context: TaskContext) -> dict[str, Any]:
        plan = self._base_plan(context)
        backup = context.resolve(getattr(context.args, "backup", None))
        target = self._target(context)
        min_row_count = int(getattr(context.args, "min_row_count", 0) or 0)
        expected_schema = getattr(context.args, "expected_schema_version", None)

        backup_payload: dict[str, Any] = {
            "path": str(backup) if backup else None,
            "exists": bool(backup and backup.exists()),
        }
        if backup and backup.exists():
            backup_fingerprint = sqlite_payload_fingerprint(backup)
            backup_payload.update(
                {
                    "sha256": backup_fingerprint["sha256"],
                    "main_sha256": backup_fingerprint["main_sha256"],
                    "sha256_algorithm": backup_fingerprint["sha256_algorithm"],
                    "sidecars": backup_fingerprint["sidecars"],
                    "size_bytes": backup.stat().st_size,
                }
            )
            ok, evidence = sqlite_integrity(backup)
            backup_payload.update(
                {
                    "sqlite_integrity_ok": ok,
                    "sqlite": evidence,
                }
            )

        target_payload: dict[str, Any] = {
            "path": str(target),
            "target_class": _target_class(target),
            "exists": target.exists(),
            "snapshot_required": target.exists(),
        }
        if target.exists():
            target_fingerprint = sqlite_payload_fingerprint(target)
            row_count, row_error = sqlite_count(target, "signals")
            target_payload.update(
                {
                    "current_sha256": target_fingerprint["sha256"],
                    "current_main_sha256": target_fingerprint["main_sha256"],
                    "current_sha256_algorithm": target_fingerprint[
                        "sha256_algorithm"
                    ],
                    "sidecars": target_fingerprint["sidecars"],
                    "size_bytes": target.stat().st_size,
                    "signals_row_count": row_count,
                    "signals_row_error": row_error,
                }
            )

        plan.update(
            {
                "backup": backup_payload,
                "target": target_payload,
                "locks_required": list(self.required_locks),
                "ack_risk_required": True,
                "ack_risk_token": self.ack_risk_token,
                "preflight_gates": [
                    "backup_exists",
                    "backup_readable",
                    "backup_hash_recorded",
                    "no_uncheckpointed_backup_wal_sidecars",
                    "backup_sqlite_integrity_ok",
                    "target_exists_or_create_allowed",
                    "target_snapshot_possible",
                    "no_unhandled_wal_shm_sidecars",
                ],
                "postflight_gates": [
                    "target_exists",
                    "target_integrity_ok",
                    "row_count_above_watermark",
                    "schema_version_matches_if_declared",
                    "no_unexpected_sidecars",
                ],
                "postflight_gate_contracts": {
                    "row_count_above_watermark": {
                        "table": "signals",
                        "operator": ">=",
                        "min_row_count": min_row_count,
                        "actual_row_count_source": (
                            "postflight target signals count"
                        ),
                    },
                    "schema_version_matches_if_declared": {
                        "expected_schema_version": expected_schema,
                    }
                },
                "artifacts": {
                    "restore_readiness": RESTORE_READINESS_ARTIFACT,
                },
                "lock_scope": {
                    "type": "global_restore_operation",
                    "target_path": str(target),
                    "locks_required": list(self.required_locks),
                    "reason": RESTORE_GLOBAL_LOCK_REASON,
                },
                "sidecars": [str(path) for path in _sidecars(target) if path.exists()],
                "rollback": {
                    "available": target.exists(),
                    "recipe": "Restore snapshots/pre_restore_target.db from the run directory to the target path.",
                    "requires_snapshot": target.exists(),
                },
                "mutation": {
                    "allowed": context.mode == "execute",
                    "operation": "replace_sqlite_database_file",
                    "blast_radius": "entire_sqlite_database_file",
                    "affected_files": [str(target)],
                    "affected_databases": [str(target)],
                    "affected_table_scope": "all_tables_in_database",
                    "affected_tables": ["*"],
                    "external_systems": [],
                },
            }
        )
        return plan

    def preflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> list[CheckResult]:
        backup = context.resolve(getattr(context.args, "backup", None))
        target = self._target(context)
        checks: list[CheckResult] = []

        backup_exists = bool(backup and backup.exists())
        checks.append(
            CheckResult(
                "backup_exists",
                backup_exists,
                str(backup) if backup else "missing --backup",
            )
        )
        checks.append(
            CheckResult(
                "backup_readable",
                bool(backup and backup.exists() and backup.is_file()),
                str(backup) if backup else "missing --backup",
            )
        )
        if backup and backup.exists():
            current_fingerprint = sqlite_payload_fingerprint(backup)
            current_hash = current_fingerprint["sha256"]
            planned_hash = plan.get("backup", {}).get("sha256")
            ok, evidence = sqlite_integrity(backup)
            checks.append(
                CheckResult(
                    "backup_hash_recorded",
                    bool(planned_hash) and current_hash == planned_hash,
                    current_hash,
                    {
                        "planned_sha256": planned_hash,
                        "current_sha256": current_hash,
                        "planned_main_sha256": plan.get("backup", {}).get(
                            "main_sha256"
                        ),
                        "current_main_sha256": current_fingerprint["main_sha256"],
                        "planned_sha256_algorithm": plan.get("backup", {}).get(
                            "sha256_algorithm"
                        ),
                        "current_sha256_algorithm": current_fingerprint[
                            "sha256_algorithm"
                        ],
                        "planned_sidecars": plan.get("backup", {}).get(
                            "sidecars", []
                        ),
                        "current_sidecars": current_fingerprint["sidecars"],
                    },
                )
            )
            backup_wal_sidecars = _fingerprint_wal_sidecars(current_fingerprint)
            checks.append(
                CheckResult(
                    "no_uncheckpointed_backup_wal_sidecars",
                    not backup_wal_sidecars,
                    ", ".join(backup_wal_sidecars) if backup_wal_sidecars else "none",
                    {
                        "present": backup_wal_sidecars,
                        "sidecars": current_fingerprint["sidecars"],
                        "required_action": (
                            "Checkpoint or copy a sidecar-free backup before restore; "
                            "Hermes restore copies only the main backup DB file."
                        ),
                    },
                )
            )
            checks.append(
                CheckResult(
                    "backup_sqlite_integrity_ok",
                    ok,
                    evidence.get("integrity_check") or evidence.get("error", ""),
                    evidence,
                )
            )
        else:
            checks.append(
                CheckResult("backup_hash_recorded", False, "backup missing")
            )
            checks.append(
                CheckResult(
                    "no_uncheckpointed_backup_wal_sidecars",
                    False,
                    "backup missing",
                )
            )
            checks.append(
                CheckResult("backup_sqlite_integrity_ok", False, "backup missing")
            )

        allow_create = bool(getattr(context.args, "allow_target_create", False))
        checks.append(
            CheckResult(
                "target_exists_or_create_allowed",
                target.exists() or allow_create,
                str(target),
            )
        )
        checks.append(
            CheckResult(
                "target_snapshot_possible",
                target.is_file() if target.exists() else allow_create,
                str(target) if target.exists() else "target missing",
            )
        )

        present_sidecars = [path.name for path in _sidecars(target) if path.exists()]
        handle_sidecars = bool(getattr(context.args, "handle_sidecars", False))
        sidecars_ok = not present_sidecars or handle_sidecars
        sidecar_evidence = {"present": present_sidecars}
        if present_sidecars and handle_sidecars:
            sidecar_evidence["handler"] = RESTORE_SIDECAR_HANDLER
        checks.append(
            CheckResult(
                "no_unhandled_wal_shm_sidecars",
                sidecars_ok,
                ", ".join(present_sidecars) if present_sidecars else "none",
                sidecar_evidence,
            )
        )
        if context.mode == "execute":
            readiness = _restore_readiness_payload(context, plan, target, backup)
            context.write_json(RESTORE_READINESS_ARTIFACT, readiness)
            checks.append(
                CheckResult(
                    "restore_readiness_bound",
                    _restore_readiness_bound(readiness),
                    str(readiness.get("status") or ""),
                    readiness,
                )
            )
        return checks

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        backup = context.resolve(getattr(context.args, "backup", None))
        target = self._target(context)
        outputs: dict[str, Any] = {
            "dryRun": True,
            "mutationCommitted": False,
            "wouldRestore": str(backup) if backup else None,
            "target": str(target),
        }
        if backup and backup.exists():
            backup_fingerprint = sqlite_payload_fingerprint(backup)
            row_count, row_error = sqlite_count(backup, "signals")
            outputs["backupSha256"] = backup_fingerprint["sha256"]
            outputs["backupMainSha256"] = backup_fingerprint["main_sha256"]
            outputs["backupSha256Algorithm"] = backup_fingerprint[
                "sha256_algorithm"
            ]
            outputs["backupSidecars"] = backup_fingerprint["sidecars"]
            outputs["backupSignalsRowCount"] = row_count
            outputs["backupSignalsRowError"] = row_error
        if target.exists():
            snapshot = copy_snapshot(
                target,
                context.artifact_path("snapshots/dry_run_target_snapshot.db"),
            )
            outputs["targetSnapshotRef"] = str(snapshot.relative_to(context.run_dir))
            outputs["targetSnapshotSha256"] = sha256_file(snapshot)
        return outputs

    def execute(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        backup = context.resolve(getattr(context.args, "backup", None))
        target = self._target(context)
        if backup is None or not backup.exists():
            raise TaskFailure("backup missing", evidence={"backup": str(backup)})

        planned_backup_hash = plan.get("backup", {}).get("sha256")
        backup_fingerprint = sqlite_payload_fingerprint(backup)
        current_backup_hash = backup_fingerprint["sha256"]
        backup_wal_sidecars = _fingerprint_wal_sidecars(backup_fingerprint)
        if backup_wal_sidecars:
            raise TaskFailure(
                "backup WAL sidecars must be checkpointed before restore",
                evidence={
                    "backup": str(backup),
                    "present": backup_wal_sidecars,
                    "sidecars": backup_fingerprint["sidecars"],
                    "required_action": (
                        "Checkpoint or copy a sidecar-free backup before restore; "
                        "Hermes restore copies only the main backup DB file."
                    ),
                },
            )
        if planned_backup_hash != current_backup_hash:
            raise TaskFailure(
                "backup hash drift detected between plan and execute",
                evidence={
                    "backup": str(backup),
                    "planned_sha256": planned_backup_hash,
                    "current_sha256": current_backup_hash,
                    "planned_main_sha256": plan.get("backup", {}).get(
                        "main_sha256"
                    ),
                    "current_main_sha256": backup_fingerprint["main_sha256"],
                    "planned_sha256_algorithm": plan.get("backup", {}).get(
                        "sha256_algorithm"
                    ),
                    "current_sha256_algorithm": backup_fingerprint[
                        "sha256_algorithm"
                    ],
                    "planned_sidecars": plan.get("backup", {}).get("sidecars", []),
                    "current_sidecars": backup_fingerprint["sidecars"],
                },
            )

        planned_target_hash = plan.get("target", {}).get("current_sha256")
        if target.exists() and planned_target_hash:
            target_fingerprint = sqlite_payload_fingerprint(target)
            current_target_hash = target_fingerprint["sha256"]
            if current_target_hash != planned_target_hash:
                raise TaskFailure(
                    "target hash drift detected between plan and execute",
                    evidence={
                        "target": str(target),
                        "planned_sha256": planned_target_hash,
                        "current_sha256": current_target_hash,
                        "planned_main_sha256": plan.get("target", {}).get(
                            "current_main_sha256"
                        ),
                        "current_main_sha256": target_fingerprint["main_sha256"],
                        "planned_sha256_algorithm": plan.get("target", {}).get(
                            "current_sha256_algorithm"
                        ),
                        "current_sha256_algorithm": target_fingerprint[
                            "sha256_algorithm"
                        ],
                        "planned_sidecars": plan.get("target", {}).get(
                            "sidecars", []
                        ),
                        "current_sidecars": target_fingerprint["sidecars"],
                    },
                )

        outputs: dict[str, Any] = {
            "backup": str(backup),
            "target": str(target),
            "backupSha256": current_backup_hash,
            "backupMainSha256": backup_fingerprint["main_sha256"],
            "backupSha256Algorithm": backup_fingerprint["sha256_algorithm"],
            "backupSidecars": backup_fingerprint["sidecars"],
        }
        if target.exists():
            snapshot = copy_snapshot(
                target,
                context.artifact_path("snapshots/pre_restore_target.db"),
            )
            outputs["preRestoreSnapshotRef"] = str(snapshot.relative_to(context.run_dir))
            outputs["preRestoreSnapshotSha256"] = sha256_file(snapshot)

        try:
            from scripts.restore_db import (
                DEFAULT_API_URL,
                RestoreError,
                restore_backup_with_lock_and_ledger,
            )

            restore_result = restore_backup_with_lock_and_ledger(
                backup,
                target,
                bool(getattr(context.args, "force", False)),
                getattr(context.args, "api_url", None) or DEFAULT_API_URL,
            )
        except RestoreError as exc:
            evidence = {
                **outputs,
                **_restore_helper_outputs(exc.partial_evidence),
                "restoreHelperEvidence": dict(exc.partial_evidence),
            }
            raise TaskFailure(str(exc), evidence=evidence) from exc
        except Exception as exc:
            raise TaskFailure(str(exc), evidence=outputs) from exc

        outputs.update(
            {
                "dbOpsLedgerStatus": restore_result.db_ops_ledger_status,
                "dbToolLockPath": str(restore_result.lock_path),
                "canonicalPreRestorePath": str(restore_result.pre_restore_backup),
                "targetSha256Before": restore_result.target_sha256_before,
                "targetSha256": restore_result.target_sha256_after,
                "restoreHelperBackupSha256": restore_result.backup_sha256,
            }
        )
        return outputs

    def postflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        outputs: dict[str, Any],
    ) -> list[CheckResult]:
        target = self._target(context)
        checks = [CheckResult("target_exists", target.exists(), str(target))]

        ok, evidence = sqlite_integrity(target)
        checks.append(
            CheckResult(
                "target_integrity_ok",
                ok,
                evidence.get("integrity_check") or evidence.get("error", ""),
                evidence,
            )
        )

        row_count, row_error = sqlite_count(target, "signals")
        min_count = int(getattr(context.args, "min_row_count", 0) or 0)
        checks.append(
            CheckResult(
                "row_count_above_watermark",
                row_count is not None and row_count >= min_count,
                f"{row_count} >= {min_count}" if row_error is None else row_error,
                {"row_count": row_count, "min_row_count": min_count},
            )
        )

        expected_schema = getattr(context.args, "expected_schema_version", None)
        schema_version = evidence.get("schema_version")
        checks.append(
            CheckResult(
                "schema_version_matches_if_declared",
                expected_schema is None or schema_version == expected_schema,
                f"actual={schema_version} expected={expected_schema}",
                {"actual": schema_version, "expected": expected_schema},
            )
        )

        present_sidecars = [path.name for path in _sidecars(target) if path.exists()]
        checks.append(
            CheckResult(
                "no_unexpected_sidecars",
                not present_sidecars,
                ", ".join(present_sidecars) if present_sidecars else "none",
                {"present": present_sidecars},
            )
        )
        return checks

    def _target(self, context: TaskContext) -> Path:
        return resolve_task_db_path(context, getattr(context.args, "target", None))


def _sidecars(db_path: Path) -> tuple[Path, Path]:
    from scripts.restore_db import _sidecar_paths

    return _sidecar_paths(db_path)


def _fingerprint_wal_sidecars(fingerprint: dict[str, Any]) -> list[str]:
    return [
        sidecar["name"]
        for sidecar in fingerprint.get("sidecars", [])
        if sidecar.get("included_in_sha256")
    ]


def _restore_readiness_payload(
    context: TaskContext,
    plan: dict[str, Any],
    target: Path,
    backup: Path | None,
) -> dict[str, Any]:
    min_row_count = int(getattr(context.args, "min_row_count", 0) or 0)
    expected_schema = getattr(context.args, "expected_schema_version", None)
    execute_eligible = context.mode == "execute"
    plan_hash = plan.get("planHash")
    return {
        "artifactVersion": 1,
        "task": RestoreDbTask.name,
        "mode": context.mode,
        "status": "bound" if execute_eligible and plan_hash else "not_bound",
        "executeEligible": execute_eligible,
        "executePlanHash": plan_hash if execute_eligible else None,
        "target": {
            "path": str(target),
            "identity": target.name,
            "class": _target_class(target),
            "exists": target.exists(),
        },
        "backup": {
            "path": str(backup) if backup else None,
            "sha256": plan.get("backup", {}).get("sha256"),
        },
        "postflight": {
            "minRowCount": min_row_count,
            "expectedSchemaVersion": expected_schema,
        },
    }


def _restore_readiness_bound(readiness: dict[str, Any]) -> bool:
    return (
        readiness.get("task") == RestoreDbTask.name
        and readiness.get("mode") == "execute"
        and readiness.get("executeEligible") is True
        and bool(readiness.get("executePlanHash"))
        and bool(readiness.get("target", {}).get("path"))
        and readiness.get("target", {}).get("class") in {"live", "canary", "custom"}
        and bool(readiness.get("backup", {}).get("sha256"))
        and readiness.get("postflight", {}).get("minRowCount") is not None
    )


def _target_class(target: Path) -> str:
    name = target.name.lower()
    if name == "signals.db":
        return "live"
    if name == "signals.db.canary" or name.endswith(".canary"):
        return "canary"
    return "custom"


def _restore_helper_outputs(evidence: dict[str, Any]) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    key_map = {
        "db_ops_ledger_status": "dbOpsLedgerStatus",
        "lock_path": "dbToolLockPath",
        "pre_restore_backup": "canonicalPreRestorePath",
        "target_sha256_before": "targetSha256Before",
        "target_sha256_after": "targetSha256",
        "backup_sha256": "restoreHelperBackupSha256",
    }
    for source_key, output_key in key_map.items():
        if source_key in evidence:
            outputs[output_key] = evidence[source_key]
    return outputs

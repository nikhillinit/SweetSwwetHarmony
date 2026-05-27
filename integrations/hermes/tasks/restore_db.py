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
    sha256_file,
    sqlite_count,
    sqlite_integrity,
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
        parser.add_argument("--target", default="signals.db")
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

        backup_payload: dict[str, Any] = {
            "path": str(backup) if backup else None,
            "exists": bool(backup and backup.exists()),
        }
        if backup and backup.exists():
            backup_payload.update(
                {
                    "sha256": sha256_file(backup),
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
            "exists": target.exists(),
            "snapshot_required": target.exists(),
        }
        if target.exists():
            row_count, row_error = sqlite_count(target, "signals")
            target_payload.update(
                {
                    "current_sha256": sha256_file(target),
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
                "sidecars": [str(path) for path in _sidecars(target) if path.exists()],
                "rollback": {
                    "available": target.exists(),
                    "recipe": "Restore snapshots/pre_restore_target.db from the run directory to the target path.",
                    "requires_snapshot": target.exists(),
                },
                "mutation": {
                    "allowed": context.mode == "execute",
                    "affected_files": [str(target)],
                    "affected_tables": ["signals", "schema_migrations"],
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
            current_hash = sha256_file(backup)
            planned_hash = plan.get("backup", {}).get("sha256")
            ok, evidence = sqlite_integrity(backup)
            checks.append(
                CheckResult(
                    "backup_hash_recorded",
                    bool(planned_hash) and current_hash == planned_hash,
                    current_hash,
                    {"planned_sha256": planned_hash, "current_sha256": current_hash},
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
        sidecars_ok = not present_sidecars or bool(
            getattr(context.args, "handle_sidecars", False)
        )
        checks.append(
            CheckResult(
                "no_unhandled_wal_shm_sidecars",
                sidecars_ok,
                ", ".join(present_sidecars) if present_sidecars else "none",
                {"present": present_sidecars},
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
            row_count, row_error = sqlite_count(backup, "signals")
            outputs["backupSha256"] = sha256_file(backup)
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
        current_backup_hash = sha256_file(backup)
        if planned_backup_hash != current_backup_hash:
            raise TaskFailure(
                "backup hash drift detected between plan and execute",
                evidence={
                    "backup": str(backup),
                    "planned_sha256": planned_backup_hash,
                    "current_sha256": current_backup_hash,
                },
            )

        planned_target_hash = plan.get("target", {}).get("current_sha256")
        if target.exists() and planned_target_hash:
            current_target_hash = sha256_file(target)
            if current_target_hash != planned_target_hash:
                raise TaskFailure(
                    "target hash drift detected between plan and execute",
                    evidence={
                        "target": str(target),
                        "planned_sha256": planned_target_hash,
                        "current_sha256": current_target_hash,
                    },
                )

        outputs: dict[str, Any] = {
            "backup": str(backup),
            "target": str(target),
            "backupSha256": current_backup_hash,
        }
        if target.exists():
            snapshot = copy_snapshot(
                target,
                context.artifact_path("snapshots/pre_restore_target.db"),
            )
            outputs["preRestoreSnapshotRef"] = str(snapshot.relative_to(context.run_dir))
            outputs["preRestoreSnapshotSha256"] = sha256_file(snapshot)

        try:
            from scripts.restore_db import DEFAULT_API_URL, restore_backup

            pre_restore = restore_backup(
                backup,
                target,
                bool(getattr(context.args, "force", False)),
                getattr(context.args, "api_url", None) or DEFAULT_API_URL,
            )
        except Exception as exc:
            raise TaskFailure(str(exc), evidence=outputs) from exc

        outputs["canonicalPreRestorePath"] = str(pre_restore)
        outputs["targetSha256"] = sha256_file(target) if target.exists() else None
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
        return context.resolve(getattr(context.args, "target", None)) or context.root / "signals.db"


def _sidecars(db_path: Path) -> tuple[Path, Path]:
    return (
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
    )

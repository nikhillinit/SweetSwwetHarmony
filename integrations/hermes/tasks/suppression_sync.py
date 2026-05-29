from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import (
    CheckResult,
    HermesTask,
    TaskContext,
    TaskFailure,
    copy_snapshot,
    run_command,
    sha256_file,
)

SUPPRESSION_DELETE_ACK = "SUPPRESSION_DELETE"
SUPPRESSION_TABLE = "suppression_cache"


class SuppressionSyncTask(HermesTask):
    name = "suppression-sync"
    description = "Locked, ledger-backed wrapper for workflows.suppression_sync."
    risk_level = "medium"
    supported_modes = ("plan-only", "preflight-only", "dry-run", "execute")
    required_locks = ("signals.db", "suppression-cache")
    ledger_backed = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
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

    def plan(self, context: TaskContext) -> dict[str, Any]:
        db_path = self._db_path(context)
        state = _inspect_suppression_db(db_path)
        delete_stale = _effective_delete_stale(context.args)
        ack_token = SUPPRESSION_DELETE_ACK if delete_stale else None

        plan = self._base_plan(context)
        plan.update(
            {
                "database": state,
                "workflow": {
                    "module": "workflows.suppression_sync",
                    "command": self._workflow_command(context, dry_run=context.mode == "dry-run"),
                    "live_contract": {
                        "db_path_arg": "--db-path",
                        "ttl_days_arg": "--ttl-days",
                        "dry_run_arg": "--dry-run",
                    },
                },
                "delete_stale_requested": delete_stale,
                "delete_stale_argument": getattr(context.args, "delete_stale", None),
                "expected_changes": {
                    "upserts": "unknown_until_workflow_runs",
                    "expired_removals": state.get("expired_count", 0)
                    if delete_stale
                    else 0,
                    "expired_removals_if_delete_stale": state.get("expired_count", 0),
                },
                "locks_required": list(self.required_locks),
                "ack_risk_required": delete_stale,
                "ack_risk_token": ack_token,
                "preflight_gates": [
                    "workflow_importable",
                    "database_openable",
                    "suppression_cache_schema_valid",
                    "destructive_removals_within_threshold",
                ],
                "postflight_gates": [
                    "suppression_sync_command_succeeded",
                    "suppression_cache_table_present",
                    "suppression_cache_schema_valid",
                    "no_duplicate_suppressions",
                    "ledger_written",
                ],
                "rollback": {
                    "available": db_path.exists(),
                    "recipe": "Restore snapshots/pre_suppression_sync.db from the run directory to the configured db path.",
                },
                "mutation": {
                    "allowed": context.mode == "execute",
                    "affected_files": [str(db_path)],
                    "affected_tables": [SUPPRESSION_TABLE],
                    "external_systems": [],
                },
                "external_reads": ["notion"],
            }
        )
        return plan

    def required_ack_token(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> str | None:
        if bool(plan.get("delete_stale_requested")):
            return SUPPRESSION_DELETE_ACK
        return None

    def preflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> list[CheckResult]:
        db_path = self._db_path(context)
        state = _inspect_suppression_db(db_path)
        workflow_path = context.root / "workflows" / "suppression_sync.py"
        checks = [
            CheckResult(
                "workflow_importable",
                workflow_path.exists(),
                "workflows/suppression_sync.py",
                {"path": str(workflow_path)},
            ),
            CheckResult(
                "database_openable",
                bool(state["openable"]),
                state.get("detail", ""),
                state,
            ),
            CheckResult(
                "suppression_cache_schema_valid",
                bool(state["schema_valid"]),
                state.get("schema_detail", ""),
                state,
            ),
        ]

        if _effective_delete_stale(context.args):
            max_removals = int(getattr(context.args, "max_removals", 25) or 25)
            estimated = int(state.get("expired_count", 0) or 0)
            checks.append(
                CheckResult(
                    "destructive_removals_within_threshold",
                    estimated <= max_removals,
                    f"{estimated} <= {max_removals}",
                    {
                        "estimated_removals": estimated,
                        "max_removals": max_removals,
                    },
                )
            )
        return checks

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        command = self._workflow_command(context, dry_run=True)
        result = run_command(command, cwd=context.root, timeout_seconds=300)
        context.write_json("suppression_sync_command.json", result)
        return {
            "dryRun": True,
            "mutationCommitted": False,
            "command": command,
            "result": result,
            "estimatedChanges": plan.get("expected_changes", {}),
        }

    def execute(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        db_path = self._db_path(context)
        outputs: dict[str, Any] = {
            "dbPath": str(db_path),
            "deleteStaleRequested": bool(plan.get("delete_stale_requested")),
        }

        if db_path.exists():
            snapshot = copy_snapshot(
                db_path,
                context.artifact_path("snapshots/pre_suppression_sync.db"),
            )
            outputs["preSyncSnapshotRef"] = str(snapshot.relative_to(context.run_dir))
            outputs["preSyncSnapshotSha256"] = sha256_file(snapshot)

        context.write_json("pre_suppression_sync_state.json", _inspect_suppression_db(db_path))

        command = self._workflow_command(context, dry_run=False)
        result = run_command(command, cwd=context.root, timeout_seconds=300)
        context.write_json("suppression_sync_command.json", result)
        outputs["command"] = command
        outputs["result"] = result
        if result["returnCode"] != 0:
            raise TaskFailure("suppression sync command failed", evidence=outputs)
        return outputs

    def postflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        outputs: dict[str, Any],
    ) -> list[CheckResult]:
        db_path = self._db_path(context)
        state = _inspect_suppression_db(db_path)
        result = outputs.get("result")
        command_succeeded = result is None or int(result.get("returnCode", 1)) == 0
        require_table = context.mode == "execute" or db_path.exists()
        table_present = bool(state["table_exists"]) if require_table else True
        schema_valid = bool(state["schema_valid"]) and table_present
        if context.mode == "dry-run" and not db_path.exists():
            schema_valid = True

        return [
            CheckResult(
                "suppression_sync_command_succeeded",
                command_succeeded,
                str(result.get("returnCode")) if result else "no command result",
                result if isinstance(result, dict) else {},
            ),
            CheckResult(
                "suppression_cache_table_present",
                table_present,
                SUPPRESSION_TABLE if table_present else "missing",
                state,
            ),
            CheckResult(
                "suppression_cache_schema_valid",
                schema_valid,
                state.get("schema_detail", ""),
                state,
            ),
            CheckResult(
                "no_duplicate_suppressions",
                not state.get("duplicates"),
                str(state.get("duplicates") or "none"),
                state,
            ),
            CheckResult(
                "ledger_written",
                (context.run_dir / "run_record.json").exists(),
                "run_record.json",
            ),
        ]

    def _db_path(self, context: TaskContext) -> Path:
        return context.resolve(getattr(context.args, "db_path", None)) or context.root / "signals.db"

    def _workflow_command(self, context: TaskContext, *, dry_run: bool) -> list[str]:
        delete_stale = _effective_delete_stale(context.args)
        command = [
            sys.executable,
            "-m",
            "workflows.suppression_sync",
            "--db-path",
            str(self._db_path(context)),
            "--ttl-days",
            str(int(getattr(context.args, "ttl_days", 7) or 7)),
        ]
        if dry_run:
            command.append("--dry-run")
        command.append("--delete-stale" if delete_stale else "--skip-clean-expired")
        return command


def _effective_delete_stale(args: argparse.Namespace) -> bool:
    raw = getattr(args, "delete_stale", None)
    if raw is None:
        return True
    return bool(raw)


def _inspect_suppression_db(db_path: Path) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "openable": True,
        "detail": "database does not exist yet; workflow can initialize it",
        "integrity_check": None,
        "table_exists": False,
        "schema_valid": True,
        "schema_detail": "database can be initialized by workflow",
        "row_count": 0,
        "expired_count": 0,
        "duplicates": [],
        "missing_columns": [],
    }
    if not db_path.exists():
        return evidence

    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=1)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            evidence["integrity_check"] = integrity[0] if integrity else "missing"
            evidence["openable"] = evidence["integrity_check"] == "ok"
            evidence["detail"] = str(evidence["integrity_check"])

            table_exists = (
                conn.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table' AND name = ?
                    """,
                    (SUPPRESSION_TABLE,),
                ).fetchone()
                is not None
            )
            evidence["table_exists"] = table_exists
            if not table_exists:
                evidence["schema_detail"] = "suppression_cache table will be initialized by workflow"
                return evidence

            columns = {
                str(row[1])
                for row in conn.execute(f"PRAGMA table_info({SUPPRESSION_TABLE})")
            }
            required = {
                "canonical_key",
                "notion_page_id",
                "status",
                "cached_at",
                "expires_at",
            }
            missing = sorted(required - columns)
            now = datetime.now(timezone.utc).isoformat()
            duplicates = [
                str(row[0])
                for row in conn.execute(
                    f"""
                    SELECT canonical_key
                    FROM {SUPPRESSION_TABLE}
                    GROUP BY canonical_key
                    HAVING COUNT(*) > 1
                    ORDER BY canonical_key
                    """
                ).fetchall()
            ]
            row_count = conn.execute(
                f"SELECT COUNT(*) FROM {SUPPRESSION_TABLE}"
            ).fetchone()
            expired_count = conn.execute(
                f"SELECT COUNT(*) FROM {SUPPRESSION_TABLE} WHERE expires_at <= ?",
                (now,),
            ).fetchone()
            evidence.update(
                {
                    "schema_valid": not missing and bool(evidence["openable"]),
                    "schema_detail": "valid suppression_cache schema"
                    if not missing
                    else f"missing columns: {', '.join(missing)}",
                    "row_count": int(row_count[0]) if row_count else 0,
                    "expired_count": int(expired_count[0]) if expired_count else 0,
                    "duplicates": duplicates,
                    "missing_columns": missing,
                }
            )
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        evidence.update(
            {
                "openable": False,
                "detail": str(exc),
                "schema_valid": False,
                "schema_detail": str(exc),
                "error": str(exc),
            }
        )
    return evidence

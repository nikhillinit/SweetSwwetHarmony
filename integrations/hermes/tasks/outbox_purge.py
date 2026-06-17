from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .base import CheckResult, HermesTask, TaskContext, TaskFailure, resolve_task_db_path

OUTBOX_PURGE_ACK = "OUTBOX_PURGE"
OUTBOX_TABLE = "notion_outbox"
OUTBOX_CANDIDATES_ARTIFACT = "outbox_candidates.json"
OUTBOX_PURGE_RESULT_ARTIFACT = "outbox_purge_result.json"
OUTBOX_PURGE_ARTIFACT_VERSION = 1

OUTBOX_STATUS_CHOICES = ("failed", "pending", "processing", "sent")
EXPECTED_OUTBOX_COLUMNS = (
    "id",
    "idempotency_key",
    "payload_json",
    "status",
    "attempts",
    "next_attempt_at",
    "last_error",
    "created_at",
    "updated_at",
    "event_type",
    "max_attempts",
)


class OutboxPurgeTask(HermesTask):
    name = "outbox-purge"
    description = "Safely purge stale local notion_outbox rows."
    risk_level = "high"
    ack_risk_token = OUTBOX_PURGE_ACK
    supported_modes = ("plan-only", "preflight-only", "dry-run", "execute")
    required_locks = ("signals.db", "notion-outbox")
    ledger_backed = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--status",
            choices=OUTBOX_STATUS_CHOICES,
            default="failed",
            help="notion_outbox status eligible for purge",
        )
        parser.add_argument(
            "--event-type",
            default="notion_push",
            help="notion_outbox event_type eligible for purge",
        )
        parser.add_argument(
            "--age-days",
            type=int,
            default=30,
            help="Only purge rows created at least this many days ago",
        )

    def plan(self, context: TaskContext) -> dict[str, Any]:
        db_path = _db_path(context)
        criteria = _criteria(context)
        state = _inspect_outbox_database(db_path, criteria)
        candidates = state.get("candidates", _empty_candidates())

        plan = self._base_plan(context)
        plan.update(
            {
                "database": state,
                "purge_criteria": {
                    "status": criteria["status"],
                    "event_type": criteria["event_type"],
                    "age_days": criteria["age_days"],
                },
                "cutoff": {"created_at_lte": criteria["cutoff_iso"]},
                "max_removals": _max_removals(context),
                "candidates": {
                    "count": candidates["count"],
                    "ids": candidates["ids"],
                    "id_hash": candidates["id_hash"],
                    "candidate_hash": candidates["candidate_hash"],
                },
                "artifacts": {
                    "candidate_snapshot": OUTBOX_CANDIDATES_ARTIFACT,
                    "purge_result": OUTBOX_PURGE_RESULT_ARTIFACT,
                    "run_record": "run_record.json",
                },
                "locks_required": list(self.required_locks),
                "ack_risk_required": True,
                "ack_risk_token": OUTBOX_PURGE_ACK,
                "preflight_gates": [
                    "purge_criteria_explicit",
                    "database_exists",
                    "database_openable",
                    "notion_outbox_table_exists",
                    "notion_outbox_schema_valid",
                    "candidate_count_within_limit",
                ],
                "postflight_gates": [
                    "outbox_candidates_artifact_written",
                    "outbox_purge_result_artifact_written",
                    "delete_result_success",
                    "matching_rows_removed",
                    "count_decrement_matches_plan",
                    "ledger_written",
                ],
                "rollback": {
                    "available": True,
                    "recipe": (
                        "Reinsert rows from outbox_candidates.json into notion_outbox "
                        "if a purge must be reversed."
                    ),
                },
                "mutation": {
                    "allowed": context.mode == "execute",
                    "affected_db": str(db_path) if context.mode == "execute" else None,
                    "affected_files": [str(db_path)] if context.mode == "execute" else [],
                    "affected_tables": [OUTBOX_TABLE],
                    "external_systems": [],
                    "ledger_artifacts": [
                        OUTBOX_CANDIDATES_ARTIFACT,
                        OUTBOX_PURGE_RESULT_ARTIFACT,
                        "run_record.json",
                    ],
                },
                "external_reads": [],
            }
        )
        return plan

    def preflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> list[CheckResult]:
        db_path = _db_path(context)
        criteria = _criteria_from_plan(plan)
        state = _inspect_outbox_database(db_path, criteria)
        candidate_count = int(state.get("candidates", {}).get("count", 0) or 0)
        max_removals = int(plan.get("max_removals", _max_removals(context)))
        criteria_valid = _criteria_valid(criteria) and max_removals >= 0

        return [
            CheckResult(
                "purge_criteria_explicit",
                criteria_valid,
                _criteria_detail(criteria, max_removals),
                {
                    "status": criteria["status"],
                    "event_type": criteria["event_type"],
                    "age_days": criteria["age_days"],
                    "max_removals": max_removals,
                },
            ),
            CheckResult(
                "database_exists",
                bool(state["exists"]),
                str(db_path) if state["exists"] else "missing",
                state,
            ),
            CheckResult(
                "database_openable",
                bool(state["openable"]),
                str(state.get("detail") or ""),
                state,
            ),
            CheckResult(
                "notion_outbox_table_exists",
                bool(state["table_exists"]),
                OUTBOX_TABLE if state["table_exists"] else "missing",
                state,
            ),
            CheckResult(
                "notion_outbox_schema_valid",
                bool(state["schema_valid"]),
                str(state.get("schema_detail") or ""),
                state,
            ),
            CheckResult(
                "candidate_count_within_limit",
                candidate_count <= max_removals,
                f"{candidate_count} <= {max_removals}",
                {
                    "candidate_count": candidate_count,
                    "max_removals": max_removals,
                },
            ),
        ]

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        snapshot = _candidate_snapshot_for_plan(_db_path(context), plan)
        context.write_json(OUTBOX_CANDIDATES_ARTIFACT, snapshot)
        return {
            "dryRun": True,
            "mutationCommitted": False,
            "candidateCount": snapshot["candidateCount"],
            "candidateHash": snapshot["candidateHash"],
            "candidateIdHash": snapshot["candidateIdHash"],
            "candidateArtifact": OUTBOX_CANDIDATES_ARTIFACT,
        }

    def execute(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        db_path = _db_path(context)
        snapshot = _candidate_snapshot_for_plan(db_path, plan)
        context.write_json(OUTBOX_CANDIDATES_ARTIFACT, snapshot)

        criteria = _criteria_from_plan(plan)
        planned_count = int(plan.get("candidates", {}).get("count", 0) or 0)
        before = _matching_summary(db_path, criteria)
        try:
            delete_result = _delete_candidate_ids(
                db_path,
                [int(item) for item in plan.get("candidates", {}).get("ids", [])],
                criteria,
            )
        except Exception as exc:
            raise TaskFailure(
                "outbox purge delete failed",
                evidence={
                    "dbPath": str(db_path),
                    "candidateCount": snapshot["candidateCount"],
                    "error": str(exc),
                },
            ) from exc

        after = _matching_summary(db_path, criteria)
        payload = {
            "artifactVersion": OUTBOX_PURGE_ARTIFACT_VERSION,
            "dryRun": False,
            "mutationCommitted": bool(delete_result["success"]),
            "dbPath": str(db_path),
            "candidateCount": planned_count,
            "snapshotCandidateCount": snapshot["candidateCount"],
            "candidateHash": snapshot["candidateHash"],
            "candidateIdHash": snapshot["candidateIdHash"],
            "candidateArtifact": OUTBOX_CANDIDATES_ARTIFACT,
            "purgeResultArtifact": OUTBOX_PURGE_RESULT_ARTIFACT,
            "purgeCriteria": plan.get("purge_criteria", {}),
            "before": before,
            "after": after,
            "deleteResult": delete_result,
        }
        context.write_json(OUTBOX_PURGE_RESULT_ARTIFACT, payload)
        return payload

    def postflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        outputs: dict[str, Any],
    ) -> list[CheckResult]:
        run_dir = context.run_dir
        candidates_path = run_dir / OUTBOX_CANDIDATES_ARTIFACT
        purge_path = run_dir / OUTBOX_PURGE_RESULT_ARTIFACT
        delete_result = outputs.get("deleteResult")
        execute_mode = context.mode == "execute"
        candidate_count = int(outputs.get("candidateCount", 0) or 0)
        before_count = _nested_int(outputs, "before", "matchingCount")
        after_count = _nested_int(outputs, "after", "matchingCount")
        deleted_count = _nested_int(outputs, "deleteResult", "deletedCount")

        if execute_mode:
            remaining = _matching_summary(_db_path(context), _criteria_from_plan(plan))
            remaining_count = int(remaining.get("matchingCount", 0) or 0)
            rows_removed = candidate_count == 0 or remaining_count == 0
            decrement_matches = (
                before_count is not None
                and after_count is not None
                and deleted_count is not None
                and before_count - after_count == deleted_count
                and deleted_count == candidate_count
            )
        else:
            rows_removed = True
            decrement_matches = True
            remaining = {}

        return [
            CheckResult(
                "outbox_candidates_artifact_written",
                candidates_path.exists(),
                OUTBOX_CANDIDATES_ARTIFACT if candidates_path.exists() else "missing",
                {"path": str(candidates_path)},
            ),
            CheckResult(
                "outbox_purge_result_artifact_written",
                (not execute_mode) or purge_path.exists(),
                OUTBOX_PURGE_RESULT_ARTIFACT if purge_path.exists() else "missing",
                {"path": str(purge_path)},
            ),
            CheckResult(
                "delete_result_success",
                (not execute_mode)
                or (
                    isinstance(delete_result, dict)
                    and bool(delete_result.get("success"))
                ),
                str(delete_result or "not required"),
                delete_result if isinstance(delete_result, dict) else {},
            ),
            CheckResult(
                "matching_rows_removed",
                rows_removed,
                "none remaining" if rows_removed else str(remaining.get("matchingCount")),
                remaining,
            ),
            CheckResult(
                "count_decrement_matches_plan",
                decrement_matches,
                _decrement_detail(before_count, after_count, deleted_count, candidate_count),
                {
                    "beforeMatchingCount": before_count,
                    "afterMatchingCount": after_count,
                    "deletedCount": deleted_count,
                    "candidateCount": candidate_count,
                },
            ),
            CheckResult(
                "ledger_written",
                (context.run_dir / "run_record.json").exists(),
                "run_record.json",
            ),
        ]


def _db_path(context: TaskContext) -> Path:
    return resolve_task_db_path(context, getattr(context.args, "db_path", None))


def _criteria(context: TaskContext) -> dict[str, Any]:
    age_days = _int_arg(getattr(context.args, "age_days", None), default=30)
    cutoff = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {
        "status": str(getattr(context.args, "status", "failed") or "").strip(),
        "event_type": str(getattr(context.args, "event_type", "notion_push") or "").strip(),
        "age_days": age_days,
        "cutoff_iso": cutoff.isoformat(),
    }


def _criteria_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    purge = plan.get("purge_criteria", {})
    cutoff = plan.get("cutoff", {})
    return {
        "status": str(purge.get("status") or ""),
        "event_type": str(purge.get("event_type") or ""),
        "age_days": int(purge.get("age_days") or 0),
        "cutoff_iso": str(cutoff.get("created_at_lte") or ""),
    }


def _criteria_valid(criteria: dict[str, Any]) -> bool:
    return (
        bool(criteria.get("status"))
        and bool(criteria.get("event_type"))
        and int(criteria.get("age_days", 0) or 0) > 0
        and bool(criteria.get("cutoff_iso"))
    )


def _criteria_detail(criteria: dict[str, Any], max_removals: int) -> str:
    if not _criteria_valid(criteria):
        return "status, event_type, and positive age_days are required"
    if max_removals < 0:
        return "max_removals must be non-negative"
    return (
        f"{criteria['status']} {criteria['event_type']} rows older than "
        f"{criteria['age_days']} days; max_removals={max_removals}"
    )


def _max_removals(context: TaskContext) -> int:
    return _int_arg(getattr(context.args, "max_removals", None), default=25)


def _int_arg(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _inspect_outbox_database(
    db_path: Path,
    criteria: dict[str, Any],
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "openable": False,
        "integrity_check": None,
        "detail": "database missing",
        "table_exists": False,
        "columns": [],
        "missing_columns": list(EXPECTED_OUTBOX_COLUMNS),
        "schema_valid": False,
        "schema_detail": "notion_outbox table missing",
        "candidates": _empty_candidates(),
    }
    if not db_path.exists():
        return evidence

    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=1)
        conn.row_factory = sqlite3.Row
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            evidence["integrity_check"] = integrity[0] if integrity else "missing"
            evidence["openable"] = evidence["integrity_check"] == "ok"
            evidence["detail"] = str(evidence["integrity_check"])
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            evidence["table_exists"] = OUTBOX_TABLE in tables
            if not evidence["table_exists"]:
                return evidence

            columns = _outbox_columns(conn)
            missing = [
                column
                for column in EXPECTED_OUTBOX_COLUMNS
                if column not in columns
            ]
            evidence["columns"] = columns
            evidence["missing_columns"] = missing
            evidence["schema_valid"] = not missing
            evidence["schema_detail"] = (
                "expected notion_outbox columns present"
                if not missing
                else f"missing columns: {', '.join(missing)}"
            )
            if not missing and _criteria_valid(criteria):
                evidence["candidates"] = _plan_candidates(
                    _candidate_snapshot(conn, criteria)
                )
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        evidence["detail"] = str(exc)
    return evidence


def _candidate_snapshot_for_plan(db_path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    ids = [int(item) for item in plan.get("candidates", {}).get("ids", [])]
    if not db_path.exists():
        return _snapshot_payload([], error="database missing")
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=1)
        conn.row_factory = sqlite3.Row
        try:
            return _snapshot_candidate_ids(conn, ids)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return _snapshot_payload([], error=str(exc))


def _matching_summary(db_path: Path, criteria: dict[str, Any]) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "matchingCount": None,
            "candidateHash": None,
            "detail": "database missing",
        }
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=1)
        conn.row_factory = sqlite3.Row
        try:
            snapshot = _candidate_snapshot(conn, criteria)
            return {
                "matchingCount": snapshot["candidateCount"],
                "candidateHash": snapshot["candidateHash"],
                "candidateIdHash": snapshot["candidateIdHash"],
                "ids": snapshot["candidateIds"],
            }
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return {
            "matchingCount": None,
            "candidateHash": None,
            "detail": str(exc),
        }


def _delete_candidate_ids(
    db_path: Path,
    ids: list[int],
    criteria: dict[str, Any],
) -> dict[str, Any]:
    if not ids:
        return {"success": True, "deletedCount": 0, "candidateIds": []}

    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in ids)
        cursor = conn.execute(
            f"""
            DELETE FROM {OUTBOX_TABLE}
            WHERE id IN ({placeholders})
              AND status = ?
              AND event_type = ?
              AND datetime(created_at) <= datetime(?)
            """,
            [
                *ids,
                criteria["status"],
                criteria["event_type"],
                criteria["cutoff_iso"],
            ],
        )
        conn.commit()
        return {
            "success": True,
            "deletedCount": int(cursor.rowcount),
            "candidateIds": ids,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _outbox_columns(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({OUTBOX_TABLE})").fetchall()
    ]


def _candidate_snapshot(
    conn: sqlite3.Connection,
    criteria: dict[str, Any],
) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT *
            FROM {OUTBOX_TABLE}
            WHERE status = ?
              AND event_type = ?
              AND datetime(created_at) <= datetime(?)
            ORDER BY id ASC
            """,
            (
                criteria["status"],
                criteria["event_type"],
                criteria["cutoff_iso"],
            ),
        ).fetchall()
    ]
    return _snapshot_payload(rows)


def _snapshot_candidate_ids(
    conn: sqlite3.Connection,
    ids: list[int],
) -> dict[str, Any]:
    if not ids:
        return _snapshot_payload([])
    placeholders = ",".join("?" for _ in ids)
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT *
            FROM {OUTBOX_TABLE}
            WHERE id IN ({placeholders})
            ORDER BY id ASC
            """,
            ids,
        ).fetchall()
    ]
    return _snapshot_payload(rows)


def _snapshot_payload(
    rows: list[dict[str, Any]],
    *,
    error: str | None = None,
) -> dict[str, Any]:
    ids = [int(row["id"]) for row in rows if row.get("id") is not None]
    payload = {
        "artifactVersion": OUTBOX_PURGE_ARTIFACT_VERSION,
        "candidateCount": len(rows),
        "candidateIds": ids,
        "candidateIdHash": _hash_json(ids),
        "candidateHash": _hash_json(rows),
        "rows": rows,
    }
    if error:
        payload["error"] = error
    return payload


def _empty_candidates() -> dict[str, Any]:
    return {
        "count": 0,
        "ids": [],
        "id_hash": _hash_json([]),
        "candidate_hash": _hash_json([]),
    }


def _plan_candidates(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": snapshot["candidateCount"],
        "ids": snapshot["candidateIds"],
        "id_hash": snapshot["candidateIdHash"],
        "candidate_hash": snapshot["candidateHash"],
    }


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _nested_int(payload: dict[str, Any], section: str, key: str) -> int | None:
    value = payload.get(section)
    if not isinstance(value, dict):
        return None
    if value.get(key) is None:
        return None
    return int(value[key])


def _decrement_detail(
    before_count: int | None,
    after_count: int | None,
    deleted_count: int | None,
    candidate_count: int,
) -> str:
    return (
        f"before={before_count}, after={after_count}, "
        f"deleted={deleted_count}, candidates={candidate_count}"
    )

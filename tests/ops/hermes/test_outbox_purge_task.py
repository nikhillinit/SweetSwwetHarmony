from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from integrations.hermes.locks import HermesLock
from integrations.hermes.tasks.base import EXIT_ACK_REQUIRED, EXIT_GATE_FAILURE
from integrations.hermes.tasks.base import EXIT_LOCK_HELD
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


def _config_path(tmp_path: Path) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = []
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _args(
    tmp_path: Path,
    *,
    db_path: Path,
    mode: str = "preflight-only",
    ack_risk: str | None = None,
    status: str = "failed",
    event_type: str = "notion_push",
    age_days: int = 30,
    max_removals: int = 25,
) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="outbox-purge",
        config=str(_config_path(tmp_path)),
        plan_only=mode == "plan-only",
        preflight_only=mode == "preflight-only",
        dry_run=mode == "dry-run",
        execute=mode == "execute",
        ack_risk=ack_risk,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="test",
        json_output=False,
        db_path=str(db_path),
        status=status,
        event_type=event_type,
        age_days=age_days,
        max_removals=max_removals,
    )


def _write_outbox_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE notion_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'notion_push',
                max_attempts INTEGER NOT NULL DEFAULT 5,
                created_by TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_outbox_row(
    path: Path,
    *,
    key: str,
    status: str = "failed",
    event_type: str = "notion_push",
    age_days: int = 45,
) -> int:
    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(days=age_days)).isoformat()
    updated_at = created_at
    conn = sqlite3.connect(path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO notion_outbox (
                idempotency_key, payload_json, status, attempts,
                next_attempt_at, last_error, created_at, updated_at,
                event_type, max_attempts, created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                json.dumps({"key": key}),
                status,
                5,
                None,
                "stale test row",
                created_at,
                updated_at,
                event_type,
                5,
                "test",
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _outbox_rows(path: Path) -> list[tuple[str, str, str]]:
    conn = sqlite3.connect(path)
    try:
        return [
            (str(row[0]), str(row[1]), str(row[2]))
            for row in conn.execute(
                """
                SELECT idempotency_key, status, event_type
                FROM notion_outbox
                ORDER BY id
                """
            ).fetchall()
        ]
    finally:
        conn.close()


def test_plan_only_writes_ledger_artifacts_and_stays_non_mutating(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_outbox_db(db_path)
    _insert_outbox_row(db_path, key="stale")

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="plan-only"),
    )

    assert result.exit_code == 0
    assert result.status == "planned"
    assert result.plan["mutation"]["allowed"] is False
    assert result.plan["mutation"]["external_systems"] == []
    assert result.plan["purge_criteria"] == {
        "status": "failed",
        "event_type": "notion_push",
        "age_days": 30,
    }
    assert result.plan["candidates"]["count"] == 1
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "task_plan.json").exists()
    assert (run_dir / "run_record.json").exists()
    assert (run_dir / "plan.md").exists()


def test_missing_target_db_fails_preflight_safely_and_emits_repair_prompt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "missing-signals.db"

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="preflight-only"),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    assert any(check.name == "database_exists" and not check.passed for check in result.checks)
    assert db_path.exists() is False
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_missing_notion_outbox_table_fails_preflight_safely_and_emits_repair_prompt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    sqlite3.connect(db_path).close()

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="preflight-only"),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    assert any(check.name == "notion_outbox_table_exists" and not check.passed for check in result.checks)
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_malformed_notion_outbox_schema_fails_preflight_safely(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE notion_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="preflight-only"),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(
        check for check in result.checks if check.name == "notion_outbox_schema_valid"
    )
    assert check.passed is False
    assert "event_type" in check.evidence["missing_columns"]
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_candidate_count_above_max_removals_fails_preflight(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    _write_outbox_db(db_path)
    _insert_outbox_row(db_path, key="stale-1")
    _insert_outbox_row(db_path, key="stale-2")

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="preflight-only", max_removals=1),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(
        check for check in result.checks if check.name == "candidate_count_within_limit"
    )
    assert check.passed is False
    assert check.evidence == {"candidate_count": 2, "max_removals": 1}


def test_dry_run_writes_candidate_snapshot_hash_and_deletes_nothing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_outbox_db(db_path)
    _insert_outbox_row(db_path, key="stale")

    result = run_registered_task(_args(tmp_path, db_path=db_path, mode="dry-run"))

    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    assert _outbox_rows(db_path) == [("stale", "failed", "notion_push")]
    assert result.outputs["dryRun"] is True
    assert result.outputs["mutationCommitted"] is False
    assert result.outputs["candidateCount"] == 1
    run_dir = Path(result.run_dir or "")
    candidate_payload = json.loads(
        (run_dir / "outbox_candidates.json").read_text(encoding="utf-8")
    )
    assert candidate_payload["candidateCount"] == 1
    assert candidate_payload["candidateHash"] == result.outputs["candidateHash"]


def test_execute_requires_outbox_purge_ack_before_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    _write_outbox_db(db_path)
    _insert_outbox_row(db_path, key="stale")

    result = run_registered_task(_args(tmp_path, db_path=db_path, mode="execute"))

    assert result.exit_code == EXIT_ACK_REQUIRED
    assert result.status == "approval_required"
    assert _outbox_rows(db_path) == [("stale", "failed", "notion_push")]
    assert (Path(result.run_dir or "") / "approval_required.json").exists()


def test_lock_conflict_on_outbox_refuses_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    _write_outbox_db(db_path)
    _insert_outbox_row(db_path, key="stale")
    config_path = _config_path(tmp_path)
    lock_path = tmp_path / "ai-logs" / "hermes" / "task-locks" / "notion-outbox.lock"
    lock = HermesLock(lock_path, mode="execute", run_id="held")
    assert lock.acquire(timeout_seconds=0) is True

    try:
        args = _args(
            tmp_path,
            db_path=db_path,
            mode="execute",
            ack_risk="OUTBOX_PURGE",
        )
        args.config = str(config_path)
        result = run_registered_task(args)
    finally:
        lock.release()

    assert result.exit_code == EXIT_LOCK_HELD
    assert result.status == "lock_held"
    assert _outbox_rows(db_path) == [("stale", "failed", "notion_push")]
    assert (Path(result.run_dir or "") / "lock_conflict.json").exists()


def test_execute_deletes_only_matching_candidate_ids_and_records_mutation_metadata(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_outbox_db(db_path)
    _insert_outbox_row(db_path, key="stale-match", status="failed", event_type="notion_push", age_days=45)
    _insert_outbox_row(db_path, key="fresh-match", status="failed", event_type="notion_push", age_days=1)
    _insert_outbox_row(db_path, key="event-mismatch", status="failed", event_type="profile_update", age_days=45)
    _insert_outbox_row(db_path, key="status-mismatch", status="sent", event_type="notion_push", age_days=45)

    result = run_registered_task(
        _args(
            tmp_path,
            db_path=db_path,
            mode="execute",
            ack_risk="OUTBOX_PURGE",
            status="failed",
            event_type="notion_push",
            age_days=30,
        ),
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    assert _outbox_rows(db_path) == [
        ("fresh-match", "failed", "notion_push"),
        ("event-mismatch", "failed", "profile_update"),
        ("status-mismatch", "sent", "notion_push"),
    ]
    assert result.outputs["deleteResult"]["deletedCount"] == 1
    assert result.outputs["mutationCommitted"] is True
    assert result.plan["mutation"] == {
        "allowed": True,
        "affected_db": str(db_path),
        "affected_files": [str(db_path)],
        "affected_tables": ["notion_outbox"],
        "external_systems": [],
        "ledger_artifacts": [
            "outbox_candidates.json",
            "outbox_purge_result.json",
            "run_record.json",
        ],
    }
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "outbox_candidates.json").exists()
    assert (run_dir / "outbox_purge_result.json").exists()


def test_execute_failure_emits_repair_prompt_and_preserves_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_outbox_db(db_path)
    _insert_outbox_row(db_path, key="stale")

    def fail_delete(*_: Any, **__: Any) -> dict[str, Any]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        "integrations.hermes.tasks.outbox_purge._delete_candidate_ids",
        fail_delete,
    )

    result = run_registered_task(
        _args(
            tmp_path,
            db_path=db_path,
            mode="execute",
            ack_risk="OUTBOX_PURGE",
        ),
    )

    assert result.exit_code == 1
    assert result.status == "failed"
    assert _outbox_rows(db_path) == [("stale", "failed", "notion_push")]
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_postflight_catches_missing_purge_artifact_and_count_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_outbox_db(db_path)
    _insert_outbox_row(db_path, key="stale")

    def fake_execute(_: Any, context: Any, plan: dict[str, Any]) -> dict[str, Any]:
        context.write_json(
            "outbox_candidates.json",
            {
                "candidateCount": plan["candidates"]["count"],
                "candidateHash": plan["candidates"]["candidate_hash"],
                "rows": [],
            },
        )
        return {
            "dryRun": False,
            "mutationCommitted": True,
            "candidateCount": plan["candidates"]["count"],
            "candidateHash": plan["candidates"]["candidate_hash"],
            "deleteResult": {"success": True, "deletedCount": 1},
            "before": {"matchingCount": 1},
            "after": {"matchingCount": 1},
        }

    monkeypatch.setattr(
        "integrations.hermes.tasks.outbox_purge.OutboxPurgeTask.execute",
        fake_execute,
    )

    result = run_registered_task(
        _args(
            tmp_path,
            db_path=db_path,
            mode="execute",
            ack_risk="OUTBOX_PURGE",
        ),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "postflight_failed"
    failed_checks = {check.name for check in result.checks if not check.passed}
    assert "outbox_purge_result_artifact_written" in failed_checks
    assert "matching_rows_removed" in failed_checks
    assert "count_decrement_matches_plan" in failed_checks
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()

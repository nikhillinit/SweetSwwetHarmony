from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from integrations.hermes.locks import HermesLock
from integrations.hermes.tasks.base import (
    EXIT_ACK_REQUIRED,
    EXIT_GATE_FAILURE,
    EXIT_LOCK_HELD,
    EXIT_TASK_FAILURE,
    sqlite_count,
)
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


@pytest.fixture(autouse=True)
def _signal_store_schema_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("storage.signal_store")
    module.CURRENT_SCHEMA_VERSION = 53
    monkeypatch.setitem(sys.modules, "storage.signal_store", module)


def _config_path(tmp_path: Path) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = []
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_db(path: Path, *, rows: int) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
        conn.execute("INSERT INTO schema_migrations (version) VALUES (53)")
        conn.execute(
            "CREATE TABLE signals (id INTEGER PRIMARY KEY, company_name TEXT)"
        )
        conn.executemany(
            "INSERT INTO signals (company_name) VALUES (?)",
            [(f"company-{index}",) for index in range(rows)],
        )
        conn.commit()
    finally:
        conn.close()


def _row_count(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0])
    finally:
        conn.close()


def test_sqlite_count_allows_only_registered_signals_table(tmp_path: Path) -> None:
    target = tmp_path / "signals.db"
    _write_db(target, rows=3)

    assert sqlite_count(target, "signals") == (3, None)

    for table_name in ("schema_migrations", "signals; DROP TABLE signals"):
        with pytest.raises(ValueError, match="unsupported sqlite count table"):
            sqlite_count(target, table_name)

    assert _row_count(target) == 3


def _args(
    tmp_path: Path,
    *,
    backup: Path | None,
    target: Path,
    mode: str = "preflight-only",
    ack_risk: str | None = None,
    min_row_count: int = 0,
    handle_sidecars: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="restore-db",
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
        backup=str(backup) if backup is not None else None,
        target=str(target),
        allow_target_create=False,
        handle_sidecars=handle_sidecars,
        force=True,
        api_url="http://127.0.0.1:9/health",
        expected_schema_version=None,
        min_row_count=min_row_count,
    )


def test_missing_backup_refuses_safely_and_emits_repair_prompt(tmp_path: Path) -> None:
    target = tmp_path / "signals.db"
    _write_db(target, rows=2)

    result = run_registered_task(
        _args(tmp_path, backup=tmp_path / "missing.db", target=target)
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    assert _row_count(target) == 2
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "task_plan.json").exists()
    assert (run_dir / "run_record.json").exists()
    assert (run_dir / "repair_prompt.md").exists()


def test_execute_requires_restore_ack_before_mutating(tmp_path: Path) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    _write_db(backup, rows=4)
    _write_db(target, rows=1)

    result = run_registered_task(
        _args(tmp_path, backup=backup, target=target, mode="execute")
    )

    assert result.exit_code == EXIT_ACK_REQUIRED
    assert result.status == "approval_required"
    assert _row_count(target) == 1
    assert (Path(result.run_dir or "") / "approval_required.json").exists()


def test_hash_drift_between_plan_and_execute_blocks_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    _write_db(backup, rows=3)
    _write_db(target, rows=1)

    from integrations.hermes.tasks.restore_db import RestoreDbTask

    original_preflight = RestoreDbTask.preflight

    def drift_after_preflight(self, context, plan):  # type: ignore[no-untyped-def]
        checks = original_preflight(self, context, plan)
        backup.unlink()
        _write_db(backup, rows=5)
        return checks

    monkeypatch.setattr(RestoreDbTask, "preflight", drift_after_preflight)

    result = run_registered_task(
        _args(
            tmp_path,
            backup=backup,
            target=target,
            mode="execute",
            ack_risk="RESTORE_DB",
        )
    )

    assert result.exit_code == EXIT_TASK_FAILURE
    assert result.status == "failed"
    assert "hash drift" in (result.error or "")
    assert _row_count(target) == 1
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "execute_failure.json").exists()
    assert (run_dir / "repair_prompt.md").exists()


def test_execute_refuses_mutation_after_task_lock_health_is_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    _write_db(backup, rows=3)
    _write_db(target, rows=1)

    from integrations.hermes.tasks.restore_db import RestoreDbTask
    from scripts import restore_db as restore_script

    original_preflight = RestoreDbTask.preflight

    def lose_lock_after_preflight(self, context, plan):  # type: ignore[no-untyped-def]
        checks = original_preflight(self, context, plan)
        assert context.acquired_locks
        lock_path = context.acquired_locks[0].lock_path
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        payload["ownerToken"] = "stolen-owner"
        lock_path.write_text(json.dumps(payload), encoding="utf-8")
        return checks

    def fail_if_called(
        *_args: object,
        **_kwargs: object,
    ) -> restore_script.RestoreBackupResult:
        raise AssertionError("restore helper should not run after lock health loss")

    monkeypatch.setattr(RestoreDbTask, "preflight", lose_lock_after_preflight)
    monkeypatch.setattr(
        restore_script,
        "restore_backup_with_lock_and_ledger",
        fail_if_called,
    )

    result = run_registered_task(
        _args(
            tmp_path,
            backup=backup,
            target=target,
            mode="execute",
            ack_risk="RESTORE_DB",
        )
    )

    assert result.exit_code == EXIT_LOCK_HELD
    assert result.status == "lock_unhealthy"
    assert _row_count(target) == 1
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "lock_health_failure.json").exists()
    payload = json.loads(
        (run_dir / "lock_health_failure.json").read_text(encoding="utf-8")
    )
    assert payload["stage"] == "before_ack_gated_execute"
    assert "ownerToken" in payload["error"]


def test_execute_uses_shared_restore_helper_and_outputs_db_ops_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    pre_restore = tmp_path / "canonical-pre-restore.db"
    lock_path = target.resolve().with_suffix(".db.dbtool.lock")
    _write_db(backup, rows=4)
    _write_db(target, rows=1)
    calls: list[tuple[object, ...]] = []

    from scripts import restore_db as restore_script

    def fake_restore_helper(*args: object, **_kwargs: object) -> restore_script.RestoreBackupResult:
        calls.append(args)
        return restore_script.RestoreBackupResult(
            backup_path=backup,
            db_path=target.resolve(),
            pre_restore_backup=pre_restore,
            target_sha256_before="target-before",
            target_sha256_after="target-after",
            backup_sha256="backup-hash",
            integrity_check="ok",
            schema_version=53,
            db_ops_ledger_status="success",
            lock_path=lock_path,
        )

    monkeypatch.setattr(
        restore_script,
        "restore_backup_with_lock_and_ledger",
        fake_restore_helper,
    )

    result = run_registered_task(
        _args(
            tmp_path,
            backup=backup,
            target=target,
            mode="execute",
            ack_risk="RESTORE_DB",
        )
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    assert calls == [
        (
            backup,
            target,
            True,
            "http://127.0.0.1:9/health",
        )
    ]
    assert result.outputs["dbOpsLedgerStatus"] == "success"
    assert result.outputs["dbToolLockPath"] == str(lock_path)
    assert result.outputs["canonicalPreRestorePath"] == str(pre_restore)
    assert result.outputs["targetSha256Before"] == "target-before"
    assert result.outputs["targetSha256"] == "target-after"
    assert result.outputs["backupSha256"] == "backup-hash"


def test_execute_wraps_restore_helper_partial_evidence_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    lock_path = target.resolve().with_suffix(".db.dbtool.lock")
    _write_db(backup, rows=4)
    _write_db(target, rows=1)

    from scripts import restore_db as restore_script

    def fail_restore_helper(*_args: object, **_kwargs: object) -> restore_script.RestoreBackupResult:
        error = restore_script.RestoreError(
            "helper failed",
            integrity_check="bad-page",
        )
        error.partial_evidence.update(
            {
                "db_ops_ledger_status": "error",
                "lock_path": str(lock_path),
                "target_sha256_before": "target-before",
                "target_sha256_after": "target-after",
                "backup_sha256": "backup-hash",
            }
        )
        raise error

    monkeypatch.setattr(
        restore_script,
        "restore_backup_with_lock_and_ledger",
        fail_restore_helper,
    )

    result = run_registered_task(
        _args(
            tmp_path,
            backup=backup,
            target=target,
            mode="execute",
            ack_risk="RESTORE_DB",
        )
    )

    assert result.exit_code == EXIT_TASK_FAILURE
    assert result.status == "failed"
    evidence = result.outputs["evidence"]
    assert evidence["dbOpsLedgerStatus"] == "error"
    assert evidence["dbToolLockPath"] == str(lock_path)
    assert evidence["targetSha256Before"] == "target-before"
    assert evidence["targetSha256"] == "target-after"
    assert evidence["backupSha256"] == "backup-hash"
    assert evidence["restoreHelperEvidence"]["integrity_check"] == "bad-page"
    assert evidence["restoreHelperEvidence"]["db_ops_ledger_status"] == "error"


def test_lock_conflict_refuses_mutation(tmp_path: Path) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    _write_db(backup, rows=4)
    _write_db(target, rows=1)
    config_path = _config_path(tmp_path)
    lock_path = tmp_path / "ai-logs" / "hermes" / "task-locks" / "signals.db.lock"
    lock = HermesLock(lock_path, mode="execute", run_id="held")
    assert lock.acquire(timeout_seconds=0) is True

    try:
        args = _args(
            tmp_path,
            backup=backup,
            target=target,
            mode="execute",
            ack_risk="RESTORE_DB",
        )
        args.config = str(config_path)
        result = run_registered_task(args)
    finally:
        lock.release()

    assert result.exit_code == EXIT_LOCK_HELD
    assert result.status == "lock_held"
    assert _row_count(target) == 1
    assert (Path(result.run_dir or "") / "lock_conflict.json").exists()


def test_wal_shm_sidecars_refuse_preflight_by_default(tmp_path: Path) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    _write_db(backup, rows=2)
    _write_db(target, rows=2)
    target.with_name(target.name + "-wal").write_text("pending", encoding="utf-8")

    result = run_registered_task(_args(tmp_path, backup=backup, target=target))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    sidecar_check = next(
        check for check in result.checks if check.name == "no_unhandled_wal_shm_sidecars"
    )
    assert sidecar_check.passed is False
    assert _row_count(target) == 2


def test_wal_shm_sidecars_can_be_explicitly_delegated_to_restore_helper(
    tmp_path: Path,
) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    _write_db(backup, rows=2)
    _write_db(target, rows=2)
    target.with_name(target.name + "-wal").write_text("pending", encoding="utf-8")
    target.with_name(target.name + "-shm").write_text("pending", encoding="utf-8")

    result = run_registered_task(
        _args(tmp_path, backup=backup, target=target, handle_sidecars=True)
    )

    assert result.exit_code == 0
    sidecar_check = next(
        check for check in result.checks if check.name == "no_unhandled_wal_shm_sidecars"
    )
    assert sidecar_check.passed is True
    assert sidecar_check.evidence == {
        "present": ["signals.db-wal", "signals.db-shm"],
        "handler": "scripts.restore_db._ensure_no_target_sidecars",
    }
    assert _row_count(target) == 2


def test_low_row_count_watermark_fails_postflight(tmp_path: Path) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    _write_db(backup, rows=1)
    _write_db(target, rows=5)

    result = run_registered_task(
        _args(
            tmp_path,
            backup=backup,
            target=target,
            mode="execute",
            ack_risk="RESTORE_DB",
            min_row_count=3,
        )
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "postflight_failed"
    assert _row_count(target) == 1
    row_check = next(
        check for check in result.checks if check.name == "row_count_above_watermark"
    )
    assert row_check.evidence == {"row_count": 1, "min_row_count": 3}
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_plan_and_dry_run_record_hashes_without_mutating(tmp_path: Path) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    _write_db(backup, rows=4)
    _write_db(target, rows=1)

    plan_result = run_registered_task(
        _args(tmp_path, backup=backup, target=target, mode="plan-only")
    )
    dry_run_result = run_registered_task(
        _args(tmp_path, backup=backup, target=target, mode="dry-run")
    )

    assert plan_result.exit_code == 0
    assert plan_result.plan["backup"]["sha256"]
    assert plan_result.plan["target"]["current_sha256"]
    assert plan_result.plan["mutation"]["allowed"] is False
    assert dry_run_result.exit_code == 0
    assert dry_run_result.outputs["mutationCommitted"] is False
    assert _row_count(target) == 1

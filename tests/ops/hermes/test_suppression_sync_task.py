from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from integrations.hermes.locks import HermesLock
from integrations.hermes.tasks.base import (
    EXIT_ACK_REQUIRED,
    EXIT_GATE_FAILURE,
    EXIT_LOCK_HELD,
)
from integrations.hermes.tasks.registry import add_task_arguments, run_registered_task

from .conftest import minimal_config_dict


def _load_suppression_workflow_with_fakes(monkeypatch):
    storage_pkg = types.ModuleType("storage")
    storage_pkg.__path__ = []  # type: ignore[attr-defined]
    storage_module = types.ModuleType("storage.signal_store")

    class FakeSignalStore:
        def __init__(
            self,
            db_path: str | Path | None = None,
            suppression_ttl_days: int = 7,
            read_only: bool = False,
            **_: Any,
        ) -> None:
            self.db_path = Path(db_path or "signals.db")
            self.suppression_ttl_days = suppression_ttl_days
            self.read_only = read_only
            self.initialized = False

        async def initialize(self) -> None:
            self.initialized = True
            if self.read_only and not self.db_path.exists():
                raise FileNotFoundError(str(self.db_path))
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path.touch(exist_ok=True)

        async def close(self) -> None:
            return None

        async def update_suppression_cache(self, entries):  # type: ignore[no-untyped-def]
            return len(entries)

        async def clean_expired_cache(self) -> int:
            return 0

    class FakeSuppressionEntry:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    storage_module.SignalStore = FakeSignalStore
    storage_module.SuppressionEntry = FakeSuppressionEntry
    storage_pkg.signal_store = storage_module

    connectors_pkg = types.ModuleType("connectors")
    connectors_pkg.__path__ = []  # type: ignore[attr-defined]
    notion_module = types.ModuleType("connectors.notion_connector_v2")

    class FakeNotionConnector:
        PROP_STATUS = "Status"
        PROP_COMPANY_NAME = "Company"
        PROP_CANONICAL_KEY = "Canonical Key"
        PROP_WEBSITE = "Website"

        def __init__(self, **_: Any) -> None:
            return None

    notion_module.NotionConnector = FakeNotionConnector
    connectors_pkg.notion_connector_v2 = notion_module

    utils_pkg = types.ModuleType("utils")
    utils_pkg.__path__ = []  # type: ignore[attr-defined]
    canonical_module = types.ModuleType("utils.canonical_keys")
    canonical_module.build_canonical_key = lambda *args, **kwargs: None
    canonical_module.normalize_domain = lambda value: value
    canonical_module.is_strong_key = lambda value: str(value).startswith("domain:")
    canonical_module._slug = lambda value: str(value).lower().replace(" ", "-")
    utils_pkg.canonical_keys = canonical_module

    monkeypatch.setitem(sys.modules, "storage", storage_pkg)
    monkeypatch.setitem(sys.modules, "storage.signal_store", storage_module)
    monkeypatch.setitem(sys.modules, "connectors", connectors_pkg)
    monkeypatch.setitem(sys.modules, "connectors.notion_connector_v2", notion_module)
    monkeypatch.setitem(sys.modules, "utils", utils_pkg)
    monkeypatch.setitem(sys.modules, "utils.canonical_keys", canonical_module)
    sys.modules.pop("workflows.suppression_sync", None)
    return importlib.import_module("workflows.suppression_sync")


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
    delete_stale: bool | None = None,
    max_removals: int = 25,
    ttl_days: int = 7,
) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="suppression-sync",
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
        ttl_days=ttl_days,
        delete_stale=delete_stale,
        max_removals=max_removals,
    )


def _write_suppression_db(
    path: Path,
    *,
    rows: list[tuple[str, str]] | None = None,
    expired_rows: int = 0,
    unique: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        unique_sql = " UNIQUE" if unique else ""
        conn.execute(
            f"""
            CREATE TABLE suppression_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_key TEXT NOT NULL{unique_sql},
                notion_page_id TEXT NOT NULL,
                status TEXT NOT NULL,
                company_name TEXT,
                cached_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                metadata TEXT
            )
            """
        )
        now = datetime.now(timezone.utc)
        fresh_expiry = (now + timedelta(days=7)).isoformat()
        expired_expiry = (now - timedelta(days=1)).isoformat()
        payload = rows or [("domain:seed.com", "notion-seed")]
        for canonical_key, page_id in payload:
            conn.execute(
                """
                INSERT INTO suppression_cache (
                    canonical_key, notion_page_id, status, company_name,
                    cached_at, expires_at, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical_key,
                    page_id,
                    "Tracking",
                    canonical_key,
                    now.isoformat(),
                    fresh_expiry,
                    None,
                ),
            )
        for index in range(expired_rows):
            conn.execute(
                """
                INSERT INTO suppression_cache (
                    canonical_key, notion_page_id, status, company_name,
                    cached_at, expires_at, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"domain:expired-{index}.com",
                    f"notion-expired-{index}",
                    "Tracking",
                    f"Expired {index}",
                    now.isoformat(),
                    expired_expiry,
                    None,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _suppression_count(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM suppression_cache").fetchone()[0])
    finally:
        conn.close()


def test_plan_only_writes_ledger_artifacts_and_stays_non_mutating(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_suppression_db(db_path)

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="plan-only"),
    )

    assert result.exit_code == 0
    assert result.status == "planned"
    assert result.plan["mutation"]["allowed"] is False
    assert result.plan["database"]["path"] == str(db_path)
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "task_plan.json").exists()
    assert (run_dir / "run_record.json").exists()
    assert (run_dir / "plan.md").exists()


def test_missing_db_is_allowed_for_workflow_initialization(tmp_path: Path) -> None:
    db_path = tmp_path / "missing-signals.db"

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="preflight-only"),
    )

    assert result.exit_code == 0
    assert result.status == "preflight_passed"
    assert db_path.exists() is False


def test_task_parser_uses_tri_state_cleanup_flags() -> None:
    parser = argparse.ArgumentParser()
    add_task_arguments(parser)

    default_args = parser.parse_args(["suppression-sync", "--preflight-only"])
    delete_args = parser.parse_args(
        ["suppression-sync", "--preflight-only", "--delete-stale"]
    )
    skip_args = parser.parse_args(
        ["suppression-sync", "--preflight-only", "--skip-clean-expired"]
    )

    assert default_args.delete_stale is None
    assert delete_args.delete_stale is True
    assert skip_args.delete_stale is False


def test_plan_default_delete_stale_matches_workflow_default(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    _write_suppression_db(db_path)

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="plan-only"),
    )

    assert result.exit_code == 0
    assert result.plan["delete_stale_requested"] is True
    assert result.plan["ack_risk_required"] is True
    assert result.plan["ack_risk_token"] == "SUPPRESSION_DELETE"
    command = result.plan["workflow"]["command"]
    assert "--delete-stale" in command
    assert "--skip-clean-expired" not in command


def test_invalid_db_fails_preflight_safely_and_emits_repair_prompt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    db_path.write_text("not sqlite", encoding="utf-8")

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="preflight-only"),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "repair_prompt.md").exists()
    assert any(check.name == "database_openable" and not check.passed for check in result.checks)


def test_lock_conflict_on_suppression_cache_refuses_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_suppression_db(db_path)
    config_path = _config_path(tmp_path)
    lock_path = tmp_path / "ai-logs" / "hermes" / "task-locks" / "suppression-cache.lock"
    lock = HermesLock(lock_path, mode="execute", run_id="held")
    assert lock.acquire(timeout_seconds=0) is True

    try:
        args = _args(tmp_path, db_path=db_path, mode="execute")
        args.config = str(config_path)
        result = run_registered_task(args)
    finally:
        lock.release()

    assert result.exit_code == EXIT_LOCK_HELD
    assert result.status == "lock_held"
    assert _suppression_count(db_path) == 1
    assert (Path(result.run_dir or "") / "lock_conflict.json").exists()


def test_dry_run_uses_default_delete_stale_workflow_flag_and_leaves_db_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_suppression_db(db_path)
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        calls.append(command)
        return {
            "command": command,
            "returnCode": 0,
            "stdout": "dry run",
            "stderr": "",
            "timedOut": False,
        }

    monkeypatch.setattr(
        "integrations.hermes.tasks.suppression_sync.run_command",
        fake_run_command,
    )

    result = run_registered_task(_args(tmp_path, db_path=db_path, mode="dry-run"))

    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    assert _suppression_count(db_path) == 1
    assert len(calls) == 1
    assert calls[0][1:3] == ["-m", "workflows.suppression_sync"]
    assert calls[0][calls[0].index("--db-path") + 1] == str(db_path)
    assert calls[0][calls[0].index("--ttl-days") + 1] == "7"
    assert "--dry-run" in calls[0]
    assert "--delete-stale" in calls[0]
    assert "--skip-clean-expired" not in calls[0]
    assert (Path(result.run_dir or "") / "suppression_sync_command.json").exists()


def test_execute_explicit_skip_snapshots_db_without_destructive_ack(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_suppression_db(db_path)
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        calls.append(command)
        return {
            "command": command,
            "returnCode": 0,
            "stdout": "synced",
            "stderr": "",
            "timedOut": False,
        }

    monkeypatch.setattr(
        "integrations.hermes.tasks.suppression_sync.run_command",
        fake_run_command,
    )

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="execute", delete_stale=False),
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    assert len(calls) == 1
    assert "--dry-run" not in calls[0]
    assert "--skip-clean-expired" in calls[0]
    assert "--delete-stale" not in calls[0]
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "snapshots" / "pre_suppression_sync.db").exists()
    assert (run_dir / "run_record.json").exists()
    assert result.outputs["preSyncSnapshotSha256"]


def test_default_delete_stale_requires_ack_before_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_suppression_db(db_path, expired_rows=1)
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        calls.append(command)
        return {
            "command": command,
            "returnCode": 0,
            "stdout": "",
            "stderr": "",
            "timedOut": False,
        }

    monkeypatch.setattr(
        "integrations.hermes.tasks.suppression_sync.run_command",
        fake_run_command,
    )

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="execute"),
    )

    assert result.exit_code == EXIT_ACK_REQUIRED
    assert result.status == "approval_required"
    assert result.outputs == {
        "requiredAck": "SUPPRESSION_DELETE",
        "providedAck": None,
    }
    assert calls == []
    assert _suppression_count(db_path) == 2
    assert (Path(result.run_dir or "") / "approval_required.json").exists()


def test_destructive_delete_stale_with_ack_passes_explicit_workflow_flag(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_suppression_db(db_path, expired_rows=1)
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        calls.append(command)
        return {
            "command": command,
            "returnCode": 0,
            "stdout": "synced",
            "stderr": "",
            "timedOut": False,
        }

    monkeypatch.setattr(
        "integrations.hermes.tasks.suppression_sync.run_command",
        fake_run_command,
    )

    result = run_registered_task(
        _args(
            tmp_path,
            db_path=db_path,
            mode="execute",
            delete_stale=True,
            ack_risk="SUPPRESSION_DELETE",
        ),
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    assert len(calls) == 1
    assert "--delete-stale" in calls[0]
    assert "--skip-clean-expired" not in calls[0]


def test_delete_stale_estimate_above_max_removals_fails_preflight(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_suppression_db(db_path, expired_rows=3)

    result = run_registered_task(
        _args(
            tmp_path,
            db_path=db_path,
            mode="preflight-only",
            delete_stale=True,
            max_removals=2,
        ),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(
        check
        for check in result.checks
        if check.name == "destructive_removals_within_threshold"
    )
    assert check.passed is False
    assert check.evidence == {"estimated_removals": 3, "max_removals": 2}


def test_postflight_catches_duplicate_suppression_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_suppression_db(db_path, unique=False)

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        conn = sqlite3.connect(db_path)
        try:
            now = datetime.now(timezone.utc)
            conn.execute(
                """
                INSERT INTO suppression_cache (
                    canonical_key, notion_page_id, status, company_name,
                    cached_at, expires_at, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "domain:seed.com",
                    "notion-duplicate",
                    "Tracking",
                    "Duplicate",
                    now.isoformat(),
                    (now + timedelta(days=7)).isoformat(),
                    None,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "command": command,
            "returnCode": 0,
            "stdout": "synced",
            "stderr": "",
            "timedOut": False,
        }

    monkeypatch.setattr(
        "integrations.hermes.tasks.suppression_sync.run_command",
        fake_run_command,
    )

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="execute", delete_stale=False),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "postflight_failed"
    duplicate_check = next(
        check for check in result.checks if check.name == "no_duplicate_suppressions"
    )
    assert duplicate_check.passed is False
    assert duplicate_check.evidence["duplicates"] == ["domain:seed.com"]
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_signals_db_lock_conflict_serializes_with_restore_db(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    _write_suppression_db(db_path)
    config_path = _config_path(tmp_path)
    lock_path = tmp_path / "ai-logs" / "hermes" / "task-locks" / "signals.db.lock"
    lock = HermesLock(lock_path, mode="execute", run_id="restore-held")
    assert lock.acquire(timeout_seconds=0) is True

    try:
        args = _args(tmp_path, db_path=db_path, mode="execute")
        args.config = str(config_path)
        result = run_registered_task(args)
    finally:
        lock.release()

    assert result.exit_code == EXIT_LOCK_HELD
    assert result.status == "lock_held"
    assert (Path(result.run_dir or "") / "lock_conflict.json").exists()


def test_workflow_can_skip_expired_cleanup(monkeypatch) -> None:
    suppression_workflow = _load_suppression_workflow_with_fakes(monkeypatch)

    class FakeStore:
        clean_called = False

        async def update_suppression_cache(self, entries):  # type: ignore[no-untyped-def]
            return len(entries)

        async def clean_expired_cache(self) -> int:
            self.clean_called = True
            return 1

    async def fake_fetch() -> list[dict[str, Any]]:
        return []

    store = FakeStore()
    sync = suppression_workflow.SuppressionSync(object(), store)
    monkeypatch.setattr(sync, "_fetch_notion_pages", fake_fetch)

    stats = asyncio.run(sync.sync(dry_run=False, clean_expired=False))

    assert stats.entries_synced == 0
    assert stats.entries_expired_cleaned == 0
    assert store.clean_called is False


def test_workflow_dry_run_with_missing_db_does_not_initialize_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    suppression_workflow = _load_suppression_workflow_with_fakes(monkeypatch)

    db_path = tmp_path / "missing" / "signals.db"

    async def fake_fetch(self) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setenv("NOTION_API_KEY", "test-key")
    monkeypatch.setenv("NOTION_DATABASE_ID", "test-db")
    monkeypatch.setattr(
        suppression_workflow.SuppressionSync,
        "_fetch_notion_pages",
        fake_fetch,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suppression_sync",
            "--dry-run",
            "--db-path",
            str(db_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(suppression_workflow.main())

    assert exc_info.value.code == 0
    assert db_path.exists() is False

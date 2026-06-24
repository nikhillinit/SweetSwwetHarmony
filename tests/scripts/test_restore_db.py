from __future__ import annotations

import inspect
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from scripts import restore_db
from scripts.restore_db import (
    MAINTENANCE_LOCK_TIMEOUT_SECONDS,
    RestoreError,
    restore_backup_with_lock_and_ledger,
)
from utils.db_tool_lock import DBToolLock


def _write_db(path: Path, *, rows: int = 3, schema_version: int = 41) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (schema_version,))
        conn.execute("CREATE TABLE data (id INTEGER PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO data (value) VALUES (?)",
            [(f"row-{index}",) for index in range(rows)],
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _row_count(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM data").fetchone()[0])
    finally:
        conn.close()


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_ledger(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture(autouse=True)
def _signal_store_schema_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("storage.signal_store")
    module.CURRENT_SCHEMA_VERSION = 41
    monkeypatch.setitem(sys.modules, "storage.signal_store", module)


def test_restore_helper_writes_success_ledger_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = _write_db(tmp_path / "backup.db", rows=5)
    target = _write_db(tmp_path / "signals.db", rows=1)
    ledger = tmp_path / "db_ops_ledger.jsonl"
    before_hash = _sha256(target)
    backup_hash = _sha256(backup)

    monkeypatch.setenv("DB_OPS_LEDGER_PATH", str(ledger))
    monkeypatch.setattr(restore_db, "_check_api_reachable", lambda _url: False)

    result = restore_backup_with_lock_and_ledger(
        backup,
        target,
        api_url="http://127.0.0.1:9/health",
    )

    assert _row_count(target) == 5
    assert result.backup_path == backup
    assert result.db_path == target.resolve()
    assert result.pre_restore_backup.exists()
    assert result.target_sha256_before == before_hash
    assert result.target_sha256_after == _sha256(target)
    assert result.backup_sha256 == backup_hash
    assert result.integrity_check == "ok"
    assert result.schema_version == 41
    assert result.db_ops_ledger_status == "success"
    assert result.lock_path == target.resolve().with_suffix(".db.dbtool.lock")
    assert not result.lock_path.exists()

    rows = _read_ledger(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row["tool_name"] == "restore_db"
    assert row["status"] == "success"
    assert row["db_path"] == str(target.resolve())
    assert row["details"]["backup_file"] == str(backup)
    assert row["details"]["pre_restore_backup"] == str(result.pre_restore_backup)
    assert row["details"]["target_sha256_before"] == before_hash
    assert row["details"]["target_sha256_after"] == result.target_sha256_after
    assert row["details"]["backup_sha256"] == backup_hash
    assert row["details"]["integrity_check"] == "ok"
    assert row["details"]["schema_version"] == 41
    assert row["details"]["lock_path"] == str(result.lock_path)


def test_restore_helper_writes_error_ledger_with_partial_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = _write_db(tmp_path / "backup.db", rows=2)
    target = _write_db(tmp_path / "signals.db", rows=1)
    ledger = tmp_path / "db_ops_ledger.jsonl"
    pre_restore = tmp_path / "pre-restore.db"

    monkeypatch.setenv("DB_OPS_LEDGER_PATH", str(ledger))

    def fail_restore(*_args: object, **_kwargs: object) -> Path:
        raise RestoreError(
            "restore exploded",
            pre_restore_backup=pre_restore,
            integrity_check="bad-page",
        )

    monkeypatch.setattr(restore_db, "restore_backup", fail_restore)

    with pytest.raises(RestoreError, match="restore exploded"):
        restore_backup_with_lock_and_ledger(backup, target)

    rows = _read_ledger(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "error"
    assert row["details"]["pre_restore_backup"] == str(pre_restore)
    assert row["details"]["integrity_check"] == "bad-page"
    assert row["details"]["target_sha256_before"] == _sha256(target)
    assert row["details"]["backup_sha256"] == _sha256(backup)
    assert row["details"]["db_ops_ledger_status"] == "error"
    assert row["details"]["error"] == "restore exploded"


def test_restore_helper_writes_lock_blocked_ledger_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = _write_db(tmp_path / "backup.db", rows=2)
    target = _write_db(tmp_path / "signals.db", rows=1)
    ledger = tmp_path / "db_ops_ledger.jsonl"
    holder = DBToolLock(target, tool_name="test-holder")
    assert holder.acquire(timeout_seconds=0) is True

    monkeypatch.setenv("DB_OPS_LEDGER_PATH", str(ledger))

    try:
        with pytest.raises(RestoreError, match="Could not acquire DB tool lock"):
            restore_backup_with_lock_and_ledger(
                backup,
                target,
                lock_timeout_seconds=0,
            )
    finally:
        holder.release()

    rows = _read_ledger(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "lock_blocked"
    assert row["details"]["holder"]["tool_name"] == "test-holder"
    assert row["details"]["backup_file"] == str(backup)
    assert row["details"]["lock_path"] == str(
        target.resolve().with_suffix(".db.dbtool.lock")
    )


def test_restore_helper_refuses_overwrite_after_lock_health_is_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = _write_db(tmp_path / "backup.db", rows=5)
    target = _write_db(tmp_path / "signals.db", rows=1)
    ledger = tmp_path / "db_ops_ledger.jsonl"
    original_copy2 = restore_db.shutil.copy2

    monkeypatch.setenv("DB_OPS_LEDGER_PATH", str(ledger))
    monkeypatch.setattr(restore_db, "_check_api_reachable", lambda _url: False)

    def steal_lock_after_pre_restore_copy(
        src: object,
        dst: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        result = original_copy2(src, dst, *args, **kwargs)
        destination = Path(dst)
        if destination.name.startswith(restore_db.PRE_RESTORE_PREFIX):
            lock_path = target.resolve().with_suffix(".db.dbtool.lock")
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
            payload["ownerToken"] = "stolen-owner"
            lock_path.write_text(json.dumps(payload), encoding="utf-8")
        return result

    monkeypatch.setattr(restore_db.shutil, "copy2", steal_lock_after_pre_restore_copy)

    with pytest.raises(RestoreError, match="lock health"):
        restore_backup_with_lock_and_ledger(
            backup,
            target,
            api_url="http://127.0.0.1:9/health",
        )

    assert _row_count(target) == 1
    rows = _read_ledger(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "error"
    assert "ownerToken" in row["details"]["heartbeat_error"]
    assert row["details"]["target_sha256_before"] == _sha256(target)


def test_cli_main_uses_restore_helper_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    pre_restore = tmp_path / "pre-restore.db"
    called: dict[str, object] = {}

    def fake_helper(*args: object, **kwargs: object) -> restore_db.RestoreBackupResult:
        called["args"] = args
        called["kwargs"] = kwargs
        return restore_db.RestoreBackupResult(
            backup_path=backup,
            db_path=target.resolve(),
            pre_restore_backup=pre_restore,
            target_sha256_before="before",
            target_sha256_after="after",
            backup_sha256="backup",
            integrity_check="ok",
            schema_version=41,
            db_ops_ledger_status="success",
            lock_path=target.resolve().with_suffix(".db.dbtool.lock"),
        )

    monkeypatch.setattr(restore_db, "restore_backup_with_lock_and_ledger", fake_helper)

    exit_code = restore_db.main(
        [
            str(backup),
            "--db-path",
            str(target),
            "--force",
            "--api-url",
            "http://127.0.0.1:9/health",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert called["args"][0] == str(backup)
    assert Path(called["args"][1]).resolve() == target.resolve()
    assert called["args"][2:] == (True, "http://127.0.0.1:9/health")
    assert "Restore complete. Pre-restore backup:" in captured.out
    assert str(pre_restore) in captured.out
    assert captured.err == ""


def test_cli_main_preserves_error_exit_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"

    def fail_helper(*_args: object, **_kwargs: object) -> restore_db.RestoreBackupResult:
        raise RestoreError("nope")

    monkeypatch.setattr(restore_db, "restore_backup_with_lock_and_ledger", fail_helper)

    exit_code = restore_db.main([str(backup), "--db-path", str(target)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "ERROR: nope" in captured.err


def test_restore_helper_defaults_to_maintenance_lock_timeout() -> None:
    """The restore entry path must default to the 180s maintenance timeout, not the 5s
    LOCK_TIMEOUT_SECONDS the code comment itself calls 'insufficient' for a 30-120s restore."""
    default = (
        inspect.signature(restore_backup_with_lock_and_ledger)
        .parameters["lock_timeout_seconds"]
        .default
    )
    assert default == MAINTENANCE_LOCK_TIMEOUT_SECONDS
    assert default >= 120


def test_restore_helper_acquires_lock_with_maintenance_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behavioral: with no explicit lock_timeout_seconds, the helper must acquire the DB tool
    lock using the 180s maintenance timeout (regression — was silently 5s)."""
    from utils.db_tool_lock import DBToolLock

    backup = _write_db(tmp_path / "backup.db", rows=5)
    target = _write_db(tmp_path / "signals.db", rows=1)
    ledger = tmp_path / "db_ops_ledger.jsonl"
    monkeypatch.setenv("DB_OPS_LEDGER_PATH", str(ledger))
    monkeypatch.setattr(restore_db, "_check_api_reachable", lambda _url: False)

    recorded: dict[str, int] = {}
    real_acquire = DBToolLock.acquire

    def spy_acquire(self, timeout_seconds: int = 30) -> bool:
        recorded["timeout"] = timeout_seconds
        return real_acquire(self, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(DBToolLock, "acquire", spy_acquire)

    restore_backup_with_lock_and_ledger(
        backup,
        target,
        api_url="http://127.0.0.1:9/health",
    )

    assert recorded["timeout"] == MAINTENANCE_LOCK_TIMEOUT_SECONDS


def test_cli_main_threads_lock_timeout_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI threads --lock-timeout-seconds through as a keyword arg, and defaults to the 180s
    maintenance timeout when the flag is omitted."""
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    called: dict[str, object] = {}

    def fake_helper(*args: object, **kwargs: object) -> restore_db.RestoreBackupResult:
        called["kwargs"] = kwargs
        return restore_db.RestoreBackupResult(
            backup_path=backup,
            db_path=target.resolve(),
            pre_restore_backup=tmp_path / "pre-restore.db",
            target_sha256_before="before",
            target_sha256_after="after",
            backup_sha256="backup",
            integrity_check="ok",
            schema_version=41,
            db_ops_ledger_status="success",
            lock_path=target.resolve().with_suffix(".db.dbtool.lock"),
        )

    monkeypatch.setattr(restore_db, "restore_backup_with_lock_and_ledger", fake_helper)

    # explicit override
    assert restore_db.main(
        [str(backup), "--db-path", str(target), "--lock-timeout-seconds", "240"]
    ) == 0
    assert called["kwargs"]["lock_timeout_seconds"] == 240

    # default when flag omitted
    called.clear()
    assert restore_db.main([str(backup), "--db-path", str(target)]) == 0
    assert called["kwargs"]["lock_timeout_seconds"] == MAINTENANCE_LOCK_TIMEOUT_SECONDS


def test_restore_helper_records_litestream_mode_off_in_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mode B: the restore result and ledger row must explicitly record
    litestream_mode='off' so a non-interactive run is unambiguous that Litestream
    orchestration did not run (cloud restore is proven by the nightly verify)."""
    backup = _write_db(tmp_path / "backup.db", rows=5)
    target = _write_db(tmp_path / "signals.db", rows=1)
    ledger = tmp_path / "db_ops_ledger.jsonl"

    monkeypatch.setenv("DB_OPS_LEDGER_PATH", str(ledger))
    monkeypatch.setattr(restore_db, "_check_api_reachable", lambda _url: False)

    result = restore_backup_with_lock_and_ledger(
        backup,
        target,
        api_url="http://127.0.0.1:9/health",
    )

    assert result.litestream_mode == "off"
    rows = _read_ledger(ledger)
    assert len(rows) == 1
    assert rows[0]["details"]["litestream_mode"] == "off"


def test_restore_helper_litestream_mode_defaults_off() -> None:
    default = (
        inspect.signature(restore_backup_with_lock_and_ledger)
        .parameters["litestream_mode"]
        .default
    )
    assert default == restore_db.LITESTREAM_MODE == "off"


def test_restore_helper_rejects_mode_a_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = _write_db(tmp_path / "backup.db", rows=1)
    target = _write_db(tmp_path / "signals.db", rows=1)
    monkeypatch.setenv("DB_OPS_LEDGER_PATH", str(tmp_path / "db_ops_ledger.jsonl"))
    with pytest.raises(RestoreError, match="not supported"):
        restore_backup_with_lock_and_ledger(backup, target, litestream_mode="required")


def test_cli_main_threads_litestream_mode_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "signals.db"
    called: dict[str, object] = {}

    def fake_helper(*args: object, **kwargs: object) -> restore_db.RestoreBackupResult:
        called["kwargs"] = kwargs
        return restore_db.RestoreBackupResult(
            backup_path=backup,
            db_path=target.resolve(),
            pre_restore_backup=tmp_path / "pre-restore.db",
            target_sha256_before="before",
            target_sha256_after="after",
            backup_sha256="backup",
            integrity_check="ok",
            schema_version=41,
            db_ops_ledger_status="success",
            lock_path=target.resolve().with_suffix(".db.dbtool.lock"),
        )

    monkeypatch.setattr(restore_db, "restore_backup_with_lock_and_ledger", fake_helper)

    # default when flag omitted
    assert restore_db.main([str(backup), "--db-path", str(target)]) == 0
    assert called["kwargs"]["litestream_mode"] == "off"

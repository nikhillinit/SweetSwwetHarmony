import ast
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest


def _current_schema_version() -> int:
    source = Path("storage/signal_store.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in module.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "CURRENT_SCHEMA_VERSION"
                for target in node.targets
            )
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("CURRENT_SCHEMA_VERSION not found")


CURRENT_SCHEMA_VERSION = _current_schema_version()


def _create_restore_db(path: Path, *, schema_version: int | None = None, signals: int = 3) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
        if schema_version is not None:
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (schema_version,),
            )
        conn.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY)")
        conn.executemany(
            "INSERT INTO signals (id) VALUES (?)",
            [(idx,) for idx in range(1, signals + 1)],
        )
        conn.commit()
    finally:
        conn.close()


def test_restore_and_verify_uses_restore_command_and_checks_database(tmp_path):
    from scripts.litestream_restore_verify import restore_and_verify

    source = tmp_path / "source.db"
    restored = tmp_path / "restored.db"
    _create_restore_db(source, schema_version=CURRENT_SCHEMA_VERSION, signals=4)
    commands: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        shutil.copy2(source, restored)
        return subprocess.CompletedProcess(command, 0, stdout="restored", stderr="")

    summary = restore_and_verify(
        replica_url="s3://example/litestream/signals.db/",
        restore_path=restored,
        min_signals=4,
        expected_schema_version=CURRENT_SCHEMA_VERSION,
        runner=fake_runner,
    )

    assert commands
    assert commands[0][0] == "litestream"
    assert "restore" in commands[0]
    assert "verify" not in commands[0]
    assert summary["integrity_check"] == "ok"
    assert summary["schema_version"] == CURRENT_SCHEMA_VERSION
    assert summary["signal_count"] == 4


def test_verify_restored_database_rejects_corrupt_sqlite(tmp_path):
    from scripts.litestream_restore_verify import RestoreVerificationError, verify_restored_database

    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"not a sqlite database")

    with pytest.raises(RestoreVerificationError, match="integrity"):
        verify_restored_database(db_path, min_signals=1)


def test_verify_restored_database_requires_schema_migrations(tmp_path):
    from scripts.litestream_restore_verify import RestoreVerificationError, verify_restored_database

    db_path = tmp_path / "signals.db"
    _create_restore_db(db_path, schema_version=None, signals=3)

    with pytest.raises(RestoreVerificationError, match="schema_migrations"):
        verify_restored_database(db_path, min_signals=1)


def test_verify_restored_database_enforces_signal_lower_bound(tmp_path):
    from scripts.litestream_restore_verify import RestoreVerificationError, verify_restored_database

    db_path = tmp_path / "signals.db"
    _create_restore_db(db_path, schema_version=CURRENT_SCHEMA_VERSION, signals=1)

    with pytest.raises(RestoreVerificationError, match="signal lower bound"):
        verify_restored_database(db_path, min_signals=2)


def test_litestream_verify_command_is_rejected():
    from scripts.litestream_restore_verify import RestoreVerificationError, run_litestream_command

    with pytest.raises(RestoreVerificationError, match="verify subcommand"):
        run_litestream_command(["litestream", "verify"], runner=lambda command: None)

from __future__ import annotations

import asyncio
import csv
import json
import sqlite3
import os
import subprocess
import sys
from pathlib import Path

from storage.signal_store import SignalStore
from utils.db_tool_lock import DBToolLock


def _read_ledger_rows(ledger_path: Path) -> list[dict]:
    """Parse JSONL ledger entries; tolerates missing file."""
    if not ledger_path.exists():
        return []
    return [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _error_rows_for(rows: list[dict], tool_name: str) -> list[dict]:
    return [
        row
        for row in rows
        if row.get("tool_name") == tool_name and row.get("status") == "error"
    ]


ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


def _run_script(relative_path: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    # Running scripts by file path puts scripts/ (not the repo root) on
    # sys.path, so absolute imports like utils.db_path_helper fail without
    # the repo root on PYTHONPATH.
    existing_pythonpath = merged_env.get("PYTHONPATH")
    merged_env["PYTHONPATH"] = (
        str(ROOT)
        if not existing_pythonpath
        else str(ROOT) + os.pathsep + existing_pythonpath
    )
    if env:
        merged_env.update(env)
    return subprocess.run(
        [PYTHON, str(ROOT / relative_path), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=merged_env,
    )


def _create_review_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE review_items (
                id INTEGER PRIMARY KEY,
                company_id TEXT,
                status TEXT,
                decided_by TEXT,
                decided_at TEXT,
                reason TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE company_files (
                company_id TEXT PRIMARY KEY,
                company_name TEXT,
                canonical_key TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                company_id TEXT,
                confidence REAL,
                source_api TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO company_files (company_id, company_name, canonical_key) VALUES ('comp-1', 'Acme', 'domain:acme.com')"
        )
        conn.execute(
            "INSERT INTO signals (id, company_id, confidence, source_api) VALUES (1, 'comp-1', 0.9, 'github')"
        )
        conn.execute(
            "INSERT INTO review_items (id, company_id, status) VALUES (1, 'comp-1', 'pending')"
        )
        conn.commit()
    finally:
        conn.close()


def _create_export_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                signal_type TEXT,
                source_api TEXT,
                canonical_key TEXT,
                company_name TEXT,
                confidence REAL,
                created_at TEXT,
                raw_data TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE signal_quality_metrics (
                signal_id INTEGER,
                human_label TEXT,
                labeled_at TEXT,
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO signals (
                id, signal_type, source_api, canonical_key, company_name,
                confidence, created_at, raw_data
            ) VALUES (
                1, 'news_mention', 'news_api', 'domain:acme.com', 'Acme',
                0.8, '2026-04-04T00:00:00+00:00',
                '{"description":"Acme launches new product","url":"https://acme.com"}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO signal_quality_metrics (signal_id, human_label, labeled_at, notes)
            VALUES (1, 'TP', '2026-04-04T00:00:00+00:00', 'looks good')
            """
        )
        conn.commit()
    finally:
        conn.close()


def _create_backfill_db(path: Path) -> None:
    async def _init() -> None:
        store = SignalStore(str(path))
        await store.initialize()
        try:
            await store._db.execute(
                """
                INSERT INTO signals (
                    signal_type, source_api, canonical_key, company_name,
                    confidence, raw_data, detected_at, created_at, company_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "job_posting",
                    "greenhouse_jobs",
                    "domain:acme.com",
                    "Acme",
                    0.8,
                    '{"url":"https://acme.com/jobs"}',
                    "2026-04-01T00:00:00+00:00",
                    "2026-04-04T00:00:00+00:00",
                    None,
                ),
            )
            await store._db.commit()
        finally:
            await store.close()

    asyncio.run(_init())


def _create_publisher_cleanup_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                canonical_key TEXT,
                raw_data TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE company_files (
                id INTEGER PRIMARY KEY,
                canonical_key TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO signals (id, canonical_key, raw_data)
            VALUES (
                1,
                'domain:techcrunch.com',
                '{"company_name": "Acme Wellness"}'
            )
            """
        )
        conn.execute(
            "INSERT INTO company_files (id, canonical_key) VALUES (1, 'domain:techcrunch.com')"
        )
        conn.commit()
    finally:
        conn.close()


def test_cleanup_publisher_keys_requires_yes_for_apply(tmp_path: Path) -> None:
    db_path = tmp_path / "publisher.db"
    _create_publisher_cleanup_db(db_path)
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/cleanup_publisher_keys.py",
        "--db",
        str(db_path),
        "--apply",
        env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
    )

    assert result.returncode == 2
    rows = _read_ledger_rows(ledger_path)
    refused_rows = [
        row
        for row in rows
        if row.get("tool_name") == "cleanup_publisher_keys"
        and row.get("status") == "refused"
    ]
    assert refused_rows, f"No refused ledger row. Rows: {rows}"
    assert refused_rows[-1]["details"]["reason"] == "missing_yes_rewrite_keys"


def test_cleanup_publisher_keys_respects_db_tool_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "publisher.db"
    _create_publisher_cleanup_db(db_path)
    ledger_path = tmp_path / "ledger.jsonl"

    lock = DBToolLock(db_path, tool_name="test-holder")
    assert lock.acquire(timeout_seconds=0)
    try:
        result = _run_script(
            "scripts/cleanup_publisher_keys.py",
            "--db",
            str(db_path),
            "--apply",
            "--yes-rewrite-keys",
            env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
        )
    finally:
        lock.release()

    assert result.returncode == 2
    rows = _read_ledger_rows(ledger_path)
    blocked_rows = [
        row
        for row in rows
        if row.get("tool_name") == "cleanup_publisher_keys"
        and row.get("status") == "lock_blocked"
    ]
    assert blocked_rows, f"No lock_blocked ledger row. Rows: {rows}"
    assert blocked_rows[-1]["details"].get("holder")


def test_cleanup_publisher_keys_records_success_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "publisher.db"
    _create_publisher_cleanup_db(db_path)
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/cleanup_publisher_keys.py",
        "--db",
        str(db_path),
        "--apply",
        "--yes-rewrite-keys",
        env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
    )

    assert result.returncode == 0
    conn = sqlite3.connect(str(db_path))
    try:
        signal_key = conn.execute("SELECT canonical_key FROM signals WHERE id = 1").fetchone()[0]
        file_key = conn.execute("SELECT canonical_key FROM company_files WHERE id = 1").fetchone()[0]
    finally:
        conn.close()
    assert signal_key == "name_loc:acme-wellness"
    assert file_key == "name_loc:acme-wellness"

    rows = _read_ledger_rows(ledger_path)
    success_rows = [
        row
        for row in rows
        if row.get("tool_name") == "cleanup_publisher_keys"
        and row.get("status") == "success"
    ]
    assert success_rows, f"No success ledger row. Rows: {rows}"
    details = success_rows[-1]["details"]
    assert details["signals_rewritten"] == 1
    assert details["company_files_rewritten"] == 1


def test_cleanup_publisher_keys_records_ledger_on_error_path(tmp_path: Path) -> None:
    db_path = tmp_path / "publisher.db"
    _create_publisher_cleanup_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DROP TABLE company_files")
        conn.commit()
    finally:
        conn.close()
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/cleanup_publisher_keys.py",
        "--db",
        str(db_path),
        "--apply",
        "--yes-rewrite-keys",
        env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
    )

    assert result.returncode == 1
    rows = _read_ledger_rows(ledger_path)
    error_rows = _error_rows_for(rows, "cleanup_publisher_keys")
    assert error_rows, f"No error ledger row for cleanup_publisher_keys. Rows: {rows}"
    details = error_rows[-1]["details"]
    assert "signals_to_rewrite" in details
    assert "company_files_to_rewrite" in details
    assert details.get("error")


def test_cleanup_publisher_keys_rolls_back_partial_apply_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "publisher.db"
    _create_publisher_cleanup_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TRIGGER fail_company_file_update
            BEFORE UPDATE ON company_files
            BEGIN
                SELECT RAISE(ABORT, 'forced company_files update failure');
            END
            """
        )
        conn.commit()
    finally:
        conn.close()
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/cleanup_publisher_keys.py",
        "--db",
        str(db_path),
        "--apply",
        "--yes-rewrite-keys",
        env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
    )

    assert result.returncode == 1
    conn = sqlite3.connect(str(db_path))
    try:
        signal_key = conn.execute("SELECT canonical_key FROM signals WHERE id = 1").fetchone()[0]
        file_key = conn.execute("SELECT canonical_key FROM company_files WHERE id = 1").fetchone()[0]
    finally:
        conn.close()
    assert signal_key == "domain:techcrunch.com"
    assert file_key == "domain:techcrunch.com"

    rows = _read_ledger_rows(ledger_path)
    error_rows = _error_rows_for(rows, "cleanup_publisher_keys")
    assert error_rows, f"No error ledger row for cleanup_publisher_keys. Rows: {rows}"
    details = error_rows[-1]["details"]
    assert details["phase"] == "apply"
    assert details["signals_to_rewrite"] == 1
    assert details["company_files_to_rewrite"] == 1
    assert details.get("error")


def test_e2e_batch_check_respects_db_path(tmp_path: Path) -> None:
    db_path = tmp_path / "review.db"
    _create_review_db(db_path)

    result = _run_script("scripts/e2e_batch_check.py", "--db-path", str(db_path), "--limit", "1")

    assert result.returncode == 0
    assert "ri.id=  1" in result.stdout


def test_e2e_batch_approve_requires_yes(tmp_path: Path) -> None:
    db_path = tmp_path / "review.db"
    _create_review_db(db_path)

    result = _run_script(
        "scripts/e2e_batch_approve.py",
        "--db-path",
        str(db_path),
        "--review-item-ids",
        "1",
    )

    assert result.returncode == 2
    conn = sqlite3.connect(str(db_path))
    try:
        status = conn.execute("SELECT status FROM review_items WHERE id = 1").fetchone()[0]
    finally:
        conn.close()
    assert status == "pending"


def test_e2e_batch_approve_updates_with_yes(tmp_path: Path) -> None:
    db_path = tmp_path / "review.db"
    _create_review_db(db_path)
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/e2e_batch_approve.py",
        "--db-path",
        str(db_path),
        "--review-item-ids",
        "1",
        "--yes",
        env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
    )

    assert result.returncode == 0
    conn = sqlite3.connect(str(db_path))
    try:
        status = conn.execute("SELECT status FROM review_items WHERE id = 1").fetchone()[0]
    finally:
        conn.close()
    assert status == "approved"
    entries = ledger_path.read_text(encoding="utf-8").strip().splitlines()
    assert any('"tool_name": "e2e_batch_approve"' in entry and '"status": "success"' in entry for entry in entries)


def test_e2e_batch_approve_respects_db_tool_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "review.db"
    _create_review_db(db_path)
    ledger_path = tmp_path / "ledger.jsonl"

    lock = DBToolLock(db_path, tool_name="test-holder")
    assert lock.acquire(timeout_seconds=0)
    try:
        result = _run_script(
            "scripts/e2e_batch_approve.py",
            "--db-path",
            str(db_path),
            "--review-item-ids",
            "1",
            "--yes",
            env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
        )
    finally:
        lock.release()

    assert result.returncode == 2
    entries = ledger_path.read_text(encoding="utf-8").strip().splitlines()
    assert any('"tool_name": "e2e_batch_approve"' in entry and '"status": "lock_blocked"' in entry for entry in entries)


def test_e2e_batch_approve_records_ledger_on_error_path(tmp_path: Path) -> None:
    """Mid-mutation failure must emit an error ledger row carrying the
    review_item_ids that were in flight, via structured ApproveError."""
    db_path = tmp_path / "review.db"
    # DB exists but lacks the review_items table — SELECT will raise
    # OperationalError after BEGIN IMMEDIATE inside the script's try block.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE other (x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/e2e_batch_approve.py",
        "--db-path",
        str(db_path),
        "--review-item-ids",
        "1,2",
        "--yes",
        env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
    )

    assert result.returncode != 0
    rows = _read_ledger_rows(ledger_path)
    error_rows = _error_rows_for(rows, "e2e_batch_approve")
    assert error_rows, f"No error ledger row for e2e_batch_approve. Rows: {rows}"
    details = error_rows[-1]["details"]
    assert details.get("review_item_ids") == [1, 2], (
        f"Expected review_item_ids=[1,2] in details, got: {details}"
    )
    assert details.get("error"), "error message missing from details"


def test_export_labeling_review_uses_custom_db_and_out(tmp_path: Path) -> None:
    db_path = tmp_path / "labels.db"
    out_path = tmp_path / "labels.csv"
    _create_export_db(db_path)

    result = _run_script(
        "scripts/export_labeling_review.py",
        "--db-path",
        str(db_path),
        "--out",
        str(out_path),
    )

    assert result.returncode == 0
    assert out_path.exists()
    with out_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[1][0] == "1"


def test_run_backfill_requires_yes_or_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "backfill.db"
    _create_backfill_db(db_path)

    result = _run_script("scripts/run_backfill.py", "--db-path", str(db_path))

    assert result.returncode == 2


def test_run_backfill_dry_run_respects_db_path(tmp_path: Path) -> None:
    db_path = tmp_path / "backfill.db"
    _create_backfill_db(db_path)

    result = _run_script("scripts/run_backfill.py", "--db-path", str(db_path), "--dry-run")

    assert result.returncode == 0
    conn = sqlite3.connect(str(db_path))
    try:
        company_id = conn.execute("SELECT company_id FROM signals").fetchone()[0]
    finally:
        conn.close()
    assert company_id is None


def test_run_backfill_updates_with_yes(tmp_path: Path) -> None:
    db_path = tmp_path / "backfill.db"
    _create_backfill_db(db_path)
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/run_backfill.py",
        "--db-path",
        str(db_path),
        "--yes",
        env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
    )

    assert result.returncode == 0
    conn = sqlite3.connect(str(db_path))
    try:
        company_id = conn.execute("SELECT company_id FROM signals").fetchone()[0]
    finally:
        conn.close()
    assert company_id is not None
    entries = ledger_path.read_text(encoding="utf-8").strip().splitlines()
    assert any('"tool_name": "run_backfill"' in entry and '"status": "success"' in entry for entry in entries)


def test_run_backfill_records_ledger_on_error_path(tmp_path: Path) -> None:
    """Mid-backfill failure must emit an error ledger row carrying the
    null_count snapshot keys via structured BackfillError. We force the
    failure by dropping the signals table after SignalStore initialization
    so the in-try-block COUNT raises before null_count_before is captured.
    (Holding an external SQLite writer lock would deadlock the script's
    own SignalStore.initialize on PRAGMA journal_mode=WAL.)"""
    db_path = tmp_path / "backfill.db"
    _create_backfill_db(db_path)
    # Drop the signals table; schema_migrations still says version is current,
    # so SignalStore.initialize in the subprocess won't re-create it.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DROP TABLE signals")
        conn.commit()
    finally:
        conn.close()
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/run_backfill.py",
        "--db-path",
        str(db_path),
        "--yes",
        env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
    )

    assert result.returncode != 0
    rows = _read_ledger_rows(ledger_path)
    error_rows = _error_rows_for(rows, "run_backfill")
    assert error_rows, f"No error ledger row for run_backfill. Rows: {rows}"
    details = error_rows[-1]["details"]
    # Key must be present in partial-evidence even if value is None
    # (the failure happens before null_count_before is populated).
    assert "null_count_before" in details, (
        f"Expected null_count_before key in details, got: {details}"
    )
    assert details.get("error"), "error message missing from details"


def test_restore_db_help_shows_shared_db_contract() -> None:
    result = _run_script("scripts/restore_db.py", "--help")

    assert result.returncode == 0
    assert "--db-path" in result.stdout
    assert "[DEPRECATED] Use --db-path instead" in result.stdout


def test_restore_db_deprecated_db_alias_warns(tmp_path: Path) -> None:
    scratch_db = tmp_path / "scratch.db"
    result = _run_script(
        "scripts/restore_db.py",
        "missing-backup.db",
        "--db",
        str(scratch_db),
    )

    assert result.returncode == 1
    assert "DEPRECATED" in result.stderr


def test_restore_db_records_ledger_on_error_path(tmp_path: Path) -> None:
    """Bad backup file → integrity check fails → structured RestoreError →
    ledger row carries integrity_check evidence."""
    db_path = tmp_path / "signals.db"
    backup_path = tmp_path / "bad_backup.db"
    backup_path.write_bytes(b"this is not a valid sqlite database")
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/restore_db.py",
        str(backup_path),
        "--db-path",
        str(db_path),
        "--force",
        env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
    )

    assert result.returncode != 0
    rows = _read_ledger_rows(ledger_path)
    error_rows = _error_rows_for(rows, "restore_db")
    assert error_rows, f"No error ledger row for restore_db. Rows: {rows}"
    details = error_rows[-1]["details"]
    assert details.get("integrity_check"), (
        f"Expected integrity_check evidence in details, got: {details}"
    )
    assert details.get("error"), "error message missing from details"


def test_restore_db_records_ledger_on_sidecar_refusal(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
        conn.execute("INSERT INTO schema_migrations VALUES (53)")
        conn.execute("CREATE TABLE data (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO data VALUES (1, 'seed')")
        conn.commit()
    finally:
        conn.close()

    backup_path = tmp_path / "backup.db"
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    writer = sqlite3.connect(str(db_path), timeout=1)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO data VALUES (2, 'busy')")
    ledger_path = tmp_path / "ledger.jsonl"

    try:
        result = _run_script(
            "scripts/restore_db.py",
            str(backup_path),
            "--db-path",
            str(db_path),
            env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
        )
    finally:
        writer.rollback()
        writer.close()

    assert result.returncode == 1
    entries = ledger_path.read_text(encoding="utf-8").strip().splitlines()
    assert any('"tool_name": "restore_db"' in entry and '"status": "error"' in entry for entry in entries)


def test_db_maintenance_records_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "maintenance.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE data (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO data (value) VALUES ('x')")
        conn.commit()
    finally:
        conn.close()

    ledger_path = tmp_path / "ledger.jsonl"
    result = _run_script(
        "scripts/db_maintenance.py",
        "--db-path",
        str(db_path),
        "--checkpoint",
        env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
    )

    assert result.returncode == 0
    entries = ledger_path.read_text(encoding="utf-8").strip().splitlines()
    assert any('"tool_name": "db_maintenance"' in entry and '"status": "success"' in entry for entry in entries)


def test_db_maintenance_records_ledger_on_error_path(tmp_path: Path) -> None:
    """Vacuum on a non-SQLite file → structured MaintenanceError →
    ledger row carries operation='vacuum' evidence."""
    db_path = tmp_path / "maintenance.db"
    db_path.write_bytes(b"this is not a sqlite database")
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/db_maintenance.py",
        "--db-path",
        str(db_path),
        "--vacuum",
        env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
    )

    assert result.returncode != 0
    rows = _read_ledger_rows(ledger_path)
    error_rows = _error_rows_for(rows, "db_maintenance")
    assert error_rows, f"No error ledger row for db_maintenance. Rows: {rows}"
    details = error_rows[-1]["details"]
    assert details.get("operation") == "vacuum", (
        f"Expected operation='vacuum' in details, got: {details}"
    )
    assert details.get("error"), "error message missing from details"


def test_db_ops_note_records_manual_entry(tmp_path: Path) -> None:
    db_path = tmp_path / "manual.db"
    db_path.write_text("placeholder", encoding="utf-8")
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/db_ops_note.py",
        "--db-path",
        str(db_path),
        "--tool-name",
        "sqlite3-shell",
        "--action",
        "manual_checkpoint",
        "--status",
        "noted",
        "--note",
        "Operator ran manual checkpoint outside repo tooling",
        env={"DB_OPS_LEDGER_PATH": str(ledger_path)},
    )

    assert result.returncode == 0
    entries = ledger_path.read_text(encoding="utf-8").strip().splitlines()
    assert any('"tool_name": "sqlite3-shell"' in entry and '"action": "manual_checkpoint"' in entry for entry in entries)

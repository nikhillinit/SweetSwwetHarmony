from __future__ import annotations

import asyncio
import csv
import sqlite3
import os
import subprocess
import sys
from pathlib import Path

from storage.signal_store import SignalStore
from utils.db_tool_lock import DBToolLock


ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


def _run_script(relative_path: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
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
            "--db",
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

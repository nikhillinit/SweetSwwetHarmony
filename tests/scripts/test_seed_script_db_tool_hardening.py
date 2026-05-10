from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from utils.db_tool_lock import DBToolLock


ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


def _run_script(
    script: str,
    *args: str,
    ledger_path: Path,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DB_OPS_LEDGER_PATH"] = str(ledger_path)
    return subprocess.run(
        [PYTHON, str(ROOT / script), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _read_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_ledger_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _ledger_rows_for(path: Path, tool_name: str, status: str) -> list[dict]:
    return [
        row
        for row in _read_ledger_rows(path)
        if row.get("tool_name") == tool_name and row.get("status") == status
    ]


def _create_seed_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE company_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT NOT NULL UNIQUE,
                company_name TEXT,
                canonical_key TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('thin', 'promoted', 'archived')),
                source_apis TEXT NOT NULL DEFAULT '[]',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                promoted_at TEXT,
                archived_at TEXT,
                metadata TEXT
            );

            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_key TEXT NOT NULL,
                source_api TEXT NOT NULL,
                created_at TEXT NOT NULL,
                signal_type TEXT DEFAULT 'news',
                confidence REAL DEFAULT 0.5,
                source_url TEXT DEFAULT '',
                detected_at TEXT DEFAULT '',
                raw_data TEXT DEFAULT '{}'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _write_domains(path: Path, *domains: str) -> None:
    path.write_text("\n".join(domains) + "\n", encoding="utf-8")


def _company_file_count(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT COUNT(*) FROM company_files").fetchone()[0]
    finally:
        conn.close()


def _insert_signal(path: Path, canonical_key: str) -> None:
    conn = sqlite3.connect(str(path))
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO signals (canonical_key, source_api, created_at) VALUES (?, ?, ?)",
            (canonical_key, "hacker_news", now),
        )
        conn.commit()
    finally:
        conn.close()


def test_seed_tier_c_default_dry_run_is_non_mutating_and_ledger_silent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    domains_path = tmp_path / "domains.txt"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_seed_db(db_path)
    _write_domains(domains_path, "freshly.com", "olipop.com")

    result = _run_script(
        "scripts/seed_tier_c_domains.py",
        "--db",
        str(db_path),
        "--domains",
        str(domains_path),
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _company_file_count(db_path) == 0
    assert _read_ledger_rows(ledger_path) == []
    assert "Preflight data_version:" in result.stdout
    report = _read_report(report_path)
    assert report["ok"] is True
    assert report["command"] == "seed_tier_c_domains"
    assert report["metrics"]["dry_run"] is True
    assert report["metrics"]["inserted"] == 2
    assert isinstance(report["metrics"]["preflight_data_version"], int)


def test_seed_tier_c_commit_records_success_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    domains_path = tmp_path / "domains.txt"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_seed_db(db_path)
    _write_domains(domains_path, "freshly.com", "olipop.com")

    result = _run_script(
        "scripts/seed_tier_c_domains.py",
        "--db",
        str(db_path),
        "--domains",
        str(domains_path),
        "--commit",
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _company_file_count(db_path) == 2
    success_rows = _ledger_rows_for(ledger_path, "seed_tier_c_domains", "success")
    assert success_rows
    details = success_rows[-1]["details"]
    assert details["inserted"] == 2
    assert details["commit_requested"] is True
    assert details["dry_run"] is False
    assert isinstance(details["preflight_data_version"], int)
    report = _read_report(report_path)
    assert report["ok"] is True
    assert report["metrics"]["preflight_data_version"] == details["preflight_data_version"]


def test_seed_tier_c_commit_lock_blocked_records_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    domains_path = tmp_path / "domains.txt"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_seed_db(db_path)
    _write_domains(domains_path, "freshly.com")

    lock = DBToolLock(db_path, tool_name="test-holder")
    assert lock.acquire(timeout_seconds=0)
    try:
        result = _run_script(
            "scripts/seed_tier_c_domains.py",
            "--db",
            str(db_path),
            "--domains",
            str(domains_path),
            "--commit",
            "--report",
            str(report_path),
            ledger_path=ledger_path,
        )
    finally:
        lock.release()

    assert result.returncode == 2
    assert _company_file_count(db_path) == 0
    blocked_rows = _ledger_rows_for(ledger_path, "seed_tier_c_domains", "lock_blocked")
    assert blocked_rows
    details = blocked_rows[-1]["details"]
    assert details["holder"]["tool_name"] == "test-holder"
    assert isinstance(details["preflight_data_version"], int)
    report = _read_report(report_path)
    assert report["ok"] is False
    assert report["metrics"]["preflight_data_version"] == details["preflight_data_version"]


def test_seed_tier_c_commit_error_rolls_back_and_records_ledger(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    domains_path = tmp_path / "domains.txt"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_seed_db(db_path)
    _write_domains(domains_path, "freshly.com", "olipop.com")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TRIGGER fail_seed_tier_c_insert
            BEFORE INSERT ON company_files
            WHEN NEW.canonical_key = 'domain:olipop.com'
            BEGIN
                SELECT RAISE(ABORT, 'forced tier c seed failure');
            END
            """
        )
        conn.commit()
    finally:
        conn.close()

    result = _run_script(
        "scripts/seed_tier_c_domains.py",
        "--db",
        str(db_path),
        "--domains",
        str(domains_path),
        "--commit",
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    assert _company_file_count(db_path) == 0
    error_rows = _ledger_rows_for(ledger_path, "seed_tier_c_domains", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "insert_row"
    assert details["rows_inserted_attempted"] == 2
    assert details["transaction_started"] is True
    assert isinstance(details["preflight_data_version"], int)
    assert "forced tier c seed failure" in details["error"]
    report = _read_report(report_path)
    assert report["ok"] is False
    assert report["metrics"]["preflight_data_version"] == details["preflight_data_version"]


def test_seed_tier_c_commit_load_error_records_typed_ledger(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_seed_db(db_path)

    result = _run_script(
        "scripts/seed_tier_c_domains.py",
        "--db",
        str(db_path),
        "--domains",
        str(tmp_path / "missing.txt"),
        "--commit",
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    assert _company_file_count(db_path) == 0
    error_rows = _ledger_rows_for(ledger_path, "seed_tier_c_domains", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "load_domains"
    assert details["transaction_started"] is False
    assert details["rows_inserted_attempted"] == 0
    assert isinstance(details["preflight_data_version"], int)
    assert "missing.txt" in details["error"]
    report = _read_report(report_path)
    assert report["ok"] is False
    assert report["metrics"]["phase"] == "load_domains"


def test_seed_job_posting_domains_cli_is_read_only_and_ledger_silent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_seed_db(db_path)
    _insert_signal(db_path, "domain:freshly.com")
    _insert_signal(db_path, "domain:olipop.com")
    before_company_files = _company_file_count(db_path)

    result = _run_script(
        "scripts/seed_job_posting_domains.py",
        "--db",
        str(db_path),
        "--format",
        "list",
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "freshly.com" in result.stdout
    assert _company_file_count(db_path) == before_company_files
    assert _read_ledger_rows(ledger_path) == []

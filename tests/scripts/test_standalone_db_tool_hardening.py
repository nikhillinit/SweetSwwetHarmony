from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
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


def _create_evidence_key_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_type TEXT NOT NULL,
                source_api TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                company_name TEXT,
                confidence REAL NOT NULL,
                raw_data TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                evidence_key TEXT
            )
            """
        )
        for url in ("https://example.com/a", "https://example.com/b"):
            raw = {"url": url, "_provenance": {"source_url": url}}
            conn.execute(
                """
                INSERT INTO signals (
                    signal_type, source_api, canonical_key, confidence,
                    raw_data, detected_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "news_mention",
                    "news_api",
                    "domain:test.com",
                    0.5,
                    json.dumps(raw),
                    "2026-01-01T00:00:00",
                    "2026-01-01T00:00:00",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _create_company_extraction_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_type TEXT NOT NULL,
                source_api TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                company_name TEXT,
                confidence REAL NOT NULL,
                raw_data TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                evidence_family TEXT,
                canonical_key_v2 TEXT,
                evidence_key TEXT
            )
            """
        )
        for title in ("FreshBowl raises $5M", "Acme raises $10M"):
            raw = {
                "title": title,
                "description": "",
                "url": "https://example.com/article",
            }
            conn.execute(
                """
                INSERT INTO signals (
                    signal_type, source_api, canonical_key, company_name,
                    confidence, raw_data, detected_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "news_mention",
                    "news_api",
                    "rss_hash",
                    None,
                    0.5,
                    json.dumps(raw),
                    "2026-01-01T00:00:00",
                    "2026-01-01T00:00:00",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _create_thesis_provenance_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE thesis_classifications (
                id INTEGER PRIMARY KEY,
                rationale TEXT,
                prompt_version TEXT,
                model TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO thesis_classifications (id, rationale, prompt_version, model)
            VALUES (?, ?, ?, ?)
            """,
            [
                (1, "LLM rationale", None, None),
                (2, "", None, None),
                (3, "already done", "active-v1", "gemini-2.0-flash"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _null_count(db_path: Path, table: str, column: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL OR {column} = ''"
        ).fetchone()[0]
    finally:
        conn.close()


def test_backfill_evidence_keys_bare_cli_defaults_to_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence.db"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_evidence_key_db(db_path)

    result = _run_script(
        "scripts/backfill_evidence_keys.py",
        "--db",
        str(db_path),
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = _read_report(report_path)
    assert report["metrics"]["dry_run"] is True
    assert report["metrics"]["rows_updated"] == 2
    assert isinstance(report["metrics"]["preflight_data_version"], int)
    assert _null_count(db_path, "signals", "evidence_key") == 2
    assert _read_ledger_rows(ledger_path) == []


def test_backfill_evidence_keys_commit_records_success_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence.db"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_evidence_key_db(db_path)

    result = _run_script(
        "scripts/backfill_evidence_keys.py",
        "--db",
        str(db_path),
        "--commit",
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _null_count(db_path, "signals", "evidence_key") == 0
    success_rows = _ledger_rows_for(ledger_path, "backfill_evidence_keys", "success")
    assert success_rows
    details = success_rows[-1]["details"]
    assert details["rows_updated"] == 2
    assert details["duplicate_groups"] == 0
    assert details["dry_run"] is False
    assert isinstance(details["preflight_data_version"], int)


def test_backfill_evidence_keys_commit_lock_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence.db"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_evidence_key_db(db_path)

    lock = DBToolLock(db_path, tool_name="test-holder")
    assert lock.acquire(timeout_seconds=0)
    try:
        result = _run_script(
            "scripts/backfill_evidence_keys.py",
            "--db",
            str(db_path),
            "--commit",
            ledger_path=ledger_path,
        )
    finally:
        lock.release()

    assert result.returncode == 2
    blocked_rows = _ledger_rows_for(ledger_path, "backfill_evidence_keys", "lock_blocked")
    assert blocked_rows
    assert blocked_rows[-1]["details"]["holder"]["tool_name"] == "test-holder"
    assert _null_count(db_path, "signals", "evidence_key") == 2


def test_backfill_evidence_keys_commit_error_rolls_back(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence.db"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_evidence_key_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TRIGGER fail_evidence_key
            BEFORE UPDATE OF evidence_key ON signals
            WHEN OLD.id = 2
            BEGIN
                SELECT RAISE(ABORT, 'forced evidence_key failure');
            END
            """
        )
        conn.commit()
    finally:
        conn.close()

    result = _run_script(
        "scripts/backfill_evidence_keys.py",
        "--db",
        str(db_path),
        "--commit",
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    assert _null_count(db_path, "signals", "evidence_key") == 2
    error_rows = _ledger_rows_for(ledger_path, "backfill_evidence_keys", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "apply_updates"
    assert details["rows_updated_attempted"] == 2
    assert details["error"]


def test_backfill_evidence_keys_preflight_reports_data_version(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence.db"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_evidence_key_db(db_path)

    result = _run_script(
        "scripts/backfill_evidence_keys.py",
        "--db",
        str(db_path),
        "--preflight",
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["clean"] is True
    assert isinstance(report["preflight_data_version"], int)


def test_backfill_company_extraction_bare_cli_defaults_to_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "company.db"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_company_extraction_db(db_path)

    result = _run_script(
        "scripts/backfill_company_extraction.py",
        "--db",
        str(db_path),
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = _read_report(report_path)
    assert report["metrics"]["dry_run"] is True
    assert report["metrics"]["updated"] == 2
    assert isinstance(report["metrics"]["preflight_data_version"], int)
    assert _null_count(db_path, "signals", "company_name") == 2
    assert _read_ledger_rows(ledger_path) == []


def test_backfill_company_extraction_commit_records_success_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "company.db"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_company_extraction_db(db_path)

    result = _run_script(
        "scripts/backfill_company_extraction.py",
        "--db",
        str(db_path),
        "--commit",
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _null_count(db_path, "signals", "company_name") == 0
    success_rows = _ledger_rows_for(ledger_path, "backfill_company_extraction", "success")
    assert success_rows
    details = success_rows[-1]["details"]
    assert details["updated"] == 2
    assert details["dry_run"] is False
    assert isinstance(details["preflight_data_version"], int)


def test_backfill_company_extraction_commit_lock_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "company.db"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_company_extraction_db(db_path)

    lock = DBToolLock(db_path, tool_name="test-holder")
    assert lock.acquire(timeout_seconds=0)
    try:
        result = _run_script(
            "scripts/backfill_company_extraction.py",
            "--db",
            str(db_path),
            "--commit",
            ledger_path=ledger_path,
        )
    finally:
        lock.release()

    assert result.returncode == 2
    blocked_rows = _ledger_rows_for(ledger_path, "backfill_company_extraction", "lock_blocked")
    assert blocked_rows
    assert blocked_rows[-1]["details"]["holder"]["tool_name"] == "test-holder"
    assert _null_count(db_path, "signals", "company_name") == 2


def test_backfill_company_extraction_commit_error_rolls_back(tmp_path: Path) -> None:
    db_path = tmp_path / "company.db"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_company_extraction_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TRIGGER fail_company_name
            BEFORE UPDATE OF company_name ON signals
            WHEN OLD.id = 2
            BEGIN
                SELECT RAISE(ABORT, 'forced company extraction failure');
            END
            """
        )
        conn.commit()
    finally:
        conn.close()

    result = _run_script(
        "scripts/backfill_company_extraction.py",
        "--db",
        str(db_path),
        "--commit",
        ledger_path=ledger_path,
    )

    assert result.returncode == 2
    assert _null_count(db_path, "signals", "company_name") == 2
    error_rows = _ledger_rows_for(ledger_path, "backfill_company_extraction", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "apply_chunk"
    assert details["updated_attempted"] == 2
    assert details["error"]


def test_backfill_company_extraction_preflight_reports_data_version(tmp_path: Path) -> None:
    db_path = tmp_path / "company.db"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_company_extraction_db(db_path)

    result = _run_script(
        "scripts/backfill_company_extraction.py",
        "--db",
        str(db_path),
        "--preflight",
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["total_news_rss_signals"] == 2
    assert isinstance(report["preflight_data_version"], int)


def test_backfill_thesis_provenance_bare_cli_defaults_to_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "thesis.db"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_thesis_provenance_db(db_path)

    result = _run_script(
        "scripts/backfill_thesis_provenance.py",
        "--db",
        str(db_path),
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = _read_report(report_path)
    assert report["metrics"]["dry_run"] is True
    assert report["metrics"]["llm_updated"] == 1
    assert report["metrics"]["kw_updated"] == 1
    assert isinstance(report["metrics"]["preflight_data_version"], int)
    assert _null_count(db_path, "thesis_classifications", "prompt_version") == 2
    assert _read_ledger_rows(ledger_path) == []


def test_backfill_thesis_provenance_commit_records_success_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "thesis.db"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_thesis_provenance_db(db_path)

    result = _run_script(
        "scripts/backfill_thesis_provenance.py",
        "--db",
        str(db_path),
        "--commit",
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _null_count(db_path, "thesis_classifications", "prompt_version") == 0
    success_rows = _ledger_rows_for(ledger_path, "backfill_thesis_provenance", "success")
    assert success_rows
    details = success_rows[-1]["details"]
    assert details["llm_updated"] == 1
    assert details["kw_updated"] == 1
    assert details["remaining_missing"] == 0
    assert isinstance(details["preflight_data_version"], int)


def test_backfill_thesis_provenance_commit_lock_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "thesis.db"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_thesis_provenance_db(db_path)

    lock = DBToolLock(db_path, tool_name="test-holder")
    assert lock.acquire(timeout_seconds=0)
    try:
        result = _run_script(
            "scripts/backfill_thesis_provenance.py",
            "--db",
            str(db_path),
            "--commit",
            ledger_path=ledger_path,
        )
    finally:
        lock.release()

    assert result.returncode == 2
    blocked_rows = _ledger_rows_for(ledger_path, "backfill_thesis_provenance", "lock_blocked")
    assert blocked_rows
    assert blocked_rows[-1]["details"]["holder"]["tool_name"] == "test-holder"
    assert _null_count(db_path, "thesis_classifications", "prompt_version") == 2


def test_backfill_thesis_provenance_commit_error_rolls_back(tmp_path: Path) -> None:
    db_path = tmp_path / "thesis.db"
    ledger_path = tmp_path / "ledger.jsonl"
    _create_thesis_provenance_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TRIGGER fail_keyword_prompt_version
            BEFORE UPDATE OF prompt_version ON thesis_classifications
            WHEN OLD.id = 2
            BEGIN
                SELECT RAISE(ABORT, 'forced thesis provenance failure');
            END
            """
        )
        conn.commit()
    finally:
        conn.close()

    result = _run_script(
        "scripts/backfill_thesis_provenance.py",
        "--db",
        str(db_path),
        "--commit",
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    assert _null_count(db_path, "thesis_classifications", "prompt_version") == 2
    error_rows = _ledger_rows_for(ledger_path, "backfill_thesis_provenance", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "update_keyword"
    assert details["llm_updated_attempted"] == 1
    assert details["kw_updated_attempted"] == 1
    assert details["error"]

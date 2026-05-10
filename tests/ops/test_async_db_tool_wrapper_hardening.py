from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from utils.db_tool_lock import DBToolLock


ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


@dataclass(frozen=True)
class CommandSpec:
    command: str
    tool_name: str
    target_column: str
    create_db: Callable[[Path], None]
    fail_trigger_sql: str


def _create_backfill_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                evidence_family TEXT,
                signal_type TEXT,
                source_api TEXT
            )
            """
        )
        rows = [
            (1, None, "github_spike", "github"),
            (2, None, "hiring_signal", "job_postings"),
            (3, None, "news_mention", "news_api"),
            (4, None, "domain_registration", "domain_whois"),
            (5, None, "product_hunt_launch", "product_hunt"),
        ]
        conn.executemany(
            "INSERT INTO signals (id, evidence_family, signal_type, source_api) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _create_rehydrate_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                canonical_key_v2 TEXT,
                signal_type TEXT,
                source_api TEXT,
                canonical_key TEXT,
                raw_data TEXT
            )
            """
        )
        rows = [
            (
                row_id,
                None,
                "github_spike",
                "github",
                f"domain:legacy-{row_id}.example",
                json.dumps({
                    "company_name": f"Company {row_id}",
                }),
            )
            for row_id in range(1, 6)
        ]
        conn.executemany(
            """
            INSERT INTO signals (
                id, canonical_key_v2, signal_type, source_api, canonical_key, raw_data
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


COMMAND_SPECS = (
    CommandSpec(
        command="backfill-evidence-family",
        tool_name="backfill_evidence_family",
        target_column="evidence_family",
        create_db=_create_backfill_db,
        fail_trigger_sql="""
            CREATE TRIGGER fail_backfill_evidence_family
            BEFORE UPDATE OF evidence_family ON signals
            WHEN OLD.id = 2
            BEGIN
                SELECT RAISE(ABORT, 'forced evidence_family failure');
            END
        """,
    ),
    CommandSpec(
        command="rehydrate-canonical-keys-v2",
        tool_name="rehydrate_canonical_keys_v2",
        target_column="canonical_key_v2",
        create_db=_create_rehydrate_db,
        fail_trigger_sql="""
            CREATE TRIGGER fail_rehydrate_canonical_key_v2
            BEFORE UPDATE OF canonical_key_v2 ON signals
            WHEN OLD.id = 2
            BEGIN
                SELECT RAISE(ABORT, 'forced canonical_key_v2 failure');
            END
        """,
    ),
)


def _run_db_tool(
    spec: CommandSpec,
    db_path: Path,
    report_path: Path,
    ledger_path: Path,
    mode: str,
    *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DB_OPS_LEDGER_PATH"] = str(ledger_path)
    return subprocess.run(
        [
            PYTHON,
            str(ROOT / "run_pipeline.py"),
            spec.command,
            "--db-path",
            str(db_path),
            mode,
            "--chunk-size",
            "2",
            "--report",
            str(report_path),
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=ROOT,
        env=env,
    )


def _run_commit(
    spec: CommandSpec,
    db_path: Path,
    report_path: Path,
    ledger_path: Path,
) -> subprocess.CompletedProcess[str]:
    return _run_db_tool(spec, db_path, report_path, ledger_path, "--commit")


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


def _null_count(db_path: Path, column: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM signals WHERE {column} IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize("spec", COMMAND_SPECS, ids=lambda spec: spec.tool_name)
def test_commit_success_writes_report_ledger_and_mutates(
    tmp_path: Path,
    spec: CommandSpec,
) -> None:
    db_path = tmp_path / f"{spec.tool_name}-success.db"
    report_path = tmp_path / f"{spec.tool_name}-success-report.json"
    ledger_path = tmp_path / f"{spec.tool_name}-success-ledger.jsonl"
    spec.create_db(db_path)

    result = _run_commit(spec, db_path, report_path, ledger_path)

    assert result.returncode == 0, result.stdout + result.stderr
    report = _read_report(report_path)
    assert report["ok"] is True
    assert report["command"] == spec.command
    assert report["errors"] == []
    assert report["metrics"]["rows_scanned"] == 5
    assert report["metrics"]["rows_updated"] == 5
    assert report["metrics"]["dry_run"] is False
    assert _null_count(db_path, spec.target_column) == 0

    success_rows = _ledger_rows_for(ledger_path, spec.tool_name, "success")
    assert success_rows, f"No success ledger row for {spec.tool_name}"
    details = success_rows[-1]["details"]
    assert details["rows_scanned"] == 5
    assert details["rows_updated"] == 5
    assert details["dry_run"] is False


@pytest.mark.parametrize("spec", COMMAND_SPECS, ids=lambda spec: spec.tool_name)
def test_dry_run_ignores_db_tool_lock_and_writes_no_ledger(
    tmp_path: Path,
    spec: CommandSpec,
) -> None:
    db_path = tmp_path / f"{spec.tool_name}-dry-run.db"
    report_path = tmp_path / f"{spec.tool_name}-dry-run-report.json"
    ledger_path = tmp_path / f"{spec.tool_name}-dry-run-ledger.jsonl"
    spec.create_db(db_path)
    before_nulls = _null_count(db_path, spec.target_column)

    lock = DBToolLock(db_path, tool_name="test-holder")
    assert lock.acquire(timeout_seconds=0)
    try:
        result = _run_db_tool(spec, db_path, report_path, ledger_path, "--dry-run")
    finally:
        lock.release()

    assert result.returncode == 0, result.stdout + result.stderr
    report = _read_report(report_path)
    assert report["ok"] is True
    assert report["command"] == spec.command
    assert report["errors"] == []
    assert report["metrics"]["dry_run"] is True
    assert _null_count(db_path, spec.target_column) == before_nulls
    assert _read_ledger_rows(ledger_path) == []


@pytest.mark.parametrize("spec", COMMAND_SPECS, ids=lambda spec: spec.tool_name)
def test_commit_lock_blocked_writes_report_ledger_and_leaves_db(
    tmp_path: Path,
    spec: CommandSpec,
) -> None:
    db_path = tmp_path / f"{spec.tool_name}-locked.db"
    report_path = tmp_path / f"{spec.tool_name}-locked-report.json"
    ledger_path = tmp_path / f"{spec.tool_name}-locked-ledger.jsonl"
    spec.create_db(db_path)
    before_nulls = _null_count(db_path, spec.target_column)

    lock = DBToolLock(db_path, tool_name="test-holder")
    assert lock.acquire(timeout_seconds=0)
    try:
        result = _run_commit(spec, db_path, report_path, ledger_path)
    finally:
        lock.release()

    assert result.returncode == 2, result.stdout + result.stderr
    report = _read_report(report_path)
    assert report["ok"] is False
    assert report["command"] == spec.command
    assert report["errors"]
    assert _null_count(db_path, spec.target_column) == before_nulls

    blocked_rows = _ledger_rows_for(ledger_path, spec.tool_name, "lock_blocked")
    assert blocked_rows, f"No lock_blocked ledger row for {spec.tool_name}"
    holder = blocked_rows[-1]["details"]["holder"]
    assert holder["tool_name"] == "test-holder"


@pytest.mark.parametrize("spec", COMMAND_SPECS, ids=lambda spec: spec.tool_name)
def test_commit_typed_error_writes_report_ledger_and_rolls_back(
    tmp_path: Path,
    spec: CommandSpec,
) -> None:
    db_path = tmp_path / f"{spec.tool_name}-error.db"
    report_path = tmp_path / f"{spec.tool_name}-error-report.json"
    ledger_path = tmp_path / f"{spec.tool_name}-error-ledger.jsonl"
    spec.create_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(spec.fail_trigger_sql)
        conn.commit()
    finally:
        conn.close()
    before_nulls = _null_count(db_path, spec.target_column)

    result = _run_commit(spec, db_path, report_path, ledger_path)

    assert result.returncode == 1, result.stdout + result.stderr
    report = _read_report(report_path)
    assert report["ok"] is False
    assert report["command"] == spec.command
    assert report["errors"]
    assert report["metrics"]["phase"] == "apply_chunk"
    assert report["metrics"]["rows_scanned"] == 2
    assert report["metrics"]["chunk_size"] == 2
    assert report["metrics"]["dry_run"] is False
    assert _null_count(db_path, spec.target_column) == before_nulls

    error_rows = _ledger_rows_for(ledger_path, spec.tool_name, "error")
    assert error_rows, f"No error ledger row for {spec.tool_name}"
    details = error_rows[-1]["details"]
    assert details["phase"] == "apply_chunk"
    assert details["rows_scanned"] == 2
    assert details["rows_updated_attempted"] == 0
    assert details["chunk_size"] == 2
    assert details["dry_run"] is False
    assert details["error"]
    if spec.tool_name == "rehydrate_canonical_keys_v2":
        assert details["sources"] == "all"
        assert details["limit"] is None
    else:
        assert details["rewrite_unknown"] is False
        assert details["source_api"] is None
        assert details["signal_type"] is None

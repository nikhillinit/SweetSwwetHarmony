from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from utils.db_tool_lock import DBToolLock


ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable

_SIGNAL_INSERT = (
    "INSERT INTO signals "
    "(company_name, source_api, signal_type, raw_data, canonical_key, confidence, detected_at, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

_LABEL_INSERT = (
    "INSERT INTO signal_quality_metrics "
    "(signal_id, canonical_key, human_label, label_source, labeled_at, notes) "
    "VALUES (?, ?, ?, 'manual', datetime('now'), ?)"
)


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
        timeout=90,
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


def _table_count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _scalar(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> object:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _exec_sql(db_path: Path, sql: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


async def _create_signalstore_db(db_path: Path) -> None:
    from storage.signal_store import SignalStore

    store = SignalStore(str(db_path))
    await store.initialize()
    await store.close()


async def _create_gc_db(db_path: Path) -> None:
    from storage.signal_store import SignalStore

    store = SignalStore(str(db_path))
    await store.initialize()
    try:
        now = "2026-05-10T00:00:00Z"
        old_archived = "2024-01-01T00:00:00Z"
        recent_archived = "2026-04-01T00:00:00Z"
        await store._db.execute(
            """
            INSERT INTO company_files (
                company_id, company_name, canonical_key, status, source_apis,
                first_seen_at, last_seen_at, archived_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old-archived",
                "Old Archived Co",
                "domain:old.example",
                "archived",
                "[]",
                "2023-01-01T00:00:00Z",
                "2023-06-01T00:00:00Z",
                old_archived,
                "{}",
            ),
        )
        await store._db.execute(
            """
            INSERT INTO company_files (
                company_id, company_name, canonical_key, status, source_apis,
                first_seen_at, last_seen_at, archived_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "recent-archived",
                "Recent Archived Co",
                "domain:recent.example",
                "archived",
                "[]",
                "2026-01-01T00:00:00Z",
                "2026-02-01T00:00:00Z",
                recent_archived,
                "{}",
            ),
        )
        await store._db.execute(
            """
            INSERT INTO company_files (
                company_id, company_name, canonical_key, status, source_apis,
                first_seen_at, last_seen_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "active-thin",
                "Active Thin Co",
                "domain:active.example",
                "thin",
                "[]",
                now,
                now,
                "{}",
            ),
        )
        review_rows = [
            ("old-archived", "rejected"),
            ("old-archived", "pending"),
            ("active-thin", "deferred"),
        ]
        for company_id, status in review_rows:
            await store._db.execute(
                """
                INSERT INTO review_items (
                    company_id, status, evidence_bundle, reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    status,
                    json.dumps({"signal_ids": [], "schema_version": 1}),
                    "fixture",
                    now,
                    now,
                ),
            )
        await store._db.commit()
    finally:
        await store.close()


async def _create_company_files_db(db_path: Path) -> None:
    from storage.signal_store import SignalStore

    store = SignalStore(str(db_path))
    await store.initialize()
    try:
        for source in ("github", "sec_edgar"):
            await store._db.execute(
                _SIGNAL_INSERT,
                (
                    "Shared Co",
                    source,
                    "test",
                    json.dumps({"company_domain": "shared.example", "description": "consumer app"}),
                    "domain:shared.example",
                    0.7,
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                ),
            )
        await store._db.commit()
    finally:
        await store.close()


async def _create_labeled_db(db_path: Path) -> None:
    from storage.signal_store import SignalStore

    store = SignalStore(str(db_path))
    await store.initialize()
    try:
        rows = [
            ("SnackCo", "github", "domain:snack.co", "TP", "consumer cpg snack"),
            ("FitApp", "news_api", "domain:fit.app", "TP", "fitness wellness app"),
            ("CryptoDAO", "github", "domain:crypto.dao", "FP", "crypto governance"),
            ("B2BTool", "hacker_news", "domain:b2b.tool", "FP", "developer analytics"),
        ]
        for name, source, key, label, description in rows:
            cursor = await store._db.execute(
                _SIGNAL_INSERT,
                (
                    name,
                    source,
                    "test",
                    json.dumps({"description": description}),
                    key,
                    0.7,
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                ),
            )
            signal_id = cursor.lastrowid
            await store._db.execute(
                _LABEL_INSERT,
                (signal_id, key, label, f"labeled {label}"),
            )
        await store._db.commit()
    finally:
        await store.close()


def _create_vectorizer(vectorizer_dir: Path, version: str) -> Path:
    from sklearn.feature_extraction.text import TfidfVectorizer
    import joblib

    vectorizer = TfidfVectorizer(max_features=3000, ngram_range=(1, 2), min_df=1)
    vectorizer.fit(
        [
            "healthy snacks organic food consumer",
            "fitness wellness app health tech",
            "enterprise saas b2b developer tools",
        ]
    )
    vectorizer_dir.mkdir(parents=True, exist_ok=True)
    path = vectorizer_dir / f"case_law_{version}.joblib"
    joblib.dump(vectorizer, path)
    return path


def _create_minimal_labeled_delete_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.executescript(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                canonical_key TEXT,
                company_id TEXT,
                company_name TEXT,
                raw_data TEXT DEFAULT '{}',
                source_api TEXT,
                confidence REAL DEFAULT 0.5,
                created_at TEXT DEFAULT '2026-01-01T00:00:00Z'
            );
            CREATE TABLE signal_quality_metrics (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                canonical_key TEXT,
                human_label TEXT,
                notes TEXT
            );
            CREATE TABLE thesis_classifications (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                category TEXT
            );
            """
        )
        rows = [
            (1, "SnackCo", "github", "domain:snack.co", "TP", "consumer cpg snack"),
            (2, "FitApp", "news_api", "domain:fit.app", "TP", "fitness wellness app"),
            (3, "CryptoDAO", "github", "domain:crypto.dao", "FP", "crypto governance"),
            (4, "B2BTool", "hacker_news", "domain:b2b.tool", "FP", "developer analytics"),
        ]
        for signal_id, name, source, key, label, description in rows:
            conn.execute(
                """
                INSERT INTO signals (
                    id, canonical_key, company_id, company_name, raw_data,
                    source_api, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    key,
                    f"cid_{signal_id}",
                    name,
                    json.dumps({"description": description}),
                    source,
                    0.7,
                    "2026-01-01T00:00:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO signal_quality_metrics (
                    signal_id, canonical_key, human_label, notes
                ) VALUES (?, ?, ?, ?)
                """,
                (signal_id, key, label, f"labeled {label}"),
            )
        conn.commit()
    finally:
        conn.close()


def _journal_mode(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    finally:
        conn.close()


def test_gc_thin_files_bare_cli_defaults_to_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_gc_db(db_path))

    result = _run_script(
        "scripts/gc_thin_files.py",
        "--db",
        str(db_path),
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _table_count(db_path, "company_files") == 3
    assert _table_count(db_path, "review_items") == 3
    assert _read_ledger_rows(ledger_path) == []
    report = _read_report(report_path)
    assert report["ok"] is True
    assert report["metrics"]["dry_run"] is True
    assert report["metrics"]["company_files_found"] == 1
    assert report["metrics"]["company_files_deleted"] == 0
    assert isinstance(report["metrics"]["preflight_data_version"], int)


def test_gc_thin_files_apply_records_success_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_gc_db(db_path))

    result = _run_script(
        "scripts/gc_thin_files.py",
        "--db",
        str(db_path),
        "--apply",
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _table_count(db_path, "company_files") == 2
    assert _scalar(
        db_path,
        "SELECT COUNT(*) FROM company_files WHERE company_id = ?",
        ("old-archived",),
    ) == 0
    assert _table_count(db_path, "review_items") == 2
    assert _scalar(
        db_path,
        "SELECT COUNT(*) FROM review_items WHERE company_id = ? AND status = ?",
        ("old-archived", "pending"),
    ) == 1
    assert _table_count(db_path, "audit_log") == 1

    report = _read_report(report_path)
    assert report["ok"] is True
    metrics = report["metrics"]
    assert metrics["dry_run"] is False
    assert metrics["company_files_found"] == 1
    assert metrics["company_files_deleted"] == 1
    assert metrics["orphaned_reviews_cleaned"] == 1
    assert metrics["audit_log_written"] is True
    assert metrics["transaction"] == "committed"
    assert isinstance(metrics["preflight_data_version"], int)

    success_rows = _ledger_rows_for(ledger_path, "gc_thin_files", "success")
    assert success_rows
    details = success_rows[-1]["details"]
    assert details["company_files_found"] == 1
    assert details["company_files_deleted"] == 1
    assert details["orphaned_reviews_cleaned"] == 1
    assert details["audit_log_written"] is True
    assert details["transaction"] == "committed"
    assert isinstance(details["preflight_data_version"], int)


def test_gc_thin_files_apply_lock_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_gc_db(db_path))

    lock = DBToolLock(db_path, tool_name="test-holder")
    assert lock.acquire(timeout_seconds=0)
    try:
        result = _run_script(
            "scripts/gc_thin_files.py",
            "--db",
            str(db_path),
            "--apply",
            "--report",
            str(report_path),
            ledger_path=ledger_path,
        )
    finally:
        lock.release()

    assert result.returncode == 2
    assert _table_count(db_path, "company_files") == 3
    blocked_rows = _ledger_rows_for(ledger_path, "gc_thin_files", "lock_blocked")
    assert blocked_rows
    details = blocked_rows[-1]["details"]
    assert details["holder"]["tool_name"] == "test-holder"
    assert details["apply"] is True
    assert isinstance(details["preflight_data_version"], int)
    report = _read_report(report_path)
    assert report["ok"] is False
    assert report["metrics"]["holder"]["tool_name"] == "test-holder"


def test_gc_thin_files_apply_preflight_error_records_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/gc_thin_files.py",
        "--db",
        str(db_path),
        "--apply",
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    error_rows = _ledger_rows_for(ledger_path, "gc_thin_files", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "preflight_data_version"
    assert details["apply"] is True
    assert details["preflight_data_version"] is None
    assert details["error"]
    report = _read_report(report_path)
    assert report["ok"] is False
    assert report["metrics"]["phase"] == "preflight_data_version"


def test_gc_thin_files_apply_delete_error_rolls_back_current_batch(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_gc_db(db_path))
    _exec_sql(
        db_path,
        """
        CREATE TRIGGER fail_gc_delete
        BEFORE DELETE ON company_files
        WHEN OLD.company_id = 'old-archived'
        BEGIN
            SELECT RAISE(ABORT, 'forced gc delete failure');
        END;
        """,
    )

    result = _run_script(
        "scripts/gc_thin_files.py",
        "--db",
        str(db_path),
        "--apply",
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    assert _table_count(db_path, "company_files") == 3
    assert _table_count(db_path, "review_items") == 3
    assert _table_count(db_path, "audit_log") == 0
    error_rows = _ledger_rows_for(ledger_path, "gc_thin_files", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "delete_company_files"
    assert details["transaction"] == "rolled_back"
    assert details["company_files_found"] == 1
    assert details["company_files_deleted"] == 0
    assert details["audit_log_written"] is False
    assert isinstance(details["preflight_data_version"], int)
    assert "forced gc delete failure" in details["error"]
    report = _read_report(report_path)
    assert report["ok"] is False
    assert report["metrics"]["transaction"] == "rolled_back"


def test_gc_thin_files_apply_audit_failure_records_partial_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_gc_db(db_path))
    _exec_sql(
        db_path,
        """
        CREATE TRIGGER fail_gc_audit
        BEFORE INSERT ON audit_log
        WHEN NEW.action_type = 'gc_thin_files'
        BEGIN
            SELECT RAISE(ABORT, 'forced audit failure');
        END;
        """,
    )

    result = _run_script(
        "scripts/gc_thin_files.py",
        "--db",
        str(db_path),
        "--apply",
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    assert _table_count(db_path, "company_files") == 2
    assert _table_count(db_path, "review_items") == 2
    assert _table_count(db_path, "audit_log") == 0
    error_rows = _ledger_rows_for(ledger_path, "gc_thin_files", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "write_audit_log"
    assert details["transaction"] == "partial_committed"
    assert details["company_files_found"] == 1
    assert details["company_files_deleted"] == 1
    assert details["orphaned_reviews_cleaned"] == 1
    assert details["audit_log_written"] is False
    assert isinstance(details["preflight_data_version"], int)
    assert "forced audit failure" in details["error"]
    report = _read_report(report_path)
    assert report["ok"] is False
    assert report["metrics"]["transaction"] == "partial_committed"


def test_backfill_company_files_bare_cli_defaults_to_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_company_files_db(db_path))

    result = _run_script(
        "scripts/backfill_company_files.py",
        "--db",
        str(db_path),
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _table_count(db_path, "company_files") == 0
    assert _read_ledger_rows(ledger_path) == []
    report = _read_report(report_path)
    assert report["metrics"]["dry_run"] is True
    assert report["metrics"]["multi_source_groups"] == 1
    assert isinstance(report["metrics"]["preflight_data_version"], int)


def test_backfill_company_files_commit_records_success_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_company_files_db(db_path))

    result = _run_script(
        "scripts/backfill_company_files.py",
        "--db",
        str(db_path),
        "--commit",
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _table_count(db_path, "company_files") == 1
    report = _read_report(report_path)
    assert report["metrics"]["dry_run"] is False
    assert report["metrics"]["created"] == 1
    assert report["metrics"]["updated"] == 1
    assert report["metrics"]["skipped"] == 0
    assert report["metrics"]["transaction"] == "committed"
    success_rows = _ledger_rows_for(ledger_path, "backfill_company_files", "success")
    assert success_rows
    details = success_rows[-1]["details"]
    assert details["multi_source_groups"] == 1
    assert details["partial_success"] is False
    assert isinstance(details["preflight_data_version"], int)


def test_backfill_company_files_commit_lock_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_company_files_db(db_path))

    lock = DBToolLock(db_path, tool_name="test-holder")
    assert lock.acquire(timeout_seconds=0)
    try:
        result = _run_script(
            "scripts/backfill_company_files.py",
            "--db",
            str(db_path),
            "--commit",
            ledger_path=ledger_path,
        )
    finally:
        lock.release()

    assert result.returncode == 2
    assert _table_count(db_path, "company_files") == 0
    blocked_rows = _ledger_rows_for(ledger_path, "backfill_company_files", "lock_blocked")
    assert blocked_rows
    assert blocked_rows[-1]["details"]["holder"]["tool_name"] == "test-holder"


def test_backfill_company_files_commit_preflight_error_records_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"
    ledger_path = tmp_path / "ledger.jsonl"

    result = _run_script(
        "scripts/backfill_company_files.py",
        "--db",
        str(db_path),
        "--commit",
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    error_rows = _ledger_rows_for(ledger_path, "backfill_company_files", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "preflight_data_version"
    assert details["commit"] is True
    assert details["error"]


def test_backfill_company_files_commit_error_rolls_back(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_company_files_db(db_path))
    _exec_sql(
        db_path,
        """
        CREATE TRIGGER fail_company_file_second_source
        BEFORE UPDATE ON company_files
        BEGIN
            SELECT RAISE(ABORT, 'forced company file failure');
        END;
        """,
    )

    result = _run_script(
        "scripts/backfill_company_files.py",
        "--db",
        str(db_path),
        "--commit",
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    assert _table_count(db_path, "company_files") == 0
    error_rows = _ledger_rows_for(ledger_path, "backfill_company_files", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "upsert_company_file"
    assert details["transaction"] == "rolled_back"
    assert details["partial_success"] is False
    assert details["error"]


def test_build_case_law_corpus_bare_cli_is_non_mutating(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    vectorizer_dir = tmp_path / "vectorizers"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_labeled_db(db_path))

    result = _run_script(
        "scripts/build_case_law_corpus.py",
        "--db",
        str(db_path),
        "--version",
        "vtest",
        "--vectorizer-dir",
        str(vectorizer_dir),
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _table_count(db_path, "precedents") == 0
    assert not (vectorizer_dir / "case_law_vtest.joblib").exists()
    assert not (vectorizer_dir / "case_law_vtest_meta.json").exists()
    assert _read_ledger_rows(ledger_path) == []
    report = _read_report(report_path)
    assert report["metrics"]["dry_run"] is True
    assert report["metrics"]["corpus_size"] == 4
    assert isinstance(report["metrics"]["preflight_data_version"], int)


def test_build_case_law_corpus_explicit_read_only_modes_are_ledger_silent(
    tmp_path: Path,
) -> None:
    for mode in ("--dry-run", "--calibrate", "--check-only"):
        db_path = tmp_path / f"{mode.removeprefix('--')}.db"
        vectorizer_dir = tmp_path / f"{mode.removeprefix('--')}-vectorizers"
        ledger_path = tmp_path / f"{mode.removeprefix('--')}.jsonl"
        asyncio.run(_create_labeled_db(db_path))

        result = _run_script(
            "scripts/build_case_law_corpus.py",
            "--db",
            str(db_path),
            "--version",
            "vtest",
            "--vectorizer-dir",
            str(vectorizer_dir),
            mode,
            ledger_path=ledger_path,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert _table_count(db_path, "precedents") == 0
        assert not (vectorizer_dir / "case_law_vtest.joblib").exists()
        assert not (vectorizer_dir / "case_law_vtest_meta.json").exists()
        assert _read_ledger_rows(ledger_path) == []


@pytest.mark.parametrize("mode_args", [(), ("--dry-run",), ("--calibrate",), ("--check-only",)])
def test_build_case_law_corpus_read_only_modes_do_not_initialize_signalstore(
    tmp_path: Path,
    mode_args: tuple[str, ...],
) -> None:
    db_path = tmp_path / f"delete-mode-{len(mode_args)}.db"
    vectorizer_dir = tmp_path / f"vectorizers-{len(mode_args)}"
    ledger_path = tmp_path / f"ledger-{len(mode_args)}.jsonl"
    _create_minimal_labeled_delete_db(db_path)
    assert _journal_mode(db_path) == "delete"

    result = _run_script(
        "scripts/build_case_law_corpus.py",
        "--db",
        str(db_path),
        "--version",
        "vtest",
        "--vectorizer-dir",
        str(vectorizer_dir),
        *mode_args,
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _journal_mode(db_path) == "delete"
    assert not db_path.with_suffix(".db-wal").exists()
    assert _read_ledger_rows(ledger_path) == []


def test_build_case_law_corpus_commit_records_success_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    vectorizer_dir = tmp_path / "vectorizers"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_labeled_db(db_path))

    result = _run_script(
        "scripts/build_case_law_corpus.py",
        "--db",
        str(db_path),
        "--version",
        "vtest",
        "--vectorizer-dir",
        str(vectorizer_dir),
        "--commit",
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _table_count(db_path, "precedents") == 4
    assert (vectorizer_dir / "case_law_vtest.joblib").exists()
    assert (vectorizer_dir / "case_law_vtest_meta.json").exists()
    report = _read_report(report_path)
    assert report["metrics"]["artifacts_finalized"] is True
    assert report["metrics"]["vectorizer_path"].endswith("case_law_vtest.joblib")
    success_rows = _ledger_rows_for(ledger_path, "build_case_law_corpus", "success")
    assert success_rows
    details = success_rows[-1]["details"]
    assert details["version"] == "vtest"
    assert details["corpus_size"] == 4
    assert details["label_counts"] == {"TP": 2, "FP": 2}
    assert details["db_transaction"] == "committed"
    assert isinstance(details["preflight_data_version"], int)


def test_build_case_law_corpus_commit_lock_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    vectorizer_dir = tmp_path / "vectorizers"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_labeled_db(db_path))

    lock = DBToolLock(db_path, tool_name="test-holder")
    assert lock.acquire(timeout_seconds=0)
    try:
        result = _run_script(
            "scripts/build_case_law_corpus.py",
            "--db",
            str(db_path),
            "--version",
            "vtest",
            "--vectorizer-dir",
            str(vectorizer_dir),
            "--commit",
            ledger_path=ledger_path,
        )
    finally:
        lock.release()

    assert result.returncode == 2
    assert _table_count(db_path, "precedents") == 0
    assert not (vectorizer_dir / "case_law_vtest.joblib").exists()
    blocked_rows = _ledger_rows_for(ledger_path, "build_case_law_corpus", "lock_blocked")
    assert blocked_rows
    assert blocked_rows[-1]["details"]["holder"]["tool_name"] == "test-holder"


def test_build_case_law_corpus_commit_error_rolls_back_and_cleans_staged_artifacts(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    vectorizer_dir = tmp_path / "vectorizers"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_labeled_db(db_path))
    _exec_sql(
        db_path,
        """
        CREATE TRIGGER fail_precedent_second_row
        BEFORE INSERT ON precedents
        WHEN NEW.signal_id = 2
        BEGIN
            SELECT RAISE(ABORT, 'forced precedent failure');
        END;
        """,
    )

    result = _run_script(
        "scripts/build_case_law_corpus.py",
        "--db",
        str(db_path),
        "--version",
        "vtest",
        "--vectorizer-dir",
        str(vectorizer_dir),
        "--commit",
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    assert _table_count(db_path, "precedents") == 0
    assert not (vectorizer_dir / "case_law_vtest.joblib").exists()
    assert not (vectorizer_dir / "case_law_vtest_meta.json").exists()
    error_rows = _ledger_rows_for(ledger_path, "build_case_law_corpus", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "insert_precedents"
    assert details["db_transaction"] == "rolled_back"
    assert details["artifact_cleanup"]["staged"]


def test_build_case_law_corpus_commit_refuses_existing_target_artifacts(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "signals.db"
    vectorizer_dir = tmp_path / "vectorizers"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_labeled_db(db_path))
    vectorizer_dir.mkdir()
    vectorizer_path = vectorizer_dir / "case_law_vtest.joblib"
    metadata_path = vectorizer_dir / "case_law_vtest_meta.json"
    vectorizer_path.write_text("existing-vectorizer", encoding="utf-8")
    metadata_path.write_text("existing-metadata", encoding="utf-8")

    result = _run_script(
        "scripts/build_case_law_corpus.py",
        "--db",
        str(db_path),
        "--version",
        "vtest",
        "--vectorizer-dir",
        str(vectorizer_dir),
        "--commit",
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    assert _table_count(db_path, "precedents") == 0
    assert vectorizer_path.read_text(encoding="utf-8") == "existing-vectorizer"
    assert metadata_path.read_text(encoding="utf-8") == "existing-metadata"
    error_rows = _ledger_rows_for(ledger_path, "build_case_law_corpus", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "preflight_artifacts"
    assert details["db_transaction"] == "not_started"
    assert details["artifact_finalization_status"][str(vectorizer_path)] == "preexisting"
    assert details["artifact_finalization_status"][str(metadata_path)] == "preexisting"
    assert "Target corpus artifact already exists" in details["error"]


def test_corpus_artifact_cleanup_tracks_failed_replace_attempt(
    tmp_path: Path,
) -> None:
    vectorizer_dir = tmp_path / "vectorizers"
    vectorizer_dir.mkdir()
    staged_vectorizer = vectorizer_dir / ".case_law_vtest.joblib.tmp"
    staged_metadata = vectorizer_dir / ".case_law_vtest_meta.json.tmp"
    final_vectorizer = vectorizer_dir / "case_law_vtest.joblib"
    final_metadata = vectorizer_dir / "case_law_vtest_meta.json"
    staged_vectorizer.write_text("new-vectorizer", encoding="utf-8")
    staged_metadata.write_text("new-metadata", encoding="utf-8")
    final_metadata.mkdir()
    status = {
        str(final_vectorizer): "not_attempted",
        str(final_metadata): "not_attempted",
    }

    from scripts.build_case_law_corpus import _cleanup_artifacts, _replace_artifact

    _replace_artifact(staged_vectorizer, final_vectorizer, status)
    with pytest.raises(OSError):
        _replace_artifact(staged_metadata, final_metadata, status)

    cleanup = _cleanup_artifacts(
        staged_paths=[staged_vectorizer, staged_metadata],
        finalized_paths=[(final_vectorizer, False), (final_metadata, False)],
        artifact_finalization_status=status,
    )

    assert not final_vectorizer.exists()
    assert final_metadata.is_dir()
    assert status[str(final_vectorizer)] == "replaced"
    assert status[str(final_metadata)] == "replace_attempted"
    assert (
        cleanup["finalized"][str(final_vectorizer)]
        == "removed_new_finalized_path"
    )
    assert cleanup["finalized"][str(final_metadata)] == "replace_attempted"


def test_build_exemplar_library_bare_cli_is_non_mutating(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    vectorizer_dir = tmp_path / "vectorizers"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_labeled_db(db_path))
    _create_vectorizer(vectorizer_dir, "vtest")

    result = _run_script(
        "scripts/build_exemplar_library.py",
        "--db",
        str(db_path),
        "--version",
        "vtest",
        "--vectorizer-dir",
        str(vectorizer_dir),
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _table_count(db_path, "thesis_exemplars") == 0
    assert _read_ledger_rows(ledger_path) == []
    report = _read_report(report_path)
    assert report["metrics"]["dry_run"] is True
    assert report["metrics"]["exemplar_count"] == 2
    assert isinstance(report["metrics"]["preflight_data_version"], int)


def test_build_exemplar_library_explicit_dry_run_is_ledger_silent(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    vectorizer_dir = tmp_path / "vectorizers"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_labeled_db(db_path))
    _create_vectorizer(vectorizer_dir, "vtest")

    result = _run_script(
        "scripts/build_exemplar_library.py",
        "--db",
        str(db_path),
        "--version",
        "vtest",
        "--vectorizer-dir",
        str(vectorizer_dir),
        "--dry-run",
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _table_count(db_path, "thesis_exemplars") == 0
    assert _read_ledger_rows(ledger_path) == []


@pytest.mark.parametrize("mode_args", [(), ("--dry-run",)])
def test_build_exemplar_library_read_only_modes_do_not_initialize_signalstore(
    tmp_path: Path,
    mode_args: tuple[str, ...],
) -> None:
    db_path = tmp_path / f"delete-mode-exemplar-{len(mode_args)}.db"
    vectorizer_dir = tmp_path / f"vectorizers-exemplar-{len(mode_args)}"
    ledger_path = tmp_path / f"ledger-exemplar-{len(mode_args)}.jsonl"
    _create_minimal_labeled_delete_db(db_path)
    _create_vectorizer(vectorizer_dir, "vtest")
    assert _journal_mode(db_path) == "delete"

    result = _run_script(
        "scripts/build_exemplar_library.py",
        "--db",
        str(db_path),
        "--version",
        "vtest",
        "--vectorizer-dir",
        str(vectorizer_dir),
        *mode_args,
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _journal_mode(db_path) == "delete"
    assert not db_path.with_suffix(".db-wal").exists()
    assert _read_ledger_rows(ledger_path) == []


def test_build_exemplar_library_commit_records_success_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    vectorizer_dir = tmp_path / "vectorizers"
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_labeled_db(db_path))
    _create_vectorizer(vectorizer_dir, "vtest")

    result = _run_script(
        "scripts/build_exemplar_library.py",
        "--db",
        str(db_path),
        "--version",
        "vtest",
        "--vectorizer-dir",
        str(vectorizer_dir),
        "--commit",
        "--report",
        str(report_path),
        ledger_path=ledger_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _table_count(db_path, "thesis_exemplars") == 2
    report = _read_report(report_path)
    assert report["metrics"]["db_transaction"] == "committed"
    assert report["metrics"]["exemplar_count"] == 2
    success_rows = _ledger_rows_for(ledger_path, "build_exemplar_library", "success")
    assert success_rows
    details = success_rows[-1]["details"]
    assert details["version"] == "vtest"
    assert details["exemplar_count"] == 2
    assert details["categories"] == {"general": 2}
    assert isinstance(details["preflight_data_version"], int)


def test_build_exemplar_library_commit_lock_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    vectorizer_dir = tmp_path / "vectorizers"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_labeled_db(db_path))
    _create_vectorizer(vectorizer_dir, "vtest")

    lock = DBToolLock(db_path, tool_name="test-holder")
    assert lock.acquire(timeout_seconds=0)
    try:
        result = _run_script(
            "scripts/build_exemplar_library.py",
            "--db",
            str(db_path),
            "--version",
            "vtest",
            "--vectorizer-dir",
            str(vectorizer_dir),
            "--commit",
            ledger_path=ledger_path,
        )
    finally:
        lock.release()

    assert result.returncode == 2
    assert _table_count(db_path, "thesis_exemplars") == 0
    blocked_rows = _ledger_rows_for(ledger_path, "build_exemplar_library", "lock_blocked")
    assert blocked_rows
    assert blocked_rows[-1]["details"]["holder"]["tool_name"] == "test-holder"


def test_build_exemplar_library_missing_vectorizer_records_load_error(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    vectorizer_dir = tmp_path / "missing-vectorizers"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_labeled_db(db_path))

    result = _run_script(
        "scripts/build_exemplar_library.py",
        "--db",
        str(db_path),
        "--version",
        "vtest",
        "--vectorizer-dir",
        str(vectorizer_dir),
        "--commit",
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    assert _table_count(db_path, "thesis_exemplars") == 0
    error_rows = _ledger_rows_for(ledger_path, "build_exemplar_library", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "load_vectorizer"
    assert details["db_transaction"] == "not_started"


def test_build_exemplar_library_commit_error_rolls_back(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    vectorizer_dir = tmp_path / "vectorizers"
    ledger_path = tmp_path / "ledger.jsonl"
    asyncio.run(_create_labeled_db(db_path))
    _create_vectorizer(vectorizer_dir, "vtest")
    _exec_sql(
        db_path,
        """
        CREATE TRIGGER fail_exemplar_fitapp
        BEFORE INSERT ON thesis_exemplars
        WHEN NEW.company_name = 'FitApp'
        BEGIN
            SELECT RAISE(ABORT, 'forced exemplar failure');
        END;
        """,
    )

    result = _run_script(
        "scripts/build_exemplar_library.py",
        "--db",
        str(db_path),
        "--version",
        "vtest",
        "--vectorizer-dir",
        str(vectorizer_dir),
        "--commit",
        ledger_path=ledger_path,
    )

    assert result.returncode == 1
    assert _table_count(db_path, "thesis_exemplars") == 0
    error_rows = _ledger_rows_for(ledger_path, "build_exemplar_library", "error")
    assert error_rows
    details = error_rows[-1]["details"]
    assert details["phase"] == "write_exemplars"
    assert details["db_transaction"] == "rolled_back"
    assert details["exemplar_count"] == 2

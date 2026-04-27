from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.inspect_live_schema import (
    inspect_database,
    load_contract,
    main,
    render_markdown_report,
)


def _create_full_schema(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                canonical_key TEXT,
                source_api TEXT,
                signal_type TEXT,
                detected_at TEXT,
                confidence REAL
            );
            CREATE TABLE quality_feedback (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                label TEXT
            );
            CREATE TABLE signal_quality_metrics (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                canonical_key TEXT,
                human_label TEXT,
                label_source TEXT,
                labeled_at TEXT,
                status_event_id INTEGER
            );
            CREATE TABLE signal_processing (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                status TEXT
            );
            CREATE TABLE thesis_ml_predictions (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                ml_enablement TEXT
            );
            CREATE TABLE notion_status_events (
                id INTEGER PRIMARY KEY,
                canonical_key TEXT,
                old_status TEXT,
                new_status TEXT,
                observed_at TEXT
            );
            """
        )
        con.commit()
    finally:
        con.close()


def _write_minimal_contract(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "required_tables": {
                    "signals": {
                        "required_columns": ["id", "canonical_key"]
                    },
                    "quality_feedback": {
                        "required_columns": ["signal_id", "label"]
                    },
                },
                "forbidden_references": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_load_contract_parses_required_tables(tmp_path):
    contract_path = _write_minimal_contract(tmp_path / "contract.json")
    contract = load_contract(contract_path)
    assert contract["version"] == 1
    assert "signals" in contract["required_tables"]
    assert contract["required_tables"]["signals"]["required_columns"] == ["id", "canonical_key"]


def test_inspect_database_returns_ok_when_all_tables_present(tmp_path):
    db_path = tmp_path / "signals.db"
    _create_full_schema(db_path)
    contract = {
        "version": 1,
        "required_tables": {
            "signals": {"required_columns": ["id", "canonical_key", "confidence"]},
            "quality_feedback": {"required_columns": ["signal_id", "label"]},
            "notion_status_events": {
                "required_columns": ["id", "canonical_key", "old_status"]
            },
        },
        "forbidden_references": [],
    }

    report = inspect_database(db_path, contract)
    assert report["ok"] is True
    assert report["missing_tables"] == []
    assert report["missing_columns"] == {}
    assert report["tables"]["signals"]["status"] == "ok"
    assert report["tables"]["notion_status_events"]["status"] == "ok"


def test_inspect_database_flags_missing_table(tmp_path):
    db_path = tmp_path / "signals.db"
    _create_full_schema(db_path)
    contract = {
        "version": 1,
        "required_tables": {
            "signals": {"required_columns": ["id"]},
            "router_decisions": {"required_columns": ["id"]},
        },
        "forbidden_references": [],
    }

    report = inspect_database(db_path, contract)
    assert report["ok"] is False
    assert "router_decisions" in report["missing_tables"]
    assert report["tables"]["router_decisions"]["status"] == "missing_table"


def test_inspect_database_flags_missing_column(tmp_path):
    db_path = tmp_path / "signals.db"
    _create_full_schema(db_path)
    contract = {
        "version": 1,
        "required_tables": {
            "notion_status_events": {
                "required_columns": ["id", "canonical_key", "signal_id"]
            }
        },
        "forbidden_references": [],
    }

    report = inspect_database(db_path, contract)
    assert report["ok"] is False
    assert "notion_status_events" in report["missing_columns"]
    assert "signal_id" in report["missing_columns"]["notion_status_events"]
    assert report["tables"]["notion_status_events"]["status"] == "missing_columns"


def test_inspect_database_does_not_modify_database(tmp_path):
    db_path = tmp_path / "signals.db"
    _create_full_schema(db_path)
    contract = {
        "version": 1,
        "required_tables": {"signals": {"required_columns": ["id"]}},
        "forbidden_references": [],
    }

    before_size = db_path.stat().st_size
    before_mtime = db_path.stat().st_mtime_ns
    inspect_database(db_path, contract)
    assert db_path.stat().st_size == before_size
    assert db_path.stat().st_mtime_ns == before_mtime


def test_inspect_database_returns_failed_open_when_db_missing(tmp_path):
    contract = {
        "version": 1,
        "required_tables": {"signals": {"required_columns": ["id"]}},
        "forbidden_references": [],
    }
    report = inspect_database(tmp_path / "does_not_exist.db", contract)
    assert report["ok"] is False
    assert report["error"] == "database_not_found"


def test_render_markdown_report_includes_status_lines(tmp_path):
    db_path = tmp_path / "signals.db"
    _create_full_schema(db_path)
    contract = {
        "version": 1,
        "required_tables": {
            "signals": {"required_columns": ["id"]},
            "router_decisions": {"required_columns": ["id"]},
        },
        "forbidden_references": [],
    }
    report = inspect_database(db_path, contract)
    md = render_markdown_report(report, db_path=db_path, contract_path=tmp_path / "contract.json")
    assert "signals" in md
    assert "router_decisions" in md
    assert "OK" in md or "ok" in md.lower()
    assert "missing_table" in md.lower() or "MISSING" in md


def test_main_exits_zero_when_contract_satisfied(tmp_path, capsys):
    db_path = tmp_path / "signals.db"
    _create_full_schema(db_path)
    contract_path = tmp_path / "contract.json"
    _write_minimal_contract(contract_path)
    out_dir = tmp_path / "wave6"

    exit_code = main(
        [
            "--db",
            str(db_path),
            "--contract",
            str(contract_path),
            "--out-dir",
            str(out_dir),
        ]
    )
    assert exit_code == 0
    assert (out_dir / "live_schema_report.json").exists()
    assert (out_dir / "live_schema_report.md").exists()


def test_main_exits_nonzero_when_required_table_missing(tmp_path):
    db_path = tmp_path / "signals.db"
    sqlite3.connect(db_path).close()  # empty DB
    contract_path = tmp_path / "contract.json"
    _write_minimal_contract(contract_path)
    out_dir = tmp_path / "wave6"

    exit_code = main(
        [
            "--db",
            str(db_path),
            "--contract",
            str(contract_path),
            "--out-dir",
            str(out_dir),
        ]
    )
    assert exit_code != 0
    payload = json.loads((out_dir / "live_schema_report.json").read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert "signals" in payload["missing_tables"]


def test_main_writes_json_and_markdown_reports(tmp_path):
    db_path = tmp_path / "signals.db"
    _create_full_schema(db_path)
    contract_path = tmp_path / "contract.json"
    _write_minimal_contract(contract_path)
    out_dir = tmp_path / "wave6"

    main(
        [
            "--db",
            str(db_path),
            "--contract",
            str(contract_path),
            "--out-dir",
            str(out_dir),
        ]
    )
    json_payload = json.loads((out_dir / "live_schema_report.json").read_text(encoding="utf-8"))
    md_payload = (out_dir / "live_schema_report.md").read_text(encoding="utf-8")

    assert json_payload["contract_version"] == 1
    assert "generated_at" in json_payload
    assert "signals" in md_payload

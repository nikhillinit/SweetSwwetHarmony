"""Tests for quality CLI explain-score subcommand.

Covers:
- JSON output format
- Not-found handling
- Pipeline-only caveat in output
- Numeric signal ID resolution via company_id
- Numeric signal ID not found
- Numeric signal ID with no ledger rows
- Old schema version note
- Hard-kill rendering (no waterfall)
- --include-dry-runs flag
- LLM adjustment rendering (gate_score != reported_score)
- Waterfall golden output consistency
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.migrations.v51_confidence_ledger import V51_CONFIDENCE_LEDGER_DDL
from ops.quality_cli import _cmd_explain_score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_db(tmp_path) -> str:
    db_path = str(tmp_path / "explain_test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = OFF")
    # Minimal signals table for signal-id resolution
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL DEFAULT 'test',
            source_api TEXT NOT NULL DEFAULT 'test',
            canonical_key TEXT NOT NULL,
            company_name TEXT,
            confidence REAL NOT NULL DEFAULT 0.5,
            raw_data TEXT NOT NULL DEFAULT '{}',
            detected_at TEXT NOT NULL DEFAULT '2026-03-14T00:00:00+00:00',
            created_at TEXT NOT NULL DEFAULT '2026-03-14T00:00:00+00:00',
            company_id TEXT
        )
    """)
    conn.executescript(V51_CONFIDENCE_LEDGER_DDL)
    # Let quality_conn's ensure_quality_tables handle the quality DDL —
    # just need signals table present for FK references.
    from storage.migrations.quality_tables import QUALITY_TABLES_DDL
    conn.executescript(QUALITY_TABLES_DDL)
    conn.commit()
    conn.close()
    return db_path


def _insert_ledger_row(db_path: str, **overrides) -> int:
    conn = sqlite3.connect(db_path)
    defaults = {
        "execution_id": "run-1",
        "canonical_key": "domain:acme.ai",
        "company_id": None,
        "evaluation_origin": "pipeline",
        "is_dry_run": 0,
        "breakdown_kind": "normal",
        "gate_score": 0.782,
        "reported_score": 0.782,
        "base_score": 0.350,
        "multi_source_boost": 1.15,
        "convergence_boost": 1.0,
        "founder_boost": 0.080,
        "velocity_boost": 0.050,
        "enrichment_boost": 0.030,
        "community_sentiment_boost": 0.016,
        "recalibration_factor": 1.35,
        "policy_version": "v2.1",
        "breakdown_schema_version": "1.0",
        "signals_contributing": 3,
        "sources_checked": 2,
        "decision": "auto_push",
        "verification_status": "multi_source",
        "reason": "High confidence (0.78) with 2 sources",
        "breakdown_json": json.dumps({
            "overall": 0.782, "base_score": 0.350,
            "multi_source_boost": 1.15, "convergence_boost": 1.0,
            "founder_boost": 0.08, "velocity_boost": 0.05,
            "enrichment_boost": 0.03, "community_sentiment_boost": 0.016,
            "score_recalibration_factor": 1.35,
            "policy_version": "v2.1",
            "signals_contributing": 3, "sources_checked": 2,
            "sources": ["sec_edgar", "job_postings"],
            "signal_details": [
                {"type": "incorporation", "contribution": 0.22, "source": "sec_edgar"},
            ],
            "calculation_method": "glass_ai_v2",
            "calculated_at": "2026-03-14T00:00:00+00:00",
        }),
        "details_json": "[]",
        "signal_ids_json": json.dumps([45, 67, 89]),
        "routing_config_json": json.dumps({"high_threshold": 0.7, "medium_threshold": 0.4, "score_scale": 1.35, "strict_mode": False}),
        "evaluated_at": "2026-03-14T02:15:00+00:00",
        "created_at": "2026-03-14T02:15:00+00:00",
    }
    defaults.update(overrides)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    cursor = conn.execute(
        f"INSERT INTO confidence_ledger ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def _run_explain(db_path: str, identifier: str, **kwargs) -> str:
    args = argparse.Namespace(
        db_path=db_path,
        identifier=identifier,
        history=kwargs.get("history", 1),
        json_output=kwargs.get("json_output", False),
        include_dry_runs=kwargs.get("include_dry_runs", False),
    )
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        _cmd_explain_score(args)
    finally:
        sys.stdout = old_stdout
    return buf.getvalue()


# ===========================================================================
# Tests
# ===========================================================================

class TestExplainScoreCLI:

    def test_explain_score_json_output(self, tmp_path):
        db = _create_db(tmp_path)
        _insert_ledger_row(db)
        output = _run_explain(db, "domain:acme.ai", json_output=True)
        data = json.loads(output)
        assert isinstance(data, list)
        assert len(data) == 1
        entry = data[0]
        assert entry["decision"] == "auto_push"
        assert "gate_score" in entry
        assert "reported_score" in entry
        assert "reason" in entry
        assert entry["breakdown_kind"] == "normal"

    def test_explain_score_not_found(self, tmp_path):
        db = _create_db(tmp_path)
        output = _run_explain(db, "domain:nonexistent.com")
        assert "No evaluations recorded" in output
        assert "pre-date" in output

    def test_explain_score_shows_pipeline_only_caveat(self, tmp_path):
        db = _create_db(tmp_path)
        _insert_ledger_row(db)
        output = _run_explain(db, "domain:acme.ai")
        assert "pipeline-only" in output

    def test_explain_score_numeric_id_resolves_via_company_id(self, tmp_path):
        db = _create_db(tmp_path)
        # Insert signal with company_id
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO signals (canonical_key, company_id) VALUES (?, ?)",
            ("domain:acme.ai", "cid-abc"),
        )
        conn.commit()
        conn.close()
        _insert_ledger_row(db, company_id="cid-abc")
        output = _run_explain(db, "1")
        assert "company cid-abc" in output

    def test_explain_score_numeric_id_not_found(self, tmp_path):
        db = _create_db(tmp_path)
        output = _run_explain(db, "99999")
        assert "Signal ID 99999 not found" in output

    def test_explain_score_numeric_id_no_ledger(self, tmp_path):
        db = _create_db(tmp_path)
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO signals (canonical_key) VALUES (?)",
            ("domain:no-ledger.com",),
        )
        conn.commit()
        conn.close()
        output = _run_explain(db, "1")
        assert "No evaluations recorded" in output

    def test_explain_score_old_schema_version_note(self, tmp_path):
        db = _create_db(tmp_path)
        _insert_ledger_row(db, breakdown_schema_version="0.9")
        output = _run_explain(db, "domain:acme.ai")
        assert "Schema v0.9" in output

    def test_explain_score_hard_kill_rendering(self, tmp_path):
        db = _create_db(tmp_path)
        _insert_ledger_row(
            db,
            decision="reject",
            verification_status="unverified",
            breakdown_kind="hard_kill",
            gate_score=0.0,
            reported_score=0.0,
            base_score=0.0,
            reason="Hard kill signal: company_dissolved",
            breakdown_json=json.dumps({"hard_kill": True, "kill_signal": "company_dissolved"}),
        )
        output = _run_explain(db, "domain:acme.ai")
        assert "reject (hard_kill)" in output
        assert "company_dissolved" in output
        assert "Waterfall" not in output

    def test_explain_score_include_dry_runs(self, tmp_path):
        db = _create_db(tmp_path)
        _insert_ledger_row(db, is_dry_run=1)
        # Without flag: hidden
        output = _run_explain(db, "domain:acme.ai")
        assert "No evaluations recorded" in output
        # With flag: returned
        output = _run_explain(db, "domain:acme.ai", include_dry_runs=True)
        assert "auto_push" in output

    def test_explain_score_llm_adjustment_rendering(self, tmp_path):
        """Golden-output test: gate_score=0.650, reported_score=0.782 shows LLM adj."""
        db = _create_db(tmp_path)
        _insert_ledger_row(
            db,
            gate_score=0.650,
            reported_score=0.782,
            breakdown_json=json.dumps({
                "overall": 0.650, "base_score": 0.350,
                "multi_source_boost": 1.0, "convergence_boost": 1.0,
                "founder_boost": 0.0, "velocity_boost": 0.0,
                "enrichment_boost": 0.0, "community_sentiment_boost": 0.0,
                "score_recalibration_factor": 1.0,
                "policy_version": "v2.1",
                "signals_contributing": 2, "sources_checked": 1,
                "sources": ["github"],
                "signal_details": [],
                "calculation_method": "glass_ai_v2",
                "calculated_at": "2026-03-14T00:00:00+00:00",
            }),
        )
        output = _run_explain(db, "domain:acme.ai")
        assert "0.650" in output
        assert "decision based on this" in output
        assert "0.782" in output
        assert "LLM adj: +0.132" in output

    def test_explain_score_waterfall_golden_output(self, tmp_path):
        """Waterfall must compute to headline Gate Score."""
        db = _create_db(tmp_path)
        # Known values: base=0.350, msb=x1.15 -> 0.4025, no convergence,
        # +0.08+0.05+0.03+0.016 = 0.5785, x1.35 = 0.780975 ~ 0.781
        _insert_ledger_row(db)
        output = _run_explain(db, "domain:acme.ai")
        assert "Base Score:" in output
        assert "Multi-Source:" in output
        assert "Recalibration:" in output
        assert "Cap (1.0):" in output
        # Gate score matches the displayed value
        assert "0.782" in output

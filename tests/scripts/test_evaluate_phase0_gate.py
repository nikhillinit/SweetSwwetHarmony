"""Tests for scripts/evaluate_phase0_gate.py."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from scripts.evaluate_phase0_gate import (
    HALT_WEEK,
    TARGET_CONVERGENCE_PCT,
    evaluate_phase0_gate,
)

_DAILY_DDL = """
CREATE TABLE quality_metrics_daily (
    metric_date TEXT NOT NULL,
    source_api TEXT,
    tp_count INTEGER DEFAULT 0,
    fp_count INTEGER DEFAULT 0,
    total_count INTEGER DEFAULT 0,
    fp_rate REAL,
    created_at TEXT
);
"""


@pytest.fixture
def gate_db(tmp_path):
    db_path = str(tmp_path / "phase0_test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(_DAILY_DDL)
    conn.close()
    return db_path


def _insert_daily(db_path, days_ago, tp, fp):
    conn = sqlite3.connect(db_path)
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    conn.execute(
        "INSERT INTO quality_metrics_daily (metric_date, tp_count, fp_count) "
        "VALUES (?, ?, ?)",
        (d.strftime("%Y-%m-%d"), tp, fp),
    )
    conn.commit()
    conn.close()


def test_missing_table(tmp_path):
    """No quality_metrics_daily → insufficient_data."""
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.close()
    result = evaluate_phase0_gate(db_path)
    assert result["tier"] == "insufficient_data"
    assert result["halt_week"] == HALT_WEEK
    assert result["target_convergence_pct"] == TARGET_CONVERGENCE_PCT


def test_empty_table(gate_db):
    """Empty table → insufficient_data."""
    result = evaluate_phase0_gate(gate_db)
    assert result["tier"] == "insufficient_data"
    assert result["current"] is None
    assert result["trend"] is None


def test_converged(gate_db):
    """Low FP rate → converged tier."""
    # Insert data within the last week: 90 TP, 5 FP = ~5.3%
    _insert_daily(gate_db, 1, 90, 5)
    _insert_daily(gate_db, 2, 85, 4)
    result = evaluate_phase0_gate(gate_db)
    assert result["tier"] == "converged"
    assert result["current"] is not None
    assert result["current"] <= TARGET_CONVERGENCE_PCT


def test_early_tier(gate_db):
    """High FP rate → early tier."""
    _insert_daily(gate_db, 1, 10, 40)  # 80% FP
    result = evaluate_phase0_gate(gate_db)
    assert result["tier"] == "early"


def test_report_structure(gate_db):
    """Report contains all expected keys."""
    _insert_daily(gate_db, 1, 50, 10)
    result = evaluate_phase0_gate(gate_db)
    assert "tier" in result
    assert "halt_week" in result
    assert "target_convergence_pct" in result
    assert "current" in result
    assert "trend" in result

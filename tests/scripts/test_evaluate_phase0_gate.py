"""Tests for scripts/evaluate_phase0_gate.py."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from scripts.evaluate_phase0_gate import (
    HALT_WEEK,
    TARGET_CONVERGENCE_PCT,
    evaluate_phase0_gate,
)

# Match the actual quality_metrics_daily schema (normalised rows)
_DAILY_DDL = """
CREATE TABLE quality_metrics_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_date TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    segment_type TEXT NOT NULL DEFAULT 'overall',
    segment_key TEXT NOT NULL DEFAULT '',
    value REAL,
    n INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@pytest.fixture
def gate_db(tmp_path):
    db_path = str(tmp_path / "phase0_test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(_DAILY_DDL)
    conn.close()
    return db_path


def _insert_fp_rate(db_path, days_ago, rate, n=100):
    """Insert an overall_fp_rate row. rate is 0.0-1.0."""
    conn = sqlite3.connect(db_path)
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO quality_metrics_daily "
        "(metric_date, metric_name, segment_type, segment_key, value, n, created_at, updated_at) "
        "VALUES (?, 'overall_fp_rate', 'overall', '', ?, ?, ?, ?)",
        (d.strftime("%Y-%m-%d"), rate, n, now_iso, now_iso),
    )
    conn.commit()
    conn.close()


def test_missing_table(tmp_path):
    """No quality_metrics_daily -> insufficient_data."""
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.close()
    result = evaluate_phase0_gate(db_path)
    assert result["tier"] == "insufficient_data"
    assert result["halt_week"] == HALT_WEEK
    assert result["target_convergence_pct"] == TARGET_CONVERGENCE_PCT


def test_empty_table(gate_db):
    """Empty table -> insufficient_data."""
    result = evaluate_phase0_gate(gate_db)
    assert result["tier"] == "insufficient_data"
    assert result["current"] is None
    assert result["trend"] is None


def test_converged(gate_db):
    """Low FP rate -> converged tier."""
    # 5% FP rate within the last week
    _insert_fp_rate(gate_db, 1, 0.05, n=100)
    _insert_fp_rate(gate_db, 2, 0.04, n=90)
    result = evaluate_phase0_gate(gate_db)
    assert result["tier"] == "converged"
    assert result["current"] is not None
    assert result["current"] <= TARGET_CONVERGENCE_PCT


def test_early_tier(gate_db):
    """High FP rate -> early tier."""
    _insert_fp_rate(gate_db, 1, 0.80, n=50)  # 80% FP
    result = evaluate_phase0_gate(gate_db)
    assert result["tier"] == "early"


def test_report_structure(gate_db):
    """Report contains all expected keys."""
    _insert_fp_rate(gate_db, 1, 0.15, n=60)
    result = evaluate_phase0_gate(gate_db)
    assert "tier" in result
    assert "halt_week" in result
    assert "target_convergence_pct" in result
    assert "current" in result
    assert "trend" in result

"""Tests for scripts.compute_discovery_kpi_baseline.

Verifies the KPI computations against a hand-built fake signals.db.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.compute_discovery_kpi_baseline import (
    compute_baseline,
    render_markdown,
)


def _now_minus_days(d: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_type TEXT,
                source_api TEXT,
                canonical_key TEXT NOT NULL,
                company_name TEXT,
                confidence REAL,
                raw_data TEXT,
                detected_at TEXT,
                created_at TEXT
            );
            CREATE TABLE company_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT NOT NULL UNIQUE,
                company_name TEXT,
                canonical_key TEXT NOT NULL,
                status TEXT NOT NULL,
                source_apis TEXT NOT NULL DEFAULT '[]',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                promoted_at TEXT,
                archived_at TEXT,
                metadata TEXT
            );
            CREATE TABLE suppression_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_key TEXT NOT NULL UNIQUE,
                notion_page_id TEXT NOT NULL,
                status TEXT NOT NULL,
                company_name TEXT,
                cached_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                metadata TEXT
            );
            CREATE TABLE signal_quality_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                human_label TEXT,
                label_source TEXT,
                labeled_by TEXT,
                labeled_at TEXT
            );
            """
        )

        def add_signal(source_api, canonical_key, days_ago, label=None):
            ts = _now_minus_days(days_ago)
            conn.execute(
                "INSERT INTO signals (signal_type, source_api, canonical_key, "
                "confidence, raw_data, detected_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("x", source_api, canonical_key, 0.6, "{}", ts, ts),
            )
            sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            if label:
                conn.execute(
                    "INSERT INTO signal_quality_metrics (signal_id, human_label, "
                    "labeled_at) VALUES (?, ?, ?)",
                    (sid, label, ts),
                )
            return sid

        # company A: strong signal 30 days ago, ambient mention 20 days ago (we found it 10d before HN)
        add_signal("sec_edgar", "domain:a.ai", 30, label="TP")
        add_signal("linkedin", "domain:a.ai", 25)
        add_signal("hacker_news", "domain:a.ai", 20)

        # company B: ambient mention only — we lagged
        add_signal("hacker_news", "domain:b.ai", 15, label="FP")

        # company C: promoted, two non-ambient classes
        add_signal("sec_edgar", "domain:c.ai", 10, label="TP")
        add_signal("job_postings", "domain:c.ai", 5)

        # company files: A, C promoted; B not
        ts = _now_minus_days(30)
        conn.execute(
            "INSERT INTO company_files (company_id, company_name, canonical_key, "
            "status, source_apis, first_seen_at, last_seen_at, promoted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("co_a", "A", "domain:a.ai", "promoted", '["sec_edgar","linkedin"]', ts, ts, ts),
        )
        conn.execute(
            "INSERT INTO company_files (company_id, company_name, canonical_key, "
            "status, source_apis, first_seen_at, last_seen_at, promoted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("co_c", "C", "domain:c.ai", "promoted", '["sec_edgar","job_postings"]', ts, ts, ts),
        )

        # Notion suppression cache: 3 entries, 1 in meeting status
        conn.execute(
            "INSERT INTO suppression_cache (canonical_key, notion_page_id, status, "
            "cached_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            ("domain:a.ai", "p1", "Source", ts, ts),
        )
        conn.execute(
            "INSERT INTO suppression_cache (canonical_key, notion_page_id, status, "
            "cached_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            ("domain:b.ai", "p2", "Tracking", ts, ts),
        )
        conn.execute(
            "INSERT INTO suppression_cache (canonical_key, notion_page_id, status, "
            "cached_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            ("domain:c.ai", "p3", "Initial Meeting / Call", ts, ts),
        )

        conn.commit()
    finally:
        conn.close()


def test_baseline_computes_signal_counts(tmp_path: Path):
    db = tmp_path / "fake_signals.db"
    _make_db(db)
    baseline = compute_baseline(production_db=db, window_days=90)
    assert baseline.n_signals_in_window > 0
    assert baseline.signal_counts_by_source.get("sec_edgar", 0) >= 2
    assert baseline.signal_counts_by_source.get("hacker_news", 0) >= 2


def test_baseline_promoted_count(tmp_path: Path):
    db = tmp_path / "fake_signals.db"
    _make_db(db)
    baseline = compute_baseline(production_db=db, window_days=90)
    assert baseline.n_companies_promoted == 2


def test_baseline_lead_time_positive(tmp_path: Path):
    """Company A: strong signal at -30d, ambient at -20d → lead time = +10 days."""
    db = tmp_path / "fake_signals.db"
    _make_db(db)
    baseline = compute_baseline(production_db=db, window_days=90)
    assert baseline.lead_time_n_with_public_mention >= 1
    # company A should contribute a positive lead time
    assert baseline.lead_time_median_days is not None
    assert baseline.lead_time_median_days > 0


def test_baseline_meeting_rate(tmp_path: Path):
    db = tmp_path / "fake_signals.db"
    _make_db(db)
    baseline = compute_baseline(production_db=db, window_days=90)
    # 1 of 3 in meeting status
    assert baseline.notion_total == 3
    assert baseline.notion_meeting_or_beyond == 1
    assert baseline.meeting_rate is not None
    assert abs(baseline.meeting_rate - 1 / 3) < 1e-6


def test_baseline_precision_at_queue(tmp_path: Path):
    db = tmp_path / "fake_signals.db"
    _make_db(db)
    baseline = compute_baseline(production_db=db, window_days=90, queue_size=20)
    # 3 labelled signals total: TP, FP, TP → 2 TPs / 3 labelled
    assert baseline.precision_at_queue_n_labelled == 3
    assert baseline.precision_at_queue_n_tp == 2
    assert baseline.precision_at_queue_value is not None
    assert abs(baseline.precision_at_queue_value - 2 / 3) < 1e-6


def test_baseline_convergence_rate(tmp_path: Path):
    db = tmp_path / "fake_signals.db"
    _make_db(db)
    baseline = compute_baseline(production_db=db, window_days=90)
    # 2 promoted: A (sec_edgar+linkedin) → 2 non-ambient classes;
    #             C (sec_edgar+job_postings) → 2 non-ambient classes
    assert baseline.convergence_n_promoted == 2
    assert baseline.convergence_n_two_or_more_classes == 2
    assert baseline.convergence_rate == 1.0


def test_baseline_handles_missing_db(tmp_path: Path):
    """If signals.db doesn't exist, return an empty baseline (don't raise)."""
    baseline = compute_baseline(production_db=tmp_path / "no_such_file.db")
    assert baseline.n_signals_in_window == 0
    assert baseline.n_companies_promoted == 0
    assert baseline.lead_time_median_days is None


def test_render_markdown_includes_caveats(tmp_path: Path):
    db = tmp_path / "fake_signals.db"
    _make_db(db)
    baseline = compute_baseline(production_db=db, window_days=90)
    md = render_markdown(baseline)
    assert "GNews-only" in md
    assert "Phase 0, task p0.10" in md
    assert "## Caveats" in md

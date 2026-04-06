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

        def add_signal(source_api, canonical_key, days_ago, label=None, signal_type="x"):
            ts = _now_minus_days(days_ago)
            conn.execute(
                "INSERT INTO signals (signal_type, source_api, canonical_key, "
                "confidence, raw_data, detected_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (signal_type, source_api, canonical_key, 0.6, "{}", ts, ts),
            )
            sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            if label:
                conn.execute(
                    "INSERT INTO signal_quality_metrics (signal_id, human_label, "
                    "labeled_at) VALUES (?, ?, ?)",
                    (sid, label, ts),
                )
            return sid

        # company A: realistic signal_types wired so the production classifier
        # collapses sec_edgar (regulatory→INFRA) and linkedin_company
        # (web_presence→INFRA) into a SINGLE discovery class. Under the simple
        # source_api-only classifier, the same two signals look like 2 distinct
        # classes (INFRA + HUMAN_TRANSITION). Pre-E3 → 2; post-E3 → 1.
        add_signal("sec_edgar", "domain:a.ai", 30, label="TP", signal_type="incorporation")
        add_signal("linkedin", "domain:a.ai", 25, signal_type="linkedin_company")
        add_signal("hacker_news", "domain:a.ai", 20, signal_type="hacker_news_mention")

        # company B: ambient mention only — we lagged
        add_signal("hacker_news", "domain:b.ai", 15, label="FP", signal_type="hacker_news_mention")

        # company C: realistic signal_types so BOTH classifiers agree it has
        # 2 distinct discovery classes (regulatory→INFRA + hiring→HIRING).
        # This is the control case that proves E3 doesn't break working
        # multi-class detection.
        add_signal("sec_edgar", "domain:c.ai", 10, label="TP", signal_type="incorporation")
        add_signal("job_postings", "domain:c.ai", 5, signal_type="hiring_signal")

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


def test_baseline_convergence_rate_uses_production_classifier(tmp_path: Path):
    """KPI 5 must classify per-signal via the production-authoritative
    classifier (verification.evidence_families.get_family) by way of
    analytics.kg_bridge.class_for_signal_row, NOT via the simpler P0
    source_api map.

    With realistic signal_types in the fixture:
    - Company A (incorporation+sec_edgar, linkedin_company+linkedin):
        - Production: BOTH map to INFRASTRUCTURE_INTENT (regulatory and
          web_presence both collapse there) → 1 discovery class → NOT in two_or_more
        - Simple source_api map (pre-E3): sec_edgar→INFRA, linkedin→HUMAN
          → 2 distinct classes → would be in two_or_more (WRONG)
    - Company C (incorporation+sec_edgar, hiring_signal+job_postings):
        - Production: regulatory→INFRA, hiring→HIRING → 2 discovery classes
        - Simple: sec_edgar→INFRA, job_postings→HIRING → 2 classes
        - Both classifiers agree → IN two_or_more

    Expected post-E3 result:
        convergence_n_promoted = 2
        convergence_n_two_or_more_classes = 1   (only C, not A)
        convergence_rate = 0.5

    Pre-E3 result (with simple classifier) would be:
        convergence_n_two_or_more_classes = 2
        convergence_rate = 1.0
    This test fails RED on pre-E3 code.
    """
    db = tmp_path / "fake_signals.db"
    _make_db(db)
    baseline = compute_baseline(production_db=db, window_days=90)
    assert baseline.convergence_n_promoted == 2
    assert baseline.convergence_n_two_or_more_classes == 1, (
        "E3: production classifier must correctly identify that linkedin_company "
        "is web_presence (INFRASTRUCTURE_INTENT), the same family as sec_edgar's "
        "incorporation (regulatory→INFRASTRUCTURE_INTENT). Company A should NOT "
        "be counted as multi-class. Pre-E3 simple classifier wrongly counts it."
    )
    assert baseline.convergence_rate == 0.5


def test_kpi5_source_shape_branch_unchanged(tmp_path: Path):
    """Pin: the source-shape branch (sole_ambient / with_any_discovery counts)
    operates on company_files.source_apis strings only — there is no signal_type
    available there, so the branch MUST continue to use classify_source_api.

    E3 must NOT change this branch. Both promoted companies in the fixture have
    source_apis with at least one non-ambient source, so:
        sole_ambient_count = 0
        with_any_discovery_class = 2
    These numbers are pinned to the source_api-only classification.
    """
    db = tmp_path / "fake_signals.db"
    _make_db(db)
    baseline = compute_baseline(production_db=db, window_days=90)
    assert baseline.promoted_sole_ambient_count == 0, (
        "Source-shape branch (line ~380 in compute_discovery_kpi_baseline.py) "
        "must use classify_source_api on company_files.source_apis strings. "
        "Both A (sec_edgar+linkedin) and C (sec_edgar+job_postings) have "
        "non-ambient sources by source_api alone."
    )
    assert baseline.promoted_with_any_discovery_class == 2


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

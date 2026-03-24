"""Shared DB fixtures for quality ops and utility tests.

Usage:
    from tests.fixtures.db import tmp_db, populated_quality_db
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Generator

import pytest


@pytest.fixture
def tmp_db() -> Generator[sqlite3.Connection, None, None]:
    """Create a temporary SQLite DB with quality tables applied.

    Yields a read-write connection. DB file is cleaned up on teardown.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    _apply_quality_schema(conn)
    conn.commit()

    yield conn

    conn.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def populated_quality_db(tmp_db: sqlite3.Connection) -> sqlite3.Connection:
    """tmp_db pre-populated with a realistic mix of labeled signals.

    Contains:
    - 20 signals across 3 source_apis (hacker_news, rss_feeds, greenhouse_jobs)
    - 15 labels (10 FP, 4 TP, 1 UNSURE) in signal_quality_metrics
    - 5 thesis_classifications
    """
    conn = tmp_db
    now = datetime.now(timezone.utc)

    # Insert signals
    sources = ["hacker_news"] * 10 + ["rss_feeds"] * 6 + ["greenhouse_jobs"] * 4
    for i in range(1, 21):
        detected = now - timedelta(days=20 - i)
        conn.execute(
            """INSERT INTO signals
               (id, signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                i,
                "test_signal",
                sources[i - 1],
                f"domain:company{i}.com",
                f"Company {i}",
                0.3 + (i % 7) * 0.1,
                json.dumps({"title": f"Signal {i}", "description": f"Description for company {i}"}),
                detected.isoformat(),
                detected.isoformat(),
            ),
        )

    # Insert labels
    labels = (
        [("FP", "hacker_news")] * 7
        + [("TP", "rss_feeds")] * 3
        + [("FP", "rss_feeds")] * 2
        + [("TP", "greenhouse_jobs")] * 1
        + [("FP", "greenhouse_jobs")] * 1
        + [("UNSURE", "hacker_news")] * 1
    )
    for idx, (label, _src) in enumerate(labels):
        signal_id = idx + 1
        labeled_at = now - timedelta(days=10 - idx % 5)
        conn.execute(
            """INSERT INTO signal_quality_metrics
               (signal_id, canonical_key, human_label, label_source, labeled_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                signal_id,
                f"domain:company{signal_id}.com",
                label,
                "manual",
                labeled_at.isoformat(),
            ),
        )

    # Insert thesis classifications for a subset
    for i in [1, 2, 5, 11, 15]:
        conn.execute(
            """INSERT INTO thesis_classifications
               (signal_id, canonical_key, keyword_score, category, classified_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                i,
                f"domain:company{i}.com",
                0.5,
                "consumer_cpg" if i % 2 == 1 else "excluded",
                now.isoformat(),
            ),
        )

    conn.commit()
    return conn


def _apply_quality_schema(conn: sqlite3.Connection) -> None:
    """Apply the minimal schema needed for quality ops tests."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY,
            signal_type TEXT NOT NULL,
            source_api TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            company_name TEXT,
            confidence REAL,
            raw_data TEXT,
            detected_at TEXT,
            created_at TEXT,
            company_id TEXT,
            evidence_family TEXT,
            canonical_key_v2 TEXT,
            evidence_key TEXT
        );

        CREATE TABLE IF NOT EXISTS signal_quality_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            canonical_key TEXT,
            human_label TEXT,
            label_source TEXT,
            labeled_by TEXT,
            labeled_at TEXT,
            notion_page_id TEXT,
            notion_status TEXT,
            status_event_id TEXT,
            days_to_outcome REAL,
            notes TEXT,
            metadata TEXT,
            UNIQUE(signal_id)
        );

        CREATE TABLE IF NOT EXISTS thesis_classifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            canonical_key TEXT,
            keyword_score REAL,
            keyword_category TEXT,
            negative_keywords TEXT,
            thesis_match INTEGER,
            thesis_fit_score REAL,
            category TEXT,
            stage_estimate TEXT,
            confidence REAL,
            rationale TEXT,
            key_signals TEXT,
            prompt_version TEXT,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            latency_ms REAL,
            competitor_flag INTEGER,
            competitor_match TEXT,
            classified_at TEXT,
            reasoning_trace TEXT,
            cot_enabled INTEGER,
            disagreement_detected INTEGER
        );

        CREATE TABLE IF NOT EXISTS quality_label_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            created_by TEXT,
            reason TEXT,
            notes TEXT,
            metadata TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS quality_metrics_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_date TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            segment_type TEXT NOT NULL DEFAULT 'overall',
            segment_key TEXT NOT NULL DEFAULT '',
            value REAL,
            n INTEGER DEFAULT 0,
            metadata TEXT,
            computed_at TEXT,
            UNIQUE(metric_date, metric_name, segment_type, segment_key)
        );
    """)

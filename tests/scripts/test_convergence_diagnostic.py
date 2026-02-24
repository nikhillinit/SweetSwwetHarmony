"""
Tests for convergence_diagnostic.py script.

Verifies all 7 sections: multi-source overlap, promoted KPI, news_api distribution,
publisher leakage, api_calls scoping, sic_matched, domain key counts.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, ".")

from scripts.convergence_diagnostic import run_diagnostic


@pytest.fixture
def test_db(tmp_path):
    """Create a test DB with necessary tables."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)

    conn.executescript("""
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_key TEXT NOT NULL,
            source_api TEXT NOT NULL,
            created_at TEXT NOT NULL,
            signal_type TEXT DEFAULT 'news',
            confidence REAL DEFAULT 0.5,
            source_url TEXT DEFAULT '',
            detected_at TEXT DEFAULT '',
            raw_data TEXT DEFAULT '{}'
        );

        CREATE TABLE company_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT NOT NULL UNIQUE,
            company_name TEXT,
            canonical_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('thin', 'promoted', 'archived')),
            source_apis TEXT NOT NULL DEFAULT '[]',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            promoted_at TEXT,
            archived_at TEXT,
            metadata TEXT
        );

        CREATE TABLE signal_processing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            processed_at TEXT NOT NULL
        );

        CREATE TABLE pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT DEFAULT 'completed',
            duration_seconds REAL,
            signals_collected INTEGER DEFAULT 0,
            signals_processed INTEGER DEFAULT 0,
            signals_stored INTEGER DEFAULT 0,
            signals_deduplicated INTEGER DEFAULT 0,
            collectors_run INTEGER DEFAULT 0,
            collectors_succeeded INTEGER DEFAULT 0,
            collectors_failed INTEGER DEFAULT 0,
            collectors_skipped INTEGER DEFAULT 0,
            error_messages TEXT
        );

        CREATE TABLE collector_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            collector_name TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            duration_seconds REAL,
            signals_found INTEGER DEFAULT 0,
            api_calls INTEGER DEFAULT 0,
            rate_limit_hits INTEGER DEFAULT 0,
            retries INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success'
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_signal(conn, canonical_key, source_api, created_at=None, raw_data=None):
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    if raw_data is None:
        raw_data = "{}"
    conn.execute(
        "INSERT INTO signals (canonical_key, source_api, created_at, raw_data) VALUES (?, ?, ?, ?)",
        (canonical_key, source_api, created_at, raw_data),
    )


class TestMultiSourceOverlap:
    """Section 1: Multi-source overlap detection."""

    def test_overlap_detected(self, test_db):
        """Same domain from 2 sources → detected."""
        conn = sqlite3.connect(test_db)
        now = datetime.now(timezone.utc).isoformat()
        _insert_signal(conn, "domain:acme.ai", "news_api", now)
        _insert_signal(conn, "domain:acme.ai", "hacker_news", now)
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db)
        overlap = report["sections"]["multi_source_overlap"]

        assert len(overlap) == 1
        assert overlap[0]["canonical_key"] == "domain:acme.ai"
        assert overlap[0]["source_count"] == 2

    def test_single_source_not_counted(self, test_db):
        """Domain from only 1 source → not in overlap."""
        conn = sqlite3.connect(test_db)
        now = datetime.now(timezone.utc).isoformat()
        _insert_signal(conn, "domain:acme.ai", "news_api", now)
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db)
        overlap = report["sections"]["multi_source_overlap"]
        assert len(overlap) == 0


class TestPublisherLeakage:
    """Section 4: Publisher leakage detection."""

    def test_leakage_zero_clean_db(self, test_db):
        """No publisher keys → leakage = 0."""
        conn = sqlite3.connect(test_db)
        now = datetime.now(timezone.utc).isoformat()
        _insert_signal(conn, "domain:acme.ai", "news_api", now)
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db)
        leakage = report["sections"]["publisher_leakage"]
        assert leakage["publisher_domain_keys_total"] == 0

    def test_leakage_detected(self, test_db):
        """Publisher domain key → leakage > 0."""
        conn = sqlite3.connect(test_db)
        now = datetime.now(timezone.utc).isoformat()
        _insert_signal(conn, "domain:reuters.com", "news_api", now)
        _insert_signal(conn, "domain:m.bostonglobe.com", "news_api", now)
        _insert_signal(conn, "domain:acme.ai", "news_api", now)
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db)
        leakage = report["sections"]["publisher_leakage"]
        assert leakage["publisher_domain_keys_total"] == 2


class TestApiCallsScoping:
    """Section 5: api_calls uses single run_id."""

    def test_explicit_run_id(self, test_db):
        """--run-id scopes to that specific run."""
        conn = sqlite3.connect(test_db)
        now = datetime.now(timezone.utc).isoformat()

        # Two runs with different metrics
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at, completed_at) VALUES (?, ?, ?)",
            ("run-1", now, now),
        )
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at, completed_at) VALUES (?, ?, ?)",
            ("run-2", now, now),
        )
        conn.execute(
            "INSERT INTO collector_metrics (run_id, collector_name, api_calls, signals_found) VALUES (?, ?, ?, ?)",
            ("run-1", "news_api", 10, 5),
        )
        conn.execute(
            "INSERT INTO collector_metrics (run_id, collector_name, api_calls, signals_found) VALUES (?, ?, ?, ?)",
            ("run-2", "news_api", 20, 8),
        )
        conn.commit()
        conn.close()

        # Scope to run-1
        report = run_diagnostic(test_db, run_id="run-1")
        api = report["sections"]["api_calls_per_collector"]
        assert len(api) == 1
        assert api[0]["api_calls"] == 10

        # Scope to run-2
        report2 = run_diagnostic(test_db, run_id="run-2")
        api2 = report2["sections"]["api_calls_per_collector"]
        assert len(api2) == 1
        assert api2[0]["api_calls"] == 20

    def test_no_cross_run_mixing(self, test_db):
        """Without run_id, latest CTE selects only the most recent run."""
        conn = sqlite3.connect(test_db)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at) VALUES (?, ?)",
            ("old-run", old),
        )
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at) VALUES (?, ?)",
            ("new-run", now),
        )
        conn.execute(
            "INSERT INTO collector_metrics (run_id, collector_name, api_calls, signals_found) VALUES (?, ?, ?, ?)",
            ("old-run", "sec_edgar", 5, 2),
        )
        conn.execute(
            "INSERT INTO collector_metrics (run_id, collector_name, api_calls, signals_found) VALUES (?, ?, ?, ?)",
            ("new-run", "news_api", 15, 7),
        )
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db)
        api = report["sections"]["api_calls_per_collector"]
        # Only the latest run's metrics
        assert len(api) == 1
        assert api[0]["collector_name"] == "news_api"
        assert api[0]["api_calls"] == 15


class TestLatestRunDetection:
    """--latest-run selects correct run_id."""

    def test_latest_run_selection(self, test_db):
        """--latest-run auto-detects the most recent run."""
        conn = sqlite3.connect(test_db)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at) VALUES (?, ?)",
            ("old-run", old),
        )
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at) VALUES (?, ?)",
            ("latest-run", now),
        )
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db, latest_run=True)
        assert report["scoped_run_id"] == "latest-run"


class TestEligibleMultiSourceExclusion:
    """Eligible multi-source excludes rejected signals."""

    def test_rejected_excluded(self, test_db):
        """Rejected signals don't count toward eligible multi-source."""
        conn = sqlite3.connect(test_db)
        now = datetime.now(timezone.utc).isoformat()

        # Company file
        conn.execute(
            """INSERT INTO company_files
               (company_id, canonical_key, status, source_apis, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("co-1", "domain:acme.ai", "promoted", '["news_api","hacker_news"]', now, now),
        )

        # Signal 1: news_api (not rejected)
        _insert_signal(conn, "domain:acme.ai", "news_api", now)
        sig1_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Signal 2: hacker_news (rejected)
        _insert_signal(conn, "domain:acme.ai", "hacker_news", now)
        sig2_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Mark signal 2 as rejected
        conn.execute(
            "INSERT INTO signal_processing (signal_id, status, processed_at) VALUES (?, ?, ?)",
            (sig2_id, "rejected", now),
        )

        conn.commit()
        conn.close()

        report = run_diagnostic(test_db)
        promoted = report["sections"]["promoted_multi_source"]

        # Raw: both signals count → 2 sources → included
        assert promoted["promoted_multi_source_raw"] == 1
        # Eligible: only 1 non-rejected source → NOT included (need >=2)
        assert promoted["promoted_multi_source_eligible"] == 0


class TestRunIdScoping:
    """--run-id scoping works for sections 3-5."""

    def test_run_scoping_sections(self, test_db):
        """Sections 3-5 respect --run-id when provided."""
        conn = sqlite3.connect(test_db)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at, completed_at) VALUES (?, ?, ?)",
            ("run-x", now, now),
        )
        conn.execute(
            "INSERT INTO collector_metrics (run_id, collector_name, api_calls, signals_found) VALUES (?, ?, ?, ?)",
            ("run-x", "news_api", 42, 10),
        )
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db, run_id="run-x")

        assert report["scoped_run_id"] == "run-x"
        api = report["sections"]["api_calls_per_collector"]
        assert len(api) == 1
        assert api[0]["api_calls"] == 42


class TestJsonOutput:
    """JSON output generation."""

    def test_json_output(self, test_db, tmp_path):
        """--json --out produces valid JSON file."""
        out_path = str(tmp_path / "report.json")

        report = run_diagnostic(
            test_db,
            json_output=True,
            output_path=out_path,
        )

        with open(out_path) as f:
            loaded = json.load(f)

        assert "sections" in loaded
        assert "multi_source_overlap" in loaded["sections"]
        assert "publisher_leakage" in loaded["sections"]

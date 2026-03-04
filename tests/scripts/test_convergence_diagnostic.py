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

from scripts.convergence_diagnostic import (
    run_diagnostic,
    _build_scope_filter,
    _determine_scope_mode,
    _format_scope_description,
    _section_11_multi_family_convergence,
)


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
            raw_data TEXT DEFAULT '{}',
            evidence_family TEXT DEFAULT NULL,
            company_name TEXT DEFAULT NULL,
            canonical_key_v2 TEXT DEFAULT NULL
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


def _insert_signal(conn, canonical_key, source_api, created_at=None, raw_data=None,
                   evidence_family=None, detected_at=None):
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    if detected_at is None:
        detected_at = created_at
    if raw_data is None:
        raw_data = "{}"
    conn.execute(
        "INSERT INTO signals (canonical_key, source_api, created_at, raw_data, evidence_family, detected_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (canonical_key, source_api, created_at, raw_data, evidence_family, detected_at),
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


class TestScopeBuilder:
    """Unit tests for _build_scope_filter()."""

    def test_no_scope_no_base(self):
        """No scope, no base_conditions -> returns '1=1', empty params."""
        where, params = _build_scope_filter()
        assert where == "1=1"
        assert params == []

    def test_no_scope_with_base(self):
        """No scope, with base_conditions -> returns base only."""
        where, params = _build_scope_filter(base_conditions=["source_api = 'news_api'"])
        assert where == "(source_api = 'news_api')"
        assert params == []

    def test_run_id_scope(self):
        """run_id -> subquery with params."""
        where, params = _build_scope_filter(run_id="run-abc")
        assert "rowid IN" in where
        assert params == ["run-abc"]

    def test_since_days_scope(self):
        """since_days -> created_at >= cutoff."""
        where, params = _build_scope_filter(since_days=7)
        assert "created_at >= ?" in where
        assert len(params) == 1
        # Param should be an ISO timestamp
        cutoff = datetime.fromisoformat(params[0])
        assert cutoff.tzinfo is not None

    def test_run_id_beats_since_days(self):
        """run_id + since_days -> only run_id used (precedence)."""
        where, params = _build_scope_filter(run_id="run-abc", since_days=7)
        assert "rowid IN" in where
        assert "created_at >= ?" not in where
        assert params == ["run-abc"]

    def test_since_days_is_not_none_check(self):
        """since_days=1 is valid (uses is not None, not truthiness)."""
        where, params = _build_scope_filter(since_days=1)
        assert "created_at >= ?" in where
        assert len(params) == 1

    def test_parenthesized_groups(self):
        """base + scope conditions are parenthesized."""
        where, params = _build_scope_filter(
            run_id="run-abc", base_conditions=["source_api = 'news_api'"]
        )
        assert where.startswith("(source_api = 'news_api') AND (rowid IN")


class TestActiveScopeOutput:
    """Tests for active_scope in report output."""

    def test_no_scope_all_time(self, test_db):
        """No scope -> mode=all_time, run_id=None."""
        report = run_diagnostic(test_db)
        scope = report["active_scope"]
        assert scope["mode"] == "all_time"
        assert scope["run_id"] is None
        assert scope["since_days"] is None
        assert scope["resolved_from_latest_run"] is False
        assert scope["applies_to_sections"] == []
        assert scope["section_5_default"] == "latest_run"

    def test_explicit_run_id(self, test_db):
        """Explicit run_id -> mode=run_id, resolved_from_latest_run=False."""
        conn = sqlite3.connect(test_db)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at, completed_at) VALUES (?, ?, ?)",
            ("run-explicit", now, now),
        )
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db, run_id="run-explicit")
        scope = report["active_scope"]
        assert scope["mode"] == "run_id"
        assert scope["run_id"] == "run-explicit"
        assert scope["resolved_from_latest_run"] is False
        assert scope["applies_to_sections"] == [3, 4, 5]
        assert scope["section_5_default"] is None

    def test_latest_run_resolved(self, test_db):
        """--latest-run resolved -> mode=run_id, resolved_from_latest_run=True."""
        conn = sqlite3.connect(test_db)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at) VALUES (?, ?)",
            ("resolved-run", now),
        )
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db, latest_run=True)
        scope = report["active_scope"]
        assert scope["mode"] == "run_id"
        assert scope["run_id"] == "resolved-run"
        assert scope["resolved_from_latest_run"] is True

    def test_since_days_scope(self, test_db):
        """since_days -> mode=since_days, since_days populated."""
        report = run_diagnostic(test_db, since_days=14)
        scope = report["active_scope"]
        assert scope["mode"] == "since_days"
        assert scope["since_days"] == 14
        assert scope["applies_to_sections"] == [3, 4]
        assert scope["section_5_default"] == "latest_run"

    def test_backward_compat_scoped_run_id(self, test_db):
        """scoped_run_id field still present alongside active_scope."""
        conn = sqlite3.connect(test_db)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at) VALUES (?, ?)",
            ("compat-run", now),
        )
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db, run_id="compat-run")
        assert report["scoped_run_id"] == "compat-run"
        assert report["active_scope"]["run_id"] == "compat-run"

    def test_latest_run_unresolved(self, test_db):
        """--latest-run with no pipeline_runs -> mode=all_time, warning."""
        report = run_diagnostic(test_db, latest_run=True)
        scope = report["active_scope"]
        assert scope["mode"] == "all_time"
        assert scope["run_id"] is None
        assert scope["resolved_from_latest_run"] is False

    def test_since_days_section5_note(self, test_db, capsys):
        """since_days set -> note about section 5 ignoring --since."""
        run_diagnostic(test_db, since_days=7)
        captured = capsys.readouterr()
        assert "Section 5 ignores --since" in captured.out


class TestScopedSectionBehavior:
    """Integration tests: scoping actually filters sections 3-4-5."""

    def _setup_run_with_signals(self, test_db, run_id, run_started, run_completed,
                                signals_inside, signals_outside=None):
        """Helper: create a run + signals inside and outside its window."""
        conn = sqlite3.connect(test_db)
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at, completed_at) VALUES (?, ?, ?)",
            (run_id, run_started, run_completed),
        )
        for sig in signals_inside:
            _insert_signal(conn, sig["key"], sig["source"], sig["created_at"])
        for sig in (signals_outside or []):
            _insert_signal(conn, sig["key"], sig["source"], sig["created_at"])
        conn.commit()
        conn.close()

    def test_section3_run_id_includes_inside(self, test_db):
        """Section 3: signals inside run window counted."""
        now = datetime.now(timezone.utc)
        started = (now - timedelta(minutes=10)).isoformat()
        completed = now.isoformat()
        inside_t = (now - timedelta(minutes=5)).isoformat()
        outside_t = (now - timedelta(hours=2)).isoformat()

        self._setup_run_with_signals(
            test_db, "run-s3", started, completed,
            signals_inside=[{"key": "domain:inside.ai", "source": "news_api", "created_at": inside_t}],
            signals_outside=[{"key": "domain:outside.ai", "source": "news_api", "created_at": outside_t}],
        )

        report = run_diagnostic(test_db, run_id="run-s3")
        s3 = report["sections"]["news_api_key_distribution"]
        total = sum(item["count"] for item in s3)
        assert total == 1  # Only inside signal

    def test_section4_run_id_includes_inside(self, test_db):
        """Section 4: leaked domains inside run window counted, outside excluded."""
        now = datetime.now(timezone.utc)
        started = (now - timedelta(minutes=10)).isoformat()
        completed = now.isoformat()
        inside_t = (now - timedelta(minutes=5)).isoformat()
        outside_t = (now - timedelta(hours=2)).isoformat()

        self._setup_run_with_signals(
            test_db, "run-s4", started, completed,
            signals_inside=[{"key": "domain:reuters.com", "source": "news_api", "created_at": inside_t}],
            signals_outside=[{"key": "domain:reuters.com", "source": "news_api", "created_at": outside_t}],
        )

        report = run_diagnostic(test_db, run_id="run-s4")
        s4 = report["sections"]["publisher_leakage"]
        assert s4["publisher_domain_keys_total"] == 1  # Only inside

    def test_section3_since_days(self, test_db):
        """Section 3: signals within N days counted, older excluded."""
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=1)).isoformat()
        old = (now - timedelta(days=30)).isoformat()

        conn = sqlite3.connect(test_db)
        _insert_signal(conn, "domain:recent.ai", "news_api", recent)
        _insert_signal(conn, "domain:old.ai", "news_api", old)
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db, since_days=7)
        s3 = report["sections"]["news_api_key_distribution"]
        total = sum(item["count"] for item in s3)
        assert total == 1  # Only recent

    def test_section4_since_days(self, test_db):
        """Section 4: leaked domains within N days counted, older excluded."""
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=1)).isoformat()
        old = (now - timedelta(days=30)).isoformat()

        conn = sqlite3.connect(test_db)
        _insert_signal(conn, "domain:reuters.com", "news_api", recent)
        _insert_signal(conn, "domain:reuters.com", "news_api", old)
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db, since_days=7)
        s4 = report["sections"]["publisher_leakage"]
        assert s4["publisher_domain_keys_total"] == 1

    def test_section5_ignores_since(self, test_db):
        """Section 5 unaffected by --since: returns latest-run data regardless."""
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(test_db)
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at) VALUES (?, ?)",
            ("latest-run", now.isoformat()),
        )
        conn.execute(
            "INSERT INTO collector_metrics (run_id, collector_name, api_calls, signals_found) VALUES (?, ?, ?, ?)",
            ("latest-run", "github", 50, 25),
        )
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db, since_days=7)
        s5 = report["sections"]["api_calls_per_collector"]
        assert len(s5) == 1
        assert s5[0]["api_calls"] == 50

    def test_run_id_overrides_since_section3(self, test_db):
        """Precedence: run_id overrides since_days for section 3."""
        now = datetime.now(timezone.utc)
        started = (now - timedelta(minutes=10)).isoformat()
        completed = now.isoformat()
        inside_t = (now - timedelta(minutes=5)).isoformat()

        self._setup_run_with_signals(
            test_db, "run-prec", started, completed,
            signals_inside=[{"key": "domain:prec.ai", "source": "news_api", "created_at": inside_t}],
        )
        # Add an old signal that since_days=365 would include
        conn = sqlite3.connect(test_db)
        old_t = (now - timedelta(days=100)).isoformat()
        _insert_signal(conn, "domain:old.ai", "news_api", old_t)
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db, run_id="run-prec", since_days=365)
        s3 = report["sections"]["news_api_key_distribution"]
        total = sum(item["count"] for item in s3)
        assert total == 1  # Only run-scoped signal

    def test_run_id_overrides_since_section4(self, test_db):
        """Precedence: run_id overrides since_days for section 4."""
        now = datetime.now(timezone.utc)
        started = (now - timedelta(minutes=10)).isoformat()
        completed = now.isoformat()
        inside_t = (now - timedelta(minutes=5)).isoformat()

        self._setup_run_with_signals(
            test_db, "run-prec4", started, completed,
            signals_inside=[{"key": "domain:reuters.com", "source": "news_api", "created_at": inside_t}],
        )
        conn = sqlite3.connect(test_db)
        old_t = (now - timedelta(days=100)).isoformat()
        _insert_signal(conn, "domain:reuters.com", "news_api", old_t)
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db, run_id="run-prec4", since_days=365)
        s4 = report["sections"]["publisher_leakage"]
        assert s4["publisher_domain_keys_total"] == 1

    def test_null_completed_at_open_window(self, test_db):
        """NULL completed_at -> open upper bound (all signals after started_at)."""
        now = datetime.now(timezone.utc)
        started = (now - timedelta(minutes=10)).isoformat()
        future_t = (now + timedelta(minutes=5)).isoformat()

        conn = sqlite3.connect(test_db)
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at, completed_at) VALUES (?, ?, ?)",
            ("run-open", started, None),
        )
        _insert_signal(conn, "domain:future.ai", "news_api", future_t)
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db, run_id="run-open")
        s3 = report["sections"]["news_api_key_distribution"]
        total = sum(item["count"] for item in s3)
        assert total == 1  # Future signal included (open window)

    def test_explicit_run_id_wins_over_latest(self, test_db):
        """--run-id + --latest-run -> explicit run_id wins."""
        conn = sqlite3.connect(test_db)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at) VALUES (?, ?)",
            ("latest-run", now),
        )
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at) VALUES (?, ?)",
            ("explicit-run", (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()),
        )
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db, run_id="explicit-run", latest_run=True)
        assert report["scoped_run_id"] == "explicit-run"
        assert report["active_scope"]["run_id"] == "explicit-run"
        assert report["active_scope"]["resolved_from_latest_run"] is False


class TestSection11MultiFamilyConvergence:
    """Section 11: Multi-family convergence gate tests."""

    def _insert_multi_family_signals(self, conn, canonical_key, families, source_apis=None):
        """Insert signals for a canonical_key across multiple evidence families."""
        now = datetime.now(timezone.utc)
        for i, family in enumerate(families):
            source = source_apis[i] if source_apis else f"collector_{family}"
            detected = (now - timedelta(days=5)).isoformat()
            _insert_signal(
                conn, canonical_key, source,
                created_at=now.isoformat(),
                evidence_family=family,
                detected_at=detected,
            )

    def test_pass_10_entities_3_families(self, test_db):
        """>=10 entities + >=3 total families -> PASS."""
        conn = sqlite3.connect(test_db)
        families = ["public_buzz", "corporate_filing", "startup_launch"]
        for i in range(12):
            # Each entity gets 2 of the 3 families (cycling)
            chosen = [families[i % 3], families[(i + 1) % 3]]
            self._insert_multi_family_signals(
                conn, f"domain:company{i}.ai", chosen,
                source_apis=[f"collector_{f}" for f in chosen],
            )
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db)
        s11 = report["sections"]["multi_family_convergence"]
        assert s11["entities_with_2plus_families"] >= 10
        assert s11["total_distinct_families"] >= 3
        assert s11["mvp_gate_pass"] is True
        assert s11["verdict"] == "PASS"

    def test_fail_10_entities_2_families(self, test_db):
        """>=10 entities + 2 families -> FAIL (need 3)."""
        conn = sqlite3.connect(test_db)
        for i in range(12):
            self._insert_multi_family_signals(
                conn, f"domain:company{i}.ai",
                ["public_buzz", "corporate_filing"],
                source_apis=["news_api", "sec_edgar"],
            )
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db)
        s11 = report["sections"]["multi_family_convergence"]
        assert s11["entities_with_2plus_families"] >= 10
        assert s11["total_distinct_families"] == 2
        assert s11["mvp_gate_pass"] is False
        assert s11["verdict"] == "FAIL"

    def test_fail_few_entities_3_families(self, test_db):
        """<10 entities + >=3 families -> FAIL."""
        conn = sqlite3.connect(test_db)
        families = ["public_buzz", "corporate_filing", "startup_launch"]
        for i in range(5):
            chosen = [families[i % 3], families[(i + 1) % 3]]
            self._insert_multi_family_signals(
                conn, f"domain:company{i}.ai", chosen,
                source_apis=[f"collector_{f}" for f in chosen],
            )
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db)
        s11 = report["sections"]["multi_family_convergence"]
        assert s11["entities_with_2plus_families"] < 10
        assert s11["total_distinct_families"] >= 3
        assert s11["mvp_gate_pass"] is False
        assert s11["verdict"] == "FAIL"

    def test_output_contains_total_distinct_families(self, test_db):
        """Output dict contains total_distinct_families with type int."""
        conn = sqlite3.connect(test_db)
        _insert_signal(conn, "domain:a.ai", "news_api",
                       evidence_family="public_buzz",
                       detected_at=datetime.now(timezone.utc).isoformat())
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db)
        s11 = report["sections"]["multi_family_convergence"]
        assert "total_distinct_families" in s11
        assert isinstance(s11["total_distinct_families"], int)

    def test_output_contains_collector_diversity_warning(self, test_db):
        """Output dict contains collector_diversity_warning with type str | None."""
        report = run_diagnostic(test_db)
        s11 = report["sections"]["multi_family_convergence"]
        assert "collector_diversity_warning" in s11
        assert s11["collector_diversity_warning"] is None or isinstance(s11["collector_diversity_warning"], str)

    def test_legacy_keys_still_present(self, test_db):
        """Legacy observability keys (collectors_with_2plus_families) still present."""
        report = run_diagnostic(test_db)
        s11 = report["sections"]["multi_family_convergence"]
        assert "collectors_with_2plus_families" in s11
        assert "families_per_collector" in s11

    def test_single_collector_diversity_warning(self, test_db):
        """Single active collector triggers diversity warning."""
        conn = sqlite3.connect(test_db)
        for i in range(3):
            _insert_signal(conn, f"domain:co{i}.ai", "news_api",
                           evidence_family="public_buzz",
                           detected_at=datetime.now(timezone.utc).isoformat())
        conn.commit()
        conn.close()

        report = run_diagnostic(test_db)
        s11 = report["sections"]["multi_family_convergence"]
        assert s11["collector_diversity_warning"] is not None
        assert "1 active collector" in s11["collector_diversity_warning"]


class TestSection11SchemaGuard:
    """Section 11: Schema compatibility guard."""

    def test_missing_evidence_family_raises(self, tmp_path):
        """Missing evidence_family column produces actionable error."""
        db_path = str(tmp_path / "minimal.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                canonical_key TEXT NOT NULL,
                source_api TEXT NOT NULL,
                created_at TEXT NOT NULL,
                detected_at TEXT DEFAULT ''
            );
        """)
        conn.commit()

        with pytest.raises(RuntimeError, match="Missing columns.*evidence_family"):
            _section_11_multi_family_convergence(conn)
        conn.close()

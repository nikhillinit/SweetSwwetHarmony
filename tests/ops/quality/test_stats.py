"""Tier 1 Foundation -- Quality Ops stats.py tests.

Tests for get_overall_stats and get_stats_by_source_api,
covering empty databases, populated databases, time-window filtering,
and min_labeled threshold filtering.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ops.quality.labels import upsert_resolved_label
from ops.quality.stats import CollectorStats, get_overall_stats, get_stats_by_source_api
from tests.ops.quality.conftest import _insert_signal, _utc_iso


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _label_signal(conn, signal_id: int, canonical_key: str, label: str) -> None:
    """Convenience wrapper around upsert_resolved_label for test setup."""
    upsert_resolved_label(
        conn,
        signal_id=signal_id,
        canonical_key=canonical_key,
        human_label=label,
        label_source="manual",
    )


# ---------------------------------------------------------------------------
# get_overall_stats
# ---------------------------------------------------------------------------

class TestOverallStats:
    """Tests for get_overall_stats()."""

    def test_overall_stats_empty_db(self, quality_db):
        """An empty database must return all-zero stats."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        stats = get_overall_stats(conn)

        assert stats["labeled"] == 0.0
        assert stats["fp"] == 0.0
        assert stats["tp"] == 0.0
        assert stats["unsure"] == 0.0
        assert stats["fp_rate"] == 0.0
        conn.close()

    def test_overall_stats_with_labels(self, quality_db_with_signals):
        """Stats must reflect the labels applied to signals — fp_rate uses decided denominator."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Label 5 signals: 2 TP, 2 FP, 1 UNSURE
        _label_signal(conn, signal_ids[0], "domain:company0.com", "TP")
        _label_signal(conn, signal_ids[1], "domain:company1.com", "TP")
        _label_signal(conn, signal_ids[2], "domain:company2.com", "FP")
        _label_signal(conn, signal_ids[3], "domain:company3.com", "FP")
        _label_signal(conn, signal_ids[4], "domain:company4.com", "UNSURE")

        stats = get_overall_stats(conn, days=30)

        assert stats["labeled"] == 5.0
        assert stats["decided"] == 4.0
        assert stats["tp"] == 2.0
        assert stats["fp"] == 2.0
        assert stats["unsure"] == 1.0
        assert stats["adj"] == 0.0
        assert stats["fp_rate"] == pytest.approx(2.0 / 4.0)  # fp / decided
        assert stats["adj_rate"] == 0.0
        assert stats["decision_rate"] == pytest.approx(4.0 / 5.0)
        conn.close()

    def test_overall_stats_adj_excluded_from_fp_rate(self, quality_db_with_signals):
        """ADJ label must not change fp_rate; adj count and adj_rate must be correct."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Label: 2 TP, 1 FP, 1 UNSURE, 1 ADJ
        _label_signal(conn, signal_ids[0], "domain:company0.com", "TP")
        _label_signal(conn, signal_ids[1], "domain:company1.com", "TP")
        _label_signal(conn, signal_ids[2], "domain:company2.com", "FP")
        _label_signal(conn, signal_ids[3], "domain:company3.com", "UNSURE")
        _label_signal(conn, signal_ids[4], "domain:company4.com", "ADJ")

        stats = get_overall_stats(conn, days=30)

        assert stats["labeled"] == 5.0
        assert stats["decided"] == 3.0  # 2 TP + 1 FP
        assert stats["adj"] == 1.0
        assert stats["fp_rate"] == pytest.approx(1.0 / 3.0)  # 1 FP / 3 decided
        assert stats["adj_rate"] == pytest.approx(1.0 / 4.0)  # 1 ADJ / (3 decided + 1 ADJ)
        assert stats["decision_rate"] == pytest.approx(3.0 / 5.0)
        conn.close()

    def test_overall_stats_window_filtering(self, quality_db):
        """Signals with detected_at outside the window must be excluded."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert a recent signal (1 day ago)
        recent_id = _insert_signal(
            conn,
            source_api="github",
            canonical_key="domain:recent.com",
            detected_at=_utc_iso(1),
        )
        _label_signal(conn, recent_id, "domain:recent.com", "TP")

        # Insert an old signal (60 days ago)
        old_id = _insert_signal(
            conn,
            source_api="github",
            canonical_key="domain:old.com",
            detected_at=_utc_iso(60),
        )
        _label_signal(conn, old_id, "domain:old.com", "FP")

        # With a 30-day window, only the recent signal should be counted
        stats = get_overall_stats(conn, days=30)

        assert stats["labeled"] == 1.0
        assert stats["tp"] == 1.0
        assert stats["fp"] == 0.0
        conn.close()


# ---------------------------------------------------------------------------
# get_stats_by_source_api
# ---------------------------------------------------------------------------

class TestStatsBySourceApi:
    """Tests for get_stats_by_source_api()."""

    def test_stats_by_source_api_empty(self, quality_db):
        """An empty database must return an empty list."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        result = get_stats_by_source_api(conn, min_labeled=1)

        assert result == []
        conn.close()

    def test_stats_by_source_api_with_labels(self, quality_db):
        """Returns CollectorStats with correct fields per source_api."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert 3 github signals: 2 TP, 1 FP
        for i in range(3):
            sid = _insert_signal(
                conn,
                source_api="github",
                canonical_key=f"domain:gh{i}.com",
                detected_at=_utc_iso(1),
            )
            label = "TP" if i < 2 else "FP"
            _label_signal(conn, sid, f"domain:gh{i}.com", label)

        result = get_stats_by_source_api(conn, min_labeled=1)

        assert len(result) == 1
        cs = result[0]
        assert isinstance(cs, CollectorStats)
        assert cs.source_api == "github"
        assert cs.labeled_signals == 3
        assert cs.tp == 2
        assert cs.fp == 1
        assert cs.unsure == 0
        assert cs.adj == 0
        assert cs.decided == 3
        assert cs.fp_rate == pytest.approx(1.0 / 3.0)
        conn.close()

    def test_stats_by_source_api_min_labeled_filter(self, quality_db):
        """Sources with fewer labeled signals than min_labeled must be excluded."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert 2 github signals (labeled)
        for i in range(2):
            sid = _insert_signal(
                conn,
                source_api="github",
                canonical_key=f"domain:mingh{i}.com",
                detected_at=_utc_iso(1),
            )
            _label_signal(conn, sid, f"domain:mingh{i}.com", "TP")

        # Insert 1 sec_edgar signal (labeled) -- below threshold
        sid_sec = _insert_signal(
            conn,
            source_api="sec_edgar",
            canonical_key="domain:minsec0.com",
            detected_at=_utc_iso(1),
        )
        _label_signal(conn, sid_sec, "domain:minsec0.com", "FP")

        # With min_labeled=2, only github should appear
        result = get_stats_by_source_api(conn, min_labeled=2)

        source_apis = [cs.source_api for cs in result]
        assert "github" in source_apis
        assert "sec_edgar" not in source_apis
        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

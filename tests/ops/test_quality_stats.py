"""Tests for ops.quality.stats — FP/TP rate aggregation."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import patch

import pytest

from ops.quality.stats import CollectorStats, get_overall_stats, get_stats_by_source_api

# Register shared DB fixtures so pytest discovers them.
pytest_plugins = ["tests.fixtures.db"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _with_row_factory(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Ensure dict-style row access that the stats module requires."""
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# 1. Overall aggregation from populated_quality_db
# ---------------------------------------------------------------------------


class TestGetOverallStats:
    """get_overall_stats returns correct fp_rate, tp count, etc."""

    def test_overall_stats_populated_db(self, populated_quality_db):
        """Verify counts and rates against the known fixture data.

        Fixture label distribution (all within 20 days):
          hacker_news:  7 FP, 3 TP
          rss_feeds:    3 FP, 1 TP, 1 UNSURE
          greenhouse_jobs: (no labels)
        Totals: 10 FP, 4 TP, 1 UNSURE, 0 ADJ, 15 labeled, 14 decided.
        """
        conn = _with_row_factory(populated_quality_db)
        result = get_overall_stats(conn, days=30)

        assert result["labeled"] == 15.0
        assert result["fp"] == 10.0
        assert result["tp"] == 4.0
        assert result["unsure"] == 1.0
        assert result["adj"] == 0.0
        assert result["decided"] == 14.0
        assert result["fp_rate"] == pytest.approx(10.0 / 14.0)
        assert result["adj_rate"] == 0.0
        assert result["decision_rate"] == pytest.approx(14.0 / 15.0)

    def test_overall_stats_days_field(self, populated_quality_db):
        """The 'days' key in the result reflects the requested window."""
        conn = _with_row_factory(populated_quality_db)
        result = get_overall_stats(conn, days=42)
        assert result["days"] == 42.0

    def test_all_values_are_floats(self, populated_quality_db):
        """Every value in the returned dict is a float (contract guarantee)."""
        conn = _with_row_factory(populated_quality_db)
        result = get_overall_stats(conn, days=30)
        for key, value in result.items():
            assert isinstance(value, float), f"{key} should be float, got {type(value)}"


# ---------------------------------------------------------------------------
# 2. By-source-api stats
# ---------------------------------------------------------------------------


class TestGetStatsBySourceApi:
    """get_stats_by_source_api returns CollectorStats per source."""

    def test_default_min_labeled_filters_small_sources(self, populated_quality_db):
        """With default min_labeled=10, only hacker_news qualifies (decided=10)."""
        conn = _with_row_factory(populated_quality_db)
        results = get_stats_by_source_api(conn, days=30)

        assert len(results) == 1
        hn = results[0]
        assert hn.source_api == "hacker_news"
        assert hn.fp == 7
        assert hn.tp == 3
        assert hn.decided == 10
        assert hn.fp_rate == pytest.approx(0.7)

    def test_low_min_labeled_includes_all_sources(self, populated_quality_db):
        """With min_labeled=1, both hacker_news and rss_feeds appear."""
        conn = _with_row_factory(populated_quality_db)
        results = get_stats_by_source_api(conn, days=30, min_labeled=1)

        source_names = [r.source_api for r in results]
        assert "hacker_news" in source_names
        assert "rss_feeds" in source_names
        assert len(results) == 2  # greenhouse_jobs has no labels

    def test_rss_feeds_stats_correct(self, populated_quality_db):
        """Verify rss_feeds numbers when min_labeled allows it through."""
        conn = _with_row_factory(populated_quality_db)
        results = get_stats_by_source_api(conn, days=30, min_labeled=1)

        rss = next(r for r in results if r.source_api == "rss_feeds")
        assert rss.fp == 3
        assert rss.tp == 1
        assert rss.unsure == 1
        assert rss.adj == 0
        assert rss.decided == 4
        assert rss.labeled_signals == 5
        assert rss.fp_rate == pytest.approx(3.0 / 4.0)

    def test_results_are_collector_stats_instances(self, populated_quality_db):
        """Each element is a CollectorStats dataclass."""
        conn = _with_row_factory(populated_quality_db)
        results = get_stats_by_source_api(conn, days=30, min_labeled=1)
        for r in results:
            assert isinstance(r, CollectorStats)

    def test_ordered_by_fp_rate_desc(self, populated_quality_db):
        """Results are ordered by fp_rate descending (highest FP first)."""
        conn = _with_row_factory(populated_quality_db)
        results = get_stats_by_source_api(conn, days=30, min_labeled=1)

        fp_rates = [r.fp_rate for r in results]
        assert fp_rates == sorted(fp_rates, reverse=True)


# ---------------------------------------------------------------------------
# 3. Date-window boundaries
# ---------------------------------------------------------------------------


class TestDateWindowBoundaries:
    """Stats with different day windows return different counts."""

    def test_days_7_vs_days_30(self, populated_quality_db):
        """A 7-day window should capture strictly fewer signals than 30-day.

        Signal detected_at ranges from now-19d (id=1) to now-0d (id=20).
        Due to sub-second timing differences between fixture creation and
        query execution, the exact boundary signal may or may not be included,
        so we assert relative ordering rather than exact counts.
        """
        conn = _with_row_factory(populated_quality_db)

        stats_30 = get_overall_stats(conn, days=30)
        stats_7 = get_overall_stats(conn, days=7)

        assert stats_30["labeled"] > stats_7["labeled"]
        assert stats_7["labeled"] >= 2.0
        assert stats_7["labeled"] <= 3.0

    def test_days_90_same_as_30(self, populated_quality_db):
        """All signals are within 20 days, so 90 and 30 yield the same counts."""
        conn = _with_row_factory(populated_quality_db)

        stats_30 = get_overall_stats(conn, days=30)
        stats_90 = get_overall_stats(conn, days=90)

        assert stats_30["labeled"] == stats_90["labeled"]
        assert stats_30["fp"] == stats_90["fp"]
        assert stats_30["tp"] == stats_90["tp"]

    def test_very_small_window_excludes_all(self, populated_quality_db):
        """A 0-day window may exclude most or all signals."""
        conn = _with_row_factory(populated_quality_db)
        # Signal 20 has detected_at = now - 0d, so days=0 should
        # include only signals detected in the last ~instant.
        # Practically this means 0 or 1 depending on timing.
        stats = get_overall_stats(conn, days=0)
        assert stats["labeled"] <= 1.0

    def test_by_source_window_filtering(self, populated_quality_db):
        """By-source stats with days=7 returns fewer qualifying sources.

        hacker_news signals (ids 1-10) have detected_at between now-19d and
        now-10d, all outside a 7-day window.  Only rss_feeds signals near
        the boundary (ids 13-15) can appear.
        """
        conn = _with_row_factory(populated_quality_db)

        results = get_stats_by_source_api(conn, days=7, min_labeled=1)
        source_names = [r.source_api for r in results]

        # hacker_news is entirely outside the 7-day window
        assert "hacker_news" not in source_names
        # rss_feeds has at least 1 decided signal in the window
        assert "rss_feeds" in source_names

        rss = next(r for r in results if r.source_api == "rss_feeds")
        assert rss.decided >= 1


# ---------------------------------------------------------------------------
# 4. Null handling — empty DB
# ---------------------------------------------------------------------------


class TestEmptyDb:
    """Empty DB returns sensible defaults (0 counts, 0.0 rates)."""

    def test_overall_stats_empty(self, tmp_db):
        """An empty DB yields all-zero stats."""
        conn = _with_row_factory(tmp_db)
        result = get_overall_stats(conn, days=30)

        assert result["labeled"] == 0.0
        assert result["fp"] == 0.0
        assert result["tp"] == 0.0
        assert result["unsure"] == 0.0
        assert result["adj"] == 0.0
        assert result["decided"] == 0.0
        assert result["fp_rate"] == 0.0
        assert result["adj_rate"] == 0.0
        assert result["decision_rate"] == 0.0

    def test_by_source_empty(self, tmp_db):
        """An empty DB yields an empty list of CollectorStats."""
        conn = _with_row_factory(tmp_db)
        results = get_stats_by_source_api(conn, days=30, min_labeled=1)
        assert results == []


# ---------------------------------------------------------------------------
# 5. min_labeled filtering
# ---------------------------------------------------------------------------


class TestMinLabeledFiltering:
    """Source APIs with fewer than min_labeled decided are excluded."""

    def test_min_labeled_exactly_at_threshold(self, populated_quality_db):
        """A source with decided == min_labeled is INCLUDED (strict < comparison).

        rss_feeds has decided=4, so min_labeled=4 means `4 < 4` is False,
        therefore rss_feeds passes through.
        """
        conn = _with_row_factory(populated_quality_db)
        results = get_stats_by_source_api(conn, days=30, min_labeled=4)

        source_names = [r.source_api for r in results]
        assert "rss_feeds" in source_names
        assert "hacker_news" in source_names

    def test_min_labeled_one_above_threshold_excludes(self, populated_quality_db):
        """A source with decided < min_labeled is excluded.

        rss_feeds has decided=4, so min_labeled=5 means `4 < 5` is True,
        therefore rss_feeds is excluded.
        """
        conn = _with_row_factory(populated_quality_db)
        results = get_stats_by_source_api(conn, days=30, min_labeled=5)

        source_names = [r.source_api for r in results]
        assert "rss_feeds" not in source_names
        assert "hacker_news" in source_names

    def test_min_labeled_above_all_sources(self, populated_quality_db):
        """min_labeled higher than any source's decided returns empty list."""
        conn = _with_row_factory(populated_quality_db)
        results = get_stats_by_source_api(conn, days=30, min_labeled=50)
        assert results == []

    def test_min_labeled_zero_includes_all_decided(self, populated_quality_db):
        """min_labeled=0 includes every source with decided > 0."""
        conn = _with_row_factory(populated_quality_db)
        results = get_stats_by_source_api(conn, days=30, min_labeled=0)

        source_names = [r.source_api for r in results]
        # Both hacker_news and rss_feeds have decided > 0
        assert "hacker_news" in source_names
        assert "rss_feeds" in source_names

    def test_min_labeled_5_excludes_rss(self, populated_quality_db):
        """rss_feeds decided=4 is excluded at min_labeled=5."""
        conn = _with_row_factory(populated_quality_db)
        results = get_stats_by_source_api(conn, days=30, min_labeled=5)

        source_names = [r.source_api for r in results]
        assert "rss_feeds" not in source_names
        assert "hacker_news" in source_names


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

"""Tests for ops.quality.patterns -- FP pattern detection over labeled signals.

Covers:
1. Positive detections: source_api_fp_rate pattern found with enough FP signals
2. Negative cases: all TP labels produce no patterns
3. Threshold-edge: FP rate just below/above threshold
4. Duplicate suppression: repeated FP descriptions produce duplicate_fp_description pattern
5. PatternConfig: custom config overrides defaults
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import pytest

from ops.quality.patterns import PatternConfig, detect_patterns
from tests.fixtures.db import tmp_db  # noqa: F401 -- fixture import


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _insert_signal(
    conn: sqlite3.Connection,
    signal_id: int,
    *,
    source_api: str = "hacker_news",
    canonical_key: str | None = None,
    raw_data: str | None = None,
    detected_at: str | None = None,
) -> None:
    """Insert a minimal signal row."""
    canonical_key = canonical_key or f"domain:company{signal_id}.com"
    detected_at = detected_at or _days_ago_iso(5)
    raw_data = raw_data or json.dumps({"description": f"Description for signal {signal_id}"})
    conn.execute(
        """INSERT INTO signals
           (id, signal_type, source_api, canonical_key, company_name,
            confidence, raw_data, detected_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            signal_id,
            "test_signal",
            source_api,
            canonical_key,
            f"Company {signal_id}",
            0.5,
            raw_data,
            detected_at,
            detected_at,
        ),
    )


def _insert_label(
    conn: sqlite3.Connection,
    signal_id: int,
    label: str,
    *,
    canonical_key: str | None = None,
) -> None:
    """Insert a label row in signal_quality_metrics."""
    canonical_key = canonical_key or f"domain:company{signal_id}.com"
    conn.execute(
        """INSERT INTO signal_quality_metrics
           (signal_id, canonical_key, human_label, label_source, labeled_at)
           VALUES (?, ?, ?, ?, ?)""",
        (signal_id, canonical_key, label, "manual", _now_iso()),
    )


def _insert_thesis_classification(
    conn: sqlite3.Connection,
    signal_id: int,
    category: str = "consumer_cpg",
) -> None:
    """Insert a thesis classification row."""
    conn.execute(
        """INSERT INTO thesis_classifications
           (signal_id, canonical_key, keyword_score, category, classified_at)
           VALUES (?, ?, ?, ?, ?)""",
        (signal_id, f"domain:company{signal_id}.com", 0.5, category, _now_iso()),
    )


def _pattern_types(patterns: List[Dict]) -> List[str]:
    """Extract just pattern type strings from results."""
    return [p["type"] for p in patterns]


# ---------------------------------------------------------------------------
# 1. Positive detections -- source_api_fp_rate
# ---------------------------------------------------------------------------

class TestSourceApiFpRateDetection:
    """With enough FP signals from a single source, detect_patterns finds
    a 'source_api_fp_rate' pattern."""

    def test_detects_source_api_fp_rate(self, tmp_db: sqlite3.Connection):
        """12 FP + 2 TP from hacker_news should produce a source_api_fp_rate pattern
        (FP rate = 12/14 ~ 0.857, above default 0.70 threshold, fp count >= 10)."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        for i in range(1, 15):
            _insert_signal(conn, i, source_api="hacker_news")

        # 12 FP, 2 TP
        for i in range(1, 13):
            _insert_label(conn, i, "FP")
        for i in range(13, 15):
            _insert_label(conn, i, "TP")

        conn.commit()

        config = PatternConfig(days=30, min_count=10, fp_rate_threshold=0.70)
        patterns = detect_patterns(conn, config=config)

        source_patterns = [p for p in patterns if p["type"] == "source_api_fp_rate"]
        assert len(source_patterns) == 1

        pat = source_patterns[0]
        assert pat["source_api"] == "hacker_news"
        assert pat["fp"] == 12
        assert pat["tp"] == 2
        assert pat["fp_rate"] == pytest.approx(12 / 14, abs=0.001)
        assert pat["window_days"] == 30
        assert "recommendation" in pat

    def test_pattern_includes_unsure_count(self, tmp_db: sqlite3.Connection):
        """UNSURE labels are counted in the output but excluded from fp_rate denominator."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        for i in range(1, 16):
            _insert_signal(conn, i, source_api="rss_feeds")

        # 11 FP, 1 TP, 3 UNSURE  =>  fp_rate = 11/12
        for i in range(1, 12):
            _insert_label(conn, i, "FP")
        _insert_label(conn, 12, "TP")
        for i in range(13, 16):
            _insert_label(conn, i, "UNSURE")

        conn.commit()

        patterns = detect_patterns(conn, config=PatternConfig(days=30, min_count=10))
        source_patterns = [p for p in patterns if p["type"] == "source_api_fp_rate"]
        assert len(source_patterns) == 1

        pat = source_patterns[0]
        assert pat["unsure"] == 3
        assert pat["fp_rate"] == pytest.approx(11 / 12, abs=0.001)


# ---------------------------------------------------------------------------
# 2. Negative cases -- all TP labels
# ---------------------------------------------------------------------------

class TestNegativeCases:
    """With all TP labels, no patterns detected."""

    def test_all_tp_no_patterns(self, tmp_db: sqlite3.Connection):
        """All signals labeled TP should produce zero patterns."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        for i in range(1, 16):
            _insert_signal(conn, i, source_api="hacker_news")
            _insert_label(conn, i, "TP")

        conn.commit()

        patterns = detect_patterns(conn, config=PatternConfig(days=30, min_count=10))
        assert patterns == []

    def test_empty_db_no_patterns(self, tmp_db: sqlite3.Connection):
        """Empty database returns empty list."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        patterns = detect_patterns(conn, config=PatternConfig(days=30, min_count=10))
        assert patterns == []

    def test_mixed_but_low_fp_rate_no_pattern(self, tmp_db: sqlite3.Connection):
        """5 FP + 15 TP => fp_rate = 0.25, well below 0.70 -- no pattern."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        for i in range(1, 21):
            _insert_signal(conn, i, source_api="greenhouse_jobs")

        for i in range(1, 6):
            _insert_label(conn, i, "FP")
        for i in range(6, 21):
            _insert_label(conn, i, "TP")

        conn.commit()

        patterns = detect_patterns(conn, config=PatternConfig(days=30, min_count=5))
        source_patterns = [p for p in patterns if p["type"] == "source_api_fp_rate"]
        assert source_patterns == []


# ---------------------------------------------------------------------------
# 3. Threshold-edge -- FP rate just below / just above threshold
# ---------------------------------------------------------------------------

class TestThresholdEdge:
    """FP rate just below threshold produces no pattern; just above produces one."""

    def test_fp_rate_just_below_threshold(self, tmp_db: sqlite3.Connection):
        """69% FP rate (< 0.70 threshold) should NOT trigger the pattern.
        Setup: 69 FP + 31 TP = 100 total, fp_rate = 0.69."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        for i in range(1, 101):
            _insert_signal(conn, i, source_api="hacker_news")

        for i in range(1, 70):
            _insert_label(conn, i, "FP")
        for i in range(70, 101):
            _insert_label(conn, i, "TP")

        conn.commit()

        config = PatternConfig(days=30, min_count=10, fp_rate_threshold=0.70)
        patterns = detect_patterns(conn, config=config)
        source_patterns = [p for p in patterns if p["type"] == "source_api_fp_rate"]
        assert source_patterns == []

    def test_fp_rate_exactly_at_threshold(self, tmp_db: sqlite3.Connection):
        """70% FP rate (== 0.70 threshold) should trigger the pattern.
        Setup: 70 FP + 30 TP = 100 total, fp_rate = 0.70."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        for i in range(1, 101):
            _insert_signal(conn, i, source_api="hacker_news")

        for i in range(1, 71):
            _insert_label(conn, i, "FP")
        for i in range(71, 101):
            _insert_label(conn, i, "TP")

        conn.commit()

        config = PatternConfig(days=30, min_count=10, fp_rate_threshold=0.70)
        patterns = detect_patterns(conn, config=config)
        source_patterns = [p for p in patterns if p["type"] == "source_api_fp_rate"]
        assert len(source_patterns) == 1
        assert source_patterns[0]["fp_rate"] == pytest.approx(0.70, abs=0.001)

    def test_fp_rate_just_above_threshold(self, tmp_db: sqlite3.Connection):
        """71% FP rate (> 0.70 threshold) should trigger the pattern.
        Setup: 71 FP + 29 TP = 100 total, fp_rate = 0.71."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        for i in range(1, 101):
            _insert_signal(conn, i, source_api="hacker_news")

        for i in range(1, 72):
            _insert_label(conn, i, "FP")
        for i in range(72, 101):
            _insert_label(conn, i, "TP")

        conn.commit()

        config = PatternConfig(days=30, min_count=10, fp_rate_threshold=0.70)
        patterns = detect_patterns(conn, config=config)
        source_patterns = [p for p in patterns if p["type"] == "source_api_fp_rate"]
        assert len(source_patterns) == 1
        assert source_patterns[0]["fp_rate"] == pytest.approx(0.71, abs=0.001)

    def test_fp_count_below_min_count_no_pattern(self, tmp_db: sqlite3.Connection):
        """9 FP + 1 TP => 90% FP rate but fp count (9) < min_count (10) -- no pattern."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        for i in range(1, 11):
            _insert_signal(conn, i, source_api="hacker_news")

        for i in range(1, 10):
            _insert_label(conn, i, "FP")
        _insert_label(conn, 10, "TP")

        conn.commit()

        config = PatternConfig(days=30, min_count=10, fp_rate_threshold=0.70)
        patterns = detect_patterns(conn, config=config)
        source_patterns = [p for p in patterns if p["type"] == "source_api_fp_rate"]
        assert source_patterns == []


# ---------------------------------------------------------------------------
# 4. Duplicate suppression -- repeated FP descriptions
# ---------------------------------------------------------------------------

class TestDuplicateFpDescription:
    """Multiple signals with identical descriptions produce a
    'duplicate_fp_description' pattern."""

    def test_detects_duplicate_fp_descriptions(self, tmp_db: sqlite3.Connection):
        """12 FP signals with the same description should produce a
        duplicate_fp_description pattern (count >= min_count=10)."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        shared_desc = "AI-powered B2B SaaS platform for enterprise analytics"
        raw = json.dumps({"description": shared_desc})

        for i in range(1, 13):
            _insert_signal(conn, i, source_api="rss_feeds", raw_data=raw)
            _insert_label(conn, i, "FP")

        conn.commit()

        config = PatternConfig(days=30, min_count=10)
        patterns = detect_patterns(conn, config=config)
        dup_patterns = [p for p in patterns if p["type"] == "duplicate_fp_description"]
        assert len(dup_patterns) == 1

        pat = dup_patterns[0]
        assert pat["count"] == 12
        assert len(pat["example_signal_ids"]) <= 10
        assert "recommendation" in pat

    def test_normalized_descriptions_match(self, tmp_db: sqlite3.Connection):
        """Descriptions differing only in case/whitespace/punctuation should
        be treated as the same normalized description.

        Note: _norm_text strips punctuation characters entirely (no space
        left behind), so 'AI-Powered' -> 'aipowered'. We use variants that
        normalize identically."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        # All of these normalize to "ai powered b2b saas platform"
        variants = [
            "ai powered b2b saas platform",
            "AI POWERED B2B SAAS PLATFORM",
            "AI  POWERED  B2B  SaaS  Platform!",
            "  ai powered b2b saas platform  ",
            "Ai Powered B2b Saas Platform.",
        ]

        for i in range(1, 11):
            desc = variants[i % len(variants)]
            raw = json.dumps({"description": desc})
            _insert_signal(conn, i, source_api="rss_feeds", raw_data=raw)
            _insert_label(conn, i, "FP")

        conn.commit()

        config = PatternConfig(days=30, min_count=10)
        patterns = detect_patterns(conn, config=config)
        dup_patterns = [p for p in patterns if p["type"] == "duplicate_fp_description"]
        assert len(dup_patterns) == 1
        assert dup_patterns[0]["count"] == 10

    def test_no_duplicate_pattern_below_min_count(self, tmp_db: sqlite3.Connection):
        """9 identical FP descriptions < min_count=10 should not trigger."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        raw = json.dumps({"description": "Identical spam description"})
        for i in range(1, 10):
            _insert_signal(conn, i, source_api="rss_feeds", raw_data=raw)
            _insert_label(conn, i, "FP")

        conn.commit()

        config = PatternConfig(days=30, min_count=10)
        patterns = detect_patterns(conn, config=config)
        dup_patterns = [p for p in patterns if p["type"] == "duplicate_fp_description"]
        assert dup_patterns == []

    def test_tp_descriptions_not_counted(self, tmp_db: sqlite3.Connection):
        """Only FP descriptions are counted for the duplicate pattern;
        TP signals with the same description are excluded."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        raw = json.dumps({"description": "Shared description across labels"})
        # 5 FP + 10 TP with same description => only 5 FP, below min_count
        for i in range(1, 6):
            _insert_signal(conn, i, source_api="rss_feeds", raw_data=raw)
            _insert_label(conn, i, "FP")
        for i in range(6, 16):
            _insert_signal(conn, i, source_api="rss_feeds", raw_data=raw)
            _insert_label(conn, i, "TP")

        conn.commit()

        config = PatternConfig(days=30, min_count=10)
        patterns = detect_patterns(conn, config=config)
        dup_patterns = [p for p in patterns if p["type"] == "duplicate_fp_description"]
        assert dup_patterns == []


# ---------------------------------------------------------------------------
# 5. PatternConfig -- custom config overrides defaults
# ---------------------------------------------------------------------------

class TestPatternConfig:
    """Custom PatternConfig overrides defaults (days, min_count, fp_rate_threshold)."""

    def test_default_config_values(self):
        """Verify defaults: days=30, min_count=10, fp_rate_threshold=0.70."""
        cfg = PatternConfig()
        assert cfg.days == 30
        assert cfg.min_count == 10
        assert cfg.fp_rate_threshold == 0.70

    def test_custom_config_overrides(self):
        """Custom values override all defaults."""
        cfg = PatternConfig(days=7, min_count=3, fp_rate_threshold=0.50)
        assert cfg.days == 7
        assert cfg.min_count == 3
        assert cfg.fp_rate_threshold == 0.50

    def test_config_is_frozen(self):
        """PatternConfig is frozen (immutable dataclass)."""
        cfg = PatternConfig()
        with pytest.raises(AttributeError):
            cfg.days = 99  # type: ignore[misc]

    def test_lower_min_count_finds_more_patterns(self, tmp_db: sqlite3.Connection):
        """With min_count=3, a source with only 4 FP / 1 TP (80% FP rate) triggers."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        for i in range(1, 6):
            _insert_signal(conn, i, source_api="hacker_news")
        for i in range(1, 5):
            _insert_label(conn, i, "FP")
        _insert_label(conn, 5, "TP")

        conn.commit()

        # Default min_count=10: no pattern (only 4 FP)
        default = detect_patterns(conn, config=PatternConfig(days=30, min_count=10))
        assert [p for p in default if p["type"] == "source_api_fp_rate"] == []

        # Custom min_count=3: pattern found
        custom = detect_patterns(conn, config=PatternConfig(days=30, min_count=3))
        source_patterns = [p for p in custom if p["type"] == "source_api_fp_rate"]
        assert len(source_patterns) == 1
        assert source_patterns[0]["fp"] == 4

    def test_lower_threshold_finds_more_patterns(self, tmp_db: sqlite3.Connection):
        """With fp_rate_threshold=0.50, a 60% FP rate triggers that would not at 0.70."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        # 12 FP + 8 TP = 60% FP rate
        for i in range(1, 21):
            _insert_signal(conn, i, source_api="hacker_news")
        for i in range(1, 13):
            _insert_label(conn, i, "FP")
        for i in range(13, 21):
            _insert_label(conn, i, "TP")

        conn.commit()

        strict = detect_patterns(conn, config=PatternConfig(days=30, min_count=10, fp_rate_threshold=0.70))
        assert [p for p in strict if p["type"] == "source_api_fp_rate"] == []

        lenient = detect_patterns(conn, config=PatternConfig(days=30, min_count=10, fp_rate_threshold=0.50))
        source_patterns = [p for p in lenient if p["type"] == "source_api_fp_rate"]
        assert len(source_patterns) == 1
        assert source_patterns[0]["fp_rate"] == pytest.approx(0.60, abs=0.001)

    def test_narrow_days_window_excludes_old_signals(self, tmp_db: sqlite3.Connection):
        """With days=3, signals detected 10 days ago are excluded."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        # Insert 12 signals detected 10 days ago (outside days=3 window)
        for i in range(1, 13):
            _insert_signal(
                conn, i,
                source_api="hacker_news",
                detected_at=_days_ago_iso(10),
            )
            _insert_label(conn, i, "FP")

        # Insert 2 recent TP signals (inside days=3 window)
        for i in range(13, 15):
            _insert_signal(
                conn, i,
                source_api="hacker_news",
                detected_at=_days_ago_iso(1),
            )
            _insert_label(conn, i, "TP")

        conn.commit()

        # days=30: sees all 14 signals => 12 FP, fp_rate = 12/14 ~ 0.857, triggers
        wide = detect_patterns(conn, config=PatternConfig(days=30, min_count=10))
        assert len([p for p in wide if p["type"] == "source_api_fp_rate"]) == 1

        # days=3: sees only 2 recent TP signals => no FP => no pattern
        narrow = detect_patterns(conn, config=PatternConfig(days=3, min_count=10))
        assert narrow == []


# ---------------------------------------------------------------------------
# 6. Edge cases and multiple pattern types
# ---------------------------------------------------------------------------

class TestMultiplePatternsAndEdgeCases:
    """Tests for interactions between pattern types and edge cases."""

    def test_multiple_pattern_types_simultaneously(self, tmp_db: sqlite3.Connection):
        """A dataset can trigger both source_api_fp_rate and
        duplicate_fp_description at the same time."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        shared_desc = "Generic B2B enterprise SaaS tooling"
        raw = json.dumps({"description": shared_desc})

        # 12 FP from same source with identical descriptions + 2 TP
        for i in range(1, 13):
            _insert_signal(conn, i, source_api="hacker_news", raw_data=raw)
            _insert_label(conn, i, "FP")
        for i in range(13, 15):
            _insert_signal(conn, i, source_api="hacker_news")
            _insert_label(conn, i, "TP")

        conn.commit()

        config = PatternConfig(days=30, min_count=10)
        patterns = detect_patterns(conn, config=config)
        types = _pattern_types(patterns)

        assert "source_api_fp_rate" in types
        assert "duplicate_fp_description" in types

    def test_signals_from_multiple_sources(self, tmp_db: sqlite3.Connection):
        """Only the high-FP source gets flagged; the clean source does not."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        sid = 1
        # Noisy source: 11 FP + 1 TP
        for _ in range(11):
            _insert_signal(conn, sid, source_api="rss_feeds")
            _insert_label(conn, sid, "FP")
            sid += 1
        _insert_signal(conn, sid, source_api="rss_feeds")
        _insert_label(conn, sid, "TP")
        sid += 1

        # Clean source: 1 FP + 11 TP
        _insert_signal(conn, sid, source_api="greenhouse_jobs")
        _insert_label(conn, sid, "FP")
        sid += 1
        for _ in range(11):
            _insert_signal(conn, sid, source_api="greenhouse_jobs")
            _insert_label(conn, sid, "TP")
            sid += 1

        conn.commit()

        config = PatternConfig(days=30, min_count=10)
        patterns = detect_patterns(conn, config=config)
        source_patterns = [p for p in patterns if p["type"] == "source_api_fp_rate"]

        assert len(source_patterns) == 1
        assert source_patterns[0]["source_api"] == "rss_feeds"

    def test_no_raw_data_no_description_pattern(self, tmp_db: sqlite3.Connection):
        """Signals with null/empty raw_data should not crash or produce
        spurious duplicate_fp_description patterns."""
        conn = tmp_db
        conn.row_factory = sqlite3.Row

        for i in range(1, 12):
            _insert_signal(conn, i, source_api="hacker_news", raw_data=None)
            _insert_label(conn, i, "FP")

        conn.commit()

        config = PatternConfig(days=30, min_count=10)
        patterns = detect_patterns(conn, config=config)
        dup_patterns = [p for p in patterns if p["type"] == "duplicate_fp_description"]
        assert dup_patterns == []


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

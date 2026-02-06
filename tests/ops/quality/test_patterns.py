"""Tests for ops.quality.patterns -- FP pattern detection over labeled signals."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ops.quality.patterns import PatternConfig, detect_patterns, _norm_text
from ops.quality.labels import upsert_resolved_label
from tests.ops.quality.conftest import _insert_signal, _utc_iso


def _label_fp(conn: sqlite3.Connection, signal_id: int, canonical_key: str) -> None:
    """Convenience: upsert a resolved FP label for a signal."""
    upsert_resolved_label(
        conn,
        signal_id=signal_id,
        canonical_key=canonical_key,
        human_label="FP",
        label_source="manual",
    )


def _label_tp(conn: sqlite3.Connection, signal_id: int, canonical_key: str) -> None:
    """Convenience: upsert a resolved TP label for a signal."""
    upsert_resolved_label(
        conn,
        signal_id=signal_id,
        canonical_key=canonical_key,
        human_label="TP",
        label_source="manual",
    )


class TestDetectPatternsEmpty:
    """Tests for detect_patterns on an empty / minimal database."""

    def test_detect_patterns_empty_db(self, quality_db):
        """detect_patterns returns an empty list when no labeled signals exist."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        config = PatternConfig(days=30, min_count=5, fp_rate_threshold=0.5)
        patterns = detect_patterns(conn, config=config)

        assert patterns == []
        conn.close()


class TestSourceApiFpRatePattern:
    """Tests for the source_api_fp_rate pattern type."""

    def test_source_api_fp_rate_pattern(self, quality_db):
        """High FP rate from a single source triggers source_api_fp_rate pattern."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert 15 signals from a bad source, all labeled FP
        for i in range(15):
            sid = _insert_signal(
                conn,
                source_api="bad_source",
                canonical_key=f"domain:badsrc{i}.com",
                detected_at=_utc_iso(1),
            )
            _label_fp(conn, sid, f"domain:badsrc{i}.com")

        config = PatternConfig(days=30, min_count=5, fp_rate_threshold=0.5)
        patterns = detect_patterns(conn, config=config)

        source_patterns = [p for p in patterns if p["type"] == "source_api_fp_rate"]
        assert len(source_patterns) >= 1
        match = source_patterns[0]
        assert match["source_api"] == "bad_source"
        assert match["fp"] == 15
        assert match["fp_rate"] >= 0.5

        conn.close()


class TestSourceApiCategoryFpRatePattern:
    """Tests for the source_api_category_fp_rate pattern type."""

    def test_source_api_category_fp_rate_pattern(self, quality_db):
        """High FP rate for a source+category combo triggers source_api_category_fp_rate."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert 15 signals, label all FP, and add thesis_classifications with a
        # specific category so the source+category concentration fires.
        for i in range(15):
            sid = _insert_signal(
                conn,
                source_api="news_source",
                canonical_key=f"domain:newscat{i}.com",
                detected_at=_utc_iso(1),
            )
            _label_fp(conn, sid, f"domain:newscat{i}.com")

            # Insert into thesis_classifications for this signal
            conn.execute(
                """
                INSERT INTO thesis_classifications (
                    signal_id, canonical_key,
                    keyword_score, keyword_category, negative_keywords,
                    thesis_match, thesis_fit_score, category, stage_estimate,
                    confidence, rationale, key_signals,
                    prompt_version, model, input_tokens, output_tokens,
                    latency_ms, competitor_flag, competitor_match, classified_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sid,
                    f"domain:newscat{i}.com",
                    0.3,  # keyword_score
                    "consumer_cpg",  # keyword_category
                    "[]",  # negative_keywords
                    0,  # thesis_match (false)
                    0.2,  # thesis_fit_score
                    "consumer_cpg",  # category -- the thesis category
                    "Pre-Seed",  # stage_estimate
                    "low",  # confidence
                    "test rationale",  # rationale
                    "[]",  # key_signals
                    "test-v1",  # prompt_version
                    "test-model",  # model
                    None,  # input_tokens
                    None,  # output_tokens
                    100,  # latency_ms
                    0,  # competitor_flag
                    None,  # competitor_match
                    _utc_iso(0),  # classified_at
                ),
            )
            conn.commit()

        config = PatternConfig(days=30, min_count=3, fp_rate_threshold=0.5)
        patterns = detect_patterns(conn, config=config)

        cat_patterns = [p for p in patterns if p["type"] == "source_api_category_fp_rate"]
        assert len(cat_patterns) >= 1
        match = cat_patterns[0]
        assert match["source_api"] == "news_source"
        assert match["thesis_category"] == "consumer_cpg"
        assert match["fp_rate"] >= 0.5

        conn.close()


class TestDuplicateFpDescriptionPattern:
    """Tests for the duplicate_fp_description pattern type."""

    def test_duplicate_fp_description_pattern(self, quality_db):
        """Identical normalized descriptions across many FP signals trigger pattern."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert 15 signals all with the same description, labeled FP
        same_desc = "This is a duplicated spam description for testing"
        for i in range(15):
            sid = _insert_signal(
                conn,
                source_api="rss_feeds",
                canonical_key=f"domain:dupdesc{i}.com",
                raw_data=json.dumps({"description": same_desc}),
                detected_at=_utc_iso(1),
            )
            _label_fp(conn, sid, f"domain:dupdesc{i}.com")

        config = PatternConfig(days=30, min_count=5, fp_rate_threshold=0.5)
        patterns = detect_patterns(conn, config=config)

        dup_patterns = [p for p in patterns if p["type"] == "duplicate_fp_description"]
        assert len(dup_patterns) >= 1
        match = dup_patterns[0]
        assert match["count"] == 15
        assert len(match["example_signal_ids"]) > 0

        conn.close()


class TestTemporalHotspotPattern:
    """Tests for the fp_temporal_hotspot pattern type."""

    def test_temporal_hotspot_pattern(self, quality_db):
        """Many FP signals at the same hour triggers fp_temporal_hotspot pattern."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert 30 FP signals all at hour 14 UTC
        fixed_ts = "2026-01-15T14:00:00+00:00"
        for i in range(30):
            sid = _insert_signal(
                conn,
                source_api="hotspot_source",
                canonical_key=f"domain:hotspot{i}.com",
                detected_at=fixed_ts,
            )
            _label_fp(conn, sid, f"domain:hotspot{i}.com")

        config = PatternConfig(days=60, min_count=5, fp_rate_threshold=0.5)
        patterns = detect_patterns(conn, config=config)

        temporal_patterns = [p for p in patterns if p["type"] == "fp_temporal_hotspot"]
        assert len(temporal_patterns) >= 1
        match = temporal_patterns[0]
        assert match["source_api"] == "hotspot_source"
        assert match["hour_utc"] == 14
        assert match["fp_count"] == 30

        conn.close()


class TestWeakCanonicalKeysPattern:
    """Tests for the weak_canonical_keys_in_fp pattern type."""

    def test_weak_canonical_keys_pattern(self, quality_db):
        """FP signals with name_loc: canonical keys trigger weak_canonical_keys pattern."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert 20 FP signals with name_loc: canonical keys
        for i in range(20):
            ckey = f"name_loc:weakco{i}_new_york"
            sid = _insert_signal(
                conn,
                source_api="weak_src",
                canonical_key=ckey,
                detected_at=_utc_iso(1),
            )
            _label_fp(conn, sid, ckey)

        config = PatternConfig(days=30, min_count=5, fp_rate_threshold=0.5)
        patterns = detect_patterns(conn, config=config)

        weak_patterns = [p for p in patterns if p["type"] == "weak_canonical_keys_in_fp"]
        assert len(weak_patterns) >= 1
        match = weak_patterns[0]
        assert match["fp_count"] == 20
        assert match["share"] >= 0.50

        conn.close()


class TestPatternThresholds:
    """Tests for threshold / config behavior."""

    def test_no_pattern_below_threshold(self, quality_db):
        """Fewer FP signals than min_count should produce no patterns."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert only 2 FP signals (below min_count=10)
        for i in range(2):
            sid = _insert_signal(
                conn,
                source_api="rare_source",
                canonical_key=f"domain:rare{i}.com",
                detected_at=_utc_iso(1),
            )
            _label_fp(conn, sid, f"domain:rare{i}.com")

        config = PatternConfig(days=30, min_count=10, fp_rate_threshold=0.5)
        patterns = detect_patterns(conn, config=config)

        assert patterns == []
        conn.close()

    def test_pattern_config_custom(self, quality_db):
        """PatternConfig with custom values affects detection thresholds."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert 6 FP signals -- this should pass min_count=5 but not min_count=10
        for i in range(6):
            sid = _insert_signal(
                conn,
                source_api="custom_source",
                canonical_key=f"domain:custom{i}.com",
                detected_at=_utc_iso(1),
            )
            _label_fp(conn, sid, f"domain:custom{i}.com")

        # With min_count=5, should detect
        config_low = PatternConfig(days=30, min_count=5, fp_rate_threshold=0.5)
        patterns_low = detect_patterns(conn, config=config_low)
        source_low = [p for p in patterns_low if p["type"] == "source_api_fp_rate"]
        assert len(source_low) >= 1

        # With min_count=10, should NOT detect
        config_high = PatternConfig(days=30, min_count=10, fp_rate_threshold=0.5)
        patterns_high = detect_patterns(conn, config=config_high)
        source_high = [p for p in patterns_high if p["type"] == "source_api_fp_rate" and p["source_api"] == "custom_source"]
        assert len(source_high) == 0

        conn.close()


class TestMultiplePatterns:
    """Tests for detecting multiple pattern types simultaneously."""

    def test_multiple_pattern_types(self, quality_db):
        """When data supports multiple patterns, both are found."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Pattern 1: source_api_fp_rate from "multi_source_a"
        for i in range(10):
            sid = _insert_signal(
                conn,
                source_api="multi_source_a",
                canonical_key=f"domain:multi_a{i}.com",
                detected_at=_utc_iso(1),
            )
            _label_fp(conn, sid, f"domain:multi_a{i}.com")

        # Pattern 2: duplicate_fp_description from "multi_source_b"
        same_desc = "Exact same spam description repeated many times"
        for i in range(10):
            sid = _insert_signal(
                conn,
                source_api="multi_source_b",
                canonical_key=f"domain:multi_b{i}.com",
                raw_data=json.dumps({"description": same_desc}),
                detected_at=_utc_iso(1),
            )
            _label_fp(conn, sid, f"domain:multi_b{i}.com")

        config = PatternConfig(days=30, min_count=5, fp_rate_threshold=0.5)
        patterns = detect_patterns(conn, config=config)

        pattern_types = {p["type"] for p in patterns}
        assert "source_api_fp_rate" in pattern_types
        assert "duplicate_fp_description" in pattern_types

        conn.close()


class TestNormText:
    """Tests for the _norm_text helper function."""

    def test_helper_norm_text(self):
        """_norm_text collapses whitespace, strips punctuation, and lowercases."""
        # Basic lowercasing
        assert _norm_text("Hello World") == "hello world"

        # Whitespace collapsing
        assert _norm_text("  lots   of    spaces  ") == "lots of spaces"

        # Punctuation removal
        assert _norm_text("Hello, World! How's it?") == "hello world hows it"

        # Empty / None
        assert _norm_text("") == ""
        assert _norm_text(None) == ""

        # Tabs and newlines
        assert _norm_text("line1\n\tline2") == "line1 line2"

        # Mixed case with numbers
        assert _norm_text("Test123 ABC") == "test123 abc"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

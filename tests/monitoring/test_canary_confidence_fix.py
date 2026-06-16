"""Tests for canary confidence scoring fix (S3 strategy).

The canary should use COALESCE(thesis_fit_score, keyword_score) instead of
COALESCE(thesis_fit_score, signals.confidence). Signals with neither score
should be skipped (not failed).

This fixes the domain mismatch where collector traction confidence (0.55-0.75)
was evaluated against thesis fit ranges (FP: 0.0-0.4).
"""

import asyncio
import pytest
import aiosqlite

from monitoring.canary_checker import (
    CanaryChecker,
    GoldenSet,
    CanaryResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _setup_db(db: aiosqlite.Connection) -> None:
    """Create minimal schema for canary tests."""
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY,
            canonical_key TEXT,
            confidence REAL,
            signal_type TEXT DEFAULT 'test',
            source_api TEXT DEFAULT 'test',
            company_name TEXT DEFAULT 'test',
            raw_data TEXT DEFAULT '{}',
            detected_at TEXT DEFAULT '2026-01-01',
            created_at TEXT DEFAULT '2026-01-01'
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS thesis_classifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            thesis_fit_score REAL,
            keyword_score REAL,
            category TEXT,
            rationale TEXT,
            model TEXT,
            classified_at TEXT DEFAULT '2026-01-01'
        )
    """)
    await db.commit()


class FakeStore:
    """Minimal store stub for canary tests."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db


# ---------------------------------------------------------------------------
# Test: FP signal with keyword_score only (no thesis_fit_score) should use
# keyword_score as the actual confidence, NOT fall back to signals.confidence.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fp_with_keyword_score_only_uses_keyword_score():
    """FP signal with keyword_score=0.05 (NULL thesis_fit_score) should PASS
    canary (expected FP range 0.0-0.4). Currently fails because canary
    falls back to signals.confidence=0.65."""
    async with aiosqlite.connect(":memory:") as db:
        await _setup_db(db)

        # Insert FP signal with high collector confidence
        await db.execute(
            "INSERT INTO signals (id, canonical_key, confidence) VALUES (1, 'hn:test-fp', 0.65)"
        )
        # Keyword-only thesis classification (thesis_fit_score NULL)
        await db.execute(
            """INSERT INTO thesis_classifications
               (signal_id, thesis_fit_score, keyword_score, category, model, classified_at)
               VALUES (1, NULL, 0.05, NULL, NULL, '2026-01-01')"""
        )
        await db.commit()

        gs = GoldenSet()
        gs.add_fp(1, "hn:test-fp")  # expected range 0.0-0.4

        checker = CanaryChecker(gs)
        result = await checker.run(FakeStore(db))

        # The FP signal should PASS because keyword_score=0.05 is within 0.0-0.4
        assert result.passed == 1, (
            f"Expected FP signal to pass with keyword_score=0.05, "
            f"but got passed={result.passed}, failed={result.failed}"
        )
        assert result.results[0].passed is True
        assert result.results[0].actual_confidence == pytest.approx(0.05, abs=0.01)


# ---------------------------------------------------------------------------
# Test: Signal with valid thesis_fit_score should still use thesis_fit_score.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signal_with_thesis_fit_score_uses_it():
    """Signal with thesis_fit_score=0.85 should use thesis_fit_score,
    not keyword_score or signals.confidence."""
    async with aiosqlite.connect(":memory:") as db:
        await _setup_db(db)

        await db.execute(
            "INSERT INTO signals (id, canonical_key, confidence) VALUES (2, 'gh:test-tp', 0.50)"
        )
        await db.execute(
            """INSERT INTO thesis_classifications
               (signal_id, thesis_fit_score, keyword_score, category, model, classified_at)
               VALUES (2, 0.85, 0.60, 'consumer_cpg', 'gemini-3.5-flash', '2026-01-01')"""
        )
        await db.commit()

        gs = GoldenSet()
        gs.add_tp(2, "gh:test-tp")  # expected range 0.6-1.0

        checker = CanaryChecker(gs)
        result = await checker.run(FakeStore(db))

        assert result.passed == 1
        assert result.results[0].actual_confidence == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# Test: Signal with NO thesis_classifications at all should be SKIPPED.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signal_without_any_thesis_classification_is_skipped():
    """Signal with no thesis_classifications row at all should be skipped,
    not evaluated against collector confidence."""
    async with aiosqlite.connect(":memory:") as db:
        await _setup_db(db)

        await db.execute(
            "INSERT INTO signals (id, canonical_key, confidence) VALUES (3, 'rss:no-thesis', 0.60)"
        )
        await db.commit()

        gs = GoldenSet()
        gs.add_fp(3, "rss:no-thesis")

        checker = CanaryChecker(gs)
        result = await checker.run(FakeStore(db))

        # Should be skipped, not failed
        assert result.skipped == 1, (
            f"Expected signal without thesis classification to be skipped, "
            f"but got skipped={result.skipped}, failed={result.failed}"
        )
        assert result.results[0].actual_confidence is None
        assert result.results[0].reason in ("no_thesis_score", "skipped")


# ---------------------------------------------------------------------------
# Test: Skip rate warning when too many signals lack scores.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_rate_tracked_in_result():
    """Canary result should track skip count for monitoring."""
    async with aiosqlite.connect(":memory:") as db:
        await _setup_db(db)

        # 3 signals: 1 with thesis_fit_score, 1 with keyword_score only, 1 with nothing
        await db.execute("INSERT INTO signals (id, canonical_key, confidence) VALUES (10, 'a', 0.70)")
        await db.execute("INSERT INTO signals (id, canonical_key, confidence) VALUES (11, 'b', 0.65)")
        await db.execute("INSERT INTO signals (id, canonical_key, confidence) VALUES (12, 'c', 0.60)")

        # Signal 10: has thesis_fit_score
        await db.execute(
            """INSERT INTO thesis_classifications
               (signal_id, thesis_fit_score, keyword_score, classified_at)
               VALUES (10, 0.0, 0.0, '2026-01-01')"""
        )
        # Signal 11: keyword_score only
        await db.execute(
            """INSERT INTO thesis_classifications
               (signal_id, thesis_fit_score, keyword_score, classified_at)
               VALUES (11, NULL, 0.05, '2026-01-01')"""
        )
        # Signal 12: nothing (no thesis_classifications row)
        await db.commit()

        gs = GoldenSet()
        gs.add_fp(10, "a")
        gs.add_fp(11, "b")
        gs.add_fp(12, "c")

        checker = CanaryChecker(gs)
        result = await checker.run(FakeStore(db))

        # Signal 10: thesis_fit_score=0.0, passes (0.0 within 0.0-0.4)
        # Signal 11: keyword_score=0.05, should pass (0.05 within 0.0-0.4) -- THIS IS THE FIX
        # Signal 12: no score, should be skipped
        assert result.skipped == 1, f"Expected 1 skipped, got {result.skipped}"
        assert result.passed == 2, f"Expected 2 passed, got {result.passed}"
        assert result.total == 3


# ---------------------------------------------------------------------------
# Test: Mixed TP/FP golden set produces correct pass rate with new scoring.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mixed_golden_set_pass_rate():
    """Mixed TP/FP golden set should produce high pass rate when using
    thesis_fit_score/keyword_score instead of raw confidence."""
    async with aiosqlite.connect(":memory:") as db:
        await _setup_db(db)

        # 2 TP signals with thesis_fit_score
        await db.execute("INSERT INTO signals (id, canonical_key, confidence) VALUES (20, 'tp1', 0.50)")
        await db.execute("INSERT INTO signals (id, canonical_key, confidence) VALUES (21, 'tp2', 0.55)")
        # 3 FP signals with keyword_score only (thesis_fit_score NULL)
        await db.execute("INSERT INTO signals (id, canonical_key, confidence) VALUES (22, 'fp1', 0.70)")
        await db.execute("INSERT INTO signals (id, canonical_key, confidence) VALUES (23, 'fp2', 0.65)")
        await db.execute("INSERT INTO signals (id, canonical_key, confidence) VALUES (24, 'fp3', 0.75)")

        # TP thesis classifications (LLM scored)
        await db.execute(
            """INSERT INTO thesis_classifications
               (signal_id, thesis_fit_score, keyword_score, model, classified_at)
               VALUES (20, 0.80, 0.50, 'gemini-3.5-flash', '2026-01-01')"""
        )
        await db.execute(
            """INSERT INTO thesis_classifications
               (signal_id, thesis_fit_score, keyword_score, model, classified_at)
               VALUES (21, 0.70, 0.45, 'gemini-3.5-flash', '2026-01-01')"""
        )
        # FP keyword-only classifications
        await db.execute(
            """INSERT INTO thesis_classifications
               (signal_id, thesis_fit_score, keyword_score, classified_at)
               VALUES (22, NULL, 0.02, '2026-01-01')"""
        )
        await db.execute(
            """INSERT INTO thesis_classifications
               (signal_id, thesis_fit_score, keyword_score, classified_at)
               VALUES (23, NULL, 0.08, '2026-01-01')"""
        )
        await db.execute(
            """INSERT INTO thesis_classifications
               (signal_id, thesis_fit_score, keyword_score, classified_at)
               VALUES (24, NULL, 0.01, '2026-01-01')"""
        )
        await db.commit()

        gs = GoldenSet()
        gs.add_tp(20, "tp1")
        gs.add_tp(21, "tp2")
        gs.add_fp(22, "fp1")
        gs.add_fp(23, "fp2")
        gs.add_fp(24, "fp3")

        checker = CanaryChecker(gs)
        result = await checker.run(FakeStore(db))

        # All 5 should be scorable (no skips)
        assert result.skipped == 0
        # All 5 should pass: TP thesis_fit_scores in 0.6-1.0, FP keyword_scores in 0.0-0.4
        assert result.passed == 5, (
            f"Expected all 5 to pass, got passed={result.passed}, failed={result.failed}. "
            f"Results: {[(r.signal_id, r.actual_confidence, r.passed, r.reason) for r in result.results]}"
        )
        assert result.pass_rate >= 0.80
        assert result.verdict == "pass"

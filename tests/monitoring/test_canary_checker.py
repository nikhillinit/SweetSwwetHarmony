"""Tests for canary framework scaffold (monitoring/canary_checker.py)."""

import os
import tempfile

import pytest
import pytest_asyncio

from storage.signal_store import SignalStore
from monitoring.canary_checker import (
    CanaryChecker,
    CanaryResult,
    CanaryRunResult,
    GoldenSet,
    GoldenSignal,
)


@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with temp file DB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = SignalStore(db_path=path)
    await store.initialize()
    yield store
    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


async def _insert_signal(store, signal_id, confidence, canonical_key="domain:test.com"):
    """Insert a test signal with given ID and confidence."""
    db = store._db
    await db.execute(
        """
        INSERT OR REPLACE INTO signals (id, signal_type, source_api, canonical_key,
                                        company_name, confidence, raw_data,
                                        detected_at, created_at)
        VALUES (?, 'test', 'test', ?, 'Test Corp', ?, '{}',
                datetime('now'), datetime('now'))
        """,
        (signal_id, canonical_key, confidence),
    )
    await db.commit()


# ============================================================================
# GoldenSet
# ============================================================================


class TestGoldenSet:
    def test_add_tp(self):
        gs = GoldenSet()
        gs.add_tp(1, "domain:acme.com", min_confidence=0.7)
        assert len(gs) == 1
        assert gs.signals[0].expected_label == "TP"
        assert gs.signals[0].expected_confidence_min == 0.7

    def test_add_fp(self):
        gs = GoldenSet()
        gs.add_fp(2, "domain:spam.com", max_confidence=0.3)
        assert len(gs) == 1
        assert gs.signals[0].expected_label == "FP"
        assert gs.signals[0].expected_confidence_max == 0.3

    def test_to_dict_and_from_list(self):
        gs = GoldenSet()
        gs.add_tp(1, "domain:a.com")
        gs.add_fp(2, "domain:b.com")
        data = gs.to_dict()
        assert len(data) == 2

        restored = GoldenSet.from_list(data)
        assert len(restored) == 2
        assert restored.signals[0].expected_label == "TP"
        assert restored.signals[1].expected_label == "FP"

    def test_empty_set(self):
        gs = GoldenSet()
        assert len(gs) == 0
        assert gs.signals == []


# ============================================================================
# CanaryChecker — scoring
# ============================================================================


class TestCanaryChecker:
    @pytest.mark.asyncio
    async def test_all_pass(self, store):
        await _insert_signal(store, 1, 0.8, "domain:good.com")
        await _insert_signal(store, 2, 0.1, "domain:bad.com")

        gs = GoldenSet()
        gs.add_tp(1, "domain:good.com", min_confidence=0.6, max_confidence=1.0)
        gs.add_fp(2, "domain:bad.com", max_confidence=0.4)

        checker = CanaryChecker(gs)
        result = await checker.run(store)

        assert result.total == 2
        assert result.passed == 2
        assert result.failed == 0
        assert result.verdict == "pass"
        assert result.pass_rate == 1.0

    @pytest.mark.asyncio
    async def test_some_fail(self, store):
        await _insert_signal(store, 1, 0.3, "domain:drifted.com")  # Was TP, now low
        await _insert_signal(store, 2, 0.1, "domain:stable.com")

        gs = GoldenSet()
        gs.add_tp(1, "domain:drifted.com", min_confidence=0.7, max_confidence=1.0)
        gs.add_fp(2, "domain:stable.com", max_confidence=0.4)

        checker = CanaryChecker(gs, pass_rate_threshold=0.9)
        result = await checker.run(store)

        assert result.total == 2
        assert result.passed == 1  # Only the FP passes
        assert result.failed == 1
        assert result.verdict == "fail"  # 50% < 90% threshold

    @pytest.mark.asyncio
    async def test_missing_signal_skipped(self, store):
        # Signal 999 doesn't exist
        gs = GoldenSet()
        gs.add_tp(999, "domain:missing.com", min_confidence=0.5)

        checker = CanaryChecker(gs)
        result = await checker.run(store)

        assert result.total == 1
        assert result.skipped == 1
        assert result.results[0].reason == "signal_not_found"

    @pytest.mark.asyncio
    async def test_missing_signal_is_skipped(self, store):
        """A golden set signal with no matching DB row is skipped."""
        gs = GoldenSet()
        gs.add_tp(9999, "domain:ghost.com")

        checker = CanaryChecker(gs)
        result = await checker.run(store)
        assert result.skipped == 1
        assert result.results[0].reason == "signal_not_found"
        assert result.results[0].actual_confidence is None

    @pytest.mark.asyncio
    async def test_degraded_verdict(self, store):
        """Degraded = pass rate below threshold but above 80% of threshold."""
        await _insert_signal(store, 1, 0.8, "domain:a.com")
        await _insert_signal(store, 2, 0.8, "domain:b.com")
        await _insert_signal(store, 3, 0.8, "domain:c.com")
        await _insert_signal(store, 4, 0.1, "domain:d.com")  # TP but low → fail
        await _insert_signal(store, 5, 0.8, "domain:e.com")

        gs = GoldenSet()
        for i in range(1, 6):
            gs.add_tp(i, f"domain:{chr(96+i)}.com", min_confidence=0.6)

        # 4 pass, 1 fail = 80%. Threshold 0.9 → below, but 0.8 >= 0.9*0.8=0.72 → degraded
        checker = CanaryChecker(gs, pass_rate_threshold=0.9)
        result = await checker.run(store)
        assert result.verdict == "degraded"

    @pytest.mark.asyncio
    async def test_delta_computation(self, store):
        await _insert_signal(store, 1, 0.9, "domain:a.com")

        gs = GoldenSet()
        gs.add_tp(1, "domain:a.com", min_confidence=0.6, max_confidence=1.0)

        checker = CanaryChecker(gs)
        result = await checker.run(store)
        # Midpoint of (0.6, 1.0) = 0.8, actual = 0.9, delta = +0.1
        assert result.results[0].delta == pytest.approx(0.1, abs=0.001)

    @pytest.mark.asyncio
    async def test_empty_golden_set(self, store):
        gs = GoldenSet()
        checker = CanaryChecker(gs)
        result = await checker.run(store)
        assert result.total == 0
        assert result.verdict == "no_data"

    @pytest.mark.asyncio
    async def test_run_timing(self, store):
        await _insert_signal(store, 1, 0.8, "domain:a.com")
        gs = GoldenSet()
        gs.add_tp(1, "domain:a.com")
        checker = CanaryChecker(gs)
        result = await checker.run(store)
        assert result.duration_ms >= 0
        assert result.run_at != ""

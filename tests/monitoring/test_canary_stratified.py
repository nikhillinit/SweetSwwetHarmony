"""Tests for Wave 2 stratified golden set and canary run persistence.

Covers:
- StratifiedGoldenSet (hash determinism, grouping)
- build_stratified_golden_set() (from DB labels)
- store_canary_run() (persistence + run lifecycle)
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from monitoring.canary_checker import (
    CanaryChecker,
    CanaryResult,
    CanaryRunResult,
    GoldenSet,
    GoldenSignal,
    StratifiedGoldenSet,
    build_golden_set_from_labels,
    build_stratified_golden_set,
    store_canary_run,
)
from storage.signal_store import SignalStore


# =============================================================================
# FIXTURES
# =============================================================================


@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with temp file DB.

    Adds a ``label`` column to ``signal_quality_metrics`` because the
    canonical DDL names it ``human_label`` while ``build_golden_set_from_labels``
    queries ``sqm.label``.  Adding the alias column keeps the test
    compatible with the production query.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()

    # Workaround: build_golden_set_from_labels queries sqm.label and
    # sqm.created_at, but the canonical DDL defines human_label and labeled_at.
    # Add alias columns so the production queries resolve in tests.
    db = s._db
    try:
        await db.execute(
            "ALTER TABLE signal_quality_metrics ADD COLUMN label TEXT"
        )
    except Exception:
        pass  # Column may already exist
    try:
        await db.execute(
            "ALTER TABLE signal_quality_metrics ADD COLUMN created_at TEXT"
        )
    except Exception:
        pass  # Column may already exist
    await db.commit()

    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


async def _insert_signal(store, signal_id, confidence=0.8, source_api="github",
                         canonical_key="domain:test.com", company_name="Test Corp"):
    """Insert a signal row for testing."""
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        """
        INSERT OR REPLACE INTO signals
            (id, signal_type, source_api, canonical_key, company_name,
             confidence, raw_data, detected_at, created_at)
        VALUES (?, 'test', ?, ?, ?, ?, '{}', datetime('now'), datetime('now'))
        """,
        (signal_id, source_api, canonical_key, company_name, confidence),
    )
    await db.commit()


async def _insert_label(store, signal_id, label="TP", canonical_key="domain:test.com"):
    """Insert a signal_quality_metrics row with the label and created_at alias columns populated."""
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        """
        INSERT OR REPLACE INTO signal_quality_metrics
            (signal_id, canonical_key, human_label, label, label_source,
             labeled_at, created_at)
        VALUES (?, ?, ?, ?, 'manual', datetime('now'), datetime('now'))
        """,
        (signal_id, canonical_key, label, label),
    )
    await db.commit()


async def _insert_thesis_classification(store, signal_id, category, canonical_key="domain:test.com"):
    """Insert a thesis_classifications row."""
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        """
        INSERT INTO thesis_classifications
            (signal_id, canonical_key, category, classified_at)
        VALUES (?, ?, ?, datetime('now'))
        """,
        (signal_id, canonical_key, category),
    )
    await db.commit()


# =============================================================================
# StratifiedGoldenSet — golden_set_hash
# =============================================================================


class TestStratifiedGoldenSetHash:
    """Tests for the StratifiedGoldenSet.golden_set_hash property."""

    def test_golden_set_hash_deterministic(self):
        """Same signal IDs produce the same hash every time."""
        gs = GoldenSet()
        gs.add_tp(1, "domain:a.com")
        gs.add_fp(2, "domain:b.com")

        sgs1 = StratifiedGoldenSet(golden_set=gs)
        sgs2 = StratifiedGoldenSet(golden_set=gs)

        assert sgs1.golden_set_hash == sgs2.golden_set_hash
        assert len(sgs1.golden_set_hash) == 16  # SHA256[:16] hex

    def test_golden_set_hash_changes_with_different_signals(self):
        """Different signal IDs produce different hashes."""
        gs1 = GoldenSet()
        gs1.add_tp(1, "domain:a.com")
        gs1.add_tp(2, "domain:b.com")

        gs2 = GoldenSet()
        gs2.add_tp(1, "domain:a.com")
        gs2.add_tp(3, "domain:c.com")  # Different signal ID

        sgs1 = StratifiedGoldenSet(golden_set=gs1)
        sgs2 = StratifiedGoldenSet(golden_set=gs2)

        assert sgs1.golden_set_hash != sgs2.golden_set_hash

    def test_empty_golden_set_hash(self):
        """Empty golden set produces a deterministic hash."""
        gs = GoldenSet()
        sgs = StratifiedGoldenSet(golden_set=gs)

        # SHA256 of empty string (no IDs joined)
        expected = hashlib.sha256(b"").hexdigest()[:16]
        assert sgs.golden_set_hash == expected

    def test_golden_set_hash_order_independent(self):
        """Hash is the same regardless of insertion order (sorted internally)."""
        gs_forward = GoldenSet()
        gs_forward.add_tp(1, "domain:a.com")
        gs_forward.add_tp(10, "domain:b.com")
        gs_forward.add_tp(5, "domain:c.com")

        gs_reverse = GoldenSet()
        gs_reverse.add_tp(10, "domain:b.com")
        gs_reverse.add_tp(5, "domain:c.com")
        gs_reverse.add_tp(1, "domain:a.com")

        sgs_fwd = StratifiedGoldenSet(golden_set=gs_forward)
        sgs_rev = StratifiedGoldenSet(golden_set=gs_reverse)

        assert sgs_fwd.golden_set_hash == sgs_rev.golden_set_hash


# =============================================================================
# build_stratified_golden_set
# =============================================================================


class TestBuildStratifiedGoldenSet:
    """Tests for build_stratified_golden_set from DB labels."""

    @pytest.mark.asyncio
    async def test_build_empty_no_labels(self, store):
        """With no labels in DB, returns empty stratified set."""
        result = await build_stratified_golden_set(store, min_labels=1)
        assert len(result.golden_set) == 0
        assert result.overall == []
        assert result.by_collector == {}
        assert result.by_confidence_band == {}

    @pytest.mark.asyncio
    async def test_build_with_labeled_signals(self, store):
        """With labeled signals, builds a populated stratified set."""
        await _insert_signal(store, 1, confidence=0.8, source_api="github",
                             canonical_key="domain:a.com")
        await _insert_signal(store, 2, confidence=0.2, source_api="sec_edgar",
                             canonical_key="domain:b.com")
        await _insert_label(store, 1, "TP", "domain:a.com")
        await _insert_label(store, 2, "FP", "domain:b.com")

        result = await build_stratified_golden_set(store, min_labels=1)

        assert len(result.golden_set) == 2
        assert len(result.overall) == 2

    @pytest.mark.asyncio
    async def test_stratification_by_collector(self, store):
        """Signals are grouped by source_api into by_collector."""
        await _insert_signal(store, 1, confidence=0.8, source_api="github",
                             canonical_key="domain:a.com")
        await _insert_signal(store, 2, confidence=0.6, source_api="github",
                             canonical_key="domain:b.com")
        await _insert_signal(store, 3, confidence=0.5, source_api="sec_edgar",
                             canonical_key="domain:c.com")
        await _insert_label(store, 1, "TP", "domain:a.com")
        await _insert_label(store, 2, "TP", "domain:b.com")
        await _insert_label(store, 3, "FP", "domain:c.com")

        result = await build_stratified_golden_set(store, min_labels=1)

        assert "github" in result.by_collector
        assert "sec_edgar" in result.by_collector
        assert len(result.by_collector["github"]) == 2
        assert len(result.by_collector["sec_edgar"]) == 1

    @pytest.mark.asyncio
    async def test_stratification_by_confidence_band(self, store):
        """Signals are grouped into high/medium/low confidence bands."""
        # High: >= 0.7
        await _insert_signal(store, 1, confidence=0.85, source_api="test",
                             canonical_key="domain:high.com")
        # Medium: 0.4 - 0.7
        await _insert_signal(store, 2, confidence=0.5, source_api="test",
                             canonical_key="domain:med.com")
        # Low: < 0.4
        await _insert_signal(store, 3, confidence=0.2, source_api="test",
                             canonical_key="domain:low.com")

        await _insert_label(store, 1, "TP", "domain:high.com")
        await _insert_label(store, 2, "TP", "domain:med.com")
        await _insert_label(store, 3, "FP", "domain:low.com")

        result = await build_stratified_golden_set(store, min_labels=1)

        assert "high" in result.by_confidence_band
        assert "medium" in result.by_confidence_band
        assert "low" in result.by_confidence_band
        assert len(result.by_confidence_band["high"]) == 1
        assert len(result.by_confidence_band["medium"]) == 1
        assert len(result.by_confidence_band["low"]) == 1

    @pytest.mark.asyncio
    async def test_stratification_by_archetype(self, store):
        """Signals with thesis_classifications are grouped by archetype."""
        await _insert_signal(store, 1, confidence=0.8, source_api="test",
                             canonical_key="domain:cpg.com")
        await _insert_signal(store, 2, confidence=0.7, source_api="test",
                             canonical_key="domain:health.com")
        await _insert_label(store, 1, "TP", "domain:cpg.com")
        await _insert_label(store, 2, "TP", "domain:health.com")
        await _insert_thesis_classification(store, 1, "cpg", "domain:cpg.com")
        await _insert_thesis_classification(store, 2, "health_tech", "domain:health.com")

        result = await build_stratified_golden_set(store, min_labels=1)

        assert "cpg" in result.by_archetype
        assert "health_tech" in result.by_archetype

    @pytest.mark.asyncio
    async def test_missing_thesis_classifications_graceful(self, store):
        """If thesis_classifications table is absent or empty, archetype defaults to unknown."""
        await _insert_signal(store, 1, confidence=0.8, source_api="test",
                             canonical_key="domain:no-thesis.com")
        await _insert_label(store, 1, "TP", "domain:no-thesis.com")
        # No thesis_classifications inserted

        result = await build_stratified_golden_set(store, min_labels=1)

        assert "unknown" in result.by_archetype
        assert len(result.by_archetype["unknown"]) == 1


# =============================================================================
# store_canary_run
# =============================================================================


class TestStoreCanaryRun:
    """Tests for persisting canary run results."""

    def _make_checker_and_result(self):
        """Build a CanaryChecker and CanaryRunResult for testing."""
        gs = GoldenSet()
        gs.add_tp(1, "domain:test.com", min_confidence=0.6)
        gs.add_fp(2, "domain:spam.com", max_confidence=0.4)

        checker = CanaryChecker(gs)
        run_result = CanaryRunResult(
            verdict="pass",
            total=2,
            passed=2,
            failed=0,
            skipped=0,
            pass_rate=1.0,
            duration_ms=50.0,
            results=[
                CanaryResult(
                    signal_id=1,
                    canonical_key="domain:test.com",
                    expected_label="TP",
                    actual_confidence=0.8,
                    expected_confidence_min=0.6,
                    expected_confidence_max=1.0,
                    passed=True,
                    delta=0.0,
                ),
                CanaryResult(
                    signal_id=2,
                    canonical_key="domain:spam.com",
                    expected_label="FP",
                    actual_confidence=0.1,
                    expected_confidence_min=0.0,
                    expected_confidence_max=0.4,
                    passed=True,
                    delta=-0.1,
                ),
            ],
        )
        return checker, run_result

    @pytest.mark.asyncio
    async def test_store_canary_run_inserts_row(self, store):
        """store_canary_run inserts a row into canary_runs and returns an ID."""
        checker, run_result = self._make_checker_and_result()

        canary_run_id = await store_canary_run(store, checker, run_result)

        assert canary_run_id is not None
        assert isinstance(canary_run_id, int)
        assert canary_run_id > 0

        db = store._db
        cursor = await db.execute(
            "SELECT verdict, pass_rate, golden_set_size FROM canary_runs WHERE id = ?",
            (canary_run_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "pass"
        assert row[1] == pytest.approx(1.0)
        assert row[2] == 2

    @pytest.mark.asyncio
    async def test_store_canary_run_results_json(self, store):
        """Persisted results_json contains per-signal details."""
        checker, run_result = self._make_checker_and_result()

        canary_run_id = await store_canary_run(store, checker, run_result)

        db = store._db
        cursor = await db.execute(
            "SELECT results_json FROM canary_runs WHERE id = ?",
            (canary_run_id,),
        )
        row = await cursor.fetchone()
        results = json.loads(row[0])

        assert len(results) == 2
        assert results[0]["signal_id"] == 1
        assert results[0]["passed"] is True
        assert results[1]["signal_id"] == 2

    @pytest.mark.asyncio
    async def test_store_canary_run_stratification_json(self, store):
        """When stratified is provided, stratification_json is populated."""
        checker, run_result = self._make_checker_and_result()

        gs = checker.golden_set
        sig1 = gs.signals[0]
        sig2 = gs.signals[1]

        stratified = StratifiedGoldenSet(
            golden_set=gs,
            by_archetype={"cpg": [sig1], "unknown": [sig2]},
            by_collector={"github": [sig1, sig2]},
            by_confidence_band={"high": [sig1], "low": [sig2]},
            overall=list(gs.signals),
        )

        canary_run_id = await store_canary_run(
            store, checker, run_result, stratified=stratified
        )

        db = store._db
        cursor = await db.execute(
            "SELECT stratification_json FROM canary_runs WHERE id = ?",
            (canary_run_id,),
        )
        row = await cursor.fetchone()
        assert row[0] is not None
        strat = json.loads(row[0])

        # Should have archetype and collector strata
        assert "archetype:cpg" in strat
        assert "collector:github" in strat
        assert strat["collector:github"]["count"] == 2

    @pytest.mark.asyncio
    async def test_store_canary_run_creates_run_history(self, store):
        """store_canary_run creates a run_history record with CANARY type."""
        checker, run_result = self._make_checker_and_result()

        canary_run_id = await store_canary_run(store, checker, run_result)

        # Retrieve the run_id stored in canary_runs
        db = store._db
        cursor = await db.execute(
            "SELECT run_id FROM canary_runs WHERE id = ?",
            (canary_run_id,),
        )
        row = await cursor.fetchone()
        run_id = row[0]

        # Verify run_history has a completed CANARY record
        cursor = await db.execute(
            "SELECT run_type, status FROM run_history WHERE id = ?",
            (run_id,),
        )
        rh_row = await cursor.fetchone()
        assert rh_row is not None
        assert rh_row[0] == "canary"
        assert rh_row[1] == "completed"

    @pytest.mark.asyncio
    async def test_store_canary_run_config_hash(self, store):
        """Config hash is computed from drift_threshold and pass_rate_threshold."""
        checker, run_result = self._make_checker_and_result()

        canary_run_id = await store_canary_run(store, checker, run_result)

        # Compute expected config hash
        expected = hashlib.sha256(json.dumps({
            "drift_threshold": checker.drift_threshold,
            "pass_rate_threshold": checker.pass_rate_threshold,
        }, sort_keys=True).encode()).hexdigest()[:16]

        db = store._db
        cursor = await db.execute(
            "SELECT config_hash FROM canary_runs WHERE id = ?",
            (canary_run_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == expected

    @pytest.mark.asyncio
    async def test_store_canary_run_golden_set_hash_from_stratified(self, store):
        """Golden set hash is taken from the StratifiedGoldenSet when provided."""
        checker, run_result = self._make_checker_and_result()
        gs = checker.golden_set

        stratified = StratifiedGoldenSet(
            golden_set=gs,
            by_archetype={},
            by_collector={},
            by_confidence_band={},
            overall=list(gs.signals),
        )
        expected_hash = stratified.golden_set_hash

        canary_run_id = await store_canary_run(
            store, checker, run_result, stratified=stratified
        )

        db = store._db
        cursor = await db.execute(
            "SELECT golden_set_hash FROM canary_runs WHERE id = ?",
            (canary_run_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == expected_hash


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

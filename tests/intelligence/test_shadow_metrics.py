"""Tests for intelligence/shadow_metrics.py.

Covers compute_shadow_metrics overall rates, stratification by collector,
confidence_band, and key_type, plus labeled_precision via signal_quality_metrics.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import aiosqlite
import pytest

from intelligence.shadow_metrics import compute_shadow_metrics


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db():
    """In-memory SQLite database with shadow + quality tables."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=OFF")

    await conn.executescript("""
        CREATE TABLE shadow_entity_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            total_signals INTEGER NOT NULL DEFAULT 0,
            phase1a_groups INTEGER NOT NULL DEFAULT 0,
            phase_g_groups INTEGER NOT NULL DEFAULT 0,
            agreements INTEGER NOT NULL DEFAULT 0,
            disagreements INTEGER NOT NULL DEFAULT 0,
            agreement_rate REAL,
            metrics_json TEXT,
            duration_ms REAL,
            inputs_hash TEXT,
            config_json TEXT,
            error_summary TEXT,
            truncated INTEGER NOT NULL DEFAULT 0,
            truncation_reason TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE shadow_disagreements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shadow_run_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL,
            canonical_key TEXT NOT NULL,
            phase1a_company_id TEXT,
            phase_g_entity_id TEXT,
            phase_g_group_key TEXT,
            disagreement_type TEXT NOT NULL,
            collector TEXT,
            confidence REAL,
            confidence_band TEXT,
            canonical_key_type TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(shadow_run_id) REFERENCES shadow_entity_runs(id) ON DELETE CASCADE
        );

        -- Use 'label' column name to match code in shadow_metrics.py (_compute_labeled_precision)
        CREATE TABLE signal_quality_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL UNIQUE,
            canonical_key TEXT,
            label TEXT NOT NULL,
            label_source TEXT NOT NULL,
            labeled_at TEXT NOT NULL
        );
    """)
    yield conn
    await conn.close()


@pytest.fixture
def mock_store(db):
    """Mock SignalStore whose _db points to the in-memory aiosqlite connection."""
    store = MagicMock()
    store._db = db
    return store


NOW = datetime.now(timezone.utc).isoformat()


async def _seed_shadow_run(
    db,
    *,
    run_id: int = 1,
    total_signals: int = 10,
    phase1a_groups: int = 5,
    phase_g_groups: int = 5,
    agreements: int = 8,
    disagreements: int = 2,
    status: str = "completed",
):
    """Insert a shadow_entity_runs row."""
    await db.execute(
        """
        INSERT INTO shadow_entity_runs (id, run_id, status, total_signals, phase1a_groups,
                                        phase_g_groups, agreements, disagreements, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, f"run-{run_id}", status, total_signals, phase1a_groups,
         phase_g_groups, agreements, disagreements, NOW),
    )
    await db.commit()


async def _seed_disagreement(
    db,
    *,
    shadow_run_id: int = 1,
    signal_id: int,
    canonical_key: str = "domain:acme.ai",
    disagreement_type: str = "over_merge",
    collector: str = "github",
    confidence_band: str = "high",
    canonical_key_type: str = "domain",
):
    """Insert a shadow_disagreements row."""
    await db.execute(
        """
        INSERT INTO shadow_disagreements (shadow_run_id, signal_id, canonical_key,
                                          disagreement_type, collector, confidence_band,
                                          canonical_key_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (shadow_run_id, signal_id, canonical_key, disagreement_type,
         collector, confidence_band, canonical_key_type, NOW),
    )
    await db.commit()


async def _seed_label(db, *, signal_id: int, label: str = "TP"):
    """Insert a signal_quality_metrics row."""
    await db.execute(
        """
        INSERT INTO signal_quality_metrics (signal_id, label, label_source, labeled_at)
        VALUES (?, ?, 'manual', ?)
        """,
        (signal_id, label, NOW),
    )
    await db.commit()


# =============================================================================
# TESTS
# =============================================================================

class TestComputeShadowMetrics:
    """Tests for compute_shadow_metrics."""

    @pytest.mark.asyncio
    async def test_metrics_no_run(self, mock_store):
        """Non-existent shadow_run_id should return empty dict."""
        result = await compute_shadow_metrics(mock_store, shadow_run_id=9999)
        assert result == {}

    @pytest.mark.asyncio
    async def test_metrics_no_disagreements(self, db, mock_store):
        """Run with no disagreements should show 100% agreement."""
        await _seed_shadow_run(
            db, run_id=1, total_signals=10,
            phase1a_groups=5, phase_g_groups=5,
            agreements=10, disagreements=0,
        )

        result = await compute_shadow_metrics(mock_store, shadow_run_id=1)

        assert "overall" in result
        overall = result["overall"]
        assert overall["total_signals"] == 10
        assert overall["agreements"] == 10
        assert overall["disagreements"] == 0
        assert overall["over_merges"] == 0
        assert overall["over_splits"] == 0
        assert overall["agreement_rate"] == 1.0
        assert overall["over_merge_rate"] == 0.0
        assert overall["over_split_rate"] == 0.0

        # Stratifications should all be empty
        assert result["by_collector"] == {}
        assert result["by_confidence_band"] == {}
        assert result["by_key_type"] == {}

        # Labeled precision should show zero
        assert result["labeled_precision"]["total_labeled"] == 0

    @pytest.mark.asyncio
    async def test_metrics_with_over_merges(self, db, mock_store):
        """Over-merge disagreements should be counted correctly."""
        await _seed_shadow_run(
            db, run_id=1, total_signals=10,
            phase1a_groups=5, phase_g_groups=4,
            agreements=8, disagreements=2,
        )
        await _seed_disagreement(db, signal_id=101, disagreement_type="over_merge")
        await _seed_disagreement(db, signal_id=102, disagreement_type="over_merge")

        result = await compute_shadow_metrics(mock_store, shadow_run_id=1)

        overall = result["overall"]
        assert overall["over_merges"] == 2
        assert overall["over_splits"] == 0
        assert overall["over_merge_rate"] == 2 / 4  # over_merges / phase_g_groups

    @pytest.mark.asyncio
    async def test_metrics_with_over_splits(self, db, mock_store):
        """Over-split disagreements should be counted correctly."""
        await _seed_shadow_run(
            db, run_id=1, total_signals=10,
            phase1a_groups=6, phase_g_groups=5,
            agreements=7, disagreements=3,
        )
        await _seed_disagreement(db, signal_id=201, disagreement_type="over_split")
        await _seed_disagreement(db, signal_id=202, disagreement_type="over_split")
        await _seed_disagreement(db, signal_id=203, disagreement_type="over_split")

        result = await compute_shadow_metrics(mock_store, shadow_run_id=1)

        overall = result["overall"]
        assert overall["over_splits"] == 3
        assert overall["over_merges"] == 0
        assert overall["over_split_rate"] == 3 / 6  # over_splits / phase1a_groups

    @pytest.mark.asyncio
    async def test_metrics_mixed_disagreements(self, db, mock_store):
        """Mix of over_merge and over_split should be stratified correctly."""
        await _seed_shadow_run(
            db, run_id=1, total_signals=20,
            phase1a_groups=8, phase_g_groups=7,
            agreements=16, disagreements=4,
        )
        await _seed_disagreement(db, signal_id=301, disagreement_type="over_merge",
                                 collector="github", confidence_band="high")
        await _seed_disagreement(db, signal_id=302, disagreement_type="over_merge",
                                 collector="sec_edgar", confidence_band="medium")
        await _seed_disagreement(db, signal_id=303, disagreement_type="over_split",
                                 collector="github", confidence_band="low")
        await _seed_disagreement(db, signal_id=304, disagreement_type="over_split",
                                 collector="github", confidence_band="high")

        result = await compute_shadow_metrics(mock_store, shadow_run_id=1)

        overall = result["overall"]
        assert overall["over_merges"] == 2
        assert overall["over_splits"] == 2
        assert overall["agreement_rate"] == 16 / 20

    @pytest.mark.asyncio
    async def test_stratification_by_collector(self, db, mock_store):
        """Disagreements should be grouped by collector."""
        await _seed_shadow_run(
            db, run_id=1, total_signals=10,
            phase1a_groups=5, phase_g_groups=5,
            agreements=7, disagreements=3,
        )
        await _seed_disagreement(db, signal_id=401, collector="github",
                                 disagreement_type="over_merge")
        await _seed_disagreement(db, signal_id=402, collector="github",
                                 disagreement_type="over_split")
        await _seed_disagreement(db, signal_id=403, collector="sec_edgar",
                                 disagreement_type="over_merge")

        result = await compute_shadow_metrics(mock_store, shadow_run_id=1)

        by_collector = result["by_collector"]
        assert "github" in by_collector
        assert by_collector["github"]["over_merge"] == 1
        assert by_collector["github"]["over_split"] == 1
        assert by_collector["github"]["total"] == 2

        assert "sec_edgar" in by_collector
        assert by_collector["sec_edgar"]["over_merge"] == 1
        assert by_collector["sec_edgar"]["total"] == 1

    @pytest.mark.asyncio
    async def test_stratification_by_confidence_band(self, db, mock_store):
        """Disagreements should be grouped by confidence_band."""
        await _seed_shadow_run(
            db, run_id=1, total_signals=10,
            phase1a_groups=5, phase_g_groups=5,
            agreements=7, disagreements=3,
        )
        await _seed_disagreement(db, signal_id=501, confidence_band="high",
                                 disagreement_type="over_merge")
        await _seed_disagreement(db, signal_id=502, confidence_band="high",
                                 disagreement_type="over_merge")
        await _seed_disagreement(db, signal_id=503, confidence_band="low",
                                 disagreement_type="over_split")

        result = await compute_shadow_metrics(mock_store, shadow_run_id=1)

        by_band = result["by_confidence_band"]
        assert "high" in by_band
        assert by_band["high"]["over_merge"] == 2
        assert by_band["high"]["total"] == 2

        assert "low" in by_band
        assert by_band["low"]["over_split"] == 1
        assert by_band["low"]["total"] == 1

    @pytest.mark.asyncio
    async def test_stratification_by_key_type(self, db, mock_store):
        """Disagreements should be grouped by canonical_key_type."""
        await _seed_shadow_run(
            db, run_id=1, total_signals=10,
            phase1a_groups=5, phase_g_groups=5,
            agreements=8, disagreements=2,
        )
        await _seed_disagreement(db, signal_id=601, canonical_key_type="domain",
                                 disagreement_type="over_merge")
        await _seed_disagreement(db, signal_id=602, canonical_key_type="github",
                                 disagreement_type="over_split")

        result = await compute_shadow_metrics(mock_store, shadow_run_id=1)

        by_key_type = result["by_key_type"]
        assert "domain" in by_key_type
        assert by_key_type["domain"]["over_merge"] == 1
        assert by_key_type["domain"]["total"] == 1

        assert "github" in by_key_type
        assert by_key_type["github"]["over_split"] == 1
        assert by_key_type["github"]["total"] == 1

    @pytest.mark.asyncio
    async def test_labeled_precision_no_labels(self, db, mock_store):
        """When no signals in disagreements have labels, precision should be zero."""
        await _seed_shadow_run(
            db, run_id=1, total_signals=5,
            phase1a_groups=3, phase_g_groups=3,
            agreements=3, disagreements=2,
        )
        await _seed_disagreement(db, signal_id=701, disagreement_type="over_merge")
        await _seed_disagreement(db, signal_id=702, disagreement_type="over_split")
        # No labels seeded

        result = await compute_shadow_metrics(mock_store, shadow_run_id=1)

        lp = result["labeled_precision"]
        assert lp["total_labeled"] == 0
        assert lp["tp_in_disagreements"] == 0
        assert lp["fp_in_disagreements"] == 0

    @pytest.mark.asyncio
    async def test_labeled_precision_with_tp_fp(self, db, mock_store):
        """Labels on disagreement signals should be counted as TP/FP."""
        await _seed_shadow_run(
            db, run_id=1, total_signals=10,
            phase1a_groups=5, phase_g_groups=5,
            agreements=7, disagreements=3,
        )
        await _seed_disagreement(db, signal_id=801, disagreement_type="over_merge")
        await _seed_disagreement(db, signal_id=802, disagreement_type="over_merge")
        await _seed_disagreement(db, signal_id=803, disagreement_type="over_split")

        # Label two of them
        await _seed_label(db, signal_id=801, label="TP")
        await _seed_label(db, signal_id=802, label="FP")
        # 803 is unlabeled

        result = await compute_shadow_metrics(mock_store, shadow_run_id=1)

        lp = result["labeled_precision"]
        assert lp["total_labeled"] == 2
        assert lp["tp_in_disagreements"] == 1
        assert lp["fp_in_disagreements"] == 1


class TestComputeShadowMetricsEdgeCases:
    """Edge case tests for compute_shadow_metrics."""

    @pytest.mark.asyncio
    async def test_metrics_zero_total_signals(self, db, mock_store):
        """Run with total_signals=0 should have agreement_rate=1.0."""
        await _seed_shadow_run(
            db, run_id=1, total_signals=0,
            phase1a_groups=0, phase_g_groups=0,
            agreements=0, disagreements=0,
        )

        result = await compute_shadow_metrics(mock_store, shadow_run_id=1)

        overall = result["overall"]
        assert overall["agreement_rate"] == 1.0
        assert overall["over_merge_rate"] == 0.0
        assert overall["over_split_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_metrics_null_collector(self, db, mock_store):
        """Disagreements with NULL collector should be grouped as 'unknown'."""
        await _seed_shadow_run(
            db, run_id=1, total_signals=5,
            phase1a_groups=3, phase_g_groups=3,
            agreements=4, disagreements=1,
        )
        # Insert disagreement with NULL collector
        await db.execute(
            """
            INSERT INTO shadow_disagreements (shadow_run_id, signal_id, canonical_key,
                                              disagreement_type, collector, confidence_band,
                                              canonical_key_type, created_at)
            VALUES (1, 901, 'domain:null.ai', 'over_merge', NULL, 'high', 'domain', ?)
            """,
            (NOW,),
        )
        await db.commit()

        result = await compute_shadow_metrics(mock_store, shadow_run_id=1)

        by_collector = result["by_collector"]
        assert "unknown" in by_collector
        assert by_collector["unknown"]["over_merge"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

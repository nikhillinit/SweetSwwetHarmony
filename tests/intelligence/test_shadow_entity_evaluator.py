"""Tests for intelligence/shadow_entity_evaluator.py.

Covers ShadowRunConfig validation, helper functions (_confidence_band,
_canonical_key_type, compute_inputs_hash), run_shadow_comparison core logic,
and persistence (store_shadow_run, store_skipped_shadow_run).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from intelligence.shadow_entity_evaluator import (
    ShadowRunConfig,
    ShadowRunResult,
    ShadowDisagreement,
    _confidence_band,
    _canonical_key_type,
    compute_inputs_hash,
    run_shadow_comparison,
    store_shadow_run,
    store_skipped_shadow_run,
    update_shadow_run_metrics,
)
from workflows.run_manager import RunType


# =============================================================================
# HELPERS
# =============================================================================

class MockROStore:
    """Mock ReadOnlyIdentityStore that returns controlled results.

    strong_key_map: canonical_key -> entity_id (for strong key lookups)
    alias_key_map:  canonical_key -> entity_id (for alias fallback lookups)
    """

    def __init__(
        self,
        strong_key_map: Optional[Dict[str, str]] = None,
        alias_key_map: Optional[Dict[str, str]] = None,
    ):
        self._strong = strong_key_map or {}
        self._alias = alias_key_map or {}

    async def lookup_strong_keys(self, keys: List[str]) -> Dict[str, str]:
        return {k: v for k, v in self._strong.items() if k in keys}

    async def lookup_alias_keys(self, keys: List[str]) -> Dict[str, str]:
        return {k: v for k, v in self._alias.items() if k in keys}


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
    """In-memory SQLite database with the tables needed by shadow entity evaluator."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=OFF")

    await conn.executescript("""
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL,
            source_api TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            company_name TEXT,
            company_id TEXT,
            confidence REAL NOT NULL,
            raw_data TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE run_history (
            id TEXT PRIMARY KEY,
            run_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            actor_id TEXT,
            actor_email TEXT,
            inputs_summary TEXT,
            inputs_hash TEXT,
            result TEXT,
            error_message TEXT,
            progress_pct INTEGER,
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            correlation_id TEXT
        );

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
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES run_history(id) ON DELETE CASCADE
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
    """)
    yield conn
    await conn.close()


@pytest.fixture
def mock_store(db):
    """Mock SignalStore whose _db points to the in-memory aiosqlite connection."""
    store = MagicMock()
    store._db = db
    return store


async def _seed_signal(
    db,
    *,
    sig_id: int,
    canonical_key: str = "domain:acme.ai",
    company_name: str = "Acme Inc",
    company_id: str = "comp_001",
    source_api: str = "github",
    confidence: float = 0.8,
    signal_type: str = "github_spike",
    detected_at: str = "2026-01-15T00:00:00Z",
    created_at: str = "2026-01-15T00:00:00Z",
):
    """Insert a signal row for testing."""
    await db.execute(
        """
        INSERT INTO signals (id, signal_type, source_api, canonical_key,
                             company_name, company_id, confidence, raw_data,
                             detected_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sig_id, signal_type, source_api, canonical_key,
            company_name, company_id, confidence, "{}",
            detected_at, created_at,
        ),
    )
    await db.commit()


# =============================================================================
# TEST: ShadowRunConfig
# =============================================================================

class TestShadowRunConfig:
    """Tests for ShadowRunConfig dataclass and validation."""

    def test_config_defaults(self):
        """Default config should have sensible values."""
        cfg = ShadowRunConfig()

        assert cfg.max_signals_per_run == 500
        assert cfg.sample_rate == 1.0
        assert cfg.timeout_seconds == 30.0
        assert cfg.max_disagreements_stored == 1000
        assert cfg.min_similarity_threshold == 0.85
        assert cfg.max_suggestions_per_run == 100

    def test_config_validation_sample_rate_zero(self):
        """sample_rate=0 should raise ValueError."""
        with pytest.raises(ValueError, match="sample_rate"):
            ShadowRunConfig(sample_rate=0.0)

    def test_config_validation_sample_rate_negative(self):
        """sample_rate=-0.1 should raise ValueError."""
        with pytest.raises(ValueError, match="sample_rate"):
            ShadowRunConfig(sample_rate=-0.1)

    def test_config_validation_sample_rate_above_one(self):
        """sample_rate=1.5 should raise ValueError."""
        with pytest.raises(ValueError, match="sample_rate"):
            ShadowRunConfig(sample_rate=1.5)

    def test_config_validation_max_signals_zero(self):
        """max_signals_per_run=0 should raise ValueError."""
        with pytest.raises(ValueError, match="max_signals_per_run"):
            ShadowRunConfig(max_signals_per_run=0)

    def test_config_validation_timeout_too_low(self):
        """timeout_seconds=0.5 should raise ValueError (must be >= 1.0)."""
        with pytest.raises(ValueError, match="timeout_seconds"):
            ShadowRunConfig(timeout_seconds=0.5)

    def test_config_from_env(self, monkeypatch):
        """from_env should read SHADOW_* env vars."""
        monkeypatch.setenv("SHADOW_MAX_SIGNALS", "100")
        monkeypatch.setenv("SHADOW_SAMPLE_RATE", "0.5")
        monkeypatch.setenv("SHADOW_TIMEOUT_SECONDS", "15")
        monkeypatch.setenv("SHADOW_MAX_DISAGREEMENTS", "200")
        monkeypatch.setenv("SHADOW_MIN_SIMILARITY", "0.9")
        monkeypatch.setenv("SHADOW_MAX_SUGGESTIONS", "50")

        cfg = ShadowRunConfig.from_env()

        assert cfg.max_signals_per_run == 100
        assert cfg.sample_rate == 0.5
        assert cfg.timeout_seconds == 15.0
        assert cfg.max_disagreements_stored == 200
        assert cfg.min_similarity_threshold == 0.9
        assert cfg.max_suggestions_per_run == 50


# =============================================================================
# TEST: Helper Functions
# =============================================================================

class TestConfidenceBand:
    """Tests for _confidence_band helper."""

    def test_confidence_band_high(self):
        """Confidence >= 0.7 should return 'high'."""
        assert _confidence_band(0.7) == "high"
        assert _confidence_band(0.85) == "high"
        assert _confidence_band(1.0) == "high"

    def test_confidence_band_medium(self):
        """Confidence in [0.4, 0.7) should return 'medium'."""
        assert _confidence_band(0.4) == "medium"
        assert _confidence_band(0.55) == "medium"
        assert _confidence_band(0.69) == "medium"

    def test_confidence_band_low(self):
        """Confidence < 0.4 should return 'low'."""
        assert _confidence_band(0.0) == "low"
        assert _confidence_band(0.2) == "low"
        assert _confidence_band(0.39) == "low"

    def test_confidence_band_none(self):
        """None confidence should return None."""
        assert _confidence_band(None) is None


class TestCanonicalKeyType:
    """Tests for _canonical_key_type helper."""

    def test_canonical_key_type_domain(self):
        """'domain:acme.ai' should return 'domain'."""
        assert _canonical_key_type("domain:acme.ai") == "domain"

    def test_canonical_key_type_github(self):
        """'github:org/repo' should return 'github'."""
        assert _canonical_key_type("github:org/repo") == "github"

    def test_canonical_key_type_name_loc(self):
        """'name_loc:acme:sf' should return 'name_loc' (first colon only)."""
        assert _canonical_key_type("name_loc:acme:sf") == "name_loc"

    def test_canonical_key_type_no_colon(self):
        """Key without colon should return None."""
        assert _canonical_key_type("acme") is None

    def test_canonical_key_type_empty_prefix(self):
        """':something' should return empty string (prefix before colon)."""
        assert _canonical_key_type(":something") == ""


class TestComputeInputsHash:
    """Tests for compute_inputs_hash."""

    def test_compute_inputs_hash_deterministic(self):
        """Same IDs should produce the same hash every time."""
        ids = [1, 2, 3]
        h1 = compute_inputs_hash(ids)
        h2 = compute_inputs_hash(ids)

        assert h1 == h2
        assert len(h1) == 16  # SHA256[:16] hex chars

    def test_compute_inputs_hash_order_independent(self):
        """Inputs are sorted internally, so order should not matter."""
        h1 = compute_inputs_hash([3, 1, 2])
        h2 = compute_inputs_hash([1, 2, 3])
        h3 = compute_inputs_hash([2, 3, 1])

        assert h1 == h2 == h3

    def test_compute_inputs_hash_different_inputs(self):
        """Different ID sets should produce different hashes."""
        h1 = compute_inputs_hash([1, 2, 3])
        h2 = compute_inputs_hash([4, 5, 6])

        assert h1 != h2

    def test_compute_inputs_hash_manual_verification(self):
        """Verify the hash matches manual SHA256 computation."""
        ids = [10, 20, 30]
        # Sorted str ids: ["10", "20", "30"] joined by \x1f
        payload = "\x1f".join(["10", "20", "30"])
        expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

        assert compute_inputs_hash(ids) == expected


# =============================================================================
# TEST: run_shadow_comparison
# =============================================================================

class TestRunShadowComparison:
    """Tests for the core comparison logic."""

    @pytest.mark.asyncio
    async def test_comparison_no_signals(self, mock_store):
        """Empty signals table should return agreement_rate=1.0."""
        ro_store = MockROStore()
        config = ShadowRunConfig(timeout_seconds=5.0)

        result = await run_shadow_comparison(mock_store, ro_store, config)

        assert result.status == "completed"
        assert result.total_signals == 0
        assert result.agreement_rate == 1.0
        assert result.disagreements == []
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    async def test_comparison_with_agreements(self, db, mock_store):
        """When Phase 1a and Phase G produce identical groups, all should agree."""
        # Seed 3 signals all in same company_id and same Phase G entity
        await _seed_signal(db, sig_id=1, canonical_key="domain:alpha.ai", company_id="comp_A")
        await _seed_signal(db, sig_id=2, canonical_key="domain:alpha.ai", company_id="comp_A",
                           source_api="sec_edgar")
        await _seed_signal(db, sig_id=3, canonical_key="domain:beta.io", company_id="comp_B",
                           source_api="hacker_news")

        # Phase G maps the same way: alpha.ai -> entity_X, beta.io -> entity_Y
        ro_store = MockROStore(
            strong_key_map={
                "domain:alpha.ai": "entity_X",
                "domain:beta.io": "entity_Y",
            },
        )
        config = ShadowRunConfig(timeout_seconds=5.0)

        result = await run_shadow_comparison(mock_store, ro_store, config)

        assert result.status == "completed"
        assert result.total_signals == 3
        assert result.phase1a_groups == 2
        assert result.phase_g_groups == 2
        # All should agree: each group maps identically
        assert result.agreements == 3
        assert result.disagreements_count == 0
        assert result.agreement_rate == 1.0

    @pytest.mark.asyncio
    async def test_comparison_with_over_merge_disagreement(self, db, mock_store):
        """When Phase G merges signals that Phase 1a keeps separate, detect over_merge."""
        # Phase 1a: two distinct companies
        await _seed_signal(db, sig_id=10, canonical_key="domain:foo.ai", company_id="comp_A")
        await _seed_signal(db, sig_id=11, canonical_key="domain:bar.ai", company_id="comp_B")

        # Phase G maps both canonical keys to the SAME entity
        # This means phase_g_groups has one group with {10, 11}
        # While phase1a_groups has comp_A={10} and comp_B={11}
        # So pg_group={10,11} is a superset of p1a_group={10} -> over_merge
        ro_store = MockROStore(
            strong_key_map={
                "domain:foo.ai": "entity_MERGED",
                "domain:bar.ai": "entity_MERGED",
            },
        )
        config = ShadowRunConfig(timeout_seconds=5.0)

        result = await run_shadow_comparison(mock_store, ro_store, config)

        assert result.status == "completed"
        assert result.total_signals == 2
        assert result.disagreements_count > 0
        # At least one over_merge disagreement
        over_merge_types = [
            d.disagreement_type for d in result.disagreements
            if d.disagreement_type == "over_merge"
        ]
        assert len(over_merge_types) >= 1

    @pytest.mark.asyncio
    async def test_comparison_signals_without_phase_g_mapping(self, db, mock_store):
        """Signals with no Phase G mapping should be counted as agreements."""
        await _seed_signal(db, sig_id=20, canonical_key="domain:orphan.ai", company_id="comp_O")

        # Phase G returns nothing for this key
        ro_store = MockROStore(strong_key_map={}, alias_key_map={})
        config = ShadowRunConfig(timeout_seconds=5.0)

        result = await run_shadow_comparison(mock_store, ro_store, config)

        assert result.status == "completed"
        assert result.total_signals == 1
        assert result.agreements == 1
        assert result.disagreements_count == 0

    @pytest.mark.asyncio
    async def test_comparison_uses_alias_fallback(self, db, mock_store):
        """Keys not found via strong lookup should fall back to alias lookup."""
        await _seed_signal(db, sig_id=30, canonical_key="domain:alias-test.ai", company_id="comp_X")

        # Strong key lookup returns nothing; alias returns the mapping
        ro_store = MockROStore(
            strong_key_map={},
            alias_key_map={"domain:alias-test.ai": "entity_ALIAS"},
        )
        config = ShadowRunConfig(timeout_seconds=5.0)

        result = await run_shadow_comparison(mock_store, ro_store, config)

        assert result.status == "completed"
        assert result.total_signals == 1
        # Phase G found a mapping via alias, so it should have 1 group
        assert result.phase_g_groups == 1


# =============================================================================
# TEST: Persistence
# =============================================================================

class TestStoreShadowRun:
    """Tests for store_shadow_run and store_skipped_shadow_run."""

    @pytest.mark.asyncio
    async def test_store_shadow_run_creates_run_history(self, db, mock_store):
        """store_shadow_run should create a run_history entry and shadow_entity_runs row."""
        result = ShadowRunResult(
            status="completed",
            total_signals=10,
            phase1a_groups=5,
            phase_g_groups=5,
            agreements=8,
            disagreements_count=2,
            agreement_rate=0.8,
            duration_ms=123.4,
            inputs_hash="abc123def456",
            config_json=json.dumps({"max_signals_per_run": 500}),
            disagreements=[
                ShadowDisagreement(
                    signal_id=1,
                    canonical_key="domain:acme.ai",
                    phase1a_company_id="comp_A",
                    phase_g_entity_id="entity_X",
                    phase_g_group_key="domain:acme.ai",
                    disagreement_type="over_merge",
                    collector="github",
                    confidence=0.8,
                    confidence_band="high",
                    canonical_key_type="domain",
                ),
                ShadowDisagreement(
                    signal_id=2,
                    canonical_key="domain:beta.io",
                    phase1a_company_id="comp_B",
                    phase_g_entity_id="entity_Y",
                    phase_g_group_key="domain:beta.io",
                    disagreement_type="over_split",
                    collector="sec_edgar",
                    confidence=0.5,
                    confidence_band="medium",
                    canonical_key_type="domain",
                ),
            ],
        )

        shadow_run_id = await store_shadow_run(mock_store, result)

        assert shadow_run_id > 0

        # Verify shadow_entity_runs row
        cursor = await db.execute(
            "SELECT status, total_signals, agreements, disagreements FROM shadow_entity_runs WHERE id = ?",
            (shadow_run_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "completed"
        assert row[1] == 10
        assert row[2] == 8
        assert row[3] == 2

        # Verify disagreements were inserted
        cursor = await db.execute(
            "SELECT COUNT(*) FROM shadow_disagreements WHERE shadow_run_id = ?",
            (shadow_run_id,),
        )
        count = (await cursor.fetchone())[0]
        assert count == 2

        # Verify run_history was created
        assert result.run_id != ""
        cursor = await db.execute(
            "SELECT run_type, status FROM run_history WHERE id = ?",
            (result.run_id,),
        )
        rh_row = await cursor.fetchone()
        assert rh_row is not None
        assert rh_row[0] == RunType.ENTITY_RESOLUTION.value
        assert rh_row[1] == "completed"

    @pytest.mark.asyncio
    async def test_store_skipped_shadow_run(self, db, mock_store):
        """store_skipped_shadow_run should create a skipped row."""
        shadow_run_id = await store_skipped_shadow_run(mock_store, reason="circuit_breaker_open")

        assert shadow_run_id > 0

        # Verify shadow_entity_runs row with status=skipped
        cursor = await db.execute(
            "SELECT status, error_summary FROM shadow_entity_runs WHERE id = ?",
            (shadow_run_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "skipped"
        assert row[1] == "circuit_breaker_open"

        # Verify run_history was also created
        cursor = await db.execute(
            "SELECT COUNT(*) FROM run_history WHERE run_type = ?",
            (RunType.ENTITY_RESOLUTION.value,),
        )
        count = (await cursor.fetchone())[0]
        assert count == 1

    @pytest.mark.asyncio
    async def test_update_shadow_run_metrics(self, db, mock_store):
        """update_shadow_run_metrics should set metrics_json on an existing run."""
        # First insert a minimal shadow run row
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """
            INSERT INTO run_history (id, run_type, status, created_at)
            VALUES ('run-metrics-test', 'entity_resolution', 'completed', ?)
            """,
            (now,),
        )
        cursor = await db.execute(
            """
            INSERT INTO shadow_entity_runs (run_id, status, created_at)
            VALUES ('run-metrics-test', 'completed', ?)
            """,
            (now,),
        )
        await db.commit()
        shadow_id = cursor.lastrowid

        metrics = json.dumps({"overall": {"agreement_rate": 0.95}})
        await update_shadow_run_metrics(mock_store, shadow_id, metrics)

        cursor = await db.execute(
            "SELECT metrics_json FROM shadow_entity_runs WHERE id = ?",
            (shadow_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert json.loads(row[0])["overall"]["agreement_rate"] == 0.95


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

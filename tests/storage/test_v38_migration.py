"""Tests for v38 Wave 2 Shadow Entity Resolution + Canary Baseline migration."""

import json
import os
import tempfile

import pytest
import pytest_asyncio

from storage.signal_store import SignalStore


@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with temp file DB — runs all migrations through v38."""
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


# ---------------------------------------------------------------------------
# Helper: seed a run_history row so FK constraints are satisfied
# ---------------------------------------------------------------------------
async def _seed_run_history(db, run_id: str = "run-001") -> str:
    """Insert a minimal run_history row and return the id."""
    await db.execute(
        """
        INSERT OR IGNORE INTO run_history (id, run_type, status, created_at)
        VALUES (?, 'shadow', 'completed', datetime('now'))
        """,
        (run_id,),
    )
    await db.commit()
    return run_id


# ===========================================================================
# TABLE EXISTENCE
# ===========================================================================

class TestV38TablesCreated:
    """All five tables must exist after v38 migration."""

    EXPECTED_TABLES = [
        "shadow_entity_runs",
        "shadow_disagreements",
        "merge_suggestions",
        "canary_runs",
        "canary_drift_alerts",
    ]

    @pytest.mark.asyncio
    async def test_tables_created(self, store):
        """All 5 v38 tables should exist after migration."""
        db = store._db
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        rows = await cursor.fetchall()
        existing = {row[0] for row in rows}
        for table in self.EXPECTED_TABLES:
            assert table in existing, f"Table {table} should exist after v38 migration"


# ===========================================================================
# COLUMN VERIFICATION
# ===========================================================================

class TestV38Columns:
    """Verify column sets for each of the five tables."""

    @pytest.mark.asyncio
    async def test_shadow_entity_runs_columns(self, store):
        """shadow_entity_runs should have the expected columns."""
        db = store._db
        cursor = await db.execute("PRAGMA table_info(shadow_entity_runs)")
        rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        expected = {
            "id", "run_id", "status", "total_signals",
            "phase1a_groups", "phase_g_groups", "agreements", "disagreements",
            "agreement_rate", "metrics_json", "duration_ms", "inputs_hash",
            "config_json", "error_summary", "truncated", "truncation_reason",
            "created_at",
        }
        assert expected.issubset(col_names), f"Missing columns: {expected - col_names}"

    @pytest.mark.asyncio
    async def test_shadow_disagreements_columns(self, store):
        """shadow_disagreements should have the expected columns."""
        db = store._db
        cursor = await db.execute("PRAGMA table_info(shadow_disagreements)")
        rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        expected = {
            "id", "shadow_run_id", "signal_id", "canonical_key",
            "phase1a_company_id", "phase_g_entity_id", "phase_g_group_key",
            "disagreement_type", "collector", "confidence",
            "confidence_band", "canonical_key_type", "created_at",
        }
        assert expected.issubset(col_names), f"Missing columns: {expected - col_names}"

    @pytest.mark.asyncio
    async def test_merge_suggestions_columns(self, store):
        """merge_suggestions should have the expected columns."""
        db = store._db
        cursor = await db.execute("PRAGMA table_info(merge_suggestions)")
        rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        expected = {
            "id", "shadow_run_id", "pair_key",
            "entity_a_company_id", "entity_b_company_id",
            "entity_a_canonical_key", "entity_b_canonical_key",
            "entity_a_company_name", "entity_b_company_name",
            "match_type", "similarity_score", "scoring_version",
            "evidence_json", "status", "reviewed_by", "reviewed_at",
            "blast_radius_json", "created_at",
        }
        assert expected.issubset(col_names), f"Missing columns: {expected - col_names}"

    @pytest.mark.asyncio
    async def test_canary_runs_columns(self, store):
        """canary_runs should have the expected columns."""
        db = store._db
        cursor = await db.execute("PRAGMA table_info(canary_runs)")
        rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        expected = {
            "id", "run_id", "golden_set_size", "golden_set_hash",
            "golden_set_version", "config_hash",
            "total_scored", "passed", "failed", "skipped",
            "pass_rate", "verdict", "drift_threshold", "pass_rate_threshold",
            "duration_ms", "results_json", "stratification_json", "created_at",
        }
        assert expected.issubset(col_names), f"Missing columns: {expected - col_names}"

    @pytest.mark.asyncio
    async def test_canary_drift_alerts_columns(self, store):
        """canary_drift_alerts should have the expected columns."""
        db = store._db
        cursor = await db.execute("PRAGMA table_info(canary_drift_alerts)")
        rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        expected = {
            "id", "canary_run_id", "alert_type", "severity",
            "signal_id", "canonical_key", "metric_name",
            "expected_value", "actual_value", "delta",
            "message", "status", "acknowledged_by", "acknowledged_at",
            "created_at",
        }
        assert expected.issubset(col_names), f"Missing columns: {expected - col_names}"


# ===========================================================================
# CHECK CONSTRAINTS
# ===========================================================================

class TestV38CheckConstraints:
    """Verify CHECK constraints reject invalid values."""

    @pytest.mark.asyncio
    async def test_shadow_runs_status_check_valid(self, store):
        """shadow_entity_runs should accept valid status values."""
        db = store._db
        run_id = await _seed_run_history(db, "run-status-valid")
        for status in ("running", "completed", "failed", "timeout", "skipped"):
            await db.execute(
                """
                INSERT INTO shadow_entity_runs (run_id, status, created_at)
                VALUES (?, ?, datetime('now'))
                """,
                (run_id, status),
            )
        await db.commit()

    @pytest.mark.asyncio
    async def test_shadow_runs_status_check_invalid(self, store):
        """shadow_entity_runs should reject invalid status values."""
        db = store._db
        run_id = await _seed_run_history(db, "run-status-bad")
        with pytest.raises(Exception):
            await db.execute(
                """
                INSERT INTO shadow_entity_runs (run_id, status, created_at)
                VALUES (?, 'BOGUS_STATUS', datetime('now'))
                """,
                (run_id,),
            )
            await db.commit()

    @pytest.mark.asyncio
    async def test_disagreement_type_check_constraint(self, store):
        """shadow_disagreements should reject invalid disagreement_type values."""
        db = store._db
        run_id = await _seed_run_history(db, "run-disagree")
        # Insert a parent shadow run first
        await db.execute(
            """
            INSERT INTO shadow_entity_runs (id, run_id, status, created_at)
            VALUES (999, ?, 'completed', datetime('now'))
            """,
            (run_id,),
        )
        await db.commit()

        # Valid types should succeed
        for dtype in ("over_merge", "over_split"):
            await db.execute(
                """
                INSERT INTO shadow_disagreements
                    (shadow_run_id, signal_id, canonical_key, disagreement_type, created_at)
                VALUES (999, 1, 'domain:test.com', ?, datetime('now'))
                """,
                (dtype,),
            )
        await db.commit()

        # Invalid type should fail
        with pytest.raises(Exception):
            await db.execute(
                """
                INSERT INTO shadow_disagreements
                    (shadow_run_id, signal_id, canonical_key, disagreement_type, created_at)
                VALUES (999, 2, 'domain:bad.com', 'wrong_type', datetime('now'))
                """,
            )
            await db.commit()

    @pytest.mark.asyncio
    async def test_merge_suggestions_status_check_constraint(self, store):
        """merge_suggestions should reject invalid status values."""
        db = store._db
        # Valid insert
        await db.execute(
            """
            INSERT INTO merge_suggestions
                (pair_key, entity_a_company_id, entity_b_company_id,
                 entity_a_canonical_key, entity_b_canonical_key,
                 match_type, similarity_score, evidence_json, status, created_at)
            VALUES ('a||b', 'cid_a', 'cid_b',
                    'domain:a.com', 'domain:b.com',
                    'fuzzy_name', 0.85, '{"reason":"name"}', 'pending', datetime('now'))
            """,
        )
        await db.commit()

        # Invalid status should fail
        with pytest.raises(Exception):
            await db.execute(
                """
                INSERT INTO merge_suggestions
                    (pair_key, entity_a_company_id, entity_b_company_id,
                     entity_a_canonical_key, entity_b_canonical_key,
                     match_type, similarity_score, evidence_json, status, created_at)
                VALUES ('c||d', 'cid_c', 'cid_d',
                        'domain:c.com', 'domain:d.com',
                        'fuzzy_name', 0.5, '{"r":"x"}', 'INVALID', datetime('now'))
                """,
            )
            await db.commit()


# ===========================================================================
# UNIQUE CONSTRAINTS
# ===========================================================================

class TestV38UniqueConstraints:

    @pytest.mark.asyncio
    async def test_merge_pair_key_unique(self, store):
        """merge_suggestions pair_key column should enforce uniqueness."""
        db = store._db
        base_params = (
            "unique_pair", "cid1", "cid2",
            "domain:u1.com", "domain:u2.com",
            "shared_domain", 0.9, '{"ev":"test"}', "pending",
        )
        await db.execute(
            """
            INSERT INTO merge_suggestions
                (pair_key, entity_a_company_id, entity_b_company_id,
                 entity_a_canonical_key, entity_b_canonical_key,
                 match_type, similarity_score, evidence_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            base_params,
        )
        await db.commit()

        # Duplicate pair_key should fail
        with pytest.raises(Exception):
            await db.execute(
                """
                INSERT INTO merge_suggestions
                    (pair_key, entity_a_company_id, entity_b_company_id,
                     entity_a_canonical_key, entity_b_canonical_key,
                     match_type, similarity_score, evidence_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                base_params,
            )
            await db.commit()


# ===========================================================================
# INDEXES
# ===========================================================================

class TestV38Indexes:

    ALL_EXPECTED_INDEXES = {
        # shadow_entity_runs
        "idx_shadow_runs_created",
        "idx_shadow_runs_status",
        # shadow_disagreements
        "idx_shadow_disagree_run",
        "idx_shadow_disagree_type",
        "idx_shadow_disagree_signal",
        # merge_suggestions
        "idx_merge_pair_key",
        "idx_merge_suggestions_status",
        "idx_merge_suggestions_created",
        "idx_merge_entity_a_status",
        "idx_merge_entity_b_status",
        # canary_runs
        "idx_canary_runs_created",
        "idx_canary_runs_verdict",
        "idx_canary_runs_hash",
        # canary_drift_alerts
        "idx_canary_drift_status",
        "idx_canary_drift_run",
    }

    @pytest.mark.asyncio
    async def test_indexes_exist(self, store):
        """All 15 indexes from v38 DDL should exist."""
        db = store._db
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        rows = await cursor.fetchall()
        existing = {row[0] for row in rows}
        missing = self.ALL_EXPECTED_INDEXES - existing
        assert not missing, f"Missing indexes: {missing}"


# ===========================================================================
# JSON VALID CONSTRAINTS
# ===========================================================================

class TestV38JsonValidConstraints:

    @pytest.mark.asyncio
    async def test_json_valid_constraint_on_metrics_json(self, store):
        """shadow_entity_runs metrics_json should reject invalid JSON."""
        db = store._db
        run_id = await _seed_run_history(db, "run-json-test")

        # Valid JSON should succeed
        await db.execute(
            """
            INSERT INTO shadow_entity_runs (run_id, status, metrics_json, created_at)
            VALUES (?, 'completed', '{"ok": true}', datetime('now'))
            """,
            (run_id,),
        )
        await db.commit()

        # NULL should succeed (constraint allows NULL)
        await db.execute(
            """
            INSERT INTO shadow_entity_runs (run_id, status, metrics_json, created_at)
            VALUES (?, 'completed', NULL, datetime('now'))
            """,
            (run_id,),
        )
        await db.commit()

        # Invalid JSON should fail
        with pytest.raises(Exception):
            await db.execute(
                """
                INSERT INTO shadow_entity_runs (run_id, status, metrics_json, created_at)
                VALUES (?, 'completed', '{not valid json', datetime('now'))
                """,
                (run_id,),
            )
            await db.commit()


# ===========================================================================
# SCHEMA VERSION
# ===========================================================================

class TestV38SchemaVersion:

    @pytest.mark.asyncio
    async def test_schema_version_is_at_least_38(self, store):
        """Schema version should be >= 38 after migration."""
        db = store._db
        cursor = await db.execute("SELECT MAX(version) FROM schema_migrations")
        row = await cursor.fetchone()
        assert row is not None
        assert int(row[0]) >= 38


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

"""Unit tests for Phase G activation gate (monitoring/phase_g_readiness.py).

10 tests covering all 5 readiness checks:
- entity_tables_present (missing -> blocked)
- blocking_index_populated (empty -> warn)
- shadow_merge_quality (high rejection rate -> blocked)
- no_orphaned_entities (orphaned signals -> warn)
- claim_facts_consistent (contradictions -> warn, disabled -> skipped)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from monitoring.phase_g_readiness import (
    check_phase_g_readiness,
    PhaseGReadinessResult,
    REQUIRED_TABLES,
    MAX_REJECTION_RATE,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def store():
    """Create a temporary SignalStore with all migrations applied."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


async def _populate_blocking_index(store, n=3):
    """Insert n rows into entity_blocking_index so the check passes.

    Uses the composite PK: (blocking_token, token_type, entity_id, alias_key).
    """
    db = store._db
    for i in range(n):
        await db.execute(
            """INSERT INTO entity_blocking_index
               (blocking_token, token_type, entity_id, alias_key, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (f"token_{i}", "first", f"ent_{i}", f"alias_{i}"),
        )
    await db.commit()


async def _insert_merge_suggestion(store, *, suggestion_id, status="pending",
                                   pair_key=None, shadow_run_id=None):
    """Insert a merge_suggestions row for testing rejection rate."""
    db = store._db
    if pair_key is None:
        pair_key = f"pair_{suggestion_id}"
    now = "2026-01-15T00:00:00Z"
    await db.execute(
        """INSERT INTO merge_suggestions
           (shadow_run_id, pair_key, entity_a_company_id, entity_b_company_id,
            entity_a_canonical_key, entity_b_canonical_key,
            match_type, similarity_score, evidence_json, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            shadow_run_id,
            pair_key,
            f"ent_a_{suggestion_id}",
            f"ent_b_{suggestion_id}",
            f"domain:a{suggestion_id}.com",
            f"domain:b{suggestion_id}.com",
            "fuzzy_name",
            0.85,
            json.dumps({"reason": "test merge suggestion"}),
            status,
            now,
        ),
    )
    await db.commit()


async def _insert_signal_with_company_id(store, company_id, canonical_key="domain:test.com"):
    """Insert a minimal signal row with a company_id.

    signals schema: id, signal_type, source_api, canonical_key, company_name,
    confidence, raw_data, detected_at, created_at, company_id (added v28).
    """
    db = store._db
    now = "2026-01-15T00:00:00Z"
    await db.execute(
        """INSERT INTO signals
           (signal_type, source_api, canonical_key, company_name,
            confidence, raw_data, detected_at, created_at, company_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "test_type",
            "test_source",
            canonical_key,
            "Test Company",
            0.5,
            '{"test": true}',
            now,
            now,
            company_id,
        ),
    )
    await db.commit()


async def _insert_entity_migration(store, from_entity_id, to_entity_id):
    """Insert an entity_migrations row."""
    db = store._db
    now = "2026-01-15T00:00:00Z"
    await db.execute(
        """INSERT INTO entity_migrations
           (from_entity_id, to_entity_id, merged_at, merge_reason)
           VALUES (?, ?, ?, ?)""",
        (from_entity_id, to_entity_id, now, "test_merge"),
    )
    await db.commit()


async def _insert_claim_fact(store, entity_id, predicate, value,
                             is_retracted=0, valid_until=None):
    """Insert a claim_facts row."""
    db = store._db
    now = "2026-01-15T00:00:00Z"
    await db.execute(
        """INSERT INTO claim_facts
           (entity_id, predicate, value_json, source_tier, confidence,
            valid_from, valid_until, observed_at, is_retracted, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entity_id,
            predicate,
            json.dumps(value),
            1,
            0.9,
            now,
            valid_until,
            now,
            is_retracted,
            now,
        ),
    )
    await db.commit()


# =============================================================================
# TESTS
# =============================================================================

class TestPhaseGReadinessGate:
    """Tests for Phase G entity resolution readiness checker."""

    @pytest.mark.asyncio
    async def test_all_checks_pass_returns_ready(self, store):
        """All tables exist and blocking index populated -> verdict 'ready', can_proceed True."""
        await _populate_blocking_index(store, n=5)

        result = await check_phase_g_readiness(store)

        assert result.verdict == "ready"
        assert result.can_proceed is True
        assert result.metrics["tables_present"] == 4
        assert result.metrics["blocking_index_rows"] >= 1

    @pytest.mark.asyncio
    async def test_missing_tables_returns_blocked(self, store):
        """Dropping a required table -> verdict 'blocked', can_proceed False."""
        db = store._db
        await db.execute("DROP TABLE IF EXISTS entity_aliases")
        await db.commit()

        result = await check_phase_g_readiness(store)

        assert result.verdict == "blocked"
        assert result.can_proceed is False
        assert len(result.reasons) >= 1
        assert "Missing tables" in result.reasons[0]
        assert "entity_aliases" in result.reasons[0]

    @pytest.mark.asyncio
    async def test_empty_blocking_index_returns_warn(self, store):
        """Tables exist but blocking index empty -> verdict 'warn', can_proceed True."""
        # After initialize(), all tables exist but entity_blocking_index has 0 rows.
        result = await check_phase_g_readiness(store)

        assert result.verdict == "warn"
        assert result.can_proceed is True
        assert result.metrics["blocking_index_rows"] == 0
        assert any("empty" in r.lower() or "blocking" in r.lower() for r in result.reasons)

    @pytest.mark.asyncio
    async def test_high_rejection_rate_returns_blocked(self, store):
        """Rejection rate > 10% -> verdict 'blocked'."""
        await _populate_blocking_index(store, n=1)

        # Insert 10 merge suggestions: 2 rejected, 8 pending -> 20% rejection rate
        for i in range(8):
            await _insert_merge_suggestion(store, suggestion_id=f"pending_{i}", status="pending")
        for i in range(2):
            await _insert_merge_suggestion(store, suggestion_id=f"rejected_{i}", status="rejected")

        result = await check_phase_g_readiness(store)

        assert result.verdict == "blocked"
        assert result.can_proceed is False
        assert result.metrics["merge_suggestions_total"] == 10
        assert result.metrics["merge_suggestions_rejected"] == 2
        assert result.metrics["merge_rejection_rate"] == 0.2
        assert any("rejection rate" in r.lower() for r in result.reasons)

    @pytest.mark.asyncio
    async def test_orphaned_entities_returns_warn(self, store):
        """Signals pointing to merged-away entity_ids -> verdict 'warn'."""
        await _populate_blocking_index(store, n=1)

        orphan_id = "orphan_abc123"
        winner_id = "winner_def456"

        # Insert a signal referencing the orphaned entity
        await _insert_signal_with_company_id(store, company_id=orphan_id)

        # Insert a migration showing orphan_id was merged into winner_id
        await _insert_entity_migration(store, from_entity_id=orphan_id, to_entity_id=winner_id)

        result = await check_phase_g_readiness(store)

        assert result.verdict == "warn"
        assert result.can_proceed is True
        assert result.metrics["orphaned_entity_ids"] >= 1
        assert any("merged-away" in r.lower() for r in result.reasons)

    @pytest.mark.asyncio
    async def test_to_dict_structure(self, store):
        """Result.to_dict() has the expected keys and types."""
        result = await check_phase_g_readiness(store)
        d = result.to_dict()

        assert "verdict" in d
        assert "reasons" in d
        assert "can_proceed" in d
        assert "metrics" in d
        assert "checked_at" in d

        assert isinstance(d["reasons"], list)
        assert isinstance(d["metrics"], dict)
        assert isinstance(d["can_proceed"], bool)
        assert isinstance(d["verdict"], str)
        assert isinstance(d["checked_at"], str)

    @pytest.mark.asyncio
    async def test_multiple_issues_all_reported(self, store):
        """Empty blocking index (warn) + orphaned entity (warn) -> 'warn' with >= 2 reasons."""
        # Do NOT populate blocking index -> first warn reason
        orphan_id = "orphan_multi"
        winner_id = "winner_multi"

        await _insert_signal_with_company_id(store, company_id=orphan_id)
        await _insert_entity_migration(store, from_entity_id=orphan_id, to_entity_id=winner_id)

        result = await check_phase_g_readiness(store)

        assert result.verdict == "warn"
        assert result.can_proceed is True
        assert len(result.reasons) >= 2
        # Should mention both blocking index and orphaned entities
        reasons_joined = " ".join(result.reasons).lower()
        assert "blocking" in reasons_joined or "empty" in reasons_joined
        assert "merged-away" in reasons_joined or "orphan" in reasons_joined

    @pytest.mark.asyncio
    async def test_claim_facts_contradiction_detected(self, store, monkeypatch):
        """USE_CLAIM_FACTS=true + contradictions -> claim_fact_contradictions >= 1."""
        monkeypatch.setenv("USE_CLAIM_FACTS", "true")

        await _populate_blocking_index(store, n=1)

        entity_id = "ent_contradiction"
        predicate = "company_name"

        # Insert two active claim facts for the same entity+predicate
        # (both have valid_until IS NULL and is_retracted = 0)
        await _insert_claim_fact(store, entity_id, predicate, "Acme Corp")
        await _insert_claim_fact(store, entity_id, predicate, "Acme Inc")

        result = await check_phase_g_readiness(store)

        assert result.metrics.get("claim_fact_contradictions", 0) >= 1
        assert any("contradiction" in r.lower() for r in result.reasons)

    @pytest.mark.asyncio
    async def test_claim_facts_skipped_when_disabled(self, store, monkeypatch):
        """USE_CLAIM_FACTS not set -> claim_facts_checked is False."""
        monkeypatch.delenv("USE_CLAIM_FACTS", raising=False)

        await _populate_blocking_index(store, n=1)

        result = await check_phase_g_readiness(store)

        assert result.metrics["claim_facts_checked"] is False

    @pytest.mark.asyncio
    async def test_zero_merge_suggestions_no_error(self, store):
        """No merge_suggestions rows -> no division by zero, rate = 0.0."""
        await _populate_blocking_index(store, n=1)

        result = await check_phase_g_readiness(store)

        assert result.metrics["merge_rejection_rate"] == 0.0
        assert result.metrics["merge_suggestions_total"] == 0
        assert result.metrics["merge_suggestions_rejected"] == 0
        # Should not be blocked or warned because of merge suggestions
        assert not any("rejection rate" in r.lower() for r in result.reasons)


class TestPhaseGEdgeCoverage:
    """Edge tests for Phase G readiness gate."""

    @pytest.mark.asyncio
    async def test_multiple_missing_tables_early_return(self, store):
        """Drop 2 required tables: blocked, both names listed, downstream metrics absent."""
        db = store._db
        await db.execute("DROP TABLE IF EXISTS entity_aliases")
        await db.execute("DROP TABLE IF EXISTS entity_key_aliases")
        await db.commit()

        result = await check_phase_g_readiness(store)

        assert result.verdict == "blocked"
        assert result.can_proceed is False
        # Both table names should appear in the reason
        reasons_text = " ".join(result.reasons)
        assert "entity_aliases" in reasons_text
        assert "entity_key_aliases" in reasons_text
        # Early return means downstream metrics are absent
        assert "blocking_index_rows" not in result.metrics
        assert "merge_suggestions_total" not in result.metrics
        assert "orphaned_entity_ids" not in result.metrics

    @pytest.mark.asyncio
    async def test_rejection_rate_exactly_at_threshold_does_not_block(self, store):
        """Exactly MAX_REJECTION_RATE does NOT block because code uses > not >=."""
        await _populate_blocking_index(store, n=1)

        # 10 suggestions, 1 rejected => rejection_rate = 0.10 = MAX_REJECTION_RATE exactly
        for i in range(9):
            await _insert_merge_suggestion(store, suggestion_id=f"ok_{i}", status="pending")
        await _insert_merge_suggestion(store, suggestion_id="rej_0", status="rejected")

        result = await check_phase_g_readiness(store)

        assert result.metrics["merge_rejection_rate"] == MAX_REJECTION_RATE
        # Exactly at threshold should NOT block (> not >=)
        assert result.verdict != "blocked"
        assert not any("rejection rate" in r.lower() for r in result.reasons)

    @pytest.mark.asyncio
    async def test_claim_facts_enabled_but_table_missing(self, store, monkeypatch):
        """USE_CLAIM_FACTS=true but claim_facts table missing: nonfatal, claim_facts_checked=False."""
        monkeypatch.setenv("USE_CLAIM_FACTS", "true")
        await _populate_blocking_index(store, n=1)

        # Drop the claim_facts table
        db = store._db
        await db.execute("DROP TABLE IF EXISTS claim_facts")
        await db.commit()

        result = await check_phase_g_readiness(store)

        # Should not block or error
        assert result.can_proceed is True
        # claim_facts_checked should be False (enabled but skipped because table missing)
        assert result.metrics["claim_facts_checked"] is False
        # Contradiction metrics should be absent (not checked)
        assert "claim_fact_contradictions" not in result.metrics


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

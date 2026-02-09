"""
Tests for v28 backfill: company_id population from canonical_key.

Covers:
- Dry-run produces correct mapping without modifying DB
- Apply mode updates all signals with company_id
- Post-backfill validator asserts no NULLs
- Root resolution handles merged entities
- Already-bound canonical keys reuse existing entity_id
- New canonical keys generate via entity_id_for_seed + register binding
"""

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from storage.entity_identity_store import EntityIdentityStore


def _entity_id(seed: str) -> str:
    """Mirror EntityIdentityStore.entity_id_for_seed."""
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]


@pytest_asyncio.fixture
async def store_with_null_company_ids():
    """Store with signals that have NULL company_id (pre-backfill state)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = SignalStore(db_path=path)
    await store.initialize()

    now = datetime.now(timezone.utc).isoformat()

    # Insert signals with NULL company_id (simulating pre-v28 state)
    for i, (key, name, source) in enumerate([
        ("domain:acme.ai", "Acme AI", "github"),
        ("domain:acme.ai", "Acme AI", "sec_edgar"),  # Same company, different source
        ("domain:startup.io", "Startup Inc", "product_hunt"),
        ("ein:987654321", "Health Co", "sec_edgar"),
    ], start=1):
        await store._db.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at, company_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            ("funding", source, key, name, 0.75, '{"test": true}', now, now)
        )
    await store._db.commit()

    yield store, path

    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def store_with_merged_entities():
    """Store with signals where some canonical_keys map to merged entities."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = SignalStore(db_path=path)
    await store.initialize()

    now = datetime.now(timezone.utc).isoformat()

    # Insert signals
    await store._db.execute(
        """INSERT INTO signals
           (signal_type, source_api, canonical_key, company_name,
            confidence, raw_data, detected_at, created_at, company_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
        ("funding", "github", "domain:old-name.com", "Old Name Inc", 0.75, '{}', now, now)
    )
    await store._db.commit()

    # Set up entity_aliases: domain:old-name.com -> entity_id_A
    entity_id_a = _entity_id("domain:old-name.com")
    entity_id_b = _entity_id("domain:new-name.com")  # merge winner
    winner = min(entity_id_a, entity_id_b)
    loser = max(entity_id_a, entity_id_b)

    await store._db.execute(
        """INSERT INTO entity_aliases (strong_key, entity_id, created_at, source_signal_id, source_key)
           VALUES (?, ?, ?, NULL, 'test')""",
        ("domain:old-name.com", loser, now)
    )
    # Set up entity_migrations: loser -> winner
    await store._db.execute(
        """INSERT INTO entity_migrations (from_entity_id, to_entity_id, merged_at, merge_reason)
           VALUES (?, ?, ?, 'test_merge')""",
        (loser, winner, now)
    )
    await store._db.commit()

    yield store, path, winner, loser

    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


# =============================================================================
# BACKFILL DRY-RUN TESTS
# =============================================================================

class TestBackfillDryRun:
    """Tests for backfill dry-run mode (no DB modifications)."""

    @pytest.mark.asyncio
    async def test_dry_run_returns_mapping(self, store_with_null_company_ids):
        """Dry-run should return signal_id -> company_id mapping without modifying DB."""
        store, path = store_with_null_company_ids

        from storage.migrations.backfill_v28_identity import backfill_company_ids

        result = await backfill_company_ids(store, dry_run=True)

        # Should return a report with mappings
        assert result is not None
        assert result["mode"] == "dry_run"
        assert result["total_signals"] == 4
        assert result["null_count_before"] == 4
        assert len(result["mappings"]) == 4

    @pytest.mark.asyncio
    async def test_dry_run_does_not_modify_db(self, store_with_null_company_ids):
        """Dry-run must not UPDATE any signals."""
        store, path = store_with_null_company_ids

        from storage.migrations.backfill_v28_identity import backfill_company_ids

        await backfill_company_ids(store, dry_run=True)

        # Verify no company_ids were set
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id IS NOT NULL"
        )
        count = (await cursor.fetchone())[0]
        assert count == 0, "Dry-run should not modify any signals"

    @pytest.mark.asyncio
    async def test_dry_run_same_canonical_key_gets_same_id(self, store_with_null_company_ids):
        """Two signals with same canonical_key should map to same company_id."""
        store, path = store_with_null_company_ids

        from storage.migrations.backfill_v28_identity import backfill_company_ids

        result = await backfill_company_ids(store, dry_run=True)

        # Signals 1 and 2 both have domain:acme.ai
        acme_ids = [
            m["company_id"] for m in result["mappings"]
            if m["canonical_key"] == "domain:acme.ai"
        ]
        assert len(acme_ids) == 2
        assert acme_ids[0] == acme_ids[1], "Same canonical_key should produce same company_id"
        assert acme_ids[0] == _entity_id("domain:acme.ai")


# =============================================================================
# BACKFILL APPLY TESTS
# =============================================================================

class TestBackfillApply:
    """Tests for backfill apply mode (writes company_id to DB)."""

    @pytest.mark.asyncio
    async def test_apply_updates_all_signals(self, store_with_null_company_ids):
        """Apply mode should set company_id on all signals."""
        store, path = store_with_null_company_ids

        from storage.migrations.backfill_v28_identity import backfill_company_ids

        result = await backfill_company_ids(store, dry_run=False)

        assert result["mode"] == "apply"

        # Verify all signals have company_id
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id IS NULL"
        )
        null_count = (await cursor.fetchone())[0]
        assert null_count == 0, "All signals should have company_id after apply"

    @pytest.mark.asyncio
    async def test_apply_uses_correct_entity_ids(self, store_with_null_company_ids):
        """Applied company_ids should match entity_id_for_seed(canonical_key)."""
        store, path = store_with_null_company_ids

        from storage.migrations.backfill_v28_identity import backfill_company_ids

        await backfill_company_ids(store, dry_run=False)

        cursor = await store._db.execute(
            "SELECT canonical_key, company_id FROM signals"
        )
        rows = await cursor.fetchall()

        for key, cid in rows:
            assert cid == _entity_id(key), f"company_id mismatch for {key}"

    @pytest.mark.asyncio
    async def test_apply_registers_strong_key_bindings(self, store_with_null_company_ids):
        """Apply should register new canonical_keys in entity_aliases."""
        store, path = store_with_null_company_ids

        from storage.migrations.backfill_v28_identity import backfill_company_ids

        await backfill_company_ids(store, dry_run=False)

        # Check entity_aliases has entries for each unique canonical_key
        cursor = await store._db.execute(
            "SELECT strong_key, entity_id FROM entity_aliases WHERE source_key = 'backfill'"
        )
        bindings = {row[0]: row[1] for row in await cursor.fetchall()}

        assert "domain:acme.ai" in bindings
        assert "domain:startup.io" in bindings
        assert "ein:987654321" in bindings
        assert bindings["domain:acme.ai"] == _entity_id("domain:acme.ai")


# =============================================================================
# ROOT RESOLUTION TESTS
# =============================================================================

class TestBackfillRootResolution:
    """Tests for backfill handling of merged entities."""

    @pytest.mark.asyncio
    async def test_resolves_merged_entity_to_root(self, store_with_merged_entities):
        """Backfill should follow entity_migrations to find root entity_id."""
        store, path, winner, loser = store_with_merged_entities

        from storage.migrations.backfill_v28_identity import backfill_company_ids

        result = await backfill_company_ids(store, dry_run=False)

        # The signal's company_id should be the merge winner, not the direct alias
        cursor = await store._db.execute(
            "SELECT company_id FROM signals WHERE canonical_key = 'domain:old-name.com'"
        )
        row = await cursor.fetchone()
        assert row[0] == winner, f"Should resolve to merge winner {winner}, got {row[0]}"


# =============================================================================
# VALIDATOR TESTS
# =============================================================================

class TestBackfillValidator:
    """Tests for post-backfill validation (shared with Task 3 migration gate)."""

    @pytest.mark.asyncio
    async def test_validator_passes_when_all_populated(self, store_with_null_company_ids):
        """Validator should pass after successful backfill."""
        store, path = store_with_null_company_ids

        from storage.migrations.backfill_v28_identity import backfill_company_ids, validate_company_ids

        await backfill_company_ids(store, dry_run=False)
        result = await validate_company_ids(store)

        assert result["valid"] is True
        assert result["null_count"] == 0
        assert result["total_signals"] == 4

    @pytest.mark.asyncio
    async def test_validator_fails_when_nulls_exist(self, store_with_null_company_ids):
        """Validator should fail when signals have NULL company_id."""
        store, path = store_with_null_company_ids

        from storage.migrations.backfill_v28_identity import validate_company_ids

        result = await validate_company_ids(store)

        assert result["valid"] is False
        assert result["null_count"] == 4

    @pytest.mark.asyncio
    async def test_validator_reports_metrics(self, store_with_null_company_ids):
        """Validator should report counts of merge-resolved vs newly-generated IDs."""
        store, path = store_with_null_company_ids

        from storage.migrations.backfill_v28_identity import backfill_company_ids, validate_company_ids

        result = await backfill_company_ids(store, dry_run=False)

        assert "newly_generated" in result
        assert "merge_resolved" in result
        assert result["newly_generated"] >= 0
        assert result["merge_resolved"] >= 0

    @pytest.mark.asyncio
    async def test_validator_on_empty_db(self):
        """Validator should pass on empty database (no signals)."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        store = SignalStore(db_path=path)
        await store.initialize()

        try:
            from storage.migrations.backfill_v28_identity import validate_company_ids

            result = await validate_company_ids(store)
            assert result["valid"] is True
            assert result["null_count"] == 0
            assert result["total_signals"] == 0
        finally:
            await store.close()
            try:
                os.unlink(path)
            except OSError:
                pass

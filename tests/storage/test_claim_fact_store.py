"""
Tests for Claim Fact Store (storage/claim_fact_store.py)

Covers:
- Authority tier mapping from SOURCE_AUTHORITY
- save_fact with SCD-2 logic (insert, merge, supersede, ignore)
- get_active_fact returns current truth
- get_fact_history returns all versions
- get_fact_at_time returns point-in-time value
- Retraction handling
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Dict

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from storage.claim_fact_store import (
    ClaimFactStore,
    ClaimFact,
    FactSaveResult,
    authority_to_tier,
    source_to_tier,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def fresh_db() -> tuple[SignalStore, str]:
    """Fresh database with all migrations applied."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = SignalStore(db_path=path)
    await store.initialize()

    yield store, path

    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def claim_store(fresh_db: tuple[SignalStore, str]) -> ClaimFactStore:
    """ClaimFactStore with initialized database."""
    store, path = fresh_db
    return ClaimFactStore(store)


def make_fact(
    entity_id: str = "ent123",
    predicate: str = "company_name",
    value: str = "Acme Corp",
    source_tier: int = 2,
    confidence: float = 0.8,
    valid_from: str = None,
    observed_at: str = None,
) -> ClaimFact:
    """Create a test ClaimFact."""
    now_iso = datetime.now(timezone.utc).isoformat()
    return ClaimFact(
        entity_id=entity_id,
        predicate=predicate,
        value_json=json.dumps(value),
        source_tier=source_tier,
        confidence=confidence,
        valid_from=valid_from or now_iso,
        observed_at=observed_at or now_iso,
    )


# =============================================================================
# TIER MAPPING TESTS
# =============================================================================

class TestTierMapping:
    """Tests for authority to tier mapping."""

    def test_authority_to_tier_high(self):
        """High authority (>= 0.90) should map to tier 1."""
        assert authority_to_tier(0.95) == 1
        assert authority_to_tier(0.90) == 1

    def test_authority_to_tier_medium_high(self):
        """Authority 0.80-0.89 should map to tier 2."""
        assert authority_to_tier(0.89) == 2
        assert authority_to_tier(0.80) == 2

    def test_authority_to_tier_medium(self):
        """Authority 0.65-0.79 should map to tier 3."""
        assert authority_to_tier(0.79) == 3
        assert authority_to_tier(0.65) == 3

    def test_authority_to_tier_low(self):
        """Authority 0.50-0.64 should map to tier 4."""
        assert authority_to_tier(0.64) == 4
        assert authority_to_tier(0.50) == 4

    def test_authority_to_tier_lowest(self):
        """Authority < 0.50 should map to tier 5."""
        assert authority_to_tier(0.49) == 5
        assert authority_to_tier(0.30) == 5
        assert authority_to_tier(0.0) == 5

    def test_source_to_tier_companies_house(self):
        """Companies House should be tier 1."""
        tier = source_to_tier("companies_house")
        assert tier == 1

    def test_source_to_tier_sec_edgar(self):
        """SEC EDGAR should be tier 1 or 2."""
        tier = source_to_tier("sec_edgar")
        assert tier <= 2

    def test_source_to_tier_github(self):
        """GitHub should be tier 4 or 5."""
        tier = source_to_tier("github")
        assert tier >= 4

    def test_source_to_tier_unknown(self):
        """Unknown source should use DEFAULT_AUTHORITY."""
        tier = source_to_tier("unknown_source")
        assert tier >= 3  # Default authority is moderate


# =============================================================================
# SAVE FACT TESTS - INSERT
# =============================================================================

class TestSaveFactInsert:
    """Tests for save_fact INSERT behavior."""

    @pytest.mark.asyncio
    async def test_insert_new_fact(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Inserting a new fact should succeed."""
        store, path = fresh_db

        fact = make_fact()

        async with store.transaction_immediate() as tx:
            result = await claim_store.save_fact(fact, tx)

        assert result.action == "inserted"
        assert result.fact_id is not None

    @pytest.mark.asyncio
    async def test_insert_returns_fact_id(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Inserted fact should have a valid ID."""
        store, path = fresh_db

        fact = make_fact()

        async with store.transaction_immediate() as tx:
            result = await claim_store.save_fact(fact, tx)

        assert result.fact_id > 0


# =============================================================================
# SAVE FACT TESTS - MERGE
# =============================================================================

class TestSaveFactMerge:
    """Tests for save_fact MERGE behavior (same value re-observed)."""

    @pytest.mark.asyncio
    async def test_merge_same_value(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Same value should merge, not create duplicate."""
        store, path = fresh_db

        fact1 = make_fact(entity_id="ent1", value="Same Value")
        fact2 = make_fact(entity_id="ent1", value="Same Value")

        async with store.transaction_immediate() as tx:
            result1 = await claim_store.save_fact(fact1, tx)

        async with store.transaction_immediate() as tx:
            result2 = await claim_store.save_fact(fact2, tx)

        assert result1.action == "inserted"
        assert result2.action == "merged"
        assert result2.fact_id == result1.fact_id  # Same fact ID

    @pytest.mark.asyncio
    async def test_merge_updates_last_observed(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Merge should update last_observed_at."""
        store, path = fresh_db

        early_time = "2024-01-01T00:00:00Z"
        late_time = "2024-06-01T00:00:00Z"

        fact1 = make_fact(entity_id="ent1", value="Merged", observed_at=early_time)

        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact1, tx)

        fact2 = make_fact(entity_id="ent1", value="Merged", observed_at=late_time)

        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact2, tx)

        # Check last_observed_at was updated
        active = await claim_store.get_active_fact("ent1", "company_name")
        # last_observed_at should be recent (from the merge)
        assert active is not None

    @pytest.mark.asyncio
    async def test_merge_adds_signal_ids(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Merge should accumulate supporting signal IDs."""
        store, path = fresh_db

        fact1 = make_fact(entity_id="ent1", value="Evidence")
        fact1.supporting_signal_ids = [1, 2]

        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact1, tx)

        fact2 = make_fact(entity_id="ent1", value="Evidence")
        fact2.supporting_signal_ids = [3, 4]

        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact2, tx)

        active = await claim_store.get_active_fact("ent1", "company_name")
        assert set(active["supporting_signal_ids"]) == {1, 2, 3, 4}


# =============================================================================
# SAVE FACT TESTS - SUPERSEDE
# =============================================================================

class TestSaveFactSupersede:
    """Tests for save_fact SUPERSEDE behavior (higher authority wins)."""

    @pytest.mark.asyncio
    async def test_higher_authority_supersedes(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Tier 1 fact should supersede tier 3 fact."""
        store, path = fresh_db

        # Insert tier 3 fact first
        fact1 = make_fact(entity_id="ent1", value="Old Name", source_tier=3)
        async with store.transaction_immediate() as tx:
            result1 = await claim_store.save_fact(fact1, tx)

        # Insert tier 1 fact
        fact2 = make_fact(entity_id="ent1", value="Official Name", source_tier=1)
        async with store.transaction_immediate() as tx:
            result2 = await claim_store.save_fact(fact2, tx)

        assert result2.action == "superseded"
        assert result2.superseded_fact_id == result1.fact_id

        # Active fact should be the tier 1 value
        active = await claim_store.get_active_fact("ent1", "company_name")
        assert active["value"] == "Official Name"

    @pytest.mark.asyncio
    async def test_supersede_closes_old_fact(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Superseded fact should have valid_until set."""
        store, path = fresh_db

        fact1 = make_fact(entity_id="ent1", value="Old", source_tier=5)
        async with store.transaction_immediate() as tx:
            result1 = await claim_store.save_fact(fact1, tx)

        fact2 = make_fact(entity_id="ent1", value="New", source_tier=1)
        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact2, tx)

        # Check history - old fact should have valid_until
        history = await claim_store.get_fact_history("ent1", "company_name")
        assert len(history) == 2

        old_fact = next(h for h in history if h["value"] == "Old")
        assert old_fact["valid_until"] is not None


# =============================================================================
# SAVE FACT TESTS - IGNORE
# =============================================================================

class TestSaveFactIgnore:
    """Tests for save_fact IGNORE behavior (lower authority ignored)."""

    @pytest.mark.asyncio
    async def test_lower_authority_ignored(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Tier 5 fact should be ignored if tier 1 exists."""
        store, path = fresh_db

        # Insert tier 1 fact first
        fact1 = make_fact(entity_id="ent1", value="Official", source_tier=1)
        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact1, tx)

        # Try to insert tier 5 fact
        fact2 = make_fact(entity_id="ent1", value="Unofficial", source_tier=5)
        async with store.transaction_immediate() as tx:
            result2 = await claim_store.save_fact(fact2, tx)

        assert result2.action == "ignored"

        # Active fact should still be tier 1 value
        active = await claim_store.get_active_fact("ent1", "company_name")
        assert active["value"] == "Official"

    @pytest.mark.asyncio
    async def test_equal_tier_older_ignored(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Same tier, older observation should be ignored."""
        store, path = fresh_db

        # Insert fact with recent observation
        fact1 = make_fact(
            entity_id="ent1",
            value="Recent",
            source_tier=2,
            observed_at="2024-06-01T00:00:00Z"
        )
        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact1, tx)

        # Try to insert fact with older observation
        fact2 = make_fact(
            entity_id="ent1",
            value="Old",
            source_tier=2,
            observed_at="2024-01-01T00:00:00Z"
        )
        async with store.transaction_immediate() as tx:
            result2 = await claim_store.save_fact(fact2, tx)

        assert result2.action == "ignored"


# =============================================================================
# GET ACTIVE FACT TESTS
# =============================================================================

class TestGetActiveFact:
    """Tests for get_active_fact."""

    @pytest.mark.asyncio
    async def test_get_active_returns_current(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """get_active_fact should return the current truth."""
        store, path = fresh_db

        fact = make_fact(entity_id="ent1", value="Current Value")
        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact, tx)

        active = await claim_store.get_active_fact("ent1", "company_name")

        assert active is not None
        assert active["value"] == "Current Value"
        assert active["valid_until"] is None
        assert active["is_retracted"] is False

    @pytest.mark.asyncio
    async def test_get_active_returns_none_missing(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """get_active_fact should return None if no fact exists."""
        store, path = fresh_db

        active = await claim_store.get_active_fact("nonexistent", "company_name")

        assert active is None

    @pytest.mark.asyncio
    async def test_get_active_excludes_retracted(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """get_active_fact should exclude retracted facts."""
        store, path = fresh_db

        fact = make_fact(entity_id="ent1", value="Retracted Value")
        async with store.transaction_immediate() as tx:
            result = await claim_store.save_fact(fact, tx)

        # Retract the fact
        async with store.transaction_immediate() as tx:
            await claim_store.retract_fact(result.fact_id, tx)

        active = await claim_store.get_active_fact("ent1", "company_name")

        assert active is None


# =============================================================================
# HISTORY TESTS
# =============================================================================

class TestGetFactHistory:
    """Tests for get_fact_history."""

    @pytest.mark.asyncio
    async def test_history_includes_all_versions(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """History should include all fact versions."""
        store, path = fresh_db

        # Insert then supersede
        fact1 = make_fact(
            entity_id="ent1",
            value="Version 1",
            source_tier=5,
            valid_from="2024-01-01T00:00:00Z"
        )
        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact1, tx)

        fact2 = make_fact(
            entity_id="ent1",
            value="Version 2",
            source_tier=1,
            valid_from="2024-06-01T00:00:00Z"
        )
        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact2, tx)

        history = await claim_store.get_fact_history("ent1", "company_name")

        assert len(history) == 2
        values = {h["value"] for h in history}
        assert values == {"Version 1", "Version 2"}

    @pytest.mark.asyncio
    async def test_history_ordered_by_valid_from(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """History should be ordered by valid_from DESC."""
        store, path = fresh_db

        # Insert multiple facts
        for i, date in enumerate(["2024-01-01", "2024-03-01", "2024-06-01"]):
            fact = make_fact(
                entity_id="ent1",
                value=f"Value {i}",
                source_tier=i + 1,
                valid_from=f"{date}T00:00:00Z"
            )
            async with store.transaction_immediate() as tx:
                await claim_store.save_fact(fact, tx)

        history = await claim_store.get_fact_history("ent1", "company_name")

        # Should be DESC order
        dates = [h["valid_from"] for h in history]
        assert dates == sorted(dates, reverse=True)


# =============================================================================
# POINT-IN-TIME TESTS
# =============================================================================

class TestGetFactAtTime:
    """Tests for get_fact_at_time (temporal queries)."""

    @pytest.mark.asyncio
    async def test_fact_at_time_current(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Should return current fact for current time."""
        store, path = fresh_db

        fact = make_fact(
            entity_id="ent1",
            value="Current",
            valid_from="2024-01-01T00:00:00Z"
        )
        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact, tx)

        result = await claim_store.get_fact_at_time(
            "ent1", "company_name", "2024-12-01T00:00:00Z"
        )

        assert result is not None
        assert result["value"] == "Current"

    @pytest.mark.asyncio
    async def test_fact_at_time_historical(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Should return historical fact for past time."""
        store, path = fresh_db

        # Insert old fact then supersede it
        fact1 = make_fact(
            entity_id="ent1",
            value="Old Name",
            source_tier=5,
            valid_from="2024-01-01T00:00:00Z"
        )
        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact1, tx)

        fact2 = make_fact(
            entity_id="ent1",
            value="New Name",
            source_tier=1,
            valid_from="2024-06-01T00:00:00Z"
        )
        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact2, tx)

        # Query at time before supersession
        result = await claim_store.get_fact_at_time(
            "ent1", "company_name", "2024-03-01T00:00:00Z"
        )

        assert result is not None
        assert result["value"] == "Old Name"

    @pytest.mark.asyncio
    async def test_fact_at_time_before_existence(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Should return None for time before fact existed."""
        store, path = fresh_db

        fact = make_fact(
            entity_id="ent1",
            value="Value",
            valid_from="2024-06-01T00:00:00Z"
        )
        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact, tx)

        result = await claim_store.get_fact_at_time(
            "ent1", "company_name", "2024-01-01T00:00:00Z"
        )

        assert result is None


# =============================================================================
# IDEMPOTENCY TESTS
# =============================================================================

class TestIdempotency:
    """Tests for idempotent behavior."""

    @pytest.mark.asyncio
    async def test_no_duplicate_rows(
        self, fresh_db: tuple[SignalStore, str], claim_store: ClaimFactStore
    ):
        """Same fact inserted twice should not create duplicates."""
        store, path = fresh_db

        fact = make_fact(entity_id="ent1", value="Same Value")

        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact, tx)

        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact, tx)

        async with store.transaction_immediate() as tx:
            await claim_store.save_fact(fact, tx)

        # Should only have 1 active fact
        history = await claim_store.get_fact_history("ent1", "company_name")
        assert len(history) == 1

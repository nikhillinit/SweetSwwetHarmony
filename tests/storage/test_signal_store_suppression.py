"""
Tests for SignalStore suppression cache operations.

Covers:
- update_suppression_cache: Bulk update cache from Notion
- check_suppression: Check if canonical key exists in cache
- TTL expiry: Expired entries should not be returned
- Bulk operations: Multiple entries at once
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore, SuppressionEntry


# =============================================================================
# UPDATE SUPPRESSION CACHE TESTS
# =============================================================================

class TestUpdateSuppressionCache:
    """Tests for update_suppression_cache method."""

    @pytest.mark.asyncio
    async def test_update_suppression_cache_single(self, store: SignalStore):
        """Should insert single entry."""
        entry = SuppressionEntry(
            canonical_key="domain:test.com",
            notion_page_id="notion-page-123",
            status="Source",
            company_name="Test Corp",
        )

        count = await store.update_suppression_cache([entry])

        assert count == 1

        # Verify stored
        result = await store.check_suppression("domain:test.com")
        assert result is not None
        assert result.notion_page_id == "notion-page-123"

    @pytest.mark.asyncio
    async def test_update_suppression_cache_multiple(self, store: SignalStore):
        """Should insert multiple entries."""
        entries = [
            SuppressionEntry(
                canonical_key=f"domain:test{i}.com",
                notion_page_id=f"notion-{i}",
                status="Source",
                company_name=f"Test Corp {i}",
            )
            for i in range(5)
        ]

        count = await store.update_suppression_cache(entries)

        assert count == 5

        # Verify all stored
        for i in range(5):
            result = await store.check_suppression(f"domain:test{i}.com")
            assert result is not None

    @pytest.mark.asyncio
    async def test_update_suppression_cache_upsert(self, store: SignalStore):
        """Should update existing entry."""
        entry1 = SuppressionEntry(
            canonical_key="domain:upsert.com",
            notion_page_id="old-page-id",
            status="Tracking",
            company_name="Old Name",
        )
        await store.update_suppression_cache([entry1])

        # Update same key with new data
        entry2 = SuppressionEntry(
            canonical_key="domain:upsert.com",
            notion_page_id="new-page-id",
            status="Source",
            company_name="New Name",
        )
        await store.update_suppression_cache([entry2])

        result = await store.check_suppression("domain:upsert.com")
        assert result.notion_page_id == "new-page-id"
        assert result.status == "Source"
        assert result.company_name == "New Name"

    @pytest.mark.asyncio
    async def test_update_suppression_cache_with_metadata(self, store: SignalStore):
        """Should store metadata."""
        entry = SuppressionEntry(
            canonical_key="domain:meta.com",
            notion_page_id="notion-meta",
            status="Source",
            metadata={"tags": ["fintech", "B2C"], "priority": "high"},
        )

        await store.update_suppression_cache([entry])

        result = await store.check_suppression("domain:meta.com")
        assert result.metadata == {"tags": ["fintech", "B2C"], "priority": "high"}


# =============================================================================
# CHECK SUPPRESSION TESTS
# =============================================================================

class TestCheckSuppression:
    """Tests for check_suppression method."""

    @pytest.mark.asyncio
    async def test_check_suppression_hit(self, store: SignalStore):
        """Should return entry when found."""
        entry = SuppressionEntry(
            canonical_key="domain:found.com",
            notion_page_id="notion-found",
            status="Source",
        )
        await store.update_suppression_cache([entry])

        result = await store.check_suppression("domain:found.com")

        assert result is not None
        assert result.canonical_key == "domain:found.com"

    @pytest.mark.asyncio
    async def test_check_suppression_miss(self, store: SignalStore):
        """Should return None when not found."""
        result = await store.check_suppression("domain:notfound.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_check_suppression_returns_all_fields(self, store: SignalStore):
        """Should return all stored fields."""
        entry = SuppressionEntry(
            canonical_key="domain:full.com",
            notion_page_id="notion-full-123",
            status="Source",
            company_name="Full Corp",
            cached_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            metadata={"source": "manual"},
        )
        await store.update_suppression_cache([entry])

        result = await store.check_suppression("domain:full.com")

        assert result.canonical_key == "domain:full.com"
        assert result.notion_page_id == "notion-full-123"
        assert result.status == "Source"
        assert result.company_name == "Full Corp"


# =============================================================================
# TTL EXPIRY TESTS
# =============================================================================

class TestSuppressionTTL:
    """Tests for suppression cache TTL expiry."""

    @pytest.mark.asyncio
    async def test_check_suppression_expired_returns_none(self, store: SignalStore):
        """Expired entries should not be returned."""
        entry = SuppressionEntry(
            canonical_key="domain:expired.com",
            notion_page_id="notion-expired",
            status="Source",
            cached_at=datetime.now(timezone.utc) - timedelta(days=10),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),  # Already expired
        )
        await store.update_suppression_cache([entry])

        result = await store.check_suppression("domain:expired.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_check_suppression_not_expired_returns_entry(self, store: SignalStore):
        """Non-expired entries should be returned."""
        entry = SuppressionEntry(
            canonical_key="domain:fresh.com",
            notion_page_id="notion-fresh",
            status="Source",
            cached_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),  # Still valid
        )
        await store.update_suppression_cache([entry])

        result = await store.check_suppression("domain:fresh.com")

        assert result is not None

    @pytest.mark.asyncio
    async def test_check_suppression_exact_expiry_boundary(self, store: SignalStore):
        """Entry at exact expiry boundary should not be returned."""
        # Set expires_at to now - should be expired
        entry = SuppressionEntry(
            canonical_key="domain:boundary.com",
            notion_page_id="notion-boundary",
            status="Source",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        await store.update_suppression_cache([entry])

        result = await store.check_suppression("domain:boundary.com")

        assert result is None


# =============================================================================
# BULK OPERATIONS TESTS
# =============================================================================

class TestSuppressionBulkOperations:
    """Tests for bulk suppression cache operations."""

    @pytest.mark.asyncio
    async def test_bulk_update_large_batch(self, store: SignalStore):
        """Should handle large batch updates."""
        entries = [
            SuppressionEntry(
                canonical_key=f"domain:bulk{i}.com",
                notion_page_id=f"notion-bulk-{i}",
                status="Source",
            )
            for i in range(100)
        ]

        count = await store.update_suppression_cache(entries)

        assert count == 100

        # Spot check a few
        assert await store.check_suppression("domain:bulk0.com") is not None
        assert await store.check_suppression("domain:bulk50.com") is not None
        assert await store.check_suppression("domain:bulk99.com") is not None

    @pytest.mark.asyncio
    async def test_bulk_update_mixed_operations(self, store: SignalStore):
        """Should handle mix of inserts and updates."""
        # Initial insert
        entry1 = SuppressionEntry(
            canonical_key="domain:mixed1.com",
            notion_page_id="old-id-1",
            status="Tracking",
        )
        await store.update_suppression_cache([entry1])

        # Mix of update and new inserts
        entries = [
            SuppressionEntry(
                canonical_key="domain:mixed1.com",  # Update
                notion_page_id="new-id-1",
                status="Source",
            ),
            SuppressionEntry(
                canonical_key="domain:mixed2.com",  # New
                notion_page_id="new-id-2",
                status="Source",
            ),
        ]
        count = await store.update_suppression_cache(entries)

        assert count == 2

        result1 = await store.check_suppression("domain:mixed1.com")
        assert result1.notion_page_id == "new-id-1"

        result2 = await store.check_suppression("domain:mixed2.com")
        assert result2 is not None


# =============================================================================
# EDGE CASES
# =============================================================================

class TestSuppressionEdgeCases:
    """Edge cases for suppression cache."""

    @pytest.mark.asyncio
    async def test_empty_entries_list(self, store: SignalStore):
        """Empty list should return 0."""
        count = await store.update_suppression_cache([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_suppression_with_special_characters_in_key(self, store: SignalStore):
        """Should handle special characters in canonical key."""
        entry = SuppressionEntry(
            canonical_key="name_loc:Acme Corp__New York, NY",
            notion_page_id="notion-special",
            status="Source",
        )
        await store.update_suppression_cache([entry])

        result = await store.check_suppression("name_loc:Acme Corp__New York, NY")
        assert result is not None

    @pytest.mark.asyncio
    async def test_suppression_cache_stores_statuses(self, store: SignalStore):
        """Should store various Notion statuses correctly."""
        statuses = ["Source", "Tracking", "Funded", "Passed", "Dilligence"]

        for i, status in enumerate(statuses):
            entry = SuppressionEntry(
                canonical_key=f"domain:status{i}.com",
                notion_page_id=f"notion-{i}",
                status=status,
            )
            await store.update_suppression_cache([entry])

        # Verify all statuses stored correctly
        for i, status in enumerate(statuses):
            result = await store.check_suppression(f"domain:status{i}.com")
            assert result.status == status

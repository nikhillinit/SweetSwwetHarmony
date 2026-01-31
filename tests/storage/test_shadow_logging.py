"""Tests for SHADOW logging infrastructure.

TDD tests for:
- shadow_log table (migration 23)
- log_shadow_computation() method
- get_shadow_logs() query method
- count_shadow_logs() method
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION


# =============================================================================
# MIGRATION TESTS
# =============================================================================

class TestShadowLogMigration:
    """Tests for shadow_log table creation."""

    @pytest.mark.asyncio
    async def test_schema_version_is_23(self):
        """Schema version should be 23 after adding shadow_log."""
        assert CURRENT_SCHEMA_VERSION >= 23

    @pytest.mark.asyncio
    async def test_shadow_log_table_exists(self, store: SignalStore):
        """shadow_log table should exist after initialize."""
        async with store.transaction() as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='shadow_log'"
            )
            result = await cursor.fetchone()
            assert result is not None, "shadow_log table should exist"

    @pytest.mark.asyncio
    async def test_shadow_log_table_has_required_columns(self, store: SignalStore):
        """shadow_log table should have all required columns."""
        async with store.transaction() as conn:
            cursor = await conn.execute("PRAGMA table_info(shadow_log)")
            columns = {row[1] for row in await cursor.fetchall()}

        expected_columns = {
            "id",
            "feature_name",
            "canonical_key",
            "computed_value",
            "signal_id",
            "logged_at",
        }
        assert expected_columns.issubset(columns), f"Missing columns: {expected_columns - columns}"

    @pytest.mark.asyncio
    async def test_shadow_log_indexes_exist(self, store: SignalStore):
        """shadow_log should have indexes for common queries."""
        async with store.transaction() as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '%shadow_log%'"
            )
            indexes = {row[0] for row in await cursor.fetchall()}

        # Should have indexes on feature_name, canonical_key, logged_at
        assert any("feature" in idx for idx in indexes), "Should have feature_name index"
        assert any("canonical" in idx or "key" in idx for idx in indexes), "Should have canonical_key index"


# =============================================================================
# LOG SHADOW COMPUTATION TESTS
# =============================================================================

class TestLogShadowComputation:
    """Tests for log_shadow_computation() method."""

    @pytest.mark.asyncio
    async def test_log_shadow_computation_basic(self, store: SignalStore):
        """Can log a basic shadow computation."""
        log_id = await store.log_shadow_computation(
            feature_name="boilerplate_defense",
            canonical_key="github_org:test-repo",
            computed_value={"match": True, "similarity": 0.85, "template": "nextjs_basic"},
        )

        assert isinstance(log_id, int)
        assert log_id > 0

    @pytest.mark.asyncio
    async def test_log_shadow_computation_with_signal_id(self, store: SignalStore, sample_signal_data: Dict[str, Any]):
        """Can link shadow computation to a signal."""
        # First create a signal
        signal_id = await store.save_signal(**sample_signal_data)

        # Log computation linked to signal
        log_id = await store.log_shadow_computation(
            feature_name="team_shape",
            canonical_key=sample_signal_data["canonical_key"],
            computed_value={"contributors": 3, "concentration": 0.6},
            signal_id=signal_id,
        )

        assert log_id > 0

    @pytest.mark.asyncio
    async def test_log_shadow_computation_without_signal_id(self, store: SignalStore):
        """Can log without signal_id (for aggregate computations)."""
        log_id = await store.log_shadow_computation(
            feature_name="founder_surfaces",
            canonical_key="github_user:founder123",
            computed_value={"profile_readme": True, "intent_markers": ["pricing", "waitlist"]},
        )

        assert log_id > 0

    @pytest.mark.asyncio
    async def test_log_shadow_computation_stores_timestamp(self, store: SignalStore):
        """logged_at should be set automatically."""
        before = datetime.now(timezone.utc)

        await store.log_shadow_computation(
            feature_name="boilerplate_defense",
            canonical_key="github_org:test",
            computed_value={"match": False},
        )

        after = datetime.now(timezone.utc)

        logs = await store.get_shadow_logs(feature_name="boilerplate_defense", limit=1)
        assert len(logs) == 1
        assert before <= logs[0]["logged_at"] <= after

    @pytest.mark.asyncio
    async def test_log_shadow_computation_json_serialization(self, store: SignalStore):
        """computed_value should serialize complex JSON correctly."""
        complex_value = {
            "nested": {"deep": {"value": 123}},
            "list": [1, 2, 3],
            "unicode": "测试",
            "bool": True,
            "null": None,
        }

        log_id = await store.log_shadow_computation(
            feature_name="test_feature",
            canonical_key="test:key",
            computed_value=complex_value,
        )

        logs = await store.get_shadow_logs(feature_name="test_feature", limit=1)
        assert logs[0]["computed_value"] == complex_value


# =============================================================================
# GET SHADOW LOGS TESTS
# =============================================================================

class TestGetShadowLogs:
    """Tests for get_shadow_logs() query method."""

    @pytest.mark.asyncio
    async def test_get_shadow_logs_empty(self, store: SignalStore):
        """Returns empty list when no logs exist."""
        logs = await store.get_shadow_logs()
        assert logs == []

    @pytest.mark.asyncio
    async def test_get_shadow_logs_all(self, store: SignalStore):
        """Returns all logs when no filters."""
        # Create multiple logs
        await store.log_shadow_computation("feature_a", "key:1", {"value": 1})
        await store.log_shadow_computation("feature_b", "key:2", {"value": 2})
        await store.log_shadow_computation("feature_a", "key:3", {"value": 3})

        logs = await store.get_shadow_logs()
        assert len(logs) == 3

    @pytest.mark.asyncio
    async def test_get_shadow_logs_filter_by_feature(self, store: SignalStore):
        """Can filter by feature_name."""
        await store.log_shadow_computation("boilerplate_defense", "key:1", {"match": True})
        await store.log_shadow_computation("team_shape", "key:2", {"contributors": 3})
        await store.log_shadow_computation("boilerplate_defense", "key:3", {"match": False})

        logs = await store.get_shadow_logs(feature_name="boilerplate_defense")
        assert len(logs) == 2
        assert all(log["feature_name"] == "boilerplate_defense" for log in logs)

    @pytest.mark.asyncio
    async def test_get_shadow_logs_filter_by_canonical_key(self, store: SignalStore):
        """Can filter by canonical_key."""
        await store.log_shadow_computation("feature_a", "github_org:acme", {"value": 1})
        await store.log_shadow_computation("feature_b", "github_org:acme", {"value": 2})
        await store.log_shadow_computation("feature_a", "github_org:other", {"value": 3})

        logs = await store.get_shadow_logs(canonical_key="github_org:acme")
        assert len(logs) == 2
        assert all(log["canonical_key"] == "github_org:acme" for log in logs)

    @pytest.mark.asyncio
    async def test_get_shadow_logs_filter_by_since(self, store: SignalStore):
        """Can filter by timestamp."""
        # Log something
        await store.log_shadow_computation("feature", "key:1", {"old": True})

        # Wait a moment
        await asyncio.sleep(0.01)
        cutoff = datetime.now(timezone.utc)
        await asyncio.sleep(0.01)

        # Log more
        await store.log_shadow_computation("feature", "key:2", {"new": True})
        await store.log_shadow_computation("feature", "key:3", {"new": True})

        logs = await store.get_shadow_logs(since=cutoff)
        assert len(logs) == 2
        assert all(log["logged_at"] >= cutoff for log in logs)

    @pytest.mark.asyncio
    async def test_get_shadow_logs_limit(self, store: SignalStore):
        """Respects limit parameter."""
        for i in range(10):
            await store.log_shadow_computation("feature", f"key:{i}", {"i": i})

        logs = await store.get_shadow_logs(limit=5)
        assert len(logs) == 5

    @pytest.mark.asyncio
    async def test_get_shadow_logs_returns_dict(self, store: SignalStore):
        """Each log should be a dict with expected keys."""
        await store.log_shadow_computation("feature", "key:1", {"value": 42})

        logs = await store.get_shadow_logs()
        assert len(logs) == 1

        log = logs[0]
        assert "id" in log
        assert "feature_name" in log
        assert "canonical_key" in log
        assert "computed_value" in log
        assert "signal_id" in log
        assert "logged_at" in log

    @pytest.mark.asyncio
    async def test_get_shadow_logs_ordered_by_logged_at_desc(self, store: SignalStore):
        """Logs should be returned newest first."""
        await store.log_shadow_computation("feature", "key:1", {"order": 1})
        await asyncio.sleep(0.01)
        await store.log_shadow_computation("feature", "key:2", {"order": 2})
        await asyncio.sleep(0.01)
        await store.log_shadow_computation("feature", "key:3", {"order": 3})

        logs = await store.get_shadow_logs()
        # Newest first
        assert logs[0]["computed_value"]["order"] == 3
        assert logs[1]["computed_value"]["order"] == 2
        assert logs[2]["computed_value"]["order"] == 1


# =============================================================================
# COUNT SHADOW LOGS TESTS
# =============================================================================

class TestCountShadowLogs:
    """Tests for count_shadow_logs() method."""

    @pytest.mark.asyncio
    async def test_count_shadow_logs_empty(self, store: SignalStore):
        """Returns 0 when no logs exist."""
        count = await store.count_shadow_logs()
        assert count == 0

    @pytest.mark.asyncio
    async def test_count_shadow_logs_all(self, store: SignalStore):
        """Counts all logs when no filter."""
        for i in range(5):
            await store.log_shadow_computation("feature", f"key:{i}", {"i": i})

        count = await store.count_shadow_logs()
        assert count == 5

    @pytest.mark.asyncio
    async def test_count_shadow_logs_by_feature(self, store: SignalStore):
        """Can count by feature_name."""
        await store.log_shadow_computation("feature_a", "key:1", {})
        await store.log_shadow_computation("feature_a", "key:2", {})
        await store.log_shadow_computation("feature_b", "key:3", {})

        count_a = await store.count_shadow_logs(feature_name="feature_a")
        count_b = await store.count_shadow_logs(feature_name="feature_b")

        assert count_a == 2
        assert count_b == 1

"""
Tests for EntityIdentityStore alias filtering: archived and expired aliases.

Covers:
- lookup_alias_keys excludes archived aliases
- lookup_alias_keys excludes expired aliases
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from storage.entity_identity_store import EntityIdentityStore


@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with all migrations applied."""
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


@pytest_asyncio.fixture
async def identity_store(store):
    return EntityIdentityStore(store)


async def _insert_alias(store, alias_key, entity_id, *,
                         archived_at=None, expires_at=None):
    """Insert a row into entity_key_aliases for testing."""
    db = store._db
    await db.execute(
        """INSERT INTO entity_key_aliases
           (alias_key, entity_id, alias_type, confidence, source, expires_at, archived_at, created_at)
           VALUES (?, ?, 'name_norm', 0.8, 'test', ?, ?, datetime('now'))""",
        (alias_key, entity_id, expires_at, archived_at),
    )
    await db.commit()


class TestAliasFiltering:

    @pytest.mark.asyncio
    async def test_archived_alias_excluded(self, store, identity_store):
        """An alias with archived_at set should not be returned by lookup_alias_keys."""
        await _insert_alias(
            store, "name_norm:old_corp", "ent_001",
            archived_at="2026-01-01T00:00:00+00:00",
        )
        # Also insert an active alias to confirm the query works
        await _insert_alias(store, "name_norm:active_corp", "ent_002")

        result = await identity_store.lookup_alias_keys(
            ["name_norm:old_corp", "name_norm:active_corp"]
        )

        assert "name_norm:old_corp" not in result
        assert "name_norm:active_corp" in result

    @pytest.mark.asyncio
    async def test_expired_alias_excluded(self, store, identity_store):
        """An alias whose expires_at is in the past should not be returned."""
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        await _insert_alias(
            store, "name_norm:expired_co", "ent_003",
            expires_at=past,
        )
        await _insert_alias(
            store, "name_norm:future_co", "ent_004",
            expires_at=future,
        )

        result = await identity_store.lookup_alias_keys(
            ["name_norm:expired_co", "name_norm:future_co"]
        )

        assert "name_norm:expired_co" not in result
        assert "name_norm:future_co" in result

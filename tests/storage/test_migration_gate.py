"""
Tests for Task 3: Migration gate — check_identity_integrity().

Uses the unified validator from backfill_v28_identity.validate_company_ids().
Pipeline should refuse to run if any signal has NULL company_id.

Covers:
- Gate blocks when NULLs exist (raises IdentityMigrationRequired)
- Gate passes when all signals have company_id
- Gate passes on empty database
- Error message includes actionable backfill command
"""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore


@pytest_asyncio.fixture
async def store_all_populated():
    """Store with signals that all have company_id set."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = SignalStore(db_path=path)
    await store.initialize()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    for key in ["domain:a.com", "domain:b.com"]:
        await store._db.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at, company_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("funding", "github", key, "Test", 0.75, '{}', now, now, f"id_{key}")
        )
    await store._db.commit()

    yield store

    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def store_with_nulls():
    """Store with signals that have NULL company_id."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = SignalStore(db_path=path)
    await store.initialize()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    for key in ["domain:a.com", "domain:b.com", "domain:c.com"]:
        await store._db.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at, company_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            ("funding", "github", key, "Test", 0.75, '{}', now, now)
        )
    await store._db.commit()

    yield store

    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def empty_store():
    """Store with no signals."""
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


class TestMigrationGate:
    """Tests for check_identity_integrity() migration gate."""

    @pytest.mark.asyncio
    async def test_gate_passes_when_all_populated(self, store_all_populated):
        """Gate should not raise when all signals have company_id."""
        from storage.identity_gate import check_identity_integrity

        # Should not raise
        await check_identity_integrity(store_all_populated)

    @pytest.mark.asyncio
    async def test_gate_blocks_when_nulls_exist(self, store_with_nulls):
        """Gate should raise IdentityMigrationRequired when NULLs exist."""
        from storage.identity_gate import (
            check_identity_integrity,
            IdentityMigrationRequired,
        )

        with pytest.raises(IdentityMigrationRequired) as exc_info:
            await check_identity_integrity(store_with_nulls)

        # Error message should include count and actionable command
        msg = str(exc_info.value)
        assert "3" in msg, "Should report count of NULL signals"
        assert "backfill" in msg.lower(), "Should mention backfill command"

    @pytest.mark.asyncio
    async def test_gate_passes_on_empty_db(self, empty_store):
        """Gate should pass on database with no signals."""
        from storage.identity_gate import check_identity_integrity

        # Should not raise
        await check_identity_integrity(empty_store)

    @pytest.mark.asyncio
    async def test_gate_error_is_actionable(self, store_with_nulls):
        """Error message should include the specific command to fix."""
        from storage.identity_gate import (
            check_identity_integrity,
            IdentityMigrationRequired,
        )

        with pytest.raises(IdentityMigrationRequired) as exc_info:
            await check_identity_integrity(store_with_nulls)

        msg = str(exc_info.value)
        assert "backfill_v28_identity" in msg, "Should reference backfill module"
        assert '--db "$DISCOVERY_DB_PATH"' in msg, "Should avoid repo-local DB path"
        assert "--apply" in msg, "Should include --apply flag"

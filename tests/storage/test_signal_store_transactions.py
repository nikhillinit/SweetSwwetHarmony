"""
Tests for SignalStore transaction semantics.

Covers:
- Commit on success
- Rollback on exception
- RuntimeError before initialize()

Uses direct SQL against a scratch table — does NOT route through
save_signal() or status helpers.
"""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore


class TestSignalStoreTransactions:
    """Transaction commit / rollback / pre-init behaviour."""

    @pytest_asyncio.fixture
    async def store(self):
        """Fresh SignalStore with temp DB."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        s = SignalStore(db_path=path)
        await s.initialize()
        # Create a tiny scratch table for isolation
        await s._db.execute(
            "CREATE TABLE _tx_scratch (id INTEGER PRIMARY KEY, val TEXT)"
        )
        await s._db.commit()
        yield s
        await s.close()
        try:
            os.unlink(path)
        except OSError:
            pass

    @pytest.mark.asyncio
    async def test_transaction_commits_on_success(self, store: SignalStore):
        """Successful block commits the INSERT."""
        async with store.transaction() as conn:
            await conn.execute(
                "INSERT INTO _tx_scratch (id, val) VALUES (1, 'committed')"
            )

        cursor = await store._db.execute("SELECT val FROM _tx_scratch WHERE id = 1")
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "committed"

    @pytest.mark.asyncio
    async def test_transaction_rolls_back_on_exception(self, store: SignalStore):
        """Exception inside the block rolls back — no row persisted."""
        with pytest.raises(ValueError, match="boom"):
            async with store.transaction() as conn:
                await conn.execute(
                    "INSERT INTO _tx_scratch (id, val) VALUES (2, 'should_vanish')"
                )
                raise ValueError("boom")

        cursor = await store._db.execute("SELECT val FROM _tx_scratch WHERE id = 2")
        row = await cursor.fetchone()
        assert row is None

    @pytest.mark.asyncio
    async def test_transaction_raises_before_initialize(self):
        """Calling transaction() on an uninitialized store raises RuntimeError."""
        uninit = SignalStore(db_path=":memory:")
        # Do NOT call initialize()
        with pytest.raises(RuntimeError, match="not initialized"):
            async with uninit.transaction():
                pass

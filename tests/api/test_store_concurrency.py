"""PR8 — Tests for concurrent API store access safety.

Validates that the shared SignalStore via app.state.store handles
concurrent access without lifecycle/threading errors. The write lock
(app.state.write_lock) serializes writes.
"""

import asyncio
import os
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestWriteLockSerialization:
    """Test that the write lock properly serializes concurrent writes."""

    def test_write_lock_is_created_in_get_write_lock(self):
        """get_write_lock creates a lock if none exists."""
        from api.db import get_write_lock
        import api.db as db_module

        # Reset global
        original = db_module._write_lock
        db_module._write_lock = None

        try:
            lock = get_write_lock()
            assert isinstance(lock, asyncio.Lock)
        finally:
            db_module._write_lock = original

    def test_set_write_lock(self):
        """set_write_lock stores the lock globally."""
        from api.db import set_write_lock, get_write_lock
        import api.db as db_module

        original = db_module._write_lock
        try:
            custom_lock = asyncio.Lock()
            set_write_lock(custom_lock)
            assert get_write_lock() is custom_lock
        finally:
            db_module._write_lock = original

    def test_write_lock_from_request_uses_app_state(self):
        """get_write_lock_from_request prefers request.app.state.write_lock."""
        from api.db import get_write_lock_from_request

        app_lock = asyncio.Lock()
        mock_request = MagicMock()
        mock_request.app.state.write_lock = app_lock

        result = get_write_lock_from_request(mock_request)
        assert result is app_lock

    def test_write_lock_from_request_falls_back_to_global(self):
        """get_write_lock_from_request falls back to global lock."""
        from api.db import get_write_lock_from_request

        mock_request = MagicMock()
        mock_request.app.state = MagicMock(spec=[])  # No write_lock attribute

        result = get_write_lock_from_request(mock_request)
        assert isinstance(result, asyncio.Lock)


class TestConcurrentReadAccess:
    """Test that concurrent reads through the shared store don't conflict."""

    @pytest.mark.asyncio
    async def test_concurrent_get_store_returns_same_instance(self):
        """Multiple concurrent get_store calls return the same store."""
        from api.db import get_store

        mock_store = MagicMock()
        mock_request = MagicMock()
        mock_request.app.state.store = mock_store

        results = await asyncio.gather(
            get_store(mock_request),
            get_store(mock_request),
            get_store(mock_request),
        )

        # All should be the exact same store instance
        assert all(r is mock_store for r in results)


class TestWriteTransaction:
    """Test write_transaction context manager."""

    @pytest.mark.asyncio
    async def test_write_transaction_acquires_lock(self):
        """write_transaction acquires and releases the write lock."""
        from api.db import write_transaction

        lock = asyncio.Lock()
        mock_request = MagicMock()
        mock_request.app.state.write_lock = lock

        assert not lock.locked()
        async with write_transaction(mock_request):
            assert lock.locked()
        assert not lock.locked()

    @pytest.mark.asyncio
    async def test_write_transaction_serializes_access(self):
        """Two concurrent write_transactions don't overlap."""
        from api.db import write_transaction

        lock = asyncio.Lock()
        mock_request = MagicMock()
        mock_request.app.state.write_lock = lock

        execution_order = []

        async def writer(name: str, delay: float):
            async with write_transaction(mock_request):
                execution_order.append(f"{name}_start")
                await asyncio.sleep(delay)
                execution_order.append(f"{name}_end")

        await asyncio.gather(
            writer("a", 0.05),
            writer("b", 0.05),
        )

        # Verify serialization: one must complete before the other starts
        a_start = execution_order.index("a_start")
        a_end = execution_order.index("a_end")
        b_start = execution_order.index("b_start")
        b_end = execution_order.index("b_end")

        # Either a completes before b starts, or b completes before a starts
        assert (a_end < b_start) or (b_end < a_start), (
            f"Write transactions overlapped: {execution_order}"
        )


class TestExecuteWrite:
    """Test execute_write helper."""

    @pytest.mark.asyncio
    async def test_execute_write_commits(self):
        """execute_write acquires lock, executes, and commits."""
        from api.db import execute_write
        import api.db as db_module

        # Set up a fresh lock
        original = db_module._write_lock
        db_module._write_lock = asyncio.Lock()

        try:
            mock_cursor = MagicMock()
            mock_db = AsyncMock()
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_db.commit = AsyncMock()

            mock_store = MagicMock()
            mock_store._get_db = AsyncMock(return_value=mock_db)

            result = await execute_write(mock_store, "UPDATE foo SET bar = ?", (1,))

            mock_db.execute.assert_awaited_once_with("UPDATE foo SET bar = ?", (1,))
            mock_db.commit.assert_awaited_once()
            assert result is mock_cursor
        finally:
            db_module._write_lock = original

    @pytest.mark.asyncio
    async def test_execute_write_with_version_raises_on_zero_rowcount(self):
        """execute_write_with_version raises OptimisticLockError when rowcount is 0."""
        from api.db import execute_write_with_version, OptimisticLockError
        import api.db as db_module

        original = db_module._write_lock
        db_module._write_lock = asyncio.Lock()

        try:
            mock_cursor = MagicMock()
            mock_cursor.rowcount = 0
            mock_db = AsyncMock()
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_db.commit = AsyncMock()

            mock_store = MagicMock()
            mock_store._get_db = AsyncMock(return_value=mock_db)

            with pytest.raises(OptimisticLockError):
                await execute_write_with_version(
                    mock_store,
                    "UPDATE foo SET bar = ? WHERE id = ? AND _version = ?",
                    (1, "abc", 5),
                )
        finally:
            db_module._write_lock = original

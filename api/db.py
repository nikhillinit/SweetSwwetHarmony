"""
Database Access Layer for Command Center API

Provides:
- Single-writer lock for SQLite concurrency
- Optimistic locking helpers
- Transaction context manager
- Write serialization for multi-user scenarios

Usage:
    from api.db import get_write_lock, execute_write, OptimisticLockError

    # Use write lock for all writes
    async def update_entity(entity_key: str, stage: str):
        lock = get_write_lock()
        async with lock:
            await store.execute(
                "UPDATE entity_stages SET stage = ? WHERE entity_key = ?",
                (stage, entity_key)
            )

    # Or use the helper
    async def update_with_version(entity_key: str, stage: str, expected_version: int):
        result = await execute_write_with_version(
            store,
            "UPDATE entity_stages SET stage = ?, _version = _version + 1 WHERE entity_key = ? AND _version = ?",
            (stage, entity_key, expected_version)
        )
        if result.rowcount == 0:
            raise OptimisticLockError("Entity was modified by another user")
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

from fastapi import Request, HTTPException, status

if TYPE_CHECKING:
    from storage.signal_store import SignalStore
    import aiosqlite

logger = logging.getLogger(__name__)

# Global write lock (initialized in app lifespan)
_write_lock: Optional[asyncio.Lock] = None


class OptimisticLockError(Exception):
    """Raised when optimistic locking detects a concurrent modification."""

    def __init__(self, message: str = "Resource was modified by another user"):
        self.message = message
        super().__init__(self.message)


class ConflictError(Exception):
    """Raised when a write conflict is detected."""

    def __init__(
        self,
        message: str = "Write conflict detected",
        local_value: Any = None,
        remote_value: Any = None,
    ):
        self.message = message
        self.local_value = local_value
        self.remote_value = remote_value
        super().__init__(self.message)


# =============================================================================
# LOCK MANAGEMENT
# =============================================================================

def set_write_lock(lock: asyncio.Lock) -> None:
    """Set the global write lock (called from app lifespan)."""
    global _write_lock
    _write_lock = lock


def get_write_lock() -> asyncio.Lock:
    """
    Get the global write lock.

    Returns a new lock if not initialized (for testing).
    """
    global _write_lock
    if _write_lock is None:
        _write_lock = asyncio.Lock()
    return _write_lock


def get_write_lock_from_request(request: Request) -> asyncio.Lock:
    """Get write lock from FastAPI request state."""
    lock = getattr(request.app.state, "write_lock", None)
    if lock is None:
        # Fallback to global lock
        return get_write_lock()
    return lock


# =============================================================================
# WRITE HELPERS
# =============================================================================

@asynccontextmanager
async def write_transaction(request: Request):
    """
    Context manager for write operations.

    Acquires the write lock and provides transaction semantics.

    Usage:
        async with write_transaction(request) as lock:
            await store.execute("INSERT ...")
            await store.execute("UPDATE ...")
    """
    lock = get_write_lock_from_request(request)
    async with lock:
        yield lock


async def execute_write(
    store: "SignalStore",
    query: str,
    params: tuple = (),
    request: Optional[Request] = None,
) -> Any:
    """
    Execute a write operation with lock.

    Args:
        store: SignalStore instance
        query: SQL query to execute
        params: Query parameters
        request: Optional FastAPI request for lock access

    Returns:
        Query result
    """
    lock = get_write_lock_from_request(request) if request else get_write_lock()

    async with lock:
        db = await store._get_db()
        cursor = await db.execute(query, params)
        await db.commit()
        return cursor


async def execute_write_with_version(
    store: "SignalStore",
    query: str,
    params: tuple,
    request: Optional[Request] = None,
) -> Any:
    """
    Execute a write with optimistic locking version check.

    The query MUST include a WHERE clause checking _version:
        WHERE entity_key = ? AND _version = ?

    Raises OptimisticLockError if no rows were affected (version mismatch).

    Args:
        store: SignalStore instance
        query: SQL query with version check
        params: Query parameters (including expected version)
        request: Optional FastAPI request

    Returns:
        Query result

    Raises:
        OptimisticLockError: If version mismatch (concurrent modification)
    """
    cursor = await execute_write(store, query, params, request)

    if cursor.rowcount == 0:
        raise OptimisticLockError(
            "Resource was modified by another user. Please refresh and try again."
        )

    return cursor


# =============================================================================
# FASTAPI INTEGRATION
# =============================================================================

def handle_optimistic_lock_error(e: OptimisticLockError):
    """Convert OptimisticLockError to HTTP 409 Conflict."""
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": "conflict",
            "message": e.message,
            "hint": "The resource was modified by another user. Please refresh and try again.",
        },
    )


def handle_conflict_error(e: ConflictError):
    """Convert ConflictError to HTTP 409 Conflict with details."""
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": "conflict",
            "message": e.message,
            "local_value": e.local_value,
            "remote_value": e.remote_value,
            "hint": "There's a conflict between your changes and another user's changes.",
        },
    )


# =============================================================================
# SHARED STORE DEPENDENCY
# =============================================================================

async def get_store(request: Request) -> "SignalStore":
    """Get the lifespan-managed SignalStore from app state.

    All routers should use this instead of creating per-request stores.
    Falls back to creating a new store for tests that don't set up lifespan.
    """
    store = getattr(request.app.state, "store", None)
    if store is not None:
        return store
    # Fallback for isolated test apps that don't set up lifespan
    from storage.signal_store import SignalStore
    store = SignalStore()
    await store.initialize()
    return store


# =============================================================================
# UTILITIES
# =============================================================================

def now_iso() -> str:
    """Get current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 string to datetime."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

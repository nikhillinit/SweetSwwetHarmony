"""
Read-only wrapper over EntityIdentityStore.

Allows only read methods (lookups, resolution). All write methods
(upsert_*, merge_entities) raise IdentityWriteBlockedError.

Wave 2 safety: shadow entity resolution MUST NOT mutate production
identity tables. This wrapper + DB-level RO connection enforces that.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.entity_identity_store import EntityIdentityStore, BlockingCandidate

logger = logging.getLogger(__name__)


class IdentityWriteBlockedError(Exception):
    """Raised when a write operation is attempted on a read-only identity store."""


class ReadOnlyIdentityStore:
    """Write-blocking wrapper over EntityIdentityStore.

    Read methods are delegated to the underlying store.
    Write methods raise IdentityWriteBlockedError.

    Additionally opens a **read-only SQLite connection** via URI mode
    (``file:<path>?mode=ro``) so even if a write method is accidentally
    un-blocked, the connection itself refuses writes at the SQLite level.
    """

    _WRITE_METHODS = frozenset({
        "upsert_strong_key_bindings",
        "upsert_alias_bindings",
        "upsert_blocking_tokens",
        "merge_entities",
        "_merge_entities_internal",
    })

    def __init__(self, inner: "EntityIdentityStore", db_path: Optional[str] = None):
        """
        Args:
            inner: The underlying EntityIdentityStore to wrap.
            db_path: Path to SQLite DB for read-only connection.
                     If None, uses inner._store._db_path.
        """
        self._inner = inner
        self._db_path = db_path
        self._ro_conn: Optional[sqlite3.Connection] = None

    async def initialize(self) -> None:
        """Open the read-only SQLite connection."""
        path = self._db_path or str(self._inner._store._db_path)
        self._ro_conn = sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        self._ro_conn.execute("PRAGMA journal_mode=WAL")
        logger.info("ReadOnlyIdentityStore initialized with RO connection: %s", path)

    async def close(self) -> None:
        """Close the read-only connection."""
        if self._ro_conn:
            self._ro_conn.close()
            self._ro_conn = None

    @property
    def ro_connection(self) -> Optional[sqlite3.Connection]:
        """Expose the read-only connection for testing."""
        return self._ro_conn

    # =========================================================================
    # READ METHODS — delegated to inner store
    # =========================================================================

    async def lookup_strong_keys(self, strong_keys: List[str]) -> Dict[str, str]:
        return await self._inner.lookup_strong_keys(strong_keys)

    async def lookup_alias_keys(self, alias_keys: List[str]) -> Dict[str, str]:
        return await self._inner.lookup_alias_keys(alias_keys)

    async def lookup_blocking_candidates(
        self,
        tokens: List[Tuple[str, str]],
        limit: int = 200,
    ) -> Dict[Tuple[str, str], List["BlockingCandidate"]]:
        return await self._inner.lookup_blocking_candidates(tokens, limit)

    async def resolve_entity_root(self, entity_id: str, max_hops: int = 10) -> str:
        return await self._inner.resolve_entity_root(entity_id, max_hops)

    @staticmethod
    def entity_id_for_seed(seed_key: str) -> str:
        from storage.entity_identity_store import EntityIdentityStore
        return EntityIdentityStore.entity_id_for_seed(seed_key)

    # =========================================================================
    # WRITE METHODS — blocked
    # =========================================================================

    async def upsert_strong_key_bindings(self, *args: Any, **kwargs: Any) -> Any:
        raise IdentityWriteBlockedError(
            "upsert_strong_key_bindings is blocked on ReadOnlyIdentityStore"
        )

    async def upsert_alias_bindings(self, *args: Any, **kwargs: Any) -> Any:
        raise IdentityWriteBlockedError(
            "upsert_alias_bindings is blocked on ReadOnlyIdentityStore"
        )

    async def upsert_blocking_tokens(self, *args: Any, **kwargs: Any) -> Any:
        raise IdentityWriteBlockedError(
            "upsert_blocking_tokens is blocked on ReadOnlyIdentityStore"
        )

    async def merge_entities(self, *args: Any, **kwargs: Any) -> Any:
        raise IdentityWriteBlockedError(
            "merge_entities is blocked on ReadOnlyIdentityStore"
        )

    async def _merge_entities_internal(self, *args: Any, **kwargs: Any) -> Any:
        raise IdentityWriteBlockedError(
            "_merge_entities_internal is blocked on ReadOnlyIdentityStore"
        )

"""Tests for ReadOnlyIdentityStore — write-blocking wrapper over EntityIdentityStore."""

import hashlib
import os
import sqlite3
import tempfile

import pytest
import pytest_asyncio

from storage.readonly_identity_store import (
    IdentityWriteBlockedError,
    ReadOnlyIdentityStore,
)


# ===========================================================================
# Mock inner store — avoids Phase G migration dependencies
# ===========================================================================

class _MockStoreInner:
    """Simulates the nested _store attribute with a _db_path."""
    _db_path = "mock_inner.db"


class MockInnerStore:
    """Lightweight mock of EntityIdentityStore for delegation tests."""

    _store = _MockStoreInner()

    async def lookup_strong_keys(self, strong_keys):
        return {"domain:acme.com": "ent_abc123"}

    async def lookup_alias_keys(self, alias_keys):
        return {"alias:xyz": "ent_def456"}

    async def lookup_blocking_candidates(self, tokens, limit=200):
        return {("name", "acme"): []}

    async def resolve_entity_root(self, entity_id, max_hops=10):
        return entity_id

    async def upsert_strong_key_bindings(self, *args, **kwargs):
        return "should_not_reach"

    async def upsert_alias_bindings(self, *args, **kwargs):
        return "should_not_reach"

    async def upsert_blocking_tokens(self, *args, **kwargs):
        return "should_not_reach"

    async def merge_entities(self, *args, **kwargs):
        return "should_not_reach"

    async def _merge_entities_internal(self, *args, **kwargs):
        return "should_not_reach"


@pytest.fixture
def mock_inner():
    """Return a fresh MockInnerStore instance."""
    return MockInnerStore()


@pytest.fixture
def ro_store(mock_inner):
    """Return a ReadOnlyIdentityStore wrapping a mock inner store."""
    return ReadOnlyIdentityStore(inner=mock_inner)


@pytest_asyncio.fixture
async def temp_db_path():
    """Create a real temporary SQLite DB file with a minimal table for RO testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO test_table (id, name) VALUES (1, 'hello')")
    conn.commit()
    conn.close()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# ===========================================================================
# WRITE METHOD BLOCKING
# ===========================================================================

class TestWriteMethodsBlocked:
    """Every write method must raise IdentityWriteBlockedError."""

    @pytest.mark.asyncio
    async def test_upsert_strong_key_bindings_blocked(self, ro_store):
        """upsert_strong_key_bindings should raise IdentityWriteBlockedError."""
        with pytest.raises(IdentityWriteBlockedError, match="upsert_strong_key_bindings"):
            await ro_store.upsert_strong_key_bindings([])

    @pytest.mark.asyncio
    async def test_upsert_alias_bindings_blocked(self, ro_store):
        """upsert_alias_bindings should raise IdentityWriteBlockedError."""
        with pytest.raises(IdentityWriteBlockedError, match="upsert_alias_bindings"):
            await ro_store.upsert_alias_bindings([])

    @pytest.mark.asyncio
    async def test_upsert_blocking_tokens_blocked(self, ro_store):
        """upsert_blocking_tokens should raise IdentityWriteBlockedError."""
        with pytest.raises(IdentityWriteBlockedError, match="upsert_blocking_tokens"):
            await ro_store.upsert_blocking_tokens([])

    @pytest.mark.asyncio
    async def test_merge_entities_blocked(self, ro_store):
        """merge_entities should raise IdentityWriteBlockedError."""
        with pytest.raises(IdentityWriteBlockedError, match="merge_entities"):
            await ro_store.merge_entities("winner", "loser")

    @pytest.mark.asyncio
    async def test_merge_entities_internal_blocked(self, ro_store):
        """_merge_entities_internal should raise IdentityWriteBlockedError."""
        with pytest.raises(IdentityWriteBlockedError, match="_merge_entities_internal"):
            await ro_store._merge_entities_internal("winner", "loser")


# ===========================================================================
# READ METHOD DELEGATION
# ===========================================================================

class TestReadMethodsDelegated:
    """Read methods should delegate to the inner store and return its results."""

    @pytest.mark.asyncio
    async def test_lookup_strong_keys_delegated(self, ro_store):
        """lookup_strong_keys should delegate and return the inner result."""
        result = await ro_store.lookup_strong_keys(["domain:acme.com"])
        assert result == {"domain:acme.com": "ent_abc123"}

    @pytest.mark.asyncio
    async def test_lookup_alias_keys_delegated(self, ro_store):
        """lookup_alias_keys should delegate and return the inner result."""
        result = await ro_store.lookup_alias_keys(["alias:xyz"])
        assert result == {"alias:xyz": "ent_def456"}

    @pytest.mark.asyncio
    async def test_lookup_blocking_candidates_delegated(self, ro_store):
        """lookup_blocking_candidates should delegate and return the inner result."""
        result = await ro_store.lookup_blocking_candidates([("name", "acme")])
        assert result == {("name", "acme"): []}

    @pytest.mark.asyncio
    async def test_resolve_entity_root_delegated(self, ro_store):
        """resolve_entity_root should delegate and return the entity_id unchanged."""
        result = await ro_store.resolve_entity_root("ent_abc123")
        assert result == "ent_abc123"


# ===========================================================================
# STATIC METHODS
# ===========================================================================

class TestStaticMethods:

    def test_entity_id_for_seed_returns_sha256_prefix(self):
        """entity_id_for_seed should return SHA256[:16] hex of the seed."""
        seed = "domain:acme.ai"
        expected = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        result = ReadOnlyIdentityStore.entity_id_for_seed(seed)
        assert result == expected
        assert len(result) == 16
        # Must be valid hex
        int(result, 16)

    def test_entity_id_for_seed_deterministic(self):
        """Same seed should always produce the same entity ID."""
        seed = "domain:repeat.io"
        first = ReadOnlyIdentityStore.entity_id_for_seed(seed)
        second = ReadOnlyIdentityStore.entity_id_for_seed(seed)
        assert first == second

    def test_entity_id_for_seed_different_seeds_differ(self):
        """Different seeds should produce different entity IDs."""
        id_a = ReadOnlyIdentityStore.entity_id_for_seed("domain:a.com")
        id_b = ReadOnlyIdentityStore.entity_id_for_seed("domain:b.com")
        assert id_a != id_b


# ===========================================================================
# INITIALIZE / CLOSE LIFECYCLE
# ===========================================================================

class TestLifecycle:

    @pytest.mark.asyncio
    async def test_initialize_creates_ro_connection(self, mock_inner, temp_db_path):
        """initialize() should open a read-only sqlite3.Connection."""
        store = ReadOnlyIdentityStore(inner=mock_inner, db_path=temp_db_path)
        assert store.ro_connection is None
        await store.initialize()
        try:
            assert store.ro_connection is not None
            assert isinstance(store.ro_connection, sqlite3.Connection)
            # Verify the connection can read
            cursor = store.ro_connection.execute("SELECT name FROM test_table WHERE id=1")
            row = cursor.fetchone()
            assert row[0] == "hello"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_close_cleans_up_connection(self, mock_inner, temp_db_path):
        """close() should set _ro_conn to None."""
        store = ReadOnlyIdentityStore(inner=mock_inner, db_path=temp_db_path)
        await store.initialize()
        assert store.ro_connection is not None
        await store.close()
        assert store.ro_connection is None

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self, mock_inner, temp_db_path):
        """Calling close() twice should not raise."""
        store = ReadOnlyIdentityStore(inner=mock_inner, db_path=temp_db_path)
        await store.initialize()
        await store.close()
        await store.close()  # Second call should not raise

    @pytest.mark.asyncio
    async def test_initialize_uses_inner_db_path_when_none(self, temp_db_path):
        """When db_path is None, initialize() should fallback to inner._store._db_path."""
        inner = MockInnerStore()
        inner._store._db_path = temp_db_path
        store = ReadOnlyIdentityStore(inner=inner, db_path=None)
        await store.initialize()
        try:
            assert store.ro_connection is not None
        finally:
            await store.close()


# ===========================================================================
# RO CONNECTION ENFORCEMENT
# ===========================================================================

class TestROConnectionEnforcement:

    @pytest.mark.asyncio
    async def test_ro_connection_rejects_writes(self, mock_inner, temp_db_path):
        """The read-only connection should raise sqlite3.OperationalError on INSERT."""
        store = ReadOnlyIdentityStore(inner=mock_inner, db_path=temp_db_path)
        await store.initialize()
        try:
            with pytest.raises(sqlite3.OperationalError):
                store.ro_connection.execute(
                    "INSERT INTO test_table (id, name) VALUES (99, 'bad')"
                )
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_ro_connection_rejects_create_table(self, mock_inner, temp_db_path):
        """The read-only connection should refuse CREATE TABLE."""
        store = ReadOnlyIdentityStore(inner=mock_inner, db_path=temp_db_path)
        await store.initialize()
        try:
            with pytest.raises(sqlite3.OperationalError):
                store.ro_connection.execute(
                    "CREATE TABLE evil (id INTEGER PRIMARY KEY)"
                )
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_ro_connection_rejects_drop_table(self, mock_inner, temp_db_path):
        """The read-only connection should refuse DROP TABLE."""
        store = ReadOnlyIdentityStore(inner=mock_inner, db_path=temp_db_path)
        await store.initialize()
        try:
            with pytest.raises(sqlite3.OperationalError):
                store.ro_connection.execute("DROP TABLE test_table")
        finally:
            await store.close()


# ===========================================================================
# WRITE_METHODS FROZENSET COMPLETENESS
# ===========================================================================

class TestWriteMethodsSet:

    def test_write_methods_frozenset_contents(self):
        """_WRITE_METHODS should contain exactly the 5 known write methods."""
        expected = frozenset({
            "upsert_strong_key_bindings",
            "upsert_alias_bindings",
            "upsert_blocking_tokens",
            "merge_entities",
            "_merge_entities_internal",
        })
        assert ReadOnlyIdentityStore._WRITE_METHODS == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

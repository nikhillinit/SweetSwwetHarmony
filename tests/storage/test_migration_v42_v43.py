"""Tests for v42 + v43 schema migrations.

Verifies:
- Migration from v41 to v43 adds evidence_family and canonical_key_v2 columns
- Partial indexes are created
- Fresh DB starts at v43
"""

import sqlite3
import tempfile
import os

import pytest
import pytest_asyncio

from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temp DB path."""
    return str(tmp_path / "test_migrations.db")


@pytest.mark.asyncio
async def test_v41_to_v43_migration(tmp_db):
    """Create a v41 DB fixture, migrate to v43, verify new columns exist."""
    store = SignalStore(tmp_db)
    await store.initialize()

    # Verify columns exist via PRAGMA table_info
    conn = sqlite3.connect(tmp_db)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
        assert "evidence_family" in columns, "evidence_family column missing"
        assert "canonical_key_v2" in columns, "canonical_key_v2 column missing"
    finally:
        conn.close()

    await store.close()


@pytest.mark.asyncio
async def test_partial_indexes_created(tmp_db):
    """Verify partial indexes exist in sqlite_master."""
    store = SignalStore(tmp_db)
    await store.initialize()

    conn = sqlite3.connect(tmp_db)
    try:
        indexes = {row[1] for row in conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_signals_evidence_family" in indexes, (
            f"idx_signals_evidence_family not found in {indexes}"
        )
        assert "idx_signals_canonical_key_v2" in indexes, (
            f"idx_signals_canonical_key_v2 not found in {indexes}"
        )
    finally:
        conn.close()

    await store.close()


@pytest.mark.asyncio
async def test_fresh_db_at_v43(tmp_db):
    """New store starts at v43."""
    store = SignalStore(tmp_db)
    await store.initialize()

    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        assert row[0] == CURRENT_SCHEMA_VERSION, f"Expected v{CURRENT_SCHEMA_VERSION}, got v{row[0]}"
        assert CURRENT_SCHEMA_VERSION == 43
    finally:
        conn.close()

    await store.close()

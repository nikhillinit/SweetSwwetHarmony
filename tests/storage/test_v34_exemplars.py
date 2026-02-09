"""Tests for v34 thesis_exemplars migration DDL.

Verifies:
- thesis_exemplars table existence + columns
- Schema version bumped to 34
- UNIQUE constraint on (exemplar_key, vectorizer_version)
- Indexes created (category partial, version, active)
"""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION


@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with temp file DB."""
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


class TestV34Migration:
    """Tests for v34 thesis_exemplars DDL."""

    @pytest.mark.asyncio
    async def test_schema_version_is_at_least_34(self):
        assert CURRENT_SCHEMA_VERSION >= 34

    @pytest.mark.asyncio
    async def test_thesis_exemplars_table_exists(self, store):
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='thesis_exemplars'"
        )
        row = await cursor.fetchone()
        assert row is not None, "thesis_exemplars table should exist"

    @pytest.mark.asyncio
    async def test_thesis_exemplars_columns(self, store):
        cursor = await store._db.execute("PRAGMA table_info(thesis_exemplars)")
        rows = await cursor.fetchall()
        col_names = {r[1] for r in rows}
        expected = {
            "id", "exemplar_key", "canonical_key", "company_name",
            "human_label", "category", "description", "corpus_text",
            "tfidf_vector", "vectorizer_version", "source", "is_active",
            "created_at", "updated_at",
        }
        assert expected.issubset(col_names), f"Missing columns: {expected - col_names}"

    @pytest.mark.asyncio
    async def test_thesis_exemplars_unique_constraint(self, store):
        """UNIQUE(exemplar_key, vectorizer_version) should be enforced."""
        import aiosqlite

        await store._db.execute(
            "INSERT INTO thesis_exemplars "
            "(exemplar_key, category, description, corpus_text, vectorizer_version) "
            "VALUES ('creator_economy', 'creators', 'test', 'corpus', 'v1.0.0')"
        )
        await store._db.commit()

        with pytest.raises(aiosqlite.IntegrityError):
            await store._db.execute(
                "INSERT INTO thesis_exemplars "
                "(exemplar_key, category, description, corpus_text, vectorizer_version) "
                "VALUES ('creator_economy', 'creators', 'test2', 'corpus2', 'v1.0.0')"
            )

    @pytest.mark.asyncio
    async def test_thesis_exemplars_indexes(self, store):
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='thesis_exemplars'"
        )
        rows = await cursor.fetchall()
        idx_names = {r[0] for r in rows}
        assert "idx_exemplars_category" in idx_names
        assert "idx_exemplars_version" in idx_names
        assert "idx_exemplars_active" in idx_names

    @pytest.mark.asyncio
    async def test_defaults(self, store):
        """human_label defaults to TP, source to auto, is_active to 1."""
        await store._db.execute(
            "INSERT INTO thesis_exemplars "
            "(exemplar_key, category, description, corpus_text, vectorizer_version) "
            "VALUES ('test_ex', 'test_cat', 'test desc', 'corpus text', 'v1.0.0')"
        )
        await store._db.commit()

        cursor = await store._db.execute(
            "SELECT human_label, source, is_active FROM thesis_exemplars WHERE exemplar_key='test_ex'"
        )
        row = await cursor.fetchone()
        assert row[0] == "TP"
        assert row[1] == "auto"
        assert row[2] == 1

"""Tests for v33 case-law migration DDL.

Verifies:
- precedents table existence + columns
- anti_pattern_proposals table existence + columns
- Schema version bumped to 33
- Indexes created
- UNIQUE constraint on (signal_id, vectorizer_version)
- Partial unique index on proposals (proposed/approved/applied only)
- FOREIGN KEY cascade on signal deletion
"""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION

_SIGNAL_INSERT = (
    "INSERT INTO signals "
    "(company_name, source_api, signal_type, raw_data, canonical_key, confidence, detected_at, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))"
)


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


class TestV33Migration:
    """Tests for v33 case-law + proposals DDL."""

    @pytest.mark.asyncio
    async def test_schema_version_is_at_least_33(self):
        assert CURRENT_SCHEMA_VERSION >= 33

    @pytest.mark.asyncio
    async def test_precedents_table_exists(self, store):
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='precedents'"
        )
        row = await cursor.fetchone()
        assert row is not None, "precedents table should exist"

    @pytest.mark.asyncio
    async def test_precedents_columns(self, store):
        cursor = await store._db.execute("PRAGMA table_info(precedents)")
        rows = await cursor.fetchall()
        col_names = {r[1] for r in rows}
        expected = {
            "id", "signal_id", "canonical_key", "company_id", "human_label",
            "corpus_text", "tfidf_vector", "similarity_text_hash",
            "signal_created_at", "vectorizer_version", "label_reason",
            "source_api", "confidence", "created_at", "updated_at",
        }
        assert expected.issubset(col_names), f"Missing columns: {expected - col_names}"

    @pytest.mark.asyncio
    async def test_precedents_unique_constraint(self, store):
        """UNIQUE(signal_id, vectorizer_version) should be enforced."""
        # Insert a dummy signal first
        await store._db.execute(
            _SIGNAL_INSERT,
            ("Test Co", "test", "test_signal", "{}", "domain:test.com", 0.5),
        )
        await store._db.commit()

        # First insert should work
        await store._db.execute(
            "INSERT INTO precedents (signal_id, canonical_key, human_label, corpus_text, vectorizer_version) "
            "VALUES (1, 'domain:test.com', 'TP', 'test text', 'v1.0.0')"
        )
        await store._db.commit()

        # Duplicate should fail
        import aiosqlite
        with pytest.raises(aiosqlite.IntegrityError):
            await store._db.execute(
                "INSERT INTO precedents (signal_id, canonical_key, human_label, corpus_text, vectorizer_version) "
                "VALUES (1, 'domain:test.com', 'TP', 'test text', 'v1.0.0')"
            )

    @pytest.mark.asyncio
    async def test_precedents_indexes(self, store):
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='precedents'"
        )
        rows = await cursor.fetchall()
        idx_names = {r[0] for r in rows}
        assert "idx_precedents_label" in idx_names
        assert "idx_precedents_company" in idx_names
        assert "idx_precedents_version" in idx_names

    @pytest.mark.asyncio
    async def test_precedents_fk_cascade(self, store):
        """Deleting a signal should cascade-delete its precedents."""
        await store._db.execute("PRAGMA foreign_keys = ON")
        await store._db.execute(
            _SIGNAL_INSERT,
            ("FK Test Co", "test", "test_signal", "{}", "domain:fk.com", 0.5),
        )
        await store._db.commit()

        # Get the signal ID
        cursor = await store._db.execute("SELECT id FROM signals WHERE canonical_key='domain:fk.com'")
        row = await cursor.fetchone()
        signal_id = row[0]

        await store._db.execute(
            "INSERT INTO precedents (signal_id, canonical_key, human_label, corpus_text, vectorizer_version) "
            "VALUES (?, 'domain:fk.com', 'TP', 'fk test', 'v1.0.0')",
            (signal_id,),
        )
        await store._db.commit()

        # Delete the signal
        await store._db.execute("DELETE FROM signals WHERE id = ?", (signal_id,))
        await store._db.commit()

        # Precedent should be gone
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM precedents WHERE signal_id = ?", (signal_id,)
        )
        count = (await cursor.fetchone())[0]
        assert count == 0, "Precedent should be cascade-deleted"

    @pytest.mark.asyncio
    async def test_proposals_table_exists(self, store):
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='anti_pattern_proposals'"
        )
        row = await cursor.fetchone()
        assert row is not None, "anti_pattern_proposals table should exist"

    @pytest.mark.asyncio
    async def test_proposals_columns(self, store):
        cursor = await store._db.execute("PRAGMA table_info(anti_pattern_proposals)")
        rows = await cursor.fetchall()
        col_names = {r[1] for r in rows}
        expected = {
            "id", "pattern_type", "pattern_key", "description",
            "proposed_action", "evidence", "confidence", "status",
            "proposed_by", "reviewed_by", "reviewed_at", "review_notes",
            "created_at", "expires_at",
        }
        assert expected.issubset(col_names), f"Missing columns: {expected - col_names}"

    @pytest.mark.asyncio
    async def test_proposals_partial_unique_index(self, store):
        """Partial index should block duplicate active proposals but allow after reject."""
        import aiosqlite

        # First proposed entry
        await store._db.execute(
            "INSERT INTO anti_pattern_proposals "
            "(pattern_type, pattern_key, description, proposed_action, evidence, confidence, status) "
            "VALUES ('source_fp_rate', 'github:crypto', 'test', '{}', '{}', 0.8, 'proposed')"
        )
        await store._db.commit()

        # Second proposed with same type+key should fail
        with pytest.raises(aiosqlite.IntegrityError):
            await store._db.execute(
                "INSERT INTO anti_pattern_proposals "
                "(pattern_type, pattern_key, description, proposed_action, evidence, confidence, status) "
                "VALUES ('source_fp_rate', 'github:crypto', 'test2', '{}', '{}', 0.9, 'proposed')"
            )
        await store._db.execute("ROLLBACK")

        # Reject the first, then a new proposed should succeed
        await store._db.execute(
            "UPDATE anti_pattern_proposals SET status='rejected' WHERE pattern_key='github:crypto'"
        )
        await store._db.commit()

        await store._db.execute(
            "INSERT INTO anti_pattern_proposals "
            "(pattern_type, pattern_key, description, proposed_action, evidence, confidence, status) "
            "VALUES ('source_fp_rate', 'github:crypto', 'test3', '{}', '{}', 0.85, 'proposed')"
        )
        await store._db.commit()

        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM anti_pattern_proposals WHERE pattern_key='github:crypto'"
        )
        count = (await cursor.fetchone())[0]
        assert count == 2, "Should have rejected + new proposed"

    @pytest.mark.asyncio
    async def test_proposals_indexes(self, store):
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='anti_pattern_proposals'"
        )
        rows = await cursor.fetchall()
        idx_names = {r[0] for r in rows}
        assert "idx_proposals_one_active" in idx_names
        assert "idx_proposals_status" in idx_names
        assert "idx_proposals_type" in idx_names

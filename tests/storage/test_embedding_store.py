"""
Tests for EmbeddingStore - SQLite storage for embeddings.

TDD: Write failing tests first, then implement.
"""

import pytest
import tempfile
import os
import numpy as np
from pathlib import Path


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestEmbeddingStoreInitialization:
    """Tests for EmbeddingStore initialization."""

    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self, temp_db):
        """Initialize should create embedding tables."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        # Check table exists
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='company_embeddings'"
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "company_embeddings"

        await store.close()

    @pytest.mark.asyncio
    async def test_initialize_creates_fts_table(self, temp_db):
        """Initialize should create FTS5 table."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        # Check FTS table exists
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='company_profiles_fts'"
        )
        row = await cursor.fetchone()

        assert row is not None

        await store.close()


class TestSaveEmbedding:
    """Tests for saving embeddings."""

    @pytest.mark.asyncio
    async def test_save_embedding_returns_id(self, temp_db):
        """save_embedding should return the row ID."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        embedding = np.array([0.1, 0.2, 0.3] * 256, dtype=np.float32)  # 768 dims
        embedding_id = await store.save_embedding(
            canonical_key="domain:acme.ai",
            embedding=embedding,
            source_text_hash="abc123",
            source_text_preview="Test preview",
        )

        assert embedding_id is not None
        assert isinstance(embedding_id, int)
        assert embedding_id > 0

        await store.close()

    @pytest.mark.asyncio
    async def test_save_embedding_upsert(self, temp_db):
        """Saving same key should update existing embedding."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        embedding1 = np.array([0.1] * 768, dtype=np.float32)
        embedding2 = np.array([0.2] * 768, dtype=np.float32)

        id1 = await store.save_embedding(
            canonical_key="domain:test.com",
            embedding=embedding1,
            source_text_hash="hash1",
        )

        id2 = await store.save_embedding(
            canonical_key="domain:test.com",
            embedding=embedding2,
            source_text_hash="hash2",
        )

        # Should have replaced, not created new
        # (Check by getting the embedding and verifying it's the second one)
        result = await store.get_embedding("domain:test.com")
        assert np.allclose(result, embedding2)

        await store.close()


class TestGetEmbedding:
    """Tests for retrieving embeddings."""

    @pytest.mark.asyncio
    async def test_get_embedding_returns_numpy_array(self, temp_db):
        """get_embedding should return a numpy array."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        original = np.random.randn(768).astype(np.float32)
        await store.save_embedding(
            canonical_key="domain:test.com",
            embedding=original,
            source_text_hash="hash1",
        )

        result = await store.get_embedding("domain:test.com")

        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert result.shape == (768,)
        assert np.allclose(result, original)

        await store.close()

    @pytest.mark.asyncio
    async def test_get_embedding_not_found_returns_none(self, temp_db):
        """get_embedding for non-existent key should return None."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        result = await store.get_embedding("domain:nonexistent.com")

        assert result is None

        await store.close()


class TestGetEmbeddingsBatch:
    """Tests for batch embedding retrieval."""

    @pytest.mark.asyncio
    async def test_get_embeddings_batch_returns_dict(self, temp_db):
        """get_embeddings_batch should return dict mapping key to embedding."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        # Save multiple embeddings
        keys = ["domain:a.com", "domain:b.com", "domain:c.com"]
        embeddings = [np.random.randn(768).astype(np.float32) for _ in keys]

        for key, emb in zip(keys, embeddings):
            await store.save_embedding(key, emb, f"hash_{key}")

        # Retrieve batch
        result = await store.get_embeddings_batch(keys)

        assert isinstance(result, dict)
        assert len(result) == 3
        for key, emb in zip(keys, embeddings):
            assert key in result
            assert np.allclose(result[key], emb)

        await store.close()

    @pytest.mark.asyncio
    async def test_get_embeddings_batch_partial_match(self, temp_db):
        """Batch should return only existing embeddings."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        # Save only one
        emb = np.random.randn(768).astype(np.float32)
        await store.save_embedding("domain:exists.com", emb, "hash1")

        # Request multiple
        result = await store.get_embeddings_batch([
            "domain:exists.com",
            "domain:missing1.com",
            "domain:missing2.com",
        ])

        assert len(result) == 1
        assert "domain:exists.com" in result
        assert "domain:missing1.com" not in result

        await store.close()


class TestStalenessDetection:
    """Tests for embedding staleness detection."""

    @pytest.mark.asyncio
    async def test_get_stale_keys_finds_changed_hashes(self, temp_db):
        """get_stale_keys should find keys where hash changed."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        # Save embeddings with known hashes
        emb = np.random.randn(768).astype(np.float32)
        await store.save_embedding("domain:unchanged.com", emb, "hash1")
        await store.save_embedding("domain:changed.com", emb, "old_hash")

        # Check staleness with new hashes
        current_hashes = {
            "domain:unchanged.com": "hash1",  # Same
            "domain:changed.com": "new_hash",  # Different
        }

        stale = await store.get_stale_keys(current_hashes)

        assert "domain:changed.com" in stale
        assert "domain:unchanged.com" not in stale

        await store.close()

    @pytest.mark.asyncio
    async def test_get_stale_keys_finds_missing(self, temp_db):
        """get_stale_keys should include keys with no stored embedding."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        # No embeddings saved
        current_hashes = {
            "domain:new1.com": "hash1",
            "domain:new2.com": "hash2",
        }

        stale = await store.get_stale_keys(current_hashes)

        # All should be "stale" (i.e., need embedding)
        assert "domain:new1.com" in stale
        assert "domain:new2.com" in stale

        await store.close()


class TestFTSOperations:
    """Tests for FTS5 profile indexing."""

    @pytest.mark.asyncio
    async def test_index_profile_for_search(self, temp_db):
        """index_profile should add to FTS index."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        await store.index_profile(
            canonical_key="domain:food.com",
            company_name="FoodCo",
            searchable_text="Food delivery for restaurants",
            category="Consumer CPG",
            business_model="B2C_marketplace",
        )

        # Search for it
        results = await store.search_profiles("food delivery", limit=10)

        assert len(results) >= 1
        assert results[0]["canonical_key"] == "domain:food.com"

        await store.close()

    @pytest.mark.asyncio
    async def test_search_profiles_with_category_filter(self, temp_db):
        """search_profiles should support category filtering."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        # Index two profiles with different categories
        await store.index_profile(
            canonical_key="domain:cpg.com",
            company_name="CPG Co",
            searchable_text="Food and beverage products",
            category="Consumer CPG",
        )
        await store.index_profile(
            canonical_key="domain:health.com",
            company_name="Health Co",
            searchable_text="Fitness and wellness products",
            category="Consumer Health Tech",
        )

        # Search with category filter
        results = await store.search_profiles(
            "products",
            category="Consumer CPG",
            limit=10,
        )

        assert len(results) == 1
        assert results[0]["canonical_key"] == "domain:cpg.com"

        await store.close()


class TestEmbeddingStats:
    """Tests for embedding statistics."""

    @pytest.mark.asyncio
    async def test_get_embedding_stats(self, temp_db):
        """get_stats should return embedding counts."""
        from storage.embedding_store import EmbeddingStore

        store = EmbeddingStore(db_path=temp_db)
        await store.initialize()

        # Save some embeddings
        emb = np.random.randn(768).astype(np.float32)
        await store.save_embedding("domain:a.com", emb, "h1")
        await store.save_embedding("domain:b.com", emb, "h2")

        stats = await store.get_stats()

        assert stats["total_embeddings"] == 2

        await store.close()


class TestContextManager:
    """Tests for context manager support."""

    @pytest.mark.asyncio
    async def test_async_context_manager(self, temp_db):
        """Should work as async context manager."""
        from storage.embedding_store import EmbeddingStore

        async with EmbeddingStore(db_path=temp_db) as store:
            emb = np.random.randn(768).astype(np.float32)
            await store.save_embedding("domain:test.com", emb, "hash1")
            result = await store.get_embedding("domain:test.com")
            assert result is not None

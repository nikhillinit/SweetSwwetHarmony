"""
Tests for SimilarCompaniesBatch - pre-computes embeddings.
"""

import pytest
import tempfile
import os
import numpy as np
from unittest.mock import Mock, AsyncMock


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestBatchResult:
    """Tests for BatchResult dataclass."""

    def test_batch_result_defaults(self):
        """BatchResult should have sensible defaults."""
        from utils.similar_companies_batch import BatchResult

        result = BatchResult()

        assert result.total_companies == 0
        assert result.new_embeddings == 0
        assert result.errors == []

    def test_batch_result_to_dict(self):
        """to_dict should return all stats."""
        from utils.similar_companies_batch import BatchResult

        result = BatchResult(
            total_companies=100,
            new_embeddings=50,
            updated_embeddings=30,
            skipped_embeddings=20,
        )

        d = result.to_dict()

        assert d["total_companies"] == 100
        assert d["new_embeddings"] == 50
        assert "duration_seconds" in d


class TestSimilarCompaniesBatch:
    """Tests for SimilarCompaniesBatch class."""

    @pytest.mark.asyncio
    async def test_run_with_no_companies(self, temp_db):
        """Run with no companies should return empty result."""
        from utils.similar_companies_batch import SimilarCompaniesBatch
        from storage.embedding_store import EmbeddingStore

        async with EmbeddingStore(db_path=temp_db) as store:
            mock_generator = Mock()
            mock_generator.embed_batch = AsyncMock(return_value=[])

            batch = SimilarCompaniesBatch(
                embedding_store=store,
                embedding_generator=mock_generator,
            )

            result = await batch.run()

            assert result.total_companies == 0
            assert result.new_embeddings == 0

    @pytest.mark.asyncio
    async def test_run_creates_new_embeddings(self, temp_db):
        """Run should create embeddings for companies without them."""
        from utils.similar_companies_batch import SimilarCompaniesBatch
        from storage.embedding_store import EmbeddingStore

        async with EmbeddingStore(db_path=temp_db) as store:
            # Index some profiles (but no embeddings yet)
            await store.index_profile(
                "domain:test1.com", "Test 1",
                "Food delivery service", "Consumer CPG", "B2C"
            )
            await store.index_profile(
                "domain:test2.com", "Test 2",
                "Travel booking platform", "Travel", "B2B"
            )

            # Mock generator
            mock_generator = Mock()
            mock_generator.embed_batch = AsyncMock(
                return_value=[
                    np.random.randn(768).astype(np.float32),
                    np.random.randn(768).astype(np.float32),
                ]
            )

            batch = SimilarCompaniesBatch(
                embedding_store=store,
                embedding_generator=mock_generator,
            )

            result = await batch.run()

            # Should have created embeddings
            assert result.new_embeddings == 2
            assert result.skipped_embeddings == 0

            # Verify embeddings exist
            emb1 = await store.get_embedding("domain:test1.com")
            emb2 = await store.get_embedding("domain:test2.com")
            assert emb1 is not None
            assert emb2 is not None

    @pytest.mark.asyncio
    async def test_run_skips_fresh_embeddings(self, temp_db):
        """Run should skip embeddings that haven't changed."""
        from utils.similar_companies_batch import SimilarCompaniesBatch
        from storage.embedding_store import EmbeddingStore
        from utils.profile_text_builder import ProfileTextBuilder

        async with EmbeddingStore(db_path=temp_db) as store:
            builder = ProfileTextBuilder()

            # Index profile
            await store.index_profile(
                "domain:test.com", "Test Co",
                "Food delivery", "Consumer CPG", "B2C"
            )

            # Pre-compute embedding with correct hash
            profile_text = builder.build_from_dict({
                "company_name": "Test Co",
                "problem_solved": "Food delivery",
                "business_model": "B2C",
                "category_hints": ["Consumer CPG"],
            })
            text_hash = builder.compute_hash(profile_text)

            existing_emb = np.random.randn(768).astype(np.float32)
            await store.save_embedding(
                "domain:test.com", existing_emb, text_hash
            )

            # Mock generator (should not be called)
            mock_generator = Mock()
            mock_generator.embed_batch = AsyncMock(
                return_value=[np.random.randn(768).astype(np.float32)]
            )

            batch = SimilarCompaniesBatch(
                embedding_store=store,
                embedding_generator=mock_generator,
            )

            result = await batch.run()

            # Should have skipped (embedding is fresh)
            assert result.skipped_embeddings == 1
            assert result.new_embeddings == 0
            assert result.updated_embeddings == 0

    @pytest.mark.asyncio
    async def test_run_force_recompute(self, temp_db):
        """Force recompute should update all embeddings."""
        from utils.similar_companies_batch import SimilarCompaniesBatch
        from storage.embedding_store import EmbeddingStore

        async with EmbeddingStore(db_path=temp_db) as store:
            # Index profile with existing embedding
            await store.index_profile(
                "domain:test.com", "Test Co",
                "Food delivery", "Consumer CPG", "B2C"
            )
            await store.save_embedding(
                "domain:test.com",
                np.random.randn(768).astype(np.float32),
                "old_hash"
            )

            # Mock generator
            mock_generator = Mock()
            mock_generator.embed_batch = AsyncMock(
                return_value=[np.random.randn(768).astype(np.float32)]
            )

            batch = SimilarCompaniesBatch(
                embedding_store=store,
                embedding_generator=mock_generator,
            )

            result = await batch.run(force_recompute=True)

            # Should have updated
            assert result.updated_embeddings == 1
            assert mock_generator.embed_batch.called

    @pytest.mark.asyncio
    async def test_run_with_limit(self, temp_db):
        """Limit should restrict number of companies processed."""
        from utils.similar_companies_batch import SimilarCompaniesBatch
        from storage.embedding_store import EmbeddingStore

        async with EmbeddingStore(db_path=temp_db) as store:
            # Index multiple profiles
            for i in range(5):
                await store.index_profile(
                    f"domain:test{i}.com", f"Test {i}",
                    "Food delivery", "Consumer CPG", "B2C"
                )

            # Mock generator
            mock_generator = Mock()
            mock_generator.embed_batch = AsyncMock(
                side_effect=lambda texts: [
                    np.random.randn(768).astype(np.float32)
                    for _ in texts
                ]
            )

            batch = SimilarCompaniesBatch(
                embedding_store=store,
                embedding_generator=mock_generator,
            )

            result = await batch.run(limit=3)

            # Should have only processed 3
            assert result.total_companies == 3


class TestBatchJobCLI:
    """Tests for CLI entry point."""

    def test_main_exists(self):
        """main function should exist."""
        from utils.similar_companies_batch import main

        assert callable(main)

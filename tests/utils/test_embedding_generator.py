"""
Tests for EmbeddingGenerator - generates Gemini embeddings.

Note: These tests mock the Gemini API to avoid actual API calls.
Integration tests with real API calls should be run separately.
"""

import pytest
import numpy as np
from unittest.mock import Mock, patch, AsyncMock


class TestEmbeddingGenerator:
    """Tests for EmbeddingGenerator class."""

    def test_init_without_api_key_uses_env(self):
        """Should use environment variable for API key."""
        from utils.embedding_generator import EmbeddingGenerator

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}):
            generator = EmbeddingGenerator()
            assert generator.api_key == "test-key"

    def test_init_with_explicit_api_key(self):
        """Should use explicit API key over env var."""
        from utils.embedding_generator import EmbeddingGenerator

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "env-key"}):
            generator = EmbeddingGenerator(api_key="explicit-key")
            assert generator.api_key == "explicit-key"

    def test_model_default(self):
        """Should use text-embedding-004 by default."""
        from utils.embedding_generator import EmbeddingGenerator

        generator = EmbeddingGenerator(api_key="test")
        assert generator.model_name == "text-embedding-004"

    def test_embedding_dimensions(self):
        """Should generate 768-dimensional embeddings."""
        from utils.embedding_generator import EmbeddingGenerator

        generator = EmbeddingGenerator(api_key="test")
        assert generator.embedding_dims == 768


class TestEmbedSingle:
    """Tests for single text embedding."""

    @pytest.mark.asyncio
    async def test_embed_returns_numpy_array(self):
        """embed() should return a numpy array."""
        from utils.embedding_generator import EmbeddingGenerator

        generator = EmbeddingGenerator(api_key="test")

        # Mock the Gemini API response
        mock_embedding = [0.1] * 768  # 768 float values
        mock_response = Mock()
        mock_response.embeddings = [Mock(values=mock_embedding)]

        with patch.object(generator, "_call_embed_api", return_value=mock_response):
            result = await generator.embed("Test text")

        assert isinstance(result, np.ndarray)
        assert result.shape == (768,)
        assert result.dtype == np.float32

    @pytest.mark.asyncio
    async def test_embed_empty_text_raises(self):
        """embed() with empty text should raise ValueError."""
        from utils.embedding_generator import EmbeddingGenerator

        generator = EmbeddingGenerator(api_key="test")

        with pytest.raises(ValueError, match="empty"):
            await generator.embed("")

    @pytest.mark.asyncio
    async def test_embed_normalizes_by_default(self):
        """Embeddings should be L2-normalized by default."""
        from utils.embedding_generator import EmbeddingGenerator

        generator = EmbeddingGenerator(api_key="test", normalize=True)

        # Mock non-normalized embedding
        mock_embedding = [1.0, 2.0, 3.0] + [0.0] * 765
        mock_response = Mock()
        mock_response.embeddings = [Mock(values=mock_embedding)]

        with patch.object(generator, "_call_embed_api", return_value=mock_response):
            result = await generator.embed("Test text")

        # Check L2 norm is approximately 1.0
        norm = np.linalg.norm(result)
        assert abs(norm - 1.0) < 0.001


class TestEmbedBatch:
    """Tests for batch embedding."""

    @pytest.mark.asyncio
    async def test_embed_batch_returns_list(self):
        """embed_batch() should return list of numpy arrays."""
        from utils.embedding_generator import EmbeddingGenerator

        generator = EmbeddingGenerator(api_key="test")

        texts = ["Text 1", "Text 2", "Text 3"]

        # Mock the batch response
        mock_embeddings = [[0.1 * i] * 768 for i in range(1, 4)]
        mock_response = Mock()
        mock_response.embeddings = [Mock(values=e) for e in mock_embeddings]

        with patch.object(generator, "_call_embed_api", return_value=mock_response):
            results = await generator.embed_batch(texts)

        assert len(results) == 3
        for result in results:
            assert isinstance(result, np.ndarray)
            assert result.shape == (768,)

    @pytest.mark.asyncio
    async def test_embed_batch_empty_list(self):
        """embed_batch() with empty list should return empty list."""
        from utils.embedding_generator import EmbeddingGenerator

        generator = EmbeddingGenerator(api_key="test")

        results = await generator.embed_batch([])

        assert results == []

    @pytest.mark.asyncio
    async def test_embed_batch_chunks_large_lists(self):
        """Large batch should be split into chunks."""
        from utils.embedding_generator import EmbeddingGenerator

        generator = EmbeddingGenerator(api_key="test", batch_size=2)

        texts = ["Text 1", "Text 2", "Text 3", "Text 4", "Text 5"]

        # Track how many times API is called
        call_count = 0

        async def mock_embed_chunk(chunk):
            nonlocal call_count
            call_count += 1
            return [np.array([0.1] * 768, dtype=np.float32) for _ in chunk]

        with patch.object(generator, "_embed_chunk", side_effect=mock_embed_chunk):
            results = await generator.embed_batch(texts)

        # Should have made 3 calls (2, 2, 1)
        assert call_count == 3
        assert len(results) == 5


class TestCosineSimilarity:
    """Tests for cosine similarity computation."""

    def test_cosine_similarity_identical_vectors(self):
        """Identical vectors should have similarity 1.0."""
        from utils.embedding_generator import cosine_similarity

        vec = np.array([1.0, 2.0, 3.0])
        similarity = cosine_similarity(vec, vec)

        assert abs(similarity - 1.0) < 0.001

    def test_cosine_similarity_orthogonal_vectors(self):
        """Orthogonal vectors should have similarity 0.0."""
        from utils.embedding_generator import cosine_similarity

        vec1 = np.array([1.0, 0.0, 0.0])
        vec2 = np.array([0.0, 1.0, 0.0])
        similarity = cosine_similarity(vec1, vec2)

        assert abs(similarity) < 0.001

    def test_cosine_similarity_opposite_vectors(self):
        """Opposite vectors should have similarity -1.0."""
        from utils.embedding_generator import cosine_similarity

        vec1 = np.array([1.0, 0.0, 0.0])
        vec2 = np.array([-1.0, 0.0, 0.0])
        similarity = cosine_similarity(vec1, vec2)

        assert abs(similarity + 1.0) < 0.001

    def test_cosine_similarity_batch(self):
        """Batch similarity against multiple vectors."""
        from utils.embedding_generator import cosine_similarity_batch

        query = np.array([1.0, 0.0, 0.0])
        candidates = np.array([
            [1.0, 0.0, 0.0],   # Same
            [0.0, 1.0, 0.0],   # Orthogonal
            [0.5, 0.5, 0.0],   # Partial
        ])

        similarities = cosine_similarity_batch(query, candidates)

        assert len(similarities) == 3
        assert abs(similarities[0] - 1.0) < 0.001  # Same
        assert abs(similarities[1]) < 0.001  # Orthogonal


class TestSerialization:
    """Tests for embedding serialization."""

    def test_serialize_to_bytes(self):
        """Should serialize embedding to bytes."""
        from utils.embedding_generator import serialize_embedding, deserialize_embedding

        embedding = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        serialized = serialize_embedding(embedding)

        assert isinstance(serialized, bytes)
        assert len(serialized) == 3 * 4  # 3 floats * 4 bytes

    def test_deserialize_from_bytes(self):
        """Should deserialize embedding from bytes."""
        from utils.embedding_generator import serialize_embedding, deserialize_embedding

        original = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        serialized = serialize_embedding(original)
        restored = deserialize_embedding(serialized)

        assert np.allclose(original, restored)

    def test_round_trip_preserves_embedding(self):
        """Serialize and deserialize should preserve embedding exactly."""
        from utils.embedding_generator import serialize_embedding, deserialize_embedding

        original = np.random.randn(768).astype(np.float32)
        restored = deserialize_embedding(serialize_embedding(original))

        assert np.allclose(original, restored, rtol=1e-7)


class TestEdgeCases:
    """Edge case tests."""

    @pytest.mark.asyncio
    async def test_embed_whitespace_only_raises(self):
        """Whitespace-only text should raise ValueError."""
        from utils.embedding_generator import EmbeddingGenerator

        generator = EmbeddingGenerator(api_key="test")

        with pytest.raises(ValueError, match="empty"):
            await generator.embed("   \n\t   ")

    @pytest.mark.asyncio
    async def test_embed_truncates_long_text(self):
        """Very long text should be truncated."""
        from utils.embedding_generator import EmbeddingGenerator

        generator = EmbeddingGenerator(api_key="test", max_input_chars=100)

        # Mock API
        mock_response = Mock()
        mock_response.embeddings = [Mock(values=[0.1] * 768)]

        captured_text = None

        def capture_call(text):
            nonlocal captured_text
            captured_text = text
            return mock_response

        with patch.object(generator, "_call_embed_api", side_effect=capture_call):
            await generator.embed("X" * 200)

        # Text should be truncated to max_input_chars
        assert len(captured_text) <= 100

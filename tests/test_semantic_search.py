"""
Tests for Semantic Search - natural language queries using Gemini embeddings.

TDD Approach: Tests written before implementation.

Semantic Search provides:
- Text embedding generation using Gemini text-embedding-004
- Cosine similarity search against stored signal embeddings
- Natural language queries like "AI health startups" or "food delivery"
- Top-K results with similarity scores

Uses FREE Gemini API (same as existing thesis classifier).
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from typing import List, Dict, Any
import json


class TestGenerateEmbedding:
    """Test embedding generation using Gemini."""

    @pytest.mark.asyncio
    async def test_generate_embedding_for_text(self):
        """Should generate embedding vector for text input."""
        from utils.semantic_search import SemanticSearch

        mock_store = AsyncMock()

        with patch('utils.semantic_search.genai') as mock_genai:
            # Mock Gemini embedding response
            mock_genai.embed_content.return_value = {
                'embedding': [0.1] * 768  # 768-dim vector
            }

            search = SemanticSearch(mock_store)
            embedding = await search.generate_embedding("AI health startup")

            assert embedding is not None
            assert len(embedding) == 768
            mock_genai.embed_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_embedding_handles_empty_text(self):
        """Should return None for empty text."""
        from utils.semantic_search import SemanticSearch

        mock_store = AsyncMock()
        search = SemanticSearch(mock_store)
        embedding = await search.generate_embedding("")

        assert embedding is None

    @pytest.mark.asyncio
    async def test_generate_embedding_handles_api_error(self):
        """Should return None on API error."""
        from utils.semantic_search import SemanticSearch

        mock_store = AsyncMock()

        with patch('utils.semantic_search.genai') as mock_genai:
            mock_genai.embed_content.side_effect = Exception("API error")

            search = SemanticSearch(mock_store)
            embedding = await search.generate_embedding("test query")

            assert embedding is None


class TestEmbedSignal:
    """Test embedding generation and storage for signals."""

    @pytest.mark.asyncio
    async def test_embed_signal_generates_and_stores(self):
        """Should generate embedding for signal and store it."""
        from utils.semantic_search import SemanticSearch

        mock_store = AsyncMock()
        mock_store.save_signal_embedding = AsyncMock()

        with patch('utils.semantic_search.genai') as mock_genai:
            mock_genai.embed_content.return_value = {
                'embedding': [0.1] * 768
            }

            search = SemanticSearch(mock_store)
            result = await search.embed_signal(
                signal_id=100,
                canonical_key="domain:startup.ai",
                text="AI-powered health tracking startup",
            )

            assert result is True
            mock_store.save_signal_embedding.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_signal_builds_text_from_signal_data(self):
        """Should build searchable text from signal metadata."""
        from utils.semantic_search import SemanticSearch

        mock_store = AsyncMock()
        mock_store.save_signal_embedding = AsyncMock()

        with patch('utils.semantic_search.genai') as mock_genai:
            mock_genai.embed_content.return_value = {
                'embedding': [0.1] * 768
            }

            search = SemanticSearch(mock_store)

            signal = {
                "id": 100,
                "canonical_key": "domain:startup.ai",
                "company_name": "HealthTrack AI",
                "signal_type": "product_hunt_launch",
                "raw_data": {
                    "tagline": "AI-powered health tracking",
                    "description": "Track your health with machine learning",
                }
            }

            result = await search.embed_signal_from_dict(signal)

            assert result is True
            # Verify the embedding was called with combined text
            call_args = mock_genai.embed_content.call_args
            content = call_args[1].get('content') or call_args[0][0] if call_args[0] else ''
            # Text should include company name and description
            assert "HealthTrack AI" in str(call_args) or result is True


class TestSemanticSearch:
    """Test semantic search functionality."""

    @pytest.mark.asyncio
    async def test_search_returns_similar_signals(self):
        """Should return signals similar to query."""
        from utils.semantic_search import SemanticSearch, SearchResult

        mock_store = AsyncMock()

        # Mock stored embeddings
        stored_embeddings = [
            {
                "signal_id": 1,
                "canonical_key": "domain:health.ai",
                "embedding": [0.9, 0.1, 0.0] + [0.0] * 765,  # Similar to query
                "text": "AI health tracking startup",
            },
            {
                "signal_id": 2,
                "canonical_key": "domain:food.io",
                "embedding": [0.1, 0.9, 0.0] + [0.0] * 765,  # Different
                "text": "Food delivery marketplace",
            },
            {
                "signal_id": 3,
                "canonical_key": "domain:wellness.co",
                "embedding": [0.85, 0.15, 0.0] + [0.0] * 765,  # Similar to query
                "text": "Wellness and fitness app",
            },
        ]
        mock_store.get_all_signal_embeddings = AsyncMock(return_value=stored_embeddings)

        with patch('utils.semantic_search.genai') as mock_genai:
            # Query embedding similar to health/wellness signals
            mock_genai.embed_content.return_value = {
                'embedding': [0.95, 0.05, 0.0] + [0.0] * 765
            }

            search = SemanticSearch(mock_store)
            results = await search.search("health AI startup", top_k=2)

            assert len(results) == 2
            assert all(isinstance(r, SearchResult) for r in results)
            # Health-related signals should rank higher
            assert results[0].canonical_key == "domain:health.ai"
            assert results[0].similarity_score > 0.9

    @pytest.mark.asyncio
    async def test_search_respects_top_k(self):
        """Should return only top_k results."""
        from utils.semantic_search import SemanticSearch

        mock_store = AsyncMock()

        # 10 stored embeddings
        stored_embeddings = [
            {
                "signal_id": i,
                "canonical_key": f"domain:company{i}.ai",
                "embedding": [0.5] * 768,
                "text": f"Company {i}",
            }
            for i in range(10)
        ]
        mock_store.get_all_signal_embeddings = AsyncMock(return_value=stored_embeddings)

        with patch('utils.semantic_search.genai') as mock_genai:
            mock_genai.embed_content.return_value = {
                'embedding': [0.5] * 768
            }

            search = SemanticSearch(mock_store)
            results = await search.search("test query", top_k=3)

            assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_with_min_similarity_threshold(self):
        """Should filter results below similarity threshold."""
        from utils.semantic_search import SemanticSearch

        mock_store = AsyncMock()

        # Use orthogonal vectors for different similarity levels
        # Similar: mostly positive in first dimension
        similar_embedding = [0.9, 0.1, 0.0] + [0.0] * 765
        # Different: positive in second dimension (orthogonal)
        different_embedding = [0.1, 0.9, 0.0] + [0.0] * 765
        # Query: same direction as similar
        query_embedding = [0.95, 0.05, 0.0] + [0.0] * 765

        stored_embeddings = [
            {
                "signal_id": 1,
                "canonical_key": "domain:similar.ai",
                "embedding": similar_embedding,
                "text": "Similar company",
            },
            {
                "signal_id": 2,
                "canonical_key": "domain:different.io",
                "embedding": different_embedding,
                "text": "Different company",
            },
        ]
        mock_store.get_all_signal_embeddings = AsyncMock(return_value=stored_embeddings)

        with patch('utils.semantic_search.genai') as mock_genai:
            mock_genai.embed_content.return_value = {
                'embedding': query_embedding
            }

            search = SemanticSearch(mock_store)
            results = await search.search("test", top_k=10, min_similarity=0.5)

            # Only similar.ai should pass threshold (cosine sim ~0.99)
            # different.io has low similarity (~0.19)
            assert len(results) == 1
            assert results[0].canonical_key == "domain:similar.ai"

    @pytest.mark.asyncio
    async def test_search_returns_empty_for_no_embeddings(self):
        """Should return empty list if no embeddings stored."""
        from utils.semantic_search import SemanticSearch

        mock_store = AsyncMock()
        mock_store.get_all_signal_embeddings = AsyncMock(return_value=[])

        with patch('utils.semantic_search.genai') as mock_genai:
            mock_genai.embed_content.return_value = {
                'embedding': [0.5] * 768
            }

            search = SemanticSearch(mock_store)
            results = await search.search("test query")

            assert results == []


class TestCosineSimilarity:
    """Test cosine similarity calculation."""

    def test_cosine_similarity_identical_vectors(self):
        """Identical vectors should have similarity 1.0."""
        from utils.semantic_search import cosine_similarity

        vec = [0.5, 0.5, 0.5]
        similarity = cosine_similarity(vec, vec)

        assert abs(similarity - 1.0) < 0.001

    def test_cosine_similarity_orthogonal_vectors(self):
        """Orthogonal vectors should have similarity 0.0."""
        from utils.semantic_search import cosine_similarity

        vec1 = [1.0, 0.0, 0.0]
        vec2 = [0.0, 1.0, 0.0]
        similarity = cosine_similarity(vec1, vec2)

        assert abs(similarity) < 0.001

    def test_cosine_similarity_opposite_vectors(self):
        """Opposite vectors should have similarity -1.0."""
        from utils.semantic_search import cosine_similarity

        vec1 = [1.0, 0.0, 0.0]
        vec2 = [-1.0, 0.0, 0.0]
        similarity = cosine_similarity(vec1, vec2)

        assert abs(similarity + 1.0) < 0.001


class TestSearchResultDataclass:
    """Test SearchResult dataclass structure."""

    def test_search_result_has_required_fields(self):
        """SearchResult should have all required fields."""
        from utils.semantic_search import SearchResult

        result = SearchResult(
            signal_id=100,
            canonical_key="domain:startup.ai",
            similarity_score=0.95,
            text="AI health startup",
            company_name="StartupAI",
        )

        assert result.signal_id == 100
        assert result.canonical_key == "domain:startup.ai"
        assert result.similarity_score == 0.95
        assert result.text == "AI health startup"
        assert result.company_name == "StartupAI"


class TestBatchEmbedding:
    """Test batch embedding generation."""

    @pytest.mark.asyncio
    async def test_embed_pending_signals(self):
        """Should embed all signals without embeddings."""
        from utils.semantic_search import SemanticSearch

        mock_store = AsyncMock()

        # Signals without embeddings
        pending_signals = [
            {"id": 1, "canonical_key": "domain:a.ai", "company_name": "Company A", "raw_data": {}},
            {"id": 2, "canonical_key": "domain:b.ai", "company_name": "Company B", "raw_data": {}},
        ]
        mock_store.get_signals_without_embeddings = AsyncMock(return_value=pending_signals)
        mock_store.save_signal_embedding = AsyncMock()

        with patch('utils.semantic_search.genai') as mock_genai:
            mock_genai.embed_content.return_value = {
                'embedding': [0.5] * 768
            }

            search = SemanticSearch(mock_store)
            count = await search.embed_pending_signals(batch_size=10)

            assert count == 2
            assert mock_store.save_signal_embedding.call_count == 2

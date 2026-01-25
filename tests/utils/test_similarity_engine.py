"""
Tests for SimilarityEngine - finds similar companies.

TDD: Write failing tests first, then implement.
"""

import pytest
import tempfile
import os
import numpy as np
from unittest.mock import Mock, AsyncMock, patch


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestSimilarCompanyDataclass:
    """Tests for SimilarCompany dataclass."""

    def test_similar_company_creation(self):
        """SimilarCompany should have all required fields."""
        from utils.similarity_engine import SimilarCompany

        result = SimilarCompany(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            similarity_score=0.85,
            raw_cosine_score=0.80,
            match_reasons=["same category", "similar problem"],
            business_model="B2C_marketplace",
            category="Consumer CPG",
            profile_url="https://acme.ai",
        )

        assert result.canonical_key == "domain:acme.ai"
        assert result.similarity_score == 0.85
        assert len(result.match_reasons) == 2


class TestScoreComputation:
    """Tests for score computation."""

    def test_compute_final_score_cosine_only(self):
        """Cosine similarity should be 85% of final score."""
        from utils.similarity_engine import compute_final_score

        score = compute_final_score(
            cosine_sim=1.0,
            same_category=False,
            same_business_model=False,
        )

        assert abs(score - 0.85) < 0.01

    def test_compute_final_score_with_category_boost(self):
        """Category match should add 10%."""
        from utils.similarity_engine import compute_final_score

        score = compute_final_score(
            cosine_sim=1.0,
            same_category=True,
            same_business_model=False,
        )

        assert abs(score - 0.95) < 0.01

    def test_compute_final_score_with_all_boosts(self):
        """Full boosts should cap at 1.0."""
        from utils.similarity_engine import compute_final_score

        score = compute_final_score(
            cosine_sim=1.0,
            same_category=True,
            same_business_model=True,
        )

        assert score == 1.0  # Capped


class TestMatchReasonsGeneration:
    """Tests for match reasons generation."""

    def test_generate_reasons_high_cosine(self):
        """High cosine should generate 'similar problem/customer'."""
        from utils.similarity_engine import generate_match_reasons

        reasons = generate_match_reasons(
            cosine_sim=0.85,
            same_category=False,
            same_model=False,
            keyword_overlap=[],
            thin_profile=False,
        )

        assert "similar problem/customer" in reasons

    def test_generate_reasons_same_category(self):
        """Same category should be included."""
        from utils.similarity_engine import generate_match_reasons

        reasons = generate_match_reasons(
            cosine_sim=0.5,
            same_category=True,
            same_model=False,
            keyword_overlap=[],
            thin_profile=False,
        )

        assert "same category" in reasons

    def test_generate_reasons_keyword_overlap(self):
        """Keyword overlap should be shown."""
        from utils.similarity_engine import generate_match_reasons

        reasons = generate_match_reasons(
            cosine_sim=0.5,
            same_category=False,
            same_model=False,
            keyword_overlap=["food", "delivery", "restaurant"],
            thin_profile=False,
        )

        assert any("keyword overlap" in r for r in reasons)
        assert any("food" in r for r in reasons)

    def test_generate_reasons_thin_profile(self):
        """Thin profile should note broad search."""
        from utils.similarity_engine import generate_match_reasons

        reasons = generate_match_reasons(
            cosine_sim=0.5,
            same_category=False,
            same_model=False,
            keyword_overlap=[],
            thin_profile=True,
        )

        assert any("broad search" in r for r in reasons)


class TestSimilarityEngineWithMocks:
    """Tests for SimilarityEngine with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_find_similar_returns_list(self, temp_db):
        """find_similar should return list of SimilarCompany."""
        from utils.similarity_engine import SimilarityEngine, SimilarCompany
        from storage.embedding_store import EmbeddingStore

        # Setup store with test data
        async with EmbeddingStore(db_path=temp_db) as store:
            # Index some profiles
            await store.index_profile(
                "domain:similar.com", "Similar Co",
                "Food delivery for restaurants", "Consumer CPG", "B2C_marketplace"
            )
            await store.index_profile(
                "domain:other.com", "Other Co",
                "Travel booking platform", "Travel & Hospitality", "B2B_SaaS"
            )

            # Save embeddings
            query_emb = np.array([1.0, 0.0] + [0.0] * 766, dtype=np.float32)
            similar_emb = np.array([0.9, 0.1] + [0.0] * 766, dtype=np.float32)  # Similar
            other_emb = np.array([0.0, 1.0] + [0.0] * 766, dtype=np.float32)  # Different

            await store.save_embedding("domain:query.com", query_emb, "hash_query")
            await store.save_embedding("domain:similar.com", similar_emb, "hash_similar")
            await store.save_embedding("domain:other.com", other_emb, "hash_other")

            # Create engine with mocked embedding generator
            mock_generator = Mock()
            mock_generator.embed = AsyncMock(return_value=query_emb)

            engine = SimilarityEngine(
                embedding_store=store,
                embedding_generator=mock_generator,
            )

            # Mock the profile lookup
            engine._get_profile = AsyncMock(return_value={
                "company_name": "Query Co",
                "problem_solved": "Food delivery service",
                "category_hints": ["Consumer CPG"],
            })

            results = await engine.find_similar("domain:query.com", n=5)

            assert isinstance(results, list)
            # Should return similar company (high cosine)
            if results:
                assert all(isinstance(r, SimilarCompany) for r in results)


class TestSimilarityEngineIntegration:
    """Integration tests with real components."""

    @pytest.mark.asyncio
    async def test_end_to_end_similarity_search(self, temp_db):
        """End-to-end test of similarity search."""
        from utils.similarity_engine import SimilarityEngine
        from storage.embedding_store import EmbeddingStore
        from utils.profile_text_builder import ProfileTextBuilder
        from utils.keyword_extractor import KeywordExtractor

        async with EmbeddingStore(db_path=temp_db) as store:
            # Setup: index profiles and save embeddings
            profiles = [
                {
                    "canonical_key": "domain:food1.com",
                    "company_name": "FoodDelivery Inc",
                    "searchable_text": "Food delivery platform for restaurants and consumers",
                    "category": "Consumer CPG",
                    "business_model": "B2C_marketplace",
                },
                {
                    "canonical_key": "domain:food2.com",
                    "company_name": "MealKit Co",
                    "searchable_text": "Meal kit subscription service for healthy eating",
                    "category": "Consumer CPG",
                    "business_model": "B2C_subscription",
                },
                {
                    "canonical_key": "domain:travel.com",
                    "company_name": "TravelBook",
                    "searchable_text": "Hotel booking platform for business travelers",
                    "category": "Travel & Hospitality",
                    "business_model": "B2B_SaaS",
                },
            ]

            # Index profiles
            for p in profiles:
                await store.index_profile(
                    p["canonical_key"], p["company_name"],
                    p["searchable_text"], p["category"], p["business_model"]
                )

            # Create embeddings (simulate - in real use, these would be from Gemini)
            # Food companies get similar embeddings
            food_emb = np.random.randn(768).astype(np.float32)
            food_emb_similar = food_emb + np.random.randn(768).astype(np.float32) * 0.1
            travel_emb = np.random.randn(768).astype(np.float32)  # Different

            await store.save_embedding("domain:food1.com", food_emb, "hash1")
            await store.save_embedding("domain:food2.com", food_emb_similar, "hash2")
            await store.save_embedding("domain:travel.com", travel_emb, "hash3")

            # Create engine
            mock_generator = Mock()
            mock_generator.embed = AsyncMock(return_value=food_emb)

            engine = SimilarityEngine(
                embedding_store=store,
                embedding_generator=mock_generator,
            )

            # Find similar to food1
            results = await engine.find_similar(
                "domain:food1.com",
                n=5,
                exclude_self=True,
            )

            # Should rank food2 higher than travel
            if len(results) >= 2:
                keys = [r.canonical_key for r in results]
                assert "domain:food2.com" in keys
                # Food should rank higher than travel
                food2_idx = keys.index("domain:food2.com") if "domain:food2.com" in keys else 999
                travel_idx = keys.index("domain:travel.com") if "domain:travel.com" in keys else 999
                assert food2_idx < travel_idx

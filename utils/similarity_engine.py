"""
SimilarityEngine - Finds similar companies using hybrid FTS+embedding approach.

Architecture:
1. Build profile text and extract keywords
2. Stage 1: FTS5 candidate retrieval (K=300)
3. Stage 2: Embedding rerank with cosine similarity
4. Apply soft boosts (category, business model)
5. Return top N with match reasons

Sprint 4: Similar Companies feature.

Usage:
    engine = SimilarityEngine(embedding_store, embedding_generator)
    results = await engine.find_similar("domain:acme.ai", n=20)

    for company in results:
        print(f"{company.company_name}: {company.similarity_score:.2f}")
        print(f"  Reasons: {', '.join(company.match_reasons)}")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from utils.profile_text_builder import ProfileTextBuilder
from utils.keyword_extractor import KeywordExtractor
from utils.embedding_generator import cosine_similarity_batch

if TYPE_CHECKING:
    from storage.embedding_store import EmbeddingStore
    from utils.embedding_generator import EmbeddingGenerator

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SimilarCompany:
    """Result from similarity search."""
    canonical_key: str
    company_name: str
    similarity_score: float       # 0.0-1.0 (final score with boosts)
    raw_cosine_score: float       # 0.0-1.0 (raw cosine similarity)
    match_reasons: List[str]      # ["same category", "similar problem", ...]
    business_model: str
    category: str
    profile_url: Optional[str] = None


@dataclass
class SimilaritySearchResult:
    """Complete result from similarity search."""
    query_key: str
    results: List[SimilarCompany]
    candidates_retrieved: int
    candidates_with_embeddings: int
    thin_profile: bool = False
    relaxation_used: bool = False


# =============================================================================
# SCORING FUNCTIONS
# =============================================================================

def compute_final_score(
    cosine_sim: float,
    same_category: bool,
    same_business_model: bool,
    cosine_weight: float = 0.85,
    category_weight: float = 0.10,
    model_weight: float = 0.05,
) -> float:
    """
    Compute final similarity score with soft boosts.

    Formula:
        final = cosine * 0.85 + category_boost * 0.10 + model_boost * 0.05

    Args:
        cosine_sim: Raw cosine similarity (0-1)
        same_category: Whether categories match
        same_business_model: Whether business models match
        cosine_weight: Weight for cosine similarity
        category_weight: Weight for category match
        model_weight: Weight for business model match

    Returns:
        Final score capped at 1.0
    """
    category_boost = 1.0 if same_category else 0.0
    model_boost = 1.0 if same_business_model else 0.0

    final = (
        cosine_sim * cosine_weight +
        category_boost * category_weight +
        model_boost * model_weight
    )

    return min(1.0, final)


def generate_match_reasons(
    cosine_sim: float,
    same_category: bool,
    same_model: bool,
    keyword_overlap: List[str],
    thin_profile: bool,
) -> List[str]:
    """
    Generate human-readable match reasons.

    Args:
        cosine_sim: Cosine similarity score
        same_category: Whether categories match
        same_model: Whether business models match
        keyword_overlap: List of overlapping keywords
        thin_profile: Whether query profile was thin

    Returns:
        List of reason strings
    """
    reasons = []

    # Semantic similarity reason
    if cosine_sim >= 0.78:
        reasons.append("similar problem/customer")
    elif cosine_sim >= 0.65:
        reasons.append("related business area")

    # Structural matches
    if same_category:
        reasons.append("same category")

    if same_model:
        reasons.append("same business model")

    # Keyword overlap
    if keyword_overlap:
        top_keywords = keyword_overlap[:3]
        reasons.append(f"keyword overlap: {', '.join(top_keywords)}")

    # Thin profile note
    if thin_profile:
        reasons.append("broad search (limited profile)")

    # Default if no reasons
    return reasons or ["general similarity"]


# =============================================================================
# SIMILARITY ENGINE
# =============================================================================

class SimilarityEngine:
    """
    Main orchestrator for finding similar companies.

    Strategy:
    1. Load/build query profile
    2. Extract keywords for FTS search
    3. Retrieve candidates via FTS5 (Stage 1)
    4. Rerank with embeddings (Stage 2)
    5. Apply soft boosts and generate reasons
    """

    # Default configuration
    DEFAULT_K_CANDIDATES = 300
    DEFAULT_N_RESULTS = 20
    DEFAULT_MIN_CATEGORY_COUNT = 50

    def __init__(
        self,
        embedding_store: "EmbeddingStore",
        embedding_generator: "EmbeddingGenerator",
        profile_text_builder: Optional[ProfileTextBuilder] = None,
        keyword_extractor: Optional[KeywordExtractor] = None,
        k_candidates: int = DEFAULT_K_CANDIDATES,
        n_results: int = DEFAULT_N_RESULTS,
        min_category_count: int = DEFAULT_MIN_CATEGORY_COUNT,
    ):
        """
        Initialize similarity engine.

        Args:
            embedding_store: Storage for embeddings and FTS
            embedding_generator: Generator for new embeddings
            profile_text_builder: Builder for profile text (optional)
            keyword_extractor: Extractor for FTS keywords (optional)
            k_candidates: Number of candidates to retrieve in Stage 1
            n_results: Default number of results to return
            min_category_count: Minimum candidates to use category narrowing
        """
        self.embedding_store = embedding_store
        self.embedding_generator = embedding_generator
        self.profile_text_builder = profile_text_builder or ProfileTextBuilder()
        self.keyword_extractor = keyword_extractor or KeywordExtractor()
        self.k_candidates = k_candidates
        self.n_results = n_results
        self.min_category_count = min_category_count

    async def find_similar(
        self,
        canonical_key: str,
        n: Optional[int] = None,
        category_filter: Optional[str] = None,
        exclude_self: bool = True,
    ) -> List[SimilarCompany]:
        """
        Find N most similar companies to the given company.

        Args:
            canonical_key: Canonical key of query company
            n: Number of results (default: self.n_results)
            category_filter: Optional category to filter by
            exclude_self: Whether to exclude the query company from results

        Returns:
            List of SimilarCompany sorted by similarity
        """
        n = n or self.n_results

        # Step 1: Get query profile
        query_profile = await self._get_profile(canonical_key)
        if not query_profile:
            logger.warning(f"No profile found for {canonical_key}")
            return []

        # Step 2: Build profile text and check if thin
        profile_text = self.profile_text_builder.build_from_dict(query_profile)
        thin_profile = self.profile_text_builder.is_thin_profile(profile_text)

        # Step 3: Extract keywords for FTS
        keywords = self.keyword_extractor.extract(profile_text, max_keywords=10)
        if not keywords:
            logger.warning(f"No keywords extracted for {canonical_key}")
            return []

        fts_query = self.keyword_extractor.build_fts_query(keywords, operator="OR")

        # Step 4: Get query embedding
        query_embedding = await self._get_or_compute_embedding(canonical_key, profile_text)
        if query_embedding is None:
            logger.warning(f"Could not get embedding for {canonical_key}")
            return []

        # Step 5: Stage 1 - FTS candidate retrieval
        query_category = query_profile.get("category_hints", [])
        query_category = query_category[0] if query_category else None

        candidates = await self._retrieve_candidates(
            fts_query=fts_query,
            category=category_filter or query_category,
            thin_profile=thin_profile,
            exclude_key=canonical_key if exclude_self else None,
        )

        if not candidates:
            logger.info(f"No candidates found for {canonical_key}")
            return []

        # Step 6: Stage 2 - Embedding rerank
        results = await self._rerank_candidates(
            query_embedding=query_embedding,
            query_profile=query_profile,
            candidates=candidates,
            keywords=keywords,
            thin_profile=thin_profile,
            n=n,
        )

        return results

    async def _get_profile(self, canonical_key: str) -> Optional[Dict[str, Any]]:
        """
        Get company profile from storage.

        Override this method to integrate with your profile storage.

        Args:
            canonical_key: Company canonical key

        Returns:
            Profile dict or None
        """
        # Default implementation: search FTS for the company
        results = await self.embedding_store.search_profiles(
            canonical_key.replace("domain:", ""),
            limit=1,
        )

        if results:
            return {
                "company_name": results[0].get("company_name", ""),
                "category_hints": [results[0].get("category", "")] if results[0].get("category") else [],
                "business_model": results[0].get("business_model", ""),
            }

        return None

    async def _get_or_compute_embedding(
        self, canonical_key: str, profile_text: str
    ) -> Optional[np.ndarray]:
        """
        Get embedding from cache or compute new one.

        Args:
            canonical_key: Company canonical key
            profile_text: Profile text for embedding

        Returns:
            768-dim numpy array or None
        """
        # Try cache first
        embedding = await self.embedding_store.get_embedding(canonical_key)
        if embedding is not None:
            return embedding

        # Compute new embedding
        try:
            embedding = await self.embedding_generator.embed(profile_text)

            # Cache it
            text_hash = self.profile_text_builder.compute_hash(profile_text)
            preview = self.profile_text_builder.get_preview(profile_text)
            await self.embedding_store.save_embedding(
                canonical_key, embedding, text_hash, preview
            )

            return embedding
        except Exception as e:
            logger.error(f"Failed to compute embedding for {canonical_key}: {e}")
            return None

    async def _retrieve_candidates(
        self,
        fts_query: str,
        category: Optional[str],
        thin_profile: bool,
        exclude_key: Optional[str],
    ) -> List[Dict[str, Any]]:
        """
        Stage 1: Retrieve candidates via FTS5.

        Uses relaxation ladder:
        1. Try with category narrowing (if enough candidates)
        2. Fall back to global search

        Args:
            fts_query: FTS query string
            category: Optional category for narrowing
            thin_profile: Whether to skip narrowing
            exclude_key: Key to exclude from results

        Returns:
            List of candidate dicts
        """
        candidates = []

        # Step 1: Try with category narrowing (if not thin profile)
        if category and not thin_profile:
            candidates = await self.embedding_store.search_profiles(
                fts_query,
                category=category,
                limit=self.k_candidates,
            )

            # Check if we have enough candidates
            if len(candidates) >= self.min_category_count:
                logger.debug(f"Using category-narrowed candidates: {len(candidates)}")
            else:
                # Fall back to global
                candidates = []

        # Step 2: Global search (no category filter)
        if not candidates:
            candidates = await self.embedding_store.search_profiles(
                fts_query,
                limit=self.k_candidates,
            )
            logger.debug(f"Using global candidates: {len(candidates)}")

        # Remove excluded key
        if exclude_key:
            candidates = [c for c in candidates if c["canonical_key"] != exclude_key]

        return candidates

    async def _rerank_candidates(
        self,
        query_embedding: np.ndarray,
        query_profile: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        keywords: List[str],
        thin_profile: bool,
        n: int,
    ) -> List[SimilarCompany]:
        """
        Stage 2: Rerank candidates using embeddings.

        Args:
            query_embedding: Query company embedding
            query_profile: Query company profile
            candidates: Candidate companies from Stage 1
            keywords: Query keywords (for overlap detection)
            thin_profile: Whether query profile was thin
            n: Number of results to return

        Returns:
            List of SimilarCompany sorted by score
        """
        # Get embeddings for all candidates
        candidate_keys = [c["canonical_key"] for c in candidates]
        embeddings_map = await self.embedding_store.get_embeddings_batch(candidate_keys)

        # Filter to candidates with embeddings
        candidates_with_emb = [
            c for c in candidates
            if c["canonical_key"] in embeddings_map
        ]

        if not candidates_with_emb:
            logger.warning("No candidates have embeddings")
            return []

        # Build embedding matrix
        candidate_embeddings = np.array([
            embeddings_map[c["canonical_key"]]
            for c in candidates_with_emb
        ])

        # Compute cosine similarities
        cosine_scores = cosine_similarity_batch(query_embedding, candidate_embeddings)

        # Get query metadata
        query_category = query_profile.get("category_hints", [])
        query_category = query_category[0] if query_category else ""
        query_model = query_profile.get("business_model", "")

        # Score and sort candidates
        scored = []
        for i, candidate in enumerate(candidates_with_emb):
            cosine = float(cosine_scores[i])

            # Check category/model match
            same_category = candidate.get("category", "") == query_category and query_category
            same_model = candidate.get("business_model", "") == query_model and query_model

            # Compute final score
            final_score = compute_final_score(cosine, same_category, same_model)

            # Find keyword overlap (simple check)
            candidate_text = candidate.get("company_name", "").lower()
            keyword_overlap = [kw for kw in keywords if kw.lower() in candidate_text]

            # Generate reasons
            reasons = generate_match_reasons(
                cosine, same_category, same_model, keyword_overlap, thin_profile
            )

            scored.append(SimilarCompany(
                canonical_key=candidate["canonical_key"],
                company_name=candidate.get("company_name", ""),
                similarity_score=final_score,
                raw_cosine_score=cosine,
                match_reasons=reasons,
                business_model=candidate.get("business_model", ""),
                category=candidate.get("category", ""),
                profile_url=None,  # Could be added from profile storage
            ))

        # Sort by score descending
        scored.sort(key=lambda x: x.similarity_score, reverse=True)

        return scored[:n]


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def find_similar_companies(
    canonical_key: str,
    embedding_store: "EmbeddingStore",
    embedding_generator: "EmbeddingGenerator",
    n: int = 20,
) -> List[SimilarCompany]:
    """
    Convenience function to find similar companies.

    Args:
        canonical_key: Query company canonical key
        embedding_store: Embedding storage
        embedding_generator: Embedding generator
        n: Number of results

    Returns:
        List of similar companies
    """
    engine = SimilarityEngine(
        embedding_store=embedding_store,
        embedding_generator=embedding_generator,
    )
    return await engine.find_similar(canonical_key, n=n)

"""
Semantic Search - natural language queries using Gemini embeddings.

Part of Deal Intelligence Engine (Phase 6).

This module provides:
- Text embedding generation using Gemini text-embedding-004 (FREE)
- Cosine similarity search against stored signal embeddings
- Natural language queries like "AI health startups" or "food delivery"
- Batch embedding generation for new signals

Uses the same Gemini API as thesis classification (no additional cost).

Usage:
    search = SemanticSearch(signal_store)

    # Embed new signals
    await search.embed_pending_signals()

    # Search
    results = await search.search("AI health startup", top_k=10)
    for r in results:
        print(f"{r.company_name}: {r.similarity_score:.2f}")
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Lazy import for google.generativeai
genai = None


def _ensure_genai():
    """Lazy load google.generativeai module."""
    global genai
    if genai is None:
        try:
            import google.generativeai as _genai
            genai = _genai
            # Configure with API key
            api_key = os.getenv("GOOGLE_API_KEY")
            if api_key:
                genai.configure(api_key=api_key)
        except ImportError:
            logger.warning("google-generativeai not installed")
            genai = None
    return genai


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SearchResult:
    """Result from semantic search."""
    signal_id: int
    canonical_key: str
    similarity_score: float
    text: str
    company_name: Optional[str] = None


# =============================================================================
# COSINE SIMILARITY
# =============================================================================

def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """
    Calculate cosine similarity between two vectors.

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Similarity score [-1, 1]
    """
    if len(vec1) != len(vec2):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot_product / (norm1 * norm2)


# =============================================================================
# SEMANTIC SEARCH
# =============================================================================

class SemanticSearch:
    """
    Semantic search using Gemini embeddings.

    Provides natural language search over stored signals using
    cosine similarity between query and signal embeddings.
    """

    EMBEDDING_MODEL = "models/text-embedding-004"
    EMBEDDING_DIM = 768

    def __init__(self, store):
        """
        Initialize with storage layer.

        Args:
            store: Storage layer with embedding methods
        """
        self.store = store

    async def generate_embedding(
        self,
        text: str,
        task_type: str = "RETRIEVAL_QUERY",
    ) -> Optional[List[float]]:
        """
        Generate embedding vector for text using Gemini.

        Args:
            text: Text to embed
            task_type: Embedding task type (RETRIEVAL_QUERY or RETRIEVAL_DOCUMENT)

        Returns:
            768-dimensional embedding vector, or None on error
        """
        if not text or not text.strip():
            return None

        _ensure_genai()
        if genai is None:
            logger.warning("Gemini not available for embedding generation")
            return None

        try:
            result = genai.embed_content(
                model=self.EMBEDDING_MODEL,
                content=text,
                task_type=task_type,
            )

            embedding = result.get('embedding')
            if embedding and len(embedding) == self.EMBEDDING_DIM:
                return embedding

            logger.warning(f"Unexpected embedding format: {type(result)}")
            return None

        except Exception as e:
            logger.warning(f"Embedding generation failed: {e}")
            return None

    async def embed_signal(
        self,
        signal_id: int,
        canonical_key: str,
        text: str,
        company_name: Optional[str] = None,
    ) -> bool:
        """
        Generate and store embedding for a signal.

        Args:
            signal_id: Signal database ID
            canonical_key: Company identifier
            text: Searchable text to embed
            company_name: Optional company name

        Returns:
            True if embedding was stored successfully
        """
        embedding = await self.generate_embedding(
            text,
            task_type="RETRIEVAL_DOCUMENT",
        )

        if embedding is None:
            return False

        try:
            await self.store.save_signal_embedding(
                signal_id=signal_id,
                canonical_key=canonical_key,
                embedding=embedding,
                text=text,
                company_name=company_name,
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to save embedding for {canonical_key}: {e}")
            return False

    async def embed_signal_from_dict(self, signal: Dict[str, Any]) -> bool:
        """
        Generate embedding from signal dictionary.

        Builds searchable text from signal metadata.

        Args:
            signal: Signal dict with id, canonical_key, company_name, raw_data

        Returns:
            True if embedding was stored successfully
        """
        signal_id = signal.get("id")
        canonical_key = signal.get("canonical_key", "")
        company_name = signal.get("company_name", "")
        raw_data = signal.get("raw_data", {})

        # Build searchable text from available fields
        text_parts = []

        if company_name:
            text_parts.append(company_name)

        if raw_data:
            # Extract relevant text fields
            for field in ["tagline", "description", "title", "headline", "summary"]:
                if field in raw_data and raw_data[field]:
                    text_parts.append(str(raw_data[field]))

            # Include category/industry if present
            for field in ["category", "industry", "sector"]:
                if field in raw_data and raw_data[field]:
                    text_parts.append(str(raw_data[field]))

        text = " ".join(text_parts)

        if not text.strip():
            # Use canonical key as fallback
            text = canonical_key

        return await self.embed_signal(
            signal_id=signal_id,
            canonical_key=canonical_key,
            text=text,
            company_name=company_name,
        )

    async def search(
        self,
        query: str,
        top_k: int = 10,
        min_similarity: float = 0.0,
    ) -> List[SearchResult]:
        """
        Search for signals similar to query.

        Args:
            query: Natural language search query
            top_k: Maximum number of results to return
            min_similarity: Minimum similarity threshold [0, 1]

        Returns:
            List of SearchResult sorted by similarity descending
        """
        # Generate query embedding
        query_embedding = await self.generate_embedding(query)
        if query_embedding is None:
            return []

        # Get all stored embeddings
        try:
            stored = await self.store.get_all_signal_embeddings()
        except Exception as e:
            logger.warning(f"Failed to get signal embeddings: {e}")
            return []

        if not stored:
            return []

        # Calculate similarity for each
        results = []
        for item in stored:
            embedding = item.get("embedding")
            if not embedding:
                continue

            similarity = cosine_similarity(query_embedding, embedding)

            if similarity >= min_similarity:
                results.append(SearchResult(
                    signal_id=item.get("signal_id", 0),
                    canonical_key=item.get("canonical_key", ""),
                    similarity_score=similarity,
                    text=item.get("text", ""),
                    company_name=item.get("company_name"),
                ))

        # Sort by similarity descending
        results.sort(key=lambda r: r.similarity_score, reverse=True)

        # Return top_k
        return results[:top_k]

    async def embed_pending_signals(
        self,
        batch_size: int = 100,
    ) -> int:
        """
        Embed all signals that don't have embeddings yet.

        Args:
            batch_size: Number of signals to process

        Returns:
            Number of signals embedded
        """
        try:
            pending = await self.store.get_signals_without_embeddings(limit=batch_size)
        except Exception as e:
            logger.warning(f"Failed to get pending signals: {e}")
            return 0

        if not pending:
            return 0

        count = 0
        for signal in pending:
            if await self.embed_signal_from_dict(signal):
                count += 1

        logger.info(f"Embedded {count}/{len(pending)} signals")
        return count

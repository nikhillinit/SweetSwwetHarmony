"""
EmbeddingGenerator - Generates Gemini embeddings for semantic similarity.

Uses Google's text-embedding-004 model (768 dimensions, free tier).

Sprint 4: Similar Companies feature.

Usage:
    generator = EmbeddingGenerator()
    embedding = await generator.embed("Company profile text here...")

    # Batch embedding
    embeddings = await generator.embed_batch(["Text 1", "Text 2", ...])

    # Similarity
    similarity = cosine_similarity(embedding1, embedding2)

Environment:
    GOOGLE_API_KEY or GEMINI_API_KEY - Get from https://aistudio.google.com/apikey
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING

import numpy as np

logger = logging.getLogger(__name__)


# =============================================================================
# EMBEDDING GENERATOR
# =============================================================================

@dataclass
class EmbeddingGenerator:
    """
    Generates embeddings using Google Gemini's text-embedding-004 model.

    Features:
    - 768-dimensional embeddings
    - Free tier: 1500 requests/minute
    - Batch embedding support
    - L2 normalization (optional)
    """

    # Model configuration
    model_name: str = "text-embedding-004"
    embedding_dims: int = 768

    # API configuration
    api_key: Optional[str] = None

    # Processing configuration
    normalize: bool = True
    max_input_chars: int = 10000
    batch_size: int = 100

    # Internal state
    _client: object = None

    def __post_init__(self):
        """Initialize API key from environment if not provided."""
        if self.api_key is None:
            self.api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

    @property
    def client(self):
        """Lazy-load Gemini client."""
        if self._client is None:
            if not self.api_key:
                raise ValueError(
                    "GOOGLE_API_KEY not set. Get one free at https://aistudio.google.com/apikey"
                )
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            except ImportError:
                raise ImportError("google-genai package required: pip install google-genai")
        return self._client

    async def embed(self, text: str) -> np.ndarray:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            768-dimensional numpy array (float32)

        Raises:
            ValueError: If text is empty
        """
        # Validate input
        text = text.strip() if text else ""
        if not text:
            raise ValueError("Cannot embed empty text")

        # Truncate if needed
        if len(text) > self.max_input_chars:
            text = text[: self.max_input_chars]
            logger.warning(f"Text truncated to {self.max_input_chars} chars")

        # Call API
        response = self._call_embed_api(text)

        # Extract embedding
        embedding = np.array(response.embeddings[0].values, dtype=np.float32)

        # Normalize if requested
        if self.normalize:
            embedding = self._normalize(embedding)

        return embedding

    async def embed_batch(
        self, texts: List[str], show_progress: bool = False
    ) -> List[np.ndarray]:
        """
        Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed
            show_progress: Whether to log progress

        Returns:
            List of 768-dimensional numpy arrays
        """
        if not texts:
            return []

        results = []

        # Process in chunks
        total_chunks = (len(texts) + self.batch_size - 1) // self.batch_size

        for i in range(0, len(texts), self.batch_size):
            chunk = texts[i : i + self.batch_size]
            chunk_num = i // self.batch_size + 1

            if show_progress:
                logger.info(f"Embedding chunk {chunk_num}/{total_chunks} ({len(chunk)} texts)")

            chunk_embeddings = await self._embed_chunk(chunk)
            results.extend(chunk_embeddings)

        return results

    async def _embed_chunk(self, texts: List[str]) -> List[np.ndarray]:
        """
        Embed a chunk of texts in a single API call.

        Args:
            texts: Chunk of texts (max batch_size)

        Returns:
            List of embeddings
        """
        # Preprocess texts
        processed = []
        for text in texts:
            text = text.strip() if text else ""
            if not text:
                text = " "  # Replace empty with space to avoid API error
            if len(text) > self.max_input_chars:
                text = text[: self.max_input_chars]
            processed.append(text)

        # Call API
        response = self._call_embed_api(processed)

        # Extract embeddings
        embeddings = []
        for emb_result in response.embeddings:
            embedding = np.array(emb_result.values, dtype=np.float32)
            if self.normalize:
                embedding = self._normalize(embedding)
            embeddings.append(embedding)

        return embeddings

    def _call_embed_api(self, content):
        """
        Call the Gemini embedding API.

        Args:
            content: Single string or list of strings

        Returns:
            API response with embeddings
        """
        return self.client.models.embed_content(
            model=self.model_name,
            contents=content,
        )

    def _normalize(self, embedding: np.ndarray) -> np.ndarray:
        """
        L2 normalize an embedding.

        Args:
            embedding: Input vector

        Returns:
            Normalized vector with L2 norm of 1.0
        """
        norm = np.linalg.norm(embedding)
        if norm > 0:
            return embedding / norm
        return embedding


# =============================================================================
# SIMILARITY FUNCTIONS
# =============================================================================

def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """
    Compute cosine similarity between two vectors.

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Similarity score between -1.0 and 1.0
    """
    # For normalized vectors, cosine = dot product
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return float(np.dot(vec1, vec2) / (norm1 * norm2))


def cosine_similarity_batch(
    query: np.ndarray, candidates: np.ndarray
) -> np.ndarray:
    """
    Compute cosine similarity between a query and multiple candidates.

    Args:
        query: Query vector (D,)
        candidates: Candidate matrix (N, D)

    Returns:
        Similarity scores (N,)
    """
    # Normalize query
    query_norm = np.linalg.norm(query)
    if query_norm == 0:
        return np.zeros(len(candidates))

    query_normalized = query / query_norm

    # Normalize candidates
    candidate_norms = np.linalg.norm(candidates, axis=1, keepdims=True)
    candidate_norms[candidate_norms == 0] = 1  # Avoid division by zero
    candidates_normalized = candidates / candidate_norms

    # Dot product (for normalized vectors, this is cosine similarity)
    return np.dot(candidates_normalized, query_normalized)


# =============================================================================
# SERIALIZATION FUNCTIONS
# =============================================================================

def serialize_embedding(embedding: np.ndarray) -> bytes:
    """
    Serialize embedding to bytes for database storage.

    Args:
        embedding: numpy array

    Returns:
        Bytes representation (float32)
    """
    return embedding.astype(np.float32).tobytes()


def deserialize_embedding(data: bytes) -> np.ndarray:
    """
    Deserialize embedding from bytes.

    Args:
        data: Bytes representation

    Returns:
        numpy array (float32)
    """
    return np.frombuffer(data, dtype=np.float32)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def embed_text(text: str, api_key: Optional[str] = None) -> np.ndarray:
    """
    Convenience function to embed a single text.

    Args:
        text: Text to embed
        api_key: Optional API key (defaults to env var)

    Returns:
        768-dimensional embedding
    """
    generator = EmbeddingGenerator(api_key=api_key)
    return await generator.embed(text)


async def embed_texts(texts: List[str], api_key: Optional[str] = None) -> List[np.ndarray]:
    """
    Convenience function to embed multiple texts.

    Args:
        texts: Texts to embed
        api_key: Optional API key (defaults to env var)

    Returns:
        List of 768-dimensional embeddings
    """
    generator = EmbeddingGenerator(api_key=api_key)
    return await generator.embed_batch(texts)

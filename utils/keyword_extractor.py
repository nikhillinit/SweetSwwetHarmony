"""
KeywordExtractor - Extracts search keywords from profile text for FTS5 queries.

Converts company profile text into a set of search keywords that can be used
for FTS5 candidate retrieval in the Similar Companies feature.

Sprint 4: Similar Companies feature.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set


# =============================================================================
# STOPWORDS
# =============================================================================

# Common English stopwords to filter out
STOPWORDS: Set[str] = {
    # Articles
    "a", "an", "the",
    # Prepositions
    "at", "by", "for", "from", "in", "into", "of", "on", "to", "with",
    # Conjunctions
    "and", "but", "or", "nor", "so", "yet",
    # Pronouns
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "him", "his",
    "she", "her", "it", "its", "they", "them", "their", "this", "that",
    "these", "those", "who", "whom", "which", "what",
    # Verbs (common)
    "is", "am", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can",
    # Other common words
    "as", "if", "then", "than", "when", "where", "how", "why",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "not", "only", "same", "too", "very",
    # Template labels (from ProfileTextBuilder)
    "company", "problem", "customer", "business", "model", "category",
}

# Domain-specific terms to boost (common in startup profiles)
DOMAIN_BOOST_TERMS: Set[str] = {
    # Consumer CPG
    "food", "beverage", "snack", "beauty", "cosmetic", "skincare",
    "personal", "care", "household", "organic", "natural", "wellness",
    # Consumer Health Tech
    "health", "fitness", "wellness", "mental", "supplement", "wearable",
    "nutrition", "diet", "exercise", "meditation", "therapy", "telehealth",
    # Travel & Hospitality
    "travel", "hotel", "hospitality", "restaurant", "booking", "tourism",
    "vacation", "experience", "accommodation", "dining",
    # Consumer Marketplace
    "marketplace", "platform", "delivery", "subscription", "commerce",
    "retail", "consumer", "shopping", "ecommerce",
    # General startup terms
    "saas", "app", "mobile", "digital", "tech", "software", "ai",
    "machine", "learning", "automation", "analytics",
}


# =============================================================================
# KEYWORD EXTRACTOR
# =============================================================================

@dataclass
class KeywordExtractor:
    """
    Extracts search keywords from profile text for FTS5 queries.

    Strategy:
    1. Tokenize and normalize text
    2. Filter stopwords
    3. Score by frequency + position (early words weighted higher)
    4. Boost domain-specific terms
    5. Return top N unique keywords
    """

    # Minimum word length to consider
    min_word_length: int = 3

    # Position decay factor (0.0 = no decay, 1.0 = full decay)
    position_decay: float = 0.3

    # Boost factor for domain terms
    domain_boost: float = 1.5

    def extract(self, text: str, max_keywords: int = 10) -> List[str]:
        """
        Extract search keywords from text.

        Args:
            text: Profile text to extract keywords from
            max_keywords: Maximum number of keywords to return

        Returns:
            List of keywords, ordered by relevance
        """
        if not text or not text.strip():
            return []

        # Tokenize
        tokens = self._tokenize(text)

        if not tokens:
            return []

        # Score tokens
        scored = self._score_tokens(tokens)

        # Sort by score descending
        sorted_keywords = sorted(scored.items(), key=lambda x: x[1], reverse=True)

        # Return top N unique keywords
        return [kw for kw, _ in sorted_keywords[:max_keywords]]

    def build_fts_query(self, keywords: List[str], operator: str = "OR") -> str:
        """
        Build an FTS5-compatible query from keywords.

        Args:
            keywords: List of keywords
            operator: "OR" or "AND"

        Returns:
            FTS5 query string
        """
        if not keywords:
            return ""

        # Escape special FTS5 characters by quoting terms
        escaped = []
        for kw in keywords:
            # Remove or escape special characters
            clean = self._escape_fts_term(kw)
            if clean:
                escaped.append(clean)

        if not escaped:
            return ""

        # Join with operator
        separator = f" {operator} "
        return separator.join(escaped)

    def _tokenize(self, text: str) -> List[str]:
        """
        Tokenize text into words.

        Args:
            text: Input text

        Returns:
            List of tokens (lowercased, filtered)
        """
        # Normalize: lowercase and replace non-alphanumeric with space
        normalized = text.lower()

        # Split hyphenated words
        normalized = re.sub(r"-", " ", normalized)

        # Extract words (alphanumeric only)
        words = re.findall(r"\b[a-z][a-z0-9]*\b", normalized)

        # Filter by length and stopwords
        tokens = []
        for word in words:
            if len(word) >= self.min_word_length and word not in STOPWORDS:
                tokens.append(word)

        return tokens

    def _score_tokens(self, tokens: List[str]) -> Dict[str, float]:
        """
        Score tokens by frequency, position, and domain relevance.

        Args:
            tokens: List of tokens

        Returns:
            Dict mapping token to score
        """
        scores: Dict[str, float] = {}
        seen_positions: Dict[str, int] = {}
        total_tokens = len(tokens)

        for i, token in enumerate(tokens):
            # Track first position (for position weighting)
            if token not in seen_positions:
                seen_positions[token] = i

            # Base score: frequency
            if token not in scores:
                scores[token] = 0.0
            scores[token] += 1.0

        # Apply position weighting and domain boost
        for token in scores:
            # Position factor: earlier = higher weight
            first_pos = seen_positions[token]
            position_factor = 1.0 - (self.position_decay * first_pos / max(total_tokens, 1))
            position_factor = max(0.1, position_factor)  # Floor at 0.1

            # Domain boost
            boost = self.domain_boost if token in DOMAIN_BOOST_TERMS else 1.0

            # Final score
            scores[token] = scores[token] * position_factor * boost

        return scores

    def _escape_fts_term(self, term: str) -> str:
        """
        Escape a term for FTS5 query.

        Args:
            term: Raw term

        Returns:
            Escaped term safe for FTS5
        """
        # Remove special FTS5 characters
        clean = re.sub(r'[":*\-+()^]', "", term)

        # Remove leading/trailing whitespace
        clean = clean.strip()

        if not clean:
            return ""

        # If contains spaces or special chars, quote it
        if " " in clean or "/" in clean:
            return f'"{clean}"'

        return clean


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def extract_keywords(text: str, max_keywords: int = 10) -> List[str]:
    """
    Convenience function to extract keywords.

    Args:
        text: Profile text
        max_keywords: Maximum keywords to return

    Returns:
        List of keywords
    """
    extractor = KeywordExtractor()
    return extractor.extract(text, max_keywords)


def build_fts_query(text: str, max_keywords: int = 10, operator: str = "OR") -> str:
    """
    Convenience function to build FTS query from text.

    Args:
        text: Profile text
        max_keywords: Maximum keywords to extract
        operator: "OR" or "AND"

    Returns:
        FTS5 query string
    """
    extractor = KeywordExtractor()
    keywords = extractor.extract(text, max_keywords)
    return extractor.build_fts_query(keywords, operator)

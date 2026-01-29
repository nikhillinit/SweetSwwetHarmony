"""
Community Sentiment Scoring Module for Discovery Engine

Provides sentiment analysis for community signals (Reddit, Telegram, Discord).
Uses local Ollama model when available, falls back to heuristic analysis.

Features:
- Zero API cost (local Ollama Gemma model)
- Startup-specific keyword detection
- Batch analysis support
- Confidence boost calculation for verification gate

Usage:
    from utils.community_sentiment import CommunitySentimentAnalyzer, SentimentConfig

    # Use with default config (Ollama if available, else heuristic)
    analyzer = CommunitySentimentAnalyzer()
    result = await analyzer.analyze("This product is amazing!")
    print(f"Sentiment: {result.label}, Score: {result.score}")

    # Calculate confidence boost for verification gate
    boost = analyzer.calculate_confidence_boost(result)
    # boost range: -0.15 (very negative) to +0.10 (very positive)

    # Aggregate community sentiment
    from utils.community_sentiment import CommunitySentiment
    sentiment = CommunitySentiment.from_results(results, source="reddit", unique_authors=5)
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Positive keywords for startup/consumer context
DEFAULT_POSITIVE_KEYWORDS = [
    # General positive
    "amazing", "awesome", "excellent", "fantastic", "great", "love",
    "wonderful", "brilliant", "outstanding", "impressive", "incredible",
    "perfect", "best", "beautiful", "revolutionary", "innovative",

    # Startup-specific positive
    "raised", "funding", "launched", "growing", "growth", "traction",
    "revenue", "profitable", "customers", "users", "scaling", "series",
    "backed", "invested", "unicorn", "valuation", "acquired", "exit",
    "viral", "trending", "disrupting", "pioneering", "breakthrough",

    # Product positive
    "useful", "helpful", "intuitive", "elegant", "fast", "reliable",
    "recommend", "love using", "game changer", "must have", "essential",

    # Consumer positive
    "delicious", "tasty", "healthy", "organic", "sustainable", "premium",
    "luxurious", "affordable", "value", "worth it", "addicted",
]

# Negative keywords for startup/consumer context
DEFAULT_NEGATIVE_KEYWORDS = [
    # General negative
    "terrible", "awful", "horrible", "worst", "hate", "disappointing",
    "useless", "broken", "frustrating", "annoying", "stupid", "dumb",
    "pathetic", "ridiculous", "garbage", "trash", "waste",

    # Startup-specific negative
    "scam", "fraud", "fraudulent", "sketchy", "shady", "sus",
    "layoffs", "shutting down", "failed", "bankrupt", "pivoting",
    "struggling", "bleeding", "burning cash", "runway", "failed to",
    "lawsuit", "sued", "investigation", "sec violation",

    # Product negative
    "buggy", "slow", "crashes", "doesn't work", "broken",
    "overpriced", "ripoff", "rip off", "not worth",

    # Consumer negative
    "disgusting", "expired", "rotten", "sick", "unsafe", "dangerous",
    "recalled", "contaminated", "fake", "counterfeit",
]

# Very negative keywords (stronger weight)
STRONG_NEGATIVE_KEYWORDS = [
    "scam", "fraud", "fraudulent", "ponzi", "pyramid scheme",
    "lawsuit", "sued", "criminal", "illegal", "sec violation",
]


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SentimentConfig:
    """Configuration for sentiment analysis."""

    # Ollama settings
    ollama_url: str = "http://localhost:11434"
    model_name: str = "gemma:2b"
    timeout_seconds: int = 30
    use_ollama_if_available: bool = True

    # Heuristic settings
    positive_keywords: List[str] = field(default_factory=lambda: DEFAULT_POSITIVE_KEYWORDS.copy())
    negative_keywords: List[str] = field(default_factory=lambda: DEFAULT_NEGATIVE_KEYWORDS.copy())
    strong_negative_keywords: List[str] = field(default_factory=lambda: STRONG_NEGATIVE_KEYWORDS.copy())

    # Scoring thresholds
    positive_threshold: float = 0.3
    negative_threshold: float = -0.3

    # Confidence boost limits (for verification gate)
    max_positive_boost: float = 0.10
    max_negative_penalty: float = -0.15


@dataclass
class SentimentResult:
    """Result of sentiment analysis for a single text."""

    score: float  # -1.0 (very negative) to 1.0 (very positive)
    label: str  # "positive", "negative", "neutral"
    confidence: float  # 0.0 to 1.0
    method: str  # "heuristic" or "ollama"
    keywords_found: List[str] = field(default_factory=list)
    raw_response: Optional[str] = None  # Ollama raw response

    @property
    def is_positive(self) -> bool:
        """True if sentiment is positive."""
        return self.label == "positive"

    @property
    def is_negative(self) -> bool:
        """True if sentiment is negative."""
        return self.label == "negative"

    @property
    def is_neutral(self) -> bool:
        """True if sentiment is neutral."""
        return self.label == "neutral"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "score": self.score,
            "label": self.label,
            "confidence": self.confidence,
            "method": self.method,
            "keywords_found": self.keywords_found,
        }


@dataclass
class CommunitySentiment:
    """
    Aggregated community sentiment for a company/product.

    Combines multiple mentions from a community source into
    a single sentiment profile.
    """

    source: str  # "reddit", "telegram", "discord"
    mention_count: int
    unique_authors: int
    avg_sentiment_score: float
    sentiment_label: str
    positive_ratio: float
    negative_ratio: float
    neutral_ratio: float
    confidence_boost: float
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_results(
        cls,
        results: List[SentimentResult],
        source: str,
        unique_authors: int,
    ) -> "CommunitySentiment":
        """
        Create CommunitySentiment from list of SentimentResults.

        Args:
            results: List of SentimentResult from individual mentions
            source: Community source (reddit, telegram, discord)
            unique_authors: Count of unique authors

        Returns:
            Aggregated CommunitySentiment
        """
        if not results:
            return cls(
                source=source,
                mention_count=0,
                unique_authors=0,
                avg_sentiment_score=0.0,
                sentiment_label="neutral",
                positive_ratio=0.0,
                negative_ratio=0.0,
                neutral_ratio=0.0,
                confidence_boost=0.0,
            )

        # Calculate averages and ratios
        scores = [r.score for r in results]
        avg_score = sum(scores) / len(scores)

        positive_count = sum(1 for r in results if r.is_positive)
        negative_count = sum(1 for r in results if r.is_negative)
        neutral_count = sum(1 for r in results if r.is_neutral)
        total = len(results)

        positive_ratio = positive_count / total
        negative_ratio = negative_count / total
        neutral_ratio = neutral_count / total

        # Determine overall label
        if positive_ratio > 0.5:
            label = "positive"
        elif negative_ratio > 0.5:
            label = "negative"
        else:
            label = "neutral"

        # Calculate confidence boost
        # Positive buzz boosts confidence, negative buzz penalizes
        if avg_score > 0.3:
            boost = min(avg_score * 0.12, 0.10)  # Max +0.10
        elif avg_score < -0.3:
            boost = max(avg_score * 0.18, -0.15)  # Max -0.15
        else:
            boost = 0.0

        return cls(
            source=source,
            mention_count=total,
            unique_authors=unique_authors,
            avg_sentiment_score=round(avg_score, 3),
            sentiment_label=label,
            positive_ratio=round(positive_ratio, 3),
            negative_ratio=round(negative_ratio, 3),
            neutral_ratio=round(neutral_ratio, 3),
            confidence_boost=round(boost, 4),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "source": self.source,
            "mention_count": self.mention_count,
            "unique_authors": self.unique_authors,
            "avg_sentiment_score": self.avg_sentiment_score,
            "sentiment_label": self.sentiment_label,
            "positive_ratio": self.positive_ratio,
            "negative_ratio": self.negative_ratio,
            "neutral_ratio": self.neutral_ratio,
            "confidence_boost": self.confidence_boost,
            "analyzed_at": self.analyzed_at.isoformat(),
        }


# =============================================================================
# HEURISTIC ANALYZER
# =============================================================================

class HeuristicSentimentAnalyzer:
    """
    Keyword-based sentiment analyzer.

    Zero cost, fast, always available.
    Uses startup/consumer-specific keywords.
    """

    def __init__(self, config: Optional[SentimentConfig] = None):
        """Initialize with optional config."""
        self.config = config or SentimentConfig()

        # Precompile keyword patterns for efficiency
        self._positive_pattern = self._compile_pattern(self.config.positive_keywords)
        self._negative_pattern = self._compile_pattern(self.config.negative_keywords)
        self._strong_negative_pattern = self._compile_pattern(self.config.strong_negative_keywords)

    def _compile_pattern(self, keywords: List[str]) -> re.Pattern:
        """Compile keywords into a single regex pattern."""
        # Escape special characters and join with OR
        escaped = [re.escape(kw) for kw in keywords]
        pattern = r'\b(' + '|'.join(escaped) + r')\b'
        return re.compile(pattern, re.IGNORECASE)

    def analyze(self, text: str) -> SentimentResult:
        """
        Analyze text sentiment using keyword matching.

        Args:
            text: Text to analyze

        Returns:
            SentimentResult with score, label, and confidence
        """
        if not text or not text.strip():
            return SentimentResult(
                score=0.0,
                label="neutral",
                confidence=0.5,
                method="heuristic",
                keywords_found=[],
            )

        text_lower = text.lower()
        keywords_found = []

        # Find positive keywords
        positive_matches = self._positive_pattern.findall(text_lower)
        positive_count = len(positive_matches)
        keywords_found.extend(positive_matches)

        # Find negative keywords
        negative_matches = self._negative_pattern.findall(text_lower)
        negative_count = len(negative_matches)
        keywords_found.extend(negative_matches)

        # Find strong negative keywords (extra weight)
        strong_negative_matches = self._strong_negative_pattern.findall(text_lower)
        strong_negative_count = len(strong_negative_matches)
        # Don't double-count - strong negatives are already in negative
        # But give them extra weight in scoring

        # Calculate raw score
        # Each positive keyword adds +0.15
        # Each negative keyword adds -0.15
        # Each strong negative keyword adds extra -0.2
        raw_score = (positive_count * 0.15) - (negative_count * 0.15) - (strong_negative_count * 0.2)

        # Clamp score to [-1, 1]
        score = max(-1.0, min(1.0, raw_score))

        # Determine label based on thresholds
        if score >= self.config.positive_threshold:
            label = "positive"
        elif score <= self.config.negative_threshold:
            label = "negative"
        else:
            label = "neutral"

        # Confidence based on keyword count and score magnitude
        total_keywords = positive_count + negative_count
        if total_keywords == 0:
            confidence = 0.5  # No evidence
        elif total_keywords >= 5:
            confidence = 0.9  # High confidence with many keywords
        else:
            confidence = 0.5 + (total_keywords * 0.08)  # Scale up with keywords

        return SentimentResult(
            score=round(score, 3),
            label=label,
            confidence=round(min(confidence, 0.95), 2),
            method="heuristic",
            keywords_found=list(set(keywords_found)),  # Dedupe
        )

    def analyze_batch(self, texts: List[str]) -> List[SentimentResult]:
        """Analyze multiple texts."""
        return [self.analyze(text) for text in texts]


# =============================================================================
# OLLAMA ANALYZER
# =============================================================================

class OllamaSentimentAnalyzer:
    """
    Ollama-based sentiment analyzer using local LLM.

    Zero API cost - runs locally on user's machine.
    Falls back to heuristic if Ollama unavailable.
    """

    def __init__(self, config: Optional[SentimentConfig] = None):
        """Initialize with optional config."""
        self.config = config or SentimentConfig()
        self._heuristic = HeuristicSentimentAnalyzer(self.config)
        self._available: Optional[bool] = None

    async def check_available(self) -> bool:
        """Check if Ollama is available and has the required model."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.config.ollama_url}/api/tags")
                if response.status_code == 200:
                    self._available = True
                    return True
        except Exception as e:
            logger.debug(f"Ollama not available: {e}")

        self._available = False
        return False

    async def analyze(self, text: str) -> SentimentResult:
        """
        Analyze text sentiment using Ollama.

        Falls back to heuristic if Ollama unavailable or errors.

        Args:
            text: Text to analyze

        Returns:
            SentimentResult
        """
        if not text or not text.strip():
            return SentimentResult(
                score=0.0,
                label="neutral",
                confidence=0.5,
                method="heuristic",
            )

        # Check availability if not cached
        if self._available is None:
            await self.check_available()

        if not self._available:
            return self._heuristic.analyze(text)

        try:
            import httpx

            prompt = self._build_prompt(text)

            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(
                    f"{self.config.ollama_url}/api/generate",
                    json={
                        "model": self.config.model_name,
                        "prompt": prompt,
                        "stream": False,
                    },
                )

                if response.status_code != 200:
                    logger.warning(f"Ollama returned {response.status_code}")
                    return self._heuristic.analyze(text)

                result = response.json()
                raw_response = result.get("response", "")

                return self._parse_response(raw_response, text)

        except asyncio.TimeoutError:
            logger.warning("Ollama request timed out, falling back to heuristic")
            return self._heuristic.analyze(text)
        except Exception as e:
            logger.warning(f"Ollama error: {e}, falling back to heuristic")
            return self._heuristic.analyze(text)

    def _build_prompt(self, text: str) -> str:
        """Build prompt for Ollama sentiment analysis."""
        return f"""Analyze the sentiment of the following text about a startup or product.
Respond with EXACTLY one line in this format: LABEL SCORE
Where LABEL is POSITIVE, NEGATIVE, or NEUTRAL
And SCORE is a number from -1.0 to 1.0

Text: {text[:500]}

Response:"""

    def _parse_response(self, raw_response: str, original_text: str) -> SentimentResult:
        """Parse Ollama response into SentimentResult."""
        try:
            # Parse "POSITIVE 0.8" format
            parts = raw_response.strip().split()
            if len(parts) >= 2:
                label = parts[0].lower()
                score = float(parts[1])

                if label not in ["positive", "negative", "neutral"]:
                    label = "neutral"

                score = max(-1.0, min(1.0, score))

                return SentimentResult(
                    score=round(score, 3),
                    label=label,
                    confidence=0.85,  # Ollama generally high confidence
                    method="ollama",
                    raw_response=raw_response,
                )
        except (ValueError, IndexError):
            pass

        # Parse failed, fall back to heuristic
        logger.debug(f"Could not parse Ollama response: {raw_response}")
        return self._heuristic.analyze(original_text)

    async def analyze_batch(self, texts: List[str]) -> List[SentimentResult]:
        """Analyze multiple texts."""
        results = []
        for text in texts:
            result = await self.analyze(text)
            results.append(result)
        return results


# =============================================================================
# MAIN ANALYZER (ORCHESTRATOR)
# =============================================================================

class CommunitySentimentAnalyzer:
    """
    Main sentiment analyzer that orchestrates Ollama and heuristic backends.

    Usage:
        analyzer = CommunitySentimentAnalyzer()

        # Async analysis (preferred)
        result = await analyzer.analyze("Great product!")

        # Sync analysis (convenience)
        result = analyzer.analyze_sync("Great product!")

        # Calculate confidence boost for verification gate
        boost = analyzer.calculate_confidence_boost(result)
    """

    def __init__(self, config: Optional[SentimentConfig] = None):
        """Initialize with optional config."""
        self.config = config or SentimentConfig()
        self._heuristic = HeuristicSentimentAnalyzer(self.config)
        self._ollama = OllamaSentimentAnalyzer(self.config)
        self._ollama_checked = False
        self._ollama_available = False

    async def analyze(self, text: str) -> SentimentResult:
        """
        Analyze text sentiment.

        Uses Ollama if available and enabled, otherwise heuristic.

        Args:
            text: Text to analyze

        Returns:
            SentimentResult
        """
        if not self.config.use_ollama_if_available:
            return self._heuristic.analyze(text)

        # Check Ollama availability (cached)
        if not self._ollama_checked:
            self._ollama_available = await self._ollama.check_available()
            self._ollama_checked = True

        if self._ollama_available:
            return await self._ollama.analyze(text)
        else:
            return self._heuristic.analyze(text)

    def analyze_sync(self, text: str) -> SentimentResult:
        """
        Synchronous analysis (uses heuristic only).

        For quick analysis without async overhead.

        Args:
            text: Text to analyze

        Returns:
            SentimentResult
        """
        return self._heuristic.analyze(text)

    async def analyze_batch(self, texts: List[str]) -> List[SentimentResult]:
        """Analyze multiple texts."""
        results = []
        for text in texts:
            result = await self.analyze(text)
            results.append(result)
        return results

    def analyze_batch_sync(self, texts: List[str]) -> List[SentimentResult]:
        """Synchronous batch analysis (uses heuristic only)."""
        return self._heuristic.analyze_batch(texts)

    def calculate_confidence_boost(self, result: SentimentResult) -> float:
        """
        Calculate confidence boost for verification gate.

        Positive community sentiment boosts confidence.
        Negative community sentiment penalizes confidence.

        Args:
            result: SentimentResult from analysis

        Returns:
            Boost value (-0.15 to +0.10)
        """
        if result.is_positive and result.score > 0.3:
            # Scale positive score to boost (max 0.10)
            boost = min(result.score * 0.12, self.config.max_positive_boost)
            return round(boost, 4)
        elif result.is_negative and result.score < -0.3:
            # Scale negative score to penalty (max -0.15)
            penalty = max(result.score * 0.18, self.config.max_negative_penalty)
            return round(penalty, 4)
        else:
            return 0.0

    def aggregate_community_sentiment(
        self,
        results: List[SentimentResult],
        source: str,
        unique_authors: int,
    ) -> CommunitySentiment:
        """
        Aggregate multiple results into CommunitySentiment.

        Args:
            results: List of SentimentResult from mentions
            source: Community source name
            unique_authors: Count of unique authors

        Returns:
            CommunitySentiment aggregate
        """
        return CommunitySentiment.from_results(results, source, unique_authors)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def analyze_sentiment_sync(text: str) -> SentimentResult:
    """
    Quick synchronous sentiment analysis.

    Uses heuristic method only.

    Args:
        text: Text to analyze

    Returns:
        SentimentResult
    """
    analyzer = HeuristicSentimentAnalyzer()
    return analyzer.analyze(text)


async def analyze_sentiment(text: str) -> SentimentResult:
    """
    Async sentiment analysis.

    Uses Ollama if available, otherwise heuristic.

    Args:
        text: Text to analyze

    Returns:
        SentimentResult
    """
    analyzer = CommunitySentimentAnalyzer()
    return await analyzer.analyze(text)


# =============================================================================
# CLI TESTING
# =============================================================================

if __name__ == "__main__":
    # Quick test
    test_texts = [
        "This startup is amazing! Just raised $10M and growing fast!",
        "Terrible product. Total scam. Stay away!",
        "The company announced a new feature today.",
        "I love using this app. Best experience ever!",
        "Overpriced garbage that doesn't work.",
    ]

    analyzer = HeuristicSentimentAnalyzer()

    print("=" * 60)
    print("COMMUNITY SENTIMENT ANALYSIS TEST")
    print("=" * 60)

    for text in test_texts:
        result = analyzer.analyze(text)
        print(f"\nText: {text[:50]}...")
        print(f"  Score: {result.score:+.2f}")
        print(f"  Label: {result.label}")
        print(f"  Confidence: {result.confidence:.2f}")
        print(f"  Keywords: {result.keywords_found[:5]}")

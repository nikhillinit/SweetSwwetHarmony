"""
LLMClassifierV2: Strict-contract LLM classifier for change classification.

This module classifies the NATURE of changes between snapshots:
- pivot: Fundamental business model change (B2C→B2B, consumer→enterprise)
- expansion: Adding new product line or market segment
- rebrand: Name/identity change without business model shift
- minor: Cosmetic changes, typo fixes, small updates
- needs_review: Unclear, requires human review

Features:
- Strict JSON output contract with schema versioning
- Input hashing for deterministic caching
- Confidence threshold override (low confidence → needs_review)
- Cache persistence for cost savings

This is Stage 2 of two-stage signal gating (after TriggerGate).
"""
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "v1"


class ClassificationLabel(Enum):
    """Types of changes that can be classified."""
    PIVOT = "pivot"
    EXPANSION = "expansion"
    REBRAND = "rebrand"
    MINOR = "minor"
    NEEDS_REVIEW = "needs_review"


@dataclass
class ClassifierConfig:
    """Configuration for LLMClassifierV2."""
    model: str = "gemini-2.0-flash"
    min_confidence: float = 0.7
    cache_enabled: bool = True
    api_key: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 300


@dataclass
class ClassificationResult:
    """Result of change classification."""
    schema_version: str
    label: ClassificationLabel
    confidence: float
    rationale: str
    input_hash: str
    cached: bool = False
    raw_response: Optional[Dict[str, Any]] = None


class LLMClassifierV2:
    """
    Strict-contract LLM classifier for change classification with caching.

    Classifies changes between old and new descriptions as:
    - pivot: Fundamental business model change
    - expansion: Adding new product line or market
    - rebrand: Name/identity change
    - minor: Cosmetic updates
    - needs_review: Unclear (or low confidence)

    Usage:
        config = ClassifierConfig()
        classifier = LLMClassifierV2(config)
        result = await classifier.classify(
            old_description="Consumer fitness app",
            new_description="Enterprise wellness platform"
        )
    """

    PROMPT_TEMPLATE = """Analyze the change between old and new company descriptions.

Old: {old_description}
New: {new_description}

Classify this change as ONE of:
- pivot: Fundamental business model change (B2C→B2B, consumer→enterprise, completely different market)
- expansion: Adding new product line or market segment while keeping core business
- rebrand: Name/identity change without business model shift
- minor: Cosmetic changes, typo fixes, small updates, wording improvements
- needs_review: Unclear, ambiguous, or requires human review

Respond with ONLY valid JSON (no markdown, no code blocks):
{{"schema_version": "v1", "label": "<label>", "confidence": <0.0-1.0>, "rationale": "<brief 1-2 sentence explanation>"}}
"""

    def __init__(self, config: Optional[ClassifierConfig] = None):
        """
        Initialize LLMClassifierV2.

        Args:
            config: Classifier configuration. Uses defaults if not provided.
        """
        self.config = config or ClassifierConfig()
        self._cache: Dict[str, ClassificationResult] = {}
        self._client = None

        # Get API key from config or environment
        self.api_key = (
            self.config.api_key
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )

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

    async def classify(
        self,
        old_description: str,
        new_description: str,
    ) -> ClassificationResult:
        """
        Classify the change between old and new descriptions.

        Args:
            old_description: Previous description
            new_description: Current description

        Returns:
            ClassificationResult with label, confidence, rationale
        """
        # Compute input hash for caching
        input_hash = self._compute_hash(old_description, new_description)

        # Check cache
        if self.config.cache_enabled and input_hash in self._cache:
            cached = self._cache[input_hash]
            return ClassificationResult(
                schema_version=cached.schema_version,
                label=cached.label,
                confidence=cached.confidence,
                rationale=cached.rationale,
                input_hash=input_hash,
                cached=True,
            )

        # Call LLM
        response = await self._call_llm(old_description, new_description)

        # Parse and validate
        result = self._parse_response(response, input_hash)

        # Apply confidence threshold override
        if result.confidence < self.config.min_confidence:
            result = ClassificationResult(
                schema_version=result.schema_version,
                label=ClassificationLabel.NEEDS_REVIEW,
                confidence=result.confidence,
                rationale=f"Low confidence ({result.confidence:.2f}): {result.rationale}",
                input_hash=input_hash,
                raw_response=response,
            )

        # Cache result
        if self.config.cache_enabled:
            self._cache[input_hash] = result

        return result

    async def _call_llm(
        self,
        old_description: str,
        new_description: str,
    ) -> Dict[str, Any]:
        """Call Gemini API to classify the change."""
        prompt = self.PROMPT_TEMPLATE.format(
            old_description=old_description or "(empty)",
            new_description=new_description or "(empty)",
        )

        try:
            from google.genai import types

            response = self.client.models.generate_content(
                model=self.config.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=self.config.temperature,
                    max_output_tokens=self.config.max_tokens,
                    response_mime_type="application/json",
                ),
            )
            response_text = response.text.strip()
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            return {
                "schema_version": SCHEMA_VERSION,
                "label": "needs_review",
                "confidence": 0.0,
                "rationale": f"API error: {str(e)}",
            }

        # Parse JSON from response
        try:
            # Handle potential markdown code blocks
            text = response_text
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse response: {e}\nResponse: {response_text[:200]}")
            return {
                "schema_version": SCHEMA_VERSION,
                "label": "needs_review",
                "confidence": 0.0,
                "rationale": f"Parse error: {str(e)}",
            }

    def _parse_response(
        self,
        response: Dict[str, Any],
        input_hash: str,
    ) -> ClassificationResult:
        """Parse and validate LLM response."""
        try:
            label = ClassificationLabel(response.get("label", "needs_review"))
        except ValueError:
            label = ClassificationLabel.NEEDS_REVIEW

        return ClassificationResult(
            schema_version=response.get("schema_version", SCHEMA_VERSION),
            label=label,
            confidence=float(response.get("confidence", 0.0)),
            rationale=response.get("rationale", ""),
            input_hash=input_hash,
            raw_response=response,
        )

    def _compute_hash(self, old: str, new: str) -> str:
        """Compute deterministic hash for input pair."""
        content = f"{old}|||{new}"
        return f"sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"

    def save_cache(self, path: str) -> None:
        """
        Persist cache to JSON file.

        Args:
            path: File path to save cache
        """
        cache_data = {}
        for hash_key, result in self._cache.items():
            cache_data[hash_key] = {
                "schema_version": result.schema_version,
                "label": result.label.value,
                "confidence": result.confidence,
                "rationale": result.rationale,
                "input_hash": result.input_hash,
            }

        with open(path, "w") as f:
            json.dump(cache_data, f, indent=2)

        logger.info(f"Saved {len(cache_data)} cache entries to {path}")

    def load_cache(self, path: str) -> None:
        """
        Load cache from JSON file.

        Args:
            path: File path to load cache from
        """
        try:
            with open(path, "r") as f:
                cache_data = json.load(f)

            for hash_key, data in cache_data.items():
                self._cache[hash_key] = ClassificationResult(
                    schema_version=data["schema_version"],
                    label=ClassificationLabel(data["label"]),
                    confidence=data["confidence"],
                    rationale=data["rationale"],
                    input_hash=data["input_hash"],
                    cached=True,
                )

            logger.info(f"Loaded {len(cache_data)} cache entries from {path}")
        except FileNotFoundError:
            logger.debug(f"No cache file at {path}")
        except Exception as e:
            logger.warning(f"Failed to load cache from {path}: {e}")

    def clear_cache(self) -> int:
        """
        Clear in-memory cache.

        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        return count

    @property
    def cache_size(self) -> int:
        """Return current cache size."""
        return len(self._cache)

"""
FunctionalExtractor - LLM-based functional schema extraction.

Extracts structured company profiles (problem, customer, approach, archetypes)
from signal data using Google Gemini. Reuses the same infrastructure as
LLMClassifier (rate limiter, circuit breaker, API client).

Cost: FREE (shares Gemini AI Studio free tier with thesis classifier)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from utils.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)


# =============================================================================
# PROMPT CONFIGURATION
# =============================================================================

EXTRACTOR_PROMPT_VERSION = "v1.0.0-func-schema"

EXTRACTOR_SYSTEM_PROMPT = """You are a venture capital analyst extracting structured company profiles from signals.

Given a company signal, extract the functional schema:
1. Problem Solved: What customer problem does this company solve? (1 sentence)
2. Customer: Who is the target customer? (specific persona)
3. Approach: How do they solve it? (1 sentence)
4. Customer Archetype: One of: creators, parents, fitness_enthusiasts, travelers, foodies, beauty_consumers, pet_owners, students, gamers, shoppers, patients, general_consumer, unknown
5. Problem Archetypes: Array of: content_monetization, meal_delivery, fitness_tracking, beauty_personalization, travel_booking, health_monitoring, marketplace, subscription, social_commerce, creator_economy, wellness, mental_health, other
6. Confidence: How confident are you in this extraction? (0.0 = guessing from minimal info, 1.0 = clear and unambiguous). Output as a number.

Output JSON only. Example:
{"problem_solved": "Helps busy parents find healthy meal options for their families", "customer": "Health-conscious parents with young children", "approach": "AI-powered meal planning with grocery delivery integration", "customer_archetype": "parents", "problem_archetypes": ["meal_delivery", "subscription"], "schema_confidence": 0.85}
"""

VALID_ARCHETYPES = {
    "creators", "parents", "fitness_enthusiasts", "travelers",
    "foodies", "beauty_consumers", "pet_owners", "students",
    "gamers", "shoppers", "patients", "general_consumer", "unknown",
}

VALID_PROBLEM_ARCHETYPES = {
    "content_monetization", "meal_delivery", "fitness_tracking",
    "beauty_personalization", "travel_booking", "health_monitoring",
    "marketplace", "subscription", "social_commerce", "creator_economy",
    "wellness", "mental_health", "other",
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class FunctionalSchema:
    """Extracted functional schema for a company."""
    company_id: str
    problem_solved_text: Optional[str] = None
    customer_text: Optional[str] = None
    approach_text: Optional[str] = None
    customer_archetype: str = "unknown"
    problem_archetypes: List[str] = field(default_factory=list)
    schema_confidence: float = 0.0
    is_advisory: bool = False
    evidence_signal_ids: List[int] = field(default_factory=list)
    extraction_model: str = ""
    extraction_prompt_version: str = EXTRACTOR_PROMPT_VERSION

    def to_storage_dict(self) -> Dict[str, Any]:
        """Convert to dict suitable for SignalStore.save_functional_schema()."""
        return {
            "company_id": self.company_id,
            "problem_solved_text": self.problem_solved_text,
            "customer_text": self.customer_text,
            "approach_text": self.approach_text,
            "customer_archetype": self.customer_archetype,
            "problem_archetypes": self.problem_archetypes,
            "schema_confidence": self.schema_confidence,
            "is_advisory": self.is_advisory,
            "evidence_signal_ids": self.evidence_signal_ids,
            "extraction_model": self.extraction_model,
            "extraction_prompt_version": self.extraction_prompt_version,
        }


# =============================================================================
# FUNCTIONAL EXTRACTOR
# =============================================================================

class FunctionalExtractor:
    """
    LLM-based functional schema extractor using Google Gemini.

    Reuses the same Gemini infrastructure as LLMClassifier:
    - Rate limiter (shared 15 RPM / 1500 RPD free tier)
    - Circuit breaker (5 failures, 10min recovery)
    - Same API key (GOOGLE_API_KEY)

    Usage:
        extractor = FunctionalExtractor()
        schema = await extractor.extract(signal_data, company_id="comp-123")
        if schema:
            await store.save_functional_schema(schema.to_storage_dict())
    """

    def __init__(
        self,
        model: str = "gemini-2.0-flash",
        api_key: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 400,
        confidence_threshold: Optional[float] = None,
        rate_limiter=None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self.model_name = model
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None

        if confidence_threshold is None:
            self.confidence_threshold = float(
                os.environ.get("FUNCTIONAL_SCHEMA_CONFIDENCE_THRESHOLD", "0.6")
            )
        else:
            self.confidence_threshold = confidence_threshold

        # Reuse rate limiter if provided (shared with thesis classifier)
        self._rate_limiter = rate_limiter
        self._circuit_breaker = circuit_breaker or CircuitBreaker(
            name="gemini_func_extractor",
            failure_threshold=5,
            recovery_timeout=600,
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

    async def extract(
        self,
        signal_data: Dict[str, Any],
        company_id: str,
        evidence_signal_ids: Optional[List[int]] = None,
    ) -> Optional[FunctionalSchema]:
        """
        Extract functional schema from signal data.

        Args:
            signal_data: Dict with title, source_context, source_api, etc.
            company_id: Company identifier for the schema
            evidence_signal_ids: Signal IDs that contributed to this extraction

        Returns:
            FunctionalSchema if successful, None if extraction failed
        """
        # Circuit breaker check
        if self._circuit_breaker.state == "open":
            logger.warning("Functional extractor circuit breaker OPEN, skipping")
            return None

        # Rate limiting (if limiter provided)
        if self._rate_limiter:
            try:
                await self._rate_limiter.acquire()
            except RuntimeError as e:
                logger.error(f"Rate limit exceeded for functional extraction: {e}")
                return None

        # Build prompt
        title = signal_data.get("title", "N/A")
        source = signal_data.get("source_api", "unknown")
        context = signal_data.get("source_context", "")

        if context and len(context) > 500:
            context = context[:500] + "..."

        user_prompt = f"""{EXTRACTOR_SYSTEM_PROMPT}

Extract the functional schema for this company signal:

Company: {title}
Source: {source}
Context: {context if context else 'N/A'}

Respond with JSON only."""

        # Call Gemini API
        start_time = time.time()

        try:
            response = await self._circuit_breaker.call(
                self._call_gemini_api,
                user_prompt,
            )
            response_text = response.text
        except CircuitOpenError as e:
            logger.warning(f"Circuit breaker rejected extraction call: {e}")
            return None
        except Exception as e:
            logger.error(f"Gemini API error during extraction: {e}")
            return None

        latency_ms = int((time.time() - start_time) * 1000)
        logger.debug(f"Functional extraction completed in {latency_ms}ms")

        # Parse response
        try:
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)

            result = json.loads(cleaned)
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"Failed to parse extraction response: {e}")
            return None

        # Validate and build schema
        return self._build_schema(
            result, company_id, evidence_signal_ids or [], latency_ms
        )

    def _build_schema(
        self,
        result: Dict[str, Any],
        company_id: str,
        evidence_signal_ids: List[int],
        latency_ms: int,
    ) -> Optional[FunctionalSchema]:
        """Build FunctionalSchema from parsed LLM response, with validation."""
        # Extract confidence (clamp to [0.0, 1.0])
        raw_confidence = result.get("schema_confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(raw_confidence)))
        except (TypeError, ValueError):
            logger.warning(f"Invalid schema_confidence value: {raw_confidence}")
            confidence = 0.0

        # Validate customer_archetype
        archetype = result.get("customer_archetype", "unknown")
        if archetype not in VALID_ARCHETYPES:
            logger.warning(f"Unknown archetype '{archetype}', defaulting to 'unknown'")
            archetype = "unknown"

        # Validate problem_archetypes
        raw_problems = result.get("problem_archetypes", [])
        if not isinstance(raw_problems, list):
            raw_problems = []
        problem_archetypes = [p for p in raw_problems if p in VALID_PROBLEM_ARCHETYPES]

        return FunctionalSchema(
            company_id=company_id,
            problem_solved_text=result.get("problem_solved"),
            customer_text=result.get("customer"),
            approach_text=result.get("approach"),
            customer_archetype=archetype,
            problem_archetypes=problem_archetypes,
            schema_confidence=confidence,
            is_advisory=confidence < self.confidence_threshold,
            evidence_signal_ids=evidence_signal_ids,
            extraction_model=self.model_name,
            extraction_prompt_version=EXTRACTOR_PROMPT_VERSION,
        )

    async def _call_gemini_api(self, prompt: str):
        """Call Gemini API (wrapped by circuit breaker)."""
        return self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={
                "temperature": self.temperature,
                "max_output_tokens": self.max_tokens,
            },
        )

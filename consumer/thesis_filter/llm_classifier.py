"""
LLM Thesis Classifier - Stage 2 of Two-Stage Thesis Filter

Uses Google Gemini (AI Studio free tier) to classify signals for consumer thesis fit.
Cost: FREE (1.5M tokens/day on AI Studio)

Categories:
- consumer_cpg: Food, beverage, beauty, personal care
- consumer_health_tech: Fitness, wellness, mental health, supplements
- travel_hospitality: Travel, hospitality, restaurants
- consumer_marketplace: Consumer-facing marketplaces

Audit trail stored in llm_classifications table.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from utils.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)


# =============================================================================
# PROMPT CONFIGURATION
# =============================================================================

CLASSIFIER_PROMPT_VERSION = "v1.4.0-gemini-adjacent"

CLASSIFIER_SYSTEM_PROMPT = """You are a venture capital analyst evaluating early-stage consumer startups.

Your task: Determine if a signal indicates a promising CONSUMER startup matching our thesis.

## Investment Thesis
We invest in PRE-SEED to SERIES A consumer companies:
- Consumer CPG: Food, beverage, snacks, beauty, personal care, household products
- Consumer Health Tech: Fitness apps, wellness, mental health, supplements, wearables
- Travel & Hospitality: Travel booking, hospitality tech, restaurants, experiences
- Consumer Marketplaces: Consumer-facing two-sided markets

## NOT In Thesis (Exclude)
- B2B/Enterprise software
- Developer tools, APIs, infrastructure
- Crypto/Web3/NFT
- Services/Consulting/Agencies
- Late-stage companies (Series B+)
- Hardware-only (no software/data moat)

## Adjacent Categories (Edge Cases — Evaluate Carefully)
These categories are SOMETIMES in thesis depending on execution:
- Creator Economy: In thesis if consumer-facing (e.g., creator monetization tools for individual creators). Out of thesis if B2B SaaS for brands.
- Pet Tech: In thesis if consumer product (pet food DTC, pet health app). Out of thesis if B2B vet software.
- EdTech: In thesis if consumer learning app (language learning, tutoring marketplace). Out of thesis if enterprise LMS.
- FinTech: In thesis if consumer financial wellness (budgeting app, savings). Out of thesis if B2B payments infra.
- FoodTech: In thesis if consumer-facing (meal kit, restaurant, food delivery). Out of thesis if B2B food supply chain.

When classifying edge cases, ask: "Is the END USER an individual consumer making a personal purchase decision?"

## Output Format
Respond ONLY with valid JSON (no markdown, no code blocks):
{
    "thesis_match": true,
    "thesis_fit_score": 0.75,
    "category": "consumer_cpg",
    "stage_estimate": "seed",
    "confidence": "high",
    "company_name": "Company Name",
    "rationale": "2-3 sentence explanation",
    "key_signals": ["signal1", "signal2"]
}

Valid categories: consumer_cpg, consumer_health_tech, travel_hospitality, consumer_marketplace, other, excluded
Valid stages: pre_seed, seed, series_a, later_stage, unknown
Valid confidence: high, medium, low

## Scoring Guide
- 0.85-1.0: Strong thesis match, clear consumer focus, likely early-stage
- 0.65-0.84: Good match, mostly consumer, may need verification
- 0.50-0.64: Marginal match, some consumer elements
- 0.30-0.49: Weak match, primarily B2B or unclear
- 0.00-0.29: No match, clearly outside thesis
"""

CHAIN_OF_THOUGHT_PROMPT = """Before providing your classification, think through these questions step by step:

1. **Consumer vs B2B**: Who is the end customer? Individual consumers or businesses?
2. **Category Fit**: Does this match CPG, Health Tech, Travel/Hospitality, or Marketplace?
3. **Excluded Categories**: Is this crypto, services, developer tools, or hardware-only?
4. **Stage Assessment**: Does this appear to be pre-seed to Series A stage?
5. **Thesis Strength**: How strongly does this align with our investment thesis?

Provide your reasoning for each step, then give your final classification."""


# =============================================================================
# RATE LIMITER
# =============================================================================

class RateLimiter:
    """
    Token bucket rate limiter for Gemini API calls.

    Gemini AI Studio free tier limits:
    - 1500 requests per day
    - 15 requests per minute

    Usage:
        limiter = RateLimiter(rpm=15, rpd=1500)
        await limiter.acquire()  # Blocks until a token is available
    """

    def __init__(self, rpm: int = 15, rpd: int = 1500):
        """
        Initialize rate limiter.

        Args:
            rpm: Requests per minute
            rpd: Requests per day
        """
        self.rpm = rpm
        self.rpd = rpd
        self._minute_calls: deque = deque()  # Timestamps of calls in last minute
        self._day_calls: deque = deque()  # Timestamps of calls in last day

    async def acquire(self) -> None:
        """
        Acquire a rate limit token, blocking if necessary.

        Raises:
            RuntimeError: If daily quota is exhausted
        """
        now = datetime.now(timezone.utc)
        minute_ago = now - timedelta(minutes=1)
        day_ago = now - timedelta(days=1)

        # Clean old entries
        while self._minute_calls and self._minute_calls[0] < minute_ago:
            self._minute_calls.popleft()
        while self._day_calls and self._day_calls[0] < day_ago:
            self._day_calls.popleft()

        # Check daily quota first (hard limit)
        if len(self._day_calls) >= self.rpd:
            raise RuntimeError(f"Daily quota exhausted ({self.rpd} requests)")

        # Check minute rate (soft limit - sleep if needed)
        if len(self._minute_calls) >= self.rpm:
            sleep_until = self._minute_calls[0] + timedelta(minutes=1)
            sleep_seconds = (sleep_until - now).total_seconds()
            if sleep_seconds > 0:
                logger.warning(f"Rate limit: sleeping {sleep_seconds:.1f}s")
                await self._sleep(sleep_seconds)

        # Record this call
        self._minute_calls.append(now)
        self._day_calls.append(now)

    async def _sleep(self, seconds: float) -> None:
        """Async sleep helper."""
        import asyncio
        await asyncio.sleep(seconds)

    def reset(self) -> None:
        """Reset rate limiter state (for testing)."""
        self._minute_calls.clear()
        self._day_calls.clear()


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ThesisClassification:
    """Result of LLM thesis classification."""
    thesis_match: bool
    thesis_fit_score: float
    category: str
    stage_estimate: str
    confidence: str
    company_name: Optional[str]
    rationale: str
    key_signals: List[str]
    prompt_version: str
    model: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    raw_response: Optional[Dict[str, Any]] = None
    # Chain-of-thought reasoning support
    cot_enabled: bool = False
    reasoning_trace: Optional[Dict[str, Any]] = None


# =============================================================================
# GEMINI CLASSIFIER (Google AI Studio - FREE)
# =============================================================================

class LLMClassifier:
    """
    LLM-based thesis classifier using Google Gemini (AI Studio free tier).

    FREE: 1.5M tokens/day = ~3,000+ signals/day at no cost.

    Usage:
        classifier = LLMClassifier()
        result = await classifier.classify({
            "title": "Show HN: My meal kit delivery startup",
            "url": "https://example.com",
            "source_api": "hn",
            "source_context": "We're launching..."
        })

    Environment:
        GOOGLE_API_KEY or GEMINI_API_KEY - Get from https://aistudio.google.com/apikey
    """

    def __init__(
        self,
        model: str = "gemini-2.0-flash",
        api_key: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 400,
        cot_enabled: Optional[bool] = None,
        rate_limiter: Optional[RateLimiter] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        """
        Initialize Gemini classifier.

        Args:
            model: Gemini model (gemini-2.0-flash recommended)
            api_key: Google API key (defaults to GOOGLE_API_KEY or GEMINI_API_KEY)
            temperature: Sampling temperature (lower = more deterministic)
            max_tokens: Max response tokens
            cot_enabled: Enable chain-of-thought reasoning (default: from ENABLE_COT_REASONING env)
            rate_limiter: Rate limiter (defaults to 15 RPM, 1500 RPD)
            circuit_breaker: Circuit breaker (defaults to 5 failures, 600s timeout)
        """
        self.model_name = model
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None
        # Chain-of-thought: enabled via param or env var
        if cot_enabled is None:
            self.cot_enabled = os.environ.get("ENABLE_COT_REASONING", "").lower() in ("true", "1", "yes")
        else:
            self.cot_enabled = cot_enabled

        # Phase 9: Rate limiting + circuit breaker
        self._rate_limiter = rate_limiter or RateLimiter(rpm=15, rpd=1500)
        self._circuit_breaker = circuit_breaker or CircuitBreaker(
            name="gemini_llm",
            failure_threshold=5,
            recovery_timeout=600  # 10 minutes
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
        signal_data: Dict[str, Any],
    ) -> ThesisClassification:
        """
        Classify a signal for thesis fit.

        Args:
            signal_data: Dict with title, url, source_api, source_context

        Returns:
            ThesisClassification result
        """
        # Phase 9: Check circuit breaker
        if self._circuit_breaker.state == "open":
            logger.warning("LLM circuit breaker OPEN, skipping classification")
            return ThesisClassification(
                thesis_match=False,
                thesis_fit_score=0.0,
                category="excluded",
                stage_estimate="unknown",
                confidence="low",
                company_name=None,
                rationale="Circuit breaker OPEN (Gemini unavailable)",
                key_signals=[],
                prompt_version=CLASSIFIER_PROMPT_VERSION,
                model=self.model_name,
            )

        # Phase 9: Rate limiting
        try:
            await self._rate_limiter.acquire()
        except RuntimeError as e:
            logger.error(f"Rate limit exceeded: {e}")
            return ThesisClassification(
                thesis_match=False,
                thesis_fit_score=0.0,
                category="excluded",
                stage_estimate="unknown",
                confidence="low",
                company_name=None,
                rationale=f"Rate limit exceeded: {str(e)}",
                key_signals=[],
                prompt_version=CLASSIFIER_PROMPT_VERSION,
                model=self.model_name,
            )

        # Build user prompt
        title = signal_data.get("title", "N/A")
        url = signal_data.get("url", "N/A")
        source = signal_data.get("source_api", "unknown")
        context = signal_data.get("source_context", "")

        # Truncate context to avoid excessive tokens
        if context and len(context) > 500:
            context = context[:500] + "..."

        # Build prompt with optional chain-of-thought
        if self.cot_enabled:
            user_prompt = f"""{CLASSIFIER_SYSTEM_PROMPT}

{CHAIN_OF_THOUGHT_PROMPT}

Evaluate this signal:

Title: {title}
URL: {url}
Source: {source}
Context: {context if context else 'N/A'}

First provide your step-by-step reasoning, then output your JSON classification."""
        else:
            user_prompt = f"""{CLASSIFIER_SYSTEM_PROMPT}

Evaluate this signal:

Title: {title}
URL: {url}
Source: {source}
Context: {context if context else 'N/A'}

Respond with JSON classification only."""

        # Call Gemini API through circuit breaker
        start_time = time.time()

        try:
            # Phase 9: Wrap API call in circuit breaker
            response = await self._circuit_breaker.call(
                self._call_gemini_api,
                user_prompt
            )
            response_text = response.text
        except CircuitOpenError as e:
            logger.warning(f"Circuit breaker rejected call: {e}")
            return ThesisClassification(
                thesis_match=False,
                thesis_fit_score=0.0,
                category="excluded",
                stage_estimate="unknown",
                confidence="low",
                company_name=None,
                rationale=f"Circuit breaker OPEN: {str(e)}",
                key_signals=[],
                prompt_version=CLASSIFIER_PROMPT_VERSION,
                model=self.model_name,
            )
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            return ThesisClassification(
                thesis_match=False,
                thesis_fit_score=0.0,
                category="excluded",
                stage_estimate="unknown",
                confidence="low",
                company_name=None,
                rationale=f"Classification failed: {str(e)}",
                key_signals=[],
                prompt_version=CLASSIFIER_PROMPT_VERSION,
                model=self.model_name,
            )

        latency_ms = int((time.time() - start_time) * 1000)

        # Parse response - handle potential markdown code blocks
        try:
            # Strip markdown code blocks if present
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                # Remove ```json and ``` markers
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)

            result = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini response: {e}\nResponse: {response_text[:200]}")
            return ThesisClassification(
                thesis_match=False,
                thesis_fit_score=0.0,
                category="excluded",
                stage_estimate="unknown",
                confidence="low",
                company_name=None,
                rationale=f"Failed to parse response: {response_text[:100]}",
                key_signals=[],
                prompt_version=CLASSIFIER_PROMPT_VERSION,
                model=self.model_name,
            )

        # Extract usage info if available
        input_tokens = None
        output_tokens = None
        try:
            if hasattr(response, 'usage_metadata'):
                input_tokens = response.usage_metadata.prompt_token_count
                output_tokens = response.usage_metadata.candidates_token_count
        except Exception:
            pass

        # Build reasoning trace if CoT enabled
        reasoning_trace = None
        if self.cot_enabled:
            reasoning_trace = {
                "cot_enabled": True,
                "raw_response_text": response_text[:2000] if response_text else None,
                "reasoning_steps": result.get("reasoning_steps", []),
            }

        return ThesisClassification(
            thesis_match=result.get("thesis_match", False),
            thesis_fit_score=float(result.get("thesis_fit_score", 0.0)),
            category=result.get("category", "other"),
            stage_estimate=result.get("stage_estimate", "unknown"),
            confidence=result.get("confidence", "low"),
            company_name=result.get("company_name"),
            rationale=result.get("rationale", ""),
            key_signals=result.get("key_signals", []),
            prompt_version=CLASSIFIER_PROMPT_VERSION,
            model=self.model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            raw_response=result,
            cot_enabled=self.cot_enabled,
            reasoning_trace=reasoning_trace,
        )

    def classify_sync(
        self,
        signal_data: Dict[str, Any],
    ) -> ThesisClassification:
        """
        Synchronous version of classify().
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self.classify(signal_data))
                return future.result()
        else:
            return asyncio.run(self.classify(signal_data))

    async def _call_gemini_api(self, user_prompt: str):
        """
        Internal helper to call Gemini API.

        Separated for circuit breaker wrapping.
        """
        from google.genai import types

        return self.client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                temperature=self.temperature,
                max_output_tokens=self.max_tokens,
                response_mime_type="application/json",
            ),
        )

    def estimate_cost(self, signal_count: int) -> float:
        """
        Estimate cost for classifying N signals.

        Gemini 2.0 Flash on AI Studio: FREE (1500 RPM, 1M tokens/day)

        Returns:
            0.0 (free tier)
        """
        return 0.0  # FREE on Google AI Studio

    @property
    def circuit_breaker_stats(self) -> dict:
        """Get circuit breaker statistics."""
        return self._circuit_breaker.stats()


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

_default_classifier: Optional[LLMClassifier] = None


def get_classifier() -> LLMClassifier:
    """Get default classifier instance."""
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = LLMClassifier()
    return _default_classifier


async def classify_signal(signal_data: Dict[str, Any]) -> ThesisClassification:
    """
    Convenience function to classify a signal.

    Args:
        signal_data: Dict with title, url, source_api, source_context

    Returns:
        ThesisClassification result
    """
    return await get_classifier().classify(signal_data)

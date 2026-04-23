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
from email.utils import parsedate_to_datetime
from enum import Enum
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from collectors.retry_strategy import RateLimitError, RetryConfig, with_retry
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from telemetry.thesis_tracing import (
    create_thesis_tracer,
    redact_error_message,
    should_include_raw_traces,
    summarize_text_payload,
)
from utils.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

GEMINI_RATE_LIMIT_RETRY_CONFIG = RetryConfig(
    max_retries=2,
    backoff_base=2.0,
    backoff_max=8.0,
    jitter=False,
)
DEFAULT_GEMINI_RETRY_AFTER_SECONDS = 5.0


# =============================================================================
# PROMPT CONFIGURATION
# =============================================================================

CLASSIFIER_PROMPT_VERSION = "v1.6.0-employer-distribution-guard"

VALID_PRIMARY_END_USERS = {
    "individual_consumer",
    "business_employee",
    "both",
    "unclear",
}

VALID_PAYING_CUSTOMERS = {
    "individual_consumer",
    "business",
    "both",
    "unclear",
}

VALID_SELLS_TO_OR_OPERATES_IN = {
    "sells_tools_to_industry",
    "operates_in_industry_for_consumers",
    "both",
    "unclear",
}


def _normalize_choice(value: Any, allowed: set[str], default: str = "unclear") -> str:
    """Normalize model-emitted structured fields to allowed enum values."""
    if not isinstance(value, str):
        return default
    normalized = value.strip().lower()
    return normalized if normalized in allowed else default


def _coerce_bool(value: Any) -> bool:
    """Parse permissive boolean values without treating arbitrary strings as truthy."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return False


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    """Convert model output to a bounded score."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, score))


def _coerce_string(value: Any, *, default: str = "") -> str:
    """Coerce provider output into a string field."""
    return value if isinstance(value, str) else default


def _coerce_optional_string(value: Any) -> Optional[str]:
    """Preserve optional string fields without forcing non-strings into text."""
    return value if isinstance(value, str) else None


def _coerce_string_list(value: Any) -> List[str]:
    """Normalize list-like string fields to a list of strings."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str):
        return [value]
    return []


def _exception_type(exc: Exception) -> str:
    """Return a redaction-safe exception label."""
    return type(exc).__name__


def _is_rate_limit_exception(exc: Exception) -> bool:
    """Return whether an exception represents an upstream rate-limit/quota response."""
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code == 429:
        return True

    message = getattr(exc, "message", "")
    if isinstance(message, str):
        message_lower = message.lower()
        if "resource exhausted" in message_lower or "rate limit" in message_lower:
            return True

    error_text = str(exc).lower()
    return "429" in error_text or "resource_exhausted" in error_text or "rate limit" in error_text


def _extract_retry_after_seconds(exc: Exception) -> Optional[float]:
    """Best-effort parse of Retry-After from provider exceptions."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers or not hasattr(headers, "get"):
        return None

    retry_after = headers.get("Retry-After")
    if retry_after is None:
        return None

    try:
        return max(0.0, float(retry_after))
    except (TypeError, ValueError):
        pass

    try:
        target = parsedate_to_datetime(retry_after)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _as_rate_limit_error(exc: Exception) -> RateLimitError:
    """Normalize provider-specific quota errors into the shared retry type."""
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", 429) or 429
    return RateLimitError(
        "Gemini rate limit exceeded",
        wait_seconds=_extract_retry_after_seconds(exc) or DEFAULT_GEMINI_RETRY_AFTER_SECONDS,
        status_code=status_code,
        endpoint="gemini.generate_content",
        response_summary=str(status_code),
    )


class ClassificationStatus(str, Enum):
    """Operational outcome for a classification attempt."""
    SUCCESS = "success"
    ERROR_API = "error_api"
    ERROR_PARSE = "error_parse"
    ERROR_RATE_LIMIT = "error_rate_limit"
    ERROR_CIRCUIT_BREAKER = "error_circuit_breaker"


class _ThesisClassifierResponse(BaseModel):
    """Internal schema for provider responses before dataclass adaptation."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    thesis_match: bool = False
    thesis_fit_score: float = 0.0
    category: str = "other"
    stage_estimate: str = "unknown"
    confidence: str = "low"
    company_name: str | None = None
    rationale: str = ""
    key_signals: List[str] = Field(default_factory=list)
    reasoning_steps: List[str] = Field(default_factory=list)
    primary_end_user: str = "unclear"
    paying_customer: str = "unclear"
    sells_to_or_operates_in: str = "unclear"

    @field_validator("thesis_match", mode="before")
    @classmethod
    def _validate_thesis_match(cls, value: Any) -> bool:
        return _coerce_bool(value)

    @field_validator("thesis_fit_score", mode="before")
    @classmethod
    def _validate_score(cls, value: Any) -> float:
        return _coerce_float(value)

    @field_validator(
        "category",
        "stage_estimate",
        "confidence",
        "rationale",
        "primary_end_user",
        "paying_customer",
        "sells_to_or_operates_in",
        mode="before",
    )
    @classmethod
    def _validate_string_field(cls, value: Any) -> str:
        return _coerce_string(value)

    @field_validator("company_name", mode="before")
    @classmethod
    def _validate_company_name(cls, value: Any) -> Optional[str]:
        return _coerce_optional_string(value)

    @field_validator("key_signals", "reasoning_steps", mode="before")
    @classmethod
    def _validate_string_list(cls, value: Any) -> List[str]:
        return _coerce_string_list(value)

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

## B2B-in-Disguise Detection
CRITICAL: A company serving a consumer industry is not automatically a consumer company.

Ask these questions before deciding:
1. Is this company selling tools TO an industry, or operating IN that industry for consumers?
2. Who is the primary end user?
3. Who is the paying customer?

Rules:
- Selling operating software/tools TO restaurants, hotels, retailers, clinics, or employers is EXCLUDED
- Operating IN those sectors for individual consumers can still be IN THESIS
- Two-sided consumer marketplaces stay in thesis when the consumer side is primary

### Employer/Work-Linked Benefits Distribution
Products distributed through employers, benefit programs, or gig platforms raise a go-to-market concern even when the end user is an individual consumer.

Default rule: If the PAYING CUSTOMER is a business/employer and there is NO evidence of an independent direct-to-consumer acquisition channel, score 0.20-0.29 (flags for human review).

Specifics:
- Employer-funded wellness, mental health, or care-navigation apps where the employer selects and pays -> score 0.20-0.25
- Insurance, stipend, or benefit products distributed via gig platforms or employer benefit packages -> score 0.20-0.25
- Insurance or benefits-infrastructure products for gig workers, even if purchased directly by the individual -> score 0.20-0.25 (insurance straddles services/health-tech boundary)
- Products with BOTH employer distribution AND a clear independent D2C channel (consumers can also buy directly without employer) -> score normally
- Consumer apps that happen to serve gig workers but are purchased directly by individuals (e.g., meal marketplace, personal fitness app) -> score normally

Examples:
- "Employer-sponsored wellness app for employees" -> paying_customer=business, no D2C evidence -> score 0.22
- "Employer-funded mental health app for workers and families" -> paying_customer=business -> score 0.23
- "Care-navigation app distributed through employer benefit programs" -> paying_customer=business -> score 0.22
- "Insurance and care app for couriers via gig platform benefits" -> benefit-distribution model -> score 0.24
- "Insurance and care app for couriers buying benefits directly" -> insurance/benefits infra for gig workers -> score 0.24
- "Mental health app with employer AND consumer subscription plans" -> paying_customer=both -> score normally
- "Marketplace app helping gig workers find discounted meals" -> paying_customer=individual_consumer, direct purchase -> score normally
- "Wellness app for rideshare drivers managing sleep and stress" -> paying_customer=individual_consumer, direct purchase -> score normally

Examples (general B2B-in-Disguise):
- "AI voice ordering system for restaurants" -> sells tools TO industry -> EXCLUDED
- "Restaurant reservation app for diners" -> operates IN industry for consumers -> IN THESIS
- "Hotel property management system" -> sells tools TO hotels -> EXCLUDED
- "BNPL for hotel bookings" -> operates IN travel for consumers -> IN THESIS

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
    "key_signals": ["signal1", "signal2"],
    "primary_end_user": "individual_consumer",
    "paying_customer": "business",
    "sells_to_or_operates_in": "sells_tools_to_industry"
}

Valid categories: consumer_cpg, consumer_health_tech, travel_hospitality, consumer_marketplace, other, excluded
Valid stages: pre_seed, seed, series_a, later_stage, unknown
Valid confidence: high, medium, low
Valid primary_end_user: individual_consumer, business_employee, both, unclear
Valid paying_customer: individual_consumer, business, both, unclear
Valid sells_to_or_operates_in: sells_tools_to_industry, operates_in_industry_for_consumers, both, unclear

## Scoring Guide
- 0.85-1.0: Strong thesis match, clear consumer focus, likely early-stage
- 0.65-0.84: Good match, mostly consumer, may need verification
- 0.50-0.64: Marginal match, some consumer elements
- 0.30-0.49: Weak match, primarily B2B or unclear
- 0.20-0.29: Ambiguous distribution — employer-funded, benefit-linked, or platform-distributed products without clear D2C pull (flags for human review)
- 0.00-0.19: No match, clearly outside thesis
"""

CHAIN_OF_THOUGHT_PROMPT = """Before providing your classification, think through these questions step by step:

1. **Consumer vs B2B**: Who is the end customer? Individual consumers or businesses?
1.5. **B2B-in-Disguise Check**: Is this selling tools TO a consumer industry or operating IN that industry for consumers? Populate primary_end_user, paying_customer, sells_to_or_operates_in.
1.6. **Distribution Channel Check**: Is this product distributed through employers, benefit programs, or gig platforms? If paying_customer is "business" and there is no evidence of independent D2C acquisition, cap thesis_fit_score at 0.20-0.29.
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
    classification_status: str = ClassificationStatus.SUCCESS.value
    primary_end_user: str = "unclear"
    paying_customer: str = "unclear"
    sells_to_or_operates_in: str = "unclear"


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
        max_tokens: int = 800,
        cot_enabled: Optional[bool] = None,
        system_prompt: Optional[str] = None,
        prompt_version: Optional[str] = None,
        rate_limiter: Optional[RateLimiter] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        tracer: Any = None,
    ):
        """
        Initialize Gemini classifier.

        Args:
            model: Gemini model (gemini-2.0-flash recommended)
            api_key: Google API key (defaults to GOOGLE_API_KEY or GEMINI_API_KEY)
            temperature: Sampling temperature (lower = more deterministic)
            max_tokens: Max response tokens
            cot_enabled: Enable chain-of-thought reasoning (default: from ENABLE_COT_REASONING env)
            system_prompt: Override for classifier system prompt
            prompt_version: Override for classifier prompt version
            rate_limiter: Rate limiter (defaults to 15 RPM, 1500 RPD)
            circuit_breaker: Circuit breaker (defaults to 5 failures, 600s timeout)
        """
        self.model_name = model
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = (
            system_prompt if system_prompt is not None else CLASSIFIER_SYSTEM_PROMPT
        )
        self.prompt_version = (
            prompt_version if prompt_version is not None else CLASSIFIER_PROMPT_VERSION
        )
        self._client = None
        self._tracer = tracer or create_thesis_tracer()
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
        include_raw = should_include_raw_traces()
        title = signal_data.get("title", "N/A")
        url = signal_data.get("url", "N/A")
        source = signal_data.get("source_api", "unknown")
        context = signal_data.get("source_context", "")
        context_truncated = bool(context and len(context) > 500)
        trace_span = self._tracer.start_span(
            "thesis.llm.classify",
            component="thesis_filter_llm_classifier",
            model=self.model_name,
            prompt_version=self.prompt_version,
            cot_enabled=self.cot_enabled,
            source_api=source,
            signal_has_url=bool(url and url != "N/A"),
            context_truncated=context_truncated,
            title_summary=summarize_text_payload(title, include_raw=include_raw),
            context_summary=summarize_text_payload(context, include_raw=include_raw),
        )
        classify_start = time.perf_counter()

        # Phase 9: Check circuit breaker
        if self._circuit_breaker.state == "open":
            logger.warning("LLM circuit breaker OPEN, skipping classification")
            result = ThesisClassification(
                thesis_match=False,
                thesis_fit_score=0.0,
                category="excluded",
                stage_estimate="unknown",
                confidence="low",
                company_name=None,
                rationale="Circuit breaker OPEN (Gemini unavailable)",
                key_signals=[],
                prompt_version=self.prompt_version,
                model=self.model_name,
                classification_status=ClassificationStatus.ERROR_CIRCUIT_BREAKER.value,
            )
            self._trace_result(
                trace_span,
                result,
                error_kind="circuit_breaker",
                error_message=result.rationale,
                classify_latency_ms=(time.perf_counter() - classify_start) * 1000,
            )
            return result

        # Phase 9: Rate limiting
        try:
            await self._rate_limiter.acquire()
        except RuntimeError as e:
            logger.error(f"Rate limit exceeded: {e}")
            result = ThesisClassification(
                thesis_match=False,
                thesis_fit_score=0.0,
                category="excluded",
                stage_estimate="unknown",
                confidence="low",
                company_name=None,
                rationale=f"Rate limit exceeded: {str(e)}",
                key_signals=[],
                prompt_version=self.prompt_version,
                model=self.model_name,
                classification_status=ClassificationStatus.ERROR_RATE_LIMIT.value,
            )
            self._trace_result(
                trace_span,
                result,
                error_kind="rate_limit",
                error_message=str(e),
                classify_latency_ms=(time.perf_counter() - classify_start) * 1000,
            )
            return result

        # Build user prompt
        # Truncate context to avoid excessive tokens
        if context_truncated:
            context = context[:500] + "..."

        # Build prompt with optional chain-of-thought
        if self.cot_enabled:
            user_prompt = f"""{self.system_prompt}

{CHAIN_OF_THOUGHT_PROMPT}

Evaluate this signal:

Title: {title}
URL: {url}
Source: {source}
Context: {context if context else 'N/A'}

First provide your step-by-step reasoning, then output your JSON classification."""
        else:
            user_prompt = f"""{self.system_prompt}

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
            result = ThesisClassification(
                thesis_match=False,
                thesis_fit_score=0.0,
                category="excluded",
                stage_estimate="unknown",
                confidence="low",
                company_name=None,
                rationale=f"Circuit breaker OPEN: {str(e)}",
                key_signals=[],
                prompt_version=self.prompt_version,
                model=self.model_name,
                classification_status=ClassificationStatus.ERROR_CIRCUIT_BREAKER.value,
            )
            self._trace_result(
                trace_span,
                result,
                error_kind="circuit_breaker",
                error_message=str(e),
                classify_latency_ms=(time.perf_counter() - classify_start) * 1000,
            )
            return result
        except Exception as e:
            error_type = _exception_type(e)
            is_rate_limited = _is_rate_limit_exception(e)
            logger.error(
                "Gemini API error: error_type=%s error_summary=%s",
                error_type,
                summarize_text_payload(str(e)),
            )
            result = ThesisClassification(
                thesis_match=False,
                thesis_fit_score=0.0,
                category="excluded",
                stage_estimate="unknown",
                confidence="low",
                company_name=None,
                rationale=(
                    f"Rate limit exceeded: {error_type}"
                    if is_rate_limited
                    else f"Classification failed: {error_type}"
                ),
                key_signals=[],
                prompt_version=self.prompt_version,
                model=self.model_name,
                classification_status=(
                    ClassificationStatus.ERROR_RATE_LIMIT.value
                    if is_rate_limited
                    else ClassificationStatus.ERROR_API.value
                ),
            )
            self._trace_result(
                trace_span,
                result,
                error_kind="rate_limit" if is_rate_limited else "api",
                error_message=error_type,
                classify_latency_ms=(time.perf_counter() - classify_start) * 1000,
            )
            return result

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
            if isinstance(result, list):
                dict_items = [item for item in result if isinstance(item, dict)]
                if not dict_items:
                    raise ValueError("Parsed JSON list did not contain an object")
                if len(dict_items) > 1:
                    logger.warning(
                        "Gemini response returned a list with %d objects; using first",
                        len(dict_items),
                    )
                result = dict_items[0]
            if not isinstance(result, dict):
                raise ValueError(f"Expected JSON object, got {type(result).__name__}")
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            error_type = _exception_type(e)
            logger.error(
                "Failed to parse Gemini response: error_type=%s response_summary=%s",
                error_type,
                summarize_text_payload(response_text),
            )
            result = ThesisClassification(
                thesis_match=False,
                thesis_fit_score=0.0,
                category="excluded",
                stage_estimate="unknown",
                confidence="low",
                company_name=None,
                rationale=f"Failed to parse response: {error_type}",
                key_signals=[],
                prompt_version=self.prompt_version,
                model=self.model_name,
                classification_status=ClassificationStatus.ERROR_PARSE.value,
            )
            self._tracer.annotate(
                trace_span,
                response_summary=summarize_text_payload(response_text, include_raw=include_raw),
            )
            self._trace_result(
                trace_span,
                result,
                error_kind="parse",
                error_message=error_type,
                classify_latency_ms=(time.perf_counter() - classify_start) * 1000,
            )
            return result

        # Extract usage info if available
        input_tokens = None
        output_tokens = None
        try:
            if hasattr(response, 'usage_metadata'):
                input_tokens = response.usage_metadata.prompt_token_count
                output_tokens = response.usage_metadata.candidates_token_count
        except Exception:
            pass

        try:
            validated_response = _ThesisClassifierResponse.model_validate(result)
        except ValidationError as exc:
            error_type = _exception_type(exc)
            logger.error(
                "Failed to validate Gemini response: error_type=%s response_summary=%s",
                error_type,
                summarize_text_payload(response_text),
            )
            result = ThesisClassification(
                thesis_match=False,
                thesis_fit_score=0.0,
                category="excluded",
                stage_estimate="unknown",
                confidence="low",
                company_name=None,
                rationale=f"Failed to validate response: {error_type}",
                key_signals=[],
                prompt_version=self.prompt_version,
                model=self.model_name,
                classification_status=ClassificationStatus.ERROR_PARSE.value,
            )
            self._tracer.annotate(
                trace_span,
                response_summary=summarize_text_payload(response_text, include_raw=include_raw),
            )
            self._trace_result(
                trace_span,
                result,
                error_kind="parse",
                error_message=error_type,
                classify_latency_ms=(time.perf_counter() - classify_start) * 1000,
            )
            return result
        raw_response_payload = validated_response.model_dump()

        # Build reasoning trace if CoT enabled
        reasoning_trace = None
        if self.cot_enabled:
            reasoning_trace = {
                "cot_enabled": True,
                "raw_response_text": (
                    response_text[:2000]
                    if include_raw and response_text
                    else None
                ),
                "reasoning_steps": raw_response_payload.get("reasoning_steps", []),
            }

        result = ThesisClassification(
            thesis_match=validated_response.thesis_match,
            thesis_fit_score=validated_response.thesis_fit_score,
            category=validated_response.category,
            stage_estimate=validated_response.stage_estimate,
            confidence=validated_response.confidence,
            company_name=validated_response.company_name,
            rationale=validated_response.rationale,
            key_signals=validated_response.key_signals,
            prompt_version=self.prompt_version,
            model=self.model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            raw_response=raw_response_payload,
            cot_enabled=self.cot_enabled,
            reasoning_trace=reasoning_trace,
            classification_status=ClassificationStatus.SUCCESS.value,
            primary_end_user=_normalize_choice(
                validated_response.primary_end_user,
                VALID_PRIMARY_END_USERS,
            ),
            paying_customer=_normalize_choice(
                validated_response.paying_customer,
                VALID_PAYING_CUSTOMERS,
            ),
            sells_to_or_operates_in=_normalize_choice(
                validated_response.sells_to_or_operates_in,
                VALID_SELLS_TO_OR_OPERATES_IN,
            ),
        )
        self._trace_result(
            trace_span,
            result,
            classify_latency_ms=(time.perf_counter() - classify_start) * 1000,
        )
        return result

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
        async def _call_once():
            try:
                structured_response = self._call_gemini_api_with_instructor(user_prompt)
                if structured_response is not None:
                    return structured_response

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
            except Exception as exc:
                if _is_rate_limit_exception(exc):
                    raise _as_rate_limit_error(exc) from exc
                raise

        return await with_retry(
            _call_once,
            GEMINI_RATE_LIMIT_RETRY_CONFIG,
            retry_on=(RateLimitError,),
        )

    def _call_gemini_api_with_instructor(self, user_prompt: str) -> Any | None:
        """Best-effort Instructor path that falls back cleanly to the legacy Gemini call."""
        try:
            import instructor
            from google.genai import types
        except ImportError:
            return None

        try:
            wrapped_client = instructor.from_genai(
                self.client,
                mode=instructor.Mode.GENAI_STRUCTURED_OUTPUTS,
            )
            response_model, completion = wrapped_client.create_with_completion(
                messages=[{"role": "user", "content": user_prompt}],
                response_model=_ThesisClassifierResponse,
                model=self.model_name,
                config=types.GenerateContentConfig(
                    temperature=self.temperature,
                    max_output_tokens=self.max_tokens,
                    response_mime_type="application/json",
                ),
            )
            return SimpleNamespace(
                text=json.dumps(response_model.model_dump()),
                usage_metadata=getattr(completion, "usage_metadata", None),
            )
        except Exception as exc:
            if _is_rate_limit_exception(exc):
                raise
            logger.warning(
                "Instructor structured call failed, falling back to legacy Gemini parsing: "
                "error_type=%s error_summary=%s",
                _exception_type(exc),
                summarize_text_payload(str(exc)),
            )
            return None

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

    def _trace_result(
        self,
        span: Any,
        result: ThesisClassification,
        *,
        error_kind: Optional[str] = None,
        error_message: Optional[str] = None,
        classify_latency_ms: Optional[float] = None,
    ) -> None:
        """Finalize tracing for a classifier result without changing business behavior."""
        if error_kind:
            self._tracer.record_error(
                span,
                error_kind=error_kind,
                message=redact_error_message(error_message or result.rationale),
            )
        self._tracer.finish(
            span,
            backend=getattr(self._tracer, "backend", "noop"),
            classification_status=result.classification_status,
            category=result.category,
            thesis_fit_score=result.thesis_fit_score,
            confidence=result.confidence,
            primary_end_user=result.primary_end_user,
            paying_customer=result.paying_customer,
            sells_to_or_operates_in=result.sells_to_or_operates_in,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            classify_latency_ms=round(classify_latency_ms, 2) if classify_latency_ms is not None else None,
            reasoning_trace_present=bool(result.reasoning_trace),
        )


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

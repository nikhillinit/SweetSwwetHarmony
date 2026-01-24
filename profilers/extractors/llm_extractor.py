"""
LLM-based Profile Extractor using Google Gemini.

Extracts structured company information from website content:
- problem_solved: What problem does the company solve?
- target_customer: Who is the primary customer?
- business_model: How does the company make money?
- pricing_model: What is the pricing structure?
- category_hints: Multi-label categorization

Uses Gemini 2.0 Flash (free tier: 1.5M tokens/day).

Usage:
    extractor = ProfileLLMExtractor()
    result = await extractor.extract(
        combined_text="Homepage content... About page...",
        source_url="https://acme.ai"
    )
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# PROMPT CONFIGURATION
# =============================================================================

EXTRACTOR_PROMPT_VERSION = "v1.0.0-gemini"

EXTRACTOR_SYSTEM_PROMPT = """You are an expert business analyst extracting structured information from company websites.

Your task: Extract key business facts from website content with evidence.

## Required Fields

For each field, provide:
1. value: The extracted information (full description)
2. short_phrase: A brief 3-5 word noun phrase summary
3. confidence: 0.0-1.0 based on clarity and evidence quality
4. evidence: The EXACT quote from the text that supports this (or null if inferred)

## Fields to Extract

1. **problem_solved**: What specific problem does this company solve?
   - Look for: "We help...", "Our mission...", value propositions, pain points addressed
   - Short phrase should be a noun phrase (e.g., "subscription churn reduction")

2. **target_customer**: Who is the primary customer?
   - Look for: Customer testimonials, "Built for...", pricing tiers, industry focus
   - Use job-to-be-done framing (e.g., "E-commerce brands seeking retention")

3. **business_model**: How does the company make money?
   - Values: B2C_subscription, B2C_marketplace, B2B_SaaS, B2B_usage, D2C_ecommerce, freemium, advertising, licensing, unknown
   - Only use "unknown" if truly unclear

4. **pricing_model**: What is the pricing structure?
   - Values: free, freemium, subscription, usage_based, per_seat, contact_sales, unknown
   - Look for /pricing page content, tier names, price points

5. **company_name**: The company's official name
   - Extract from title, logo text, or explicit mentions

6. **category_hints**: Multi-label categorization (array)
   - Options: Consumer CPG, Consumer Health Tech, Travel & Hospitality, Consumer Marketplace, B2B SaaS, Developer Tools, Fintech, Other
   - Can select multiple

## Output Format
Respond ONLY with valid JSON (no markdown, no code blocks):
{
    "problem_solved": {
        "value": "Helps meal kit companies reduce customer churn through personalized recommendations",
        "short_phrase": "subscription churn reduction",
        "confidence": 0.85,
        "evidence": "We reduce churn by 40% for meal kit brands..."
    },
    "target_customer": {
        "value": "D2C subscription brands in food and beverage",
        "short_phrase": "D2C food subscription brands",
        "confidence": 0.9,
        "evidence": "Built for meal kit and grocery delivery companies..."
    },
    "business_model": {
        "value": "B2B_SaaS",
        "short_phrase": "B2B SaaS",
        "confidence": 0.8,
        "evidence": null
    },
    "pricing_model": {
        "value": "usage_based",
        "short_phrase": "usage-based pricing",
        "confidence": 0.7,
        "evidence": "Pricing based on monthly active subscribers..."
    },
    "company_name": {
        "value": "ChurnGuard",
        "short_phrase": "ChurnGuard",
        "confidence": 0.95,
        "evidence": null
    },
    "category_hints": ["Consumer CPG", "B2B SaaS"]
}

## Confidence Scoring
- 0.9-1.0: Explicit statement found with direct quote
- 0.7-0.89: Strong inference from clear context
- 0.5-0.69: Moderate inference, some ambiguity
- 0.3-0.49: Weak inference, limited evidence
- 0.0-0.29: Best guess, very little evidence

If a field cannot be determined at all, set its value to null (not the whole object, just the value field)."""


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ExtractedFieldResult:
    """Raw extraction result for a single field from LLM."""
    value: Optional[str]
    short_phrase: Optional[str]
    confidence: float
    evidence: Optional[str]


@dataclass
class LLMExtractionResult:
    """Complete LLM extraction result."""
    problem_solved: Optional[ExtractedFieldResult] = None
    target_customer: Optional[ExtractedFieldResult] = None
    business_model: Optional[ExtractedFieldResult] = None
    pricing_model: Optional[ExtractedFieldResult] = None
    company_name: Optional[ExtractedFieldResult] = None
    category_hints: List[str] = field(default_factory=list)

    # Metadata
    prompt_version: str = EXTRACTOR_PROMPT_VERSION
    model: str = "gemini-2.0-flash"
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: int = 0
    raw_response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# =============================================================================
# LLM EXTRACTOR
# =============================================================================

class ProfileLLMExtractor:
    """
    LLM-based profile extractor using Google Gemini (AI Studio free tier).

    FREE: 1.5M tokens/day = ~2,500+ profiles/day at no cost.

    Usage:
        extractor = ProfileLLMExtractor()
        result = await extractor.extract(
            combined_text="Homepage content here...",
            source_url="https://acme.ai"
        )

    Environment:
        GOOGLE_API_KEY or GEMINI_API_KEY - Get from https://aistudio.google.com/apikey
    """

    def __init__(
        self,
        model: str = "gemini-2.0-flash",
        api_key: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 800,
        max_input_chars: int = 15000,
    ):
        """
        Initialize Gemini extractor.

        Args:
            model: Gemini model (gemini-2.0-flash recommended)
            api_key: Google API key (defaults to GOOGLE_API_KEY or GEMINI_API_KEY)
            temperature: Sampling temperature (lower = more deterministic)
            max_tokens: Max response tokens
            max_input_chars: Max characters to send to LLM
        """
        self.model_name = model
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_input_chars = max_input_chars
        self._client = None

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
        combined_text: str,
        source_url: str,
    ):
        """
        Extract structured profile from website text.

        Args:
            combined_text: Combined text from all fetched pages
            source_url: Primary source URL (for evidence linking)

        Returns:
            ProfileExtractionResult (from url_profiler module)
        """
        from profilers.url_profiler import ProfileExtractionResult, ExtractedField

        # Truncate input if needed
        if len(combined_text) > self.max_input_chars:
            combined_text = combined_text[:self.max_input_chars] + "\n\n[Content truncated]"

        # Build prompt
        user_prompt = f"""{EXTRACTOR_SYSTEM_PROMPT}

## Website Content to Analyze

{combined_text}

## Instructions
Extract the structured information. Respond with JSON only."""

        # Call Gemini API
        start_time = time.time()
        llm_result = await self._call_gemini(user_prompt)
        latency_ms = int((time.time() - start_time) * 1000)

        if llm_result.error:
            logger.error(f"LLM extraction failed: {llm_result.error}")
            return ProfileExtractionResult(
                extraction_method="llm_error",
                extraction_time_ms=latency_ms,
            )

        # Convert LLMExtractionResult to ProfileExtractionResult
        return self._to_profile_result(llm_result, source_url, latency_ms)

    async def _call_gemini(self, prompt: str) -> LLMExtractionResult:
        """
        Call Gemini API and parse response.

        Args:
            prompt: Full prompt with system + user content

        Returns:
            LLMExtractionResult
        """
        try:
            from google.genai import types

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=self.temperature,
                    max_output_tokens=self.max_tokens,
                    response_mime_type="application/json",
                ),
            )
            response_text = response.text

        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            return LLMExtractionResult(error=str(e))

        # Parse response
        try:
            # Strip markdown code blocks if present
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)

            result = json.loads(cleaned)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini response: {e}\nResponse: {response_text[:200]}")
            return LLMExtractionResult(error=f"JSON parse error: {str(e)}")

        # Extract token usage if available
        input_tokens = None
        output_tokens = None
        try:
            if hasattr(response, 'usage_metadata'):
                input_tokens = response.usage_metadata.prompt_token_count
                output_tokens = response.usage_metadata.candidates_token_count
        except Exception:
            pass

        # Parse individual fields
        return LLMExtractionResult(
            problem_solved=self._parse_field(result.get("problem_solved")),
            target_customer=self._parse_field(result.get("target_customer")),
            business_model=self._parse_field(result.get("business_model")),
            pricing_model=self._parse_field(result.get("pricing_model")),
            company_name=self._parse_field(result.get("company_name")),
            category_hints=result.get("category_hints", []),
            model=self.model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_response=result,
        )

    def _parse_field(self, field_data: Optional[Dict[str, Any]]) -> Optional[ExtractedFieldResult]:
        """Parse a single field from LLM response."""
        if not field_data or not isinstance(field_data, dict):
            return None

        value = field_data.get("value")
        if value is None:
            return None

        return ExtractedFieldResult(
            value=str(value),
            short_phrase=field_data.get("short_phrase", str(value)[:50]),
            confidence=float(field_data.get("confidence", 0.5)),
            evidence=field_data.get("evidence"),
        )

    def _to_profile_result(
        self,
        llm_result: LLMExtractionResult,
        source_url: str,
        latency_ms: int,
    ):
        """
        Convert LLMExtractionResult to ProfileExtractionResult.

        Args:
            llm_result: Raw LLM extraction
            source_url: Source URL for evidence linking
            latency_ms: Extraction latency

        Returns:
            ProfileExtractionResult
        """
        from profilers.url_profiler import ProfileExtractionResult, ExtractedField

        def to_extracted_field(
            field_result: Optional[ExtractedFieldResult],
        ) -> Optional[ExtractedField]:
            if not field_result or not field_result.value:
                return None

            return ExtractedField(
                value=field_result.value,
                short_phrase=field_result.short_phrase or field_result.value[:50],
                confidence=field_result.confidence,
                evidence_snippet=field_result.evidence,
                source_url=source_url,
                extraction_method="llm",
            )

        return ProfileExtractionResult(
            problem_solved=to_extracted_field(llm_result.problem_solved),
            target_customer=to_extracted_field(llm_result.target_customer),
            business_model=to_extracted_field(llm_result.business_model),
            pricing_model=to_extracted_field(llm_result.pricing_model),
            company_name=to_extracted_field(llm_result.company_name),
            category_hints=llm_result.category_hints or [],
            extraction_method="llm",
            extraction_time_ms=latency_ms,
            input_tokens=llm_result.input_tokens,
            output_tokens=llm_result.output_tokens,
        )


# =============================================================================
# SYNC WRAPPER
# =============================================================================

def extract_profile_sync(
    combined_text: str,
    source_url: str,
    api_key: Optional[str] = None,
):
    """
    Synchronous wrapper for profile extraction.

    Args:
        combined_text: Website text content
        source_url: Source URL
        api_key: Optional API key override

    Returns:
        ProfileExtractionResult
    """
    import asyncio

    extractor = ProfileLLMExtractor(api_key=api_key)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, extractor.extract(combined_text, source_url))
            return future.result()
    else:
        return asyncio.run(extractor.extract(combined_text, source_url))

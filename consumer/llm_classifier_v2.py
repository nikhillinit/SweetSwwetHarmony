"""
LLMClassifierV2: Strict-contract LLM classifier for change classification.

This module classifies the NATURE of changes between snapshots:
- pivot: Fundamental business model change (B2C->B2B, consumer->enterprise)
- expansion: Adding new product line or market segment
- rebrand: Name/identity change without business model shift
- minor: Cosmetic changes, typo fixes, small updates
- needs_review: Unclear, requires human review

Features:
- Strict JSON output contract with schema versioning
- Input hashing for deterministic caching
- Confidence threshold override (low confidence -> needs_review)
- Cache persistence for cost savings

This is Stage 2 of two-stage signal gating (after TriggerGate).
"""
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from consumer._gemini_instructor import load_instructor_genai
from pydantic import BaseModel, ConfigDict, field_validator
from telemetry.thesis_tracing import create_thesis_tracer, summarize_text_payload

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
    tracer: Any = None


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


class _LLMClassifierV2Response(BaseModel):
    """Internal schema for provider responses before dataclass adaptation."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    schema_version: str = SCHEMA_VERSION
    label: str = ClassificationLabel.NEEDS_REVIEW.value
    confidence: float = 0.0
    rationale: str = ""

    @field_validator("schema_version", "label", "rationale", mode="before")
    @classmethod
    def _coerce_string(cls, value: Any, info) -> str:
        if not isinstance(value, str):
            if info.field_name == "schema_version":
                return SCHEMA_VERSION
            if info.field_name == "label":
                return ClassificationLabel.NEEDS_REVIEW.value
            return ""
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, confidence))


def _exception_type(exc: Exception) -> str:
    """Return a redaction-safe error label for logs and fallback messages."""

    return type(exc).__name__


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
- pivot: Fundamental business model change (B2C->B2B, consumer->enterprise, completely different market)
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
        self._tracer = self.config.tracer or create_thesis_tracer()

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
            except ImportError as exc:
                raise ImportError(
                    "google-genai package required: pip install google-genai"
                ) from exc
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

        input_hash = self._compute_hash(old_description, new_description)
        trace_span = self._tracer.start_span(
            "thesis.llm.classify",
            component="llm_classifier_v2",
            model=self.config.model,
            cache_enabled=self.config.cache_enabled,
            input_hash=input_hash,
        )
        trace_start = time.perf_counter()

        try:
            if self.config.cache_enabled and input_hash in self._cache:
                cached = self._cache[input_hash]
                result = ClassificationResult(
                    schema_version=cached.schema_version,
                    label=cached.label,
                    confidence=cached.confidence,
                    rationale=cached.rationale,
                    input_hash=input_hash,
                    cached=True,
                )
                self._tracer.finish(
                    trace_span,
                    cache_hit=True,
                    cached=True,
                    label=result.label.value,
                    confidence=result.confidence,
                    latency_ms=round((time.perf_counter() - trace_start) * 1000, 2),
                )
                return result

            response = await self._call_llm(old_description, new_description)

            result = self._parse_response(response, input_hash)
            error_kind = self._get_trace_error_kind(response)
            if error_kind:
                self._tracer.record_error(
                    trace_span,
                    error_kind=error_kind,
                    message=response.get("rationale", ""),
                )

            confidence_override = False
            if result.confidence < self.config.min_confidence:
                confidence_override = True
                result = ClassificationResult(
                    schema_version=result.schema_version,
                    label=ClassificationLabel.NEEDS_REVIEW,
                    confidence=result.confidence,
                    rationale=(
                        f"Low confidence ({result.confidence:.2f}): {result.rationale}"
                    ),
                    input_hash=input_hash,
                    raw_response=response,
                )

            if self.config.cache_enabled:
                self._cache[input_hash] = result

            self._tracer.finish(
                trace_span,
                cache_hit=False,
                cached=result.cached,
                label=result.label.value,
                confidence=result.confidence,
                fallback_used=bool(error_kind),
                confidence_override=confidence_override,
                latency_ms=round((time.perf_counter() - trace_start) * 1000, 2),
            )
            return result
        except Exception as exc:
            self._tracer.record_error(
                trace_span,
                error_kind="unexpected",
                message=_exception_type(exc),
            )
            self._tracer.finish(
                trace_span,
                cache_hit=False,
                latency_ms=round((time.perf_counter() - trace_start) * 1000, 2),
            )
            raise

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

        structured_response = self._call_llm_with_instructor(prompt)
        if structured_response is not None:
            return structured_response

        return self._call_llm_with_manual_json(prompt)

    def _call_llm_with_instructor(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Try Instructor-backed structured output before falling back to manual JSON parsing."""

        deps = load_instructor_genai()
        if deps is None:
            return None

        try:
            wrapped_client = deps.instructor.from_genai(
                self.client,
                mode=deps.instructor.Mode.GENAI_STRUCTURED_OUTPUTS,
            )
            response_model, _completion = wrapped_client.create_with_completion(
                messages=[{"role": "user", "content": prompt}],
                response_model=_LLMClassifierV2Response,
                model=self.config.model,
                config=deps.types.GenerateContentConfig(
                    temperature=self.config.temperature,
                    max_output_tokens=self.config.max_tokens,
                    response_mime_type="application/json",
                ),
            )
            return response_model.model_dump()
        except Exception as exc:
            logger.warning(
                "Instructor structured call failed, falling back to manual JSON parsing: "
                "error_type=%s error_summary=%s",
                _exception_type(exc),
                summarize_text_payload(str(exc)),
            )
            return None

    def _call_llm_with_manual_json(self, prompt: str) -> Dict[str, Any]:
        """Preserve the legacy Gemini JSON path as a safe fallback."""

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
        except Exception as exc:
            error_type = _exception_type(exc)
            logger.error(
                "Gemini API error: error_type=%s error_summary=%s",
                error_type,
                summarize_text_payload(str(exc)),
            )
            return {
                "schema_version": SCHEMA_VERSION,
                "label": "needs_review",
                "confidence": 0.0,
                "rationale": f"API error: {error_type}",
            }

        try:
            text = response_text
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            return json.loads(text)
        except json.JSONDecodeError as exc:
            error_type = _exception_type(exc)
            logger.error(
                "Failed to parse response: error_type=%s response_summary=%s",
                error_type,
                summarize_text_payload(response_text),
            )
            return {
                "schema_version": SCHEMA_VERSION,
                "label": "needs_review",
                "confidence": 0.0,
                "rationale": f"Parse error: {error_type}",
            }

    def _parse_response(
        self,
        response: Dict[str, Any],
        input_hash: str,
    ) -> ClassificationResult:
        """Parse and validate LLM response."""

        validated = _LLMClassifierV2Response.model_validate(
            response if isinstance(response, dict) else {}
        )
        try:
            label = ClassificationLabel(validated.label)
        except ValueError:
            label = ClassificationLabel.NEEDS_REVIEW

        return ClassificationResult(
            schema_version=validated.schema_version,
            label=label,
            confidence=validated.confidence,
            rationale=validated.rationale,
            input_hash=input_hash,
            raw_response=validated.model_dump(),
        )

    def _compute_hash(self, old: str, new: str) -> str:
        """Compute deterministic hash for input pair."""

        content = f"{old}|||{new}"
        return f"sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"

    def _get_trace_error_kind(self, response: Dict[str, Any]) -> Optional[str]:
        """Classify fallback response shapes for tracing."""

        rationale = response.get("rationale", "")
        if not isinstance(rationale, str):
            return None
        lowered = rationale.lower()
        if lowered.startswith("api error:"):
            return "api"
        if lowered.startswith("parse error:"):
            return "parse"
        return None

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

        with open(path, "w", encoding="utf-8") as file:
            json.dump(cache_data, file, indent=2)

        logger.info("Saved %s cache entries to %s", len(cache_data), path)

    def load_cache(self, path: str) -> None:
        """
        Load cache from JSON file.

        Args:
            path: File path to load cache from
        """

        try:
            with open(path, "r", encoding="utf-8") as file:
                cache_data = json.load(file)

            for hash_key, data in cache_data.items():
                self._cache[hash_key] = ClassificationResult(
                    schema_version=data["schema_version"],
                    label=ClassificationLabel(data["label"]),
                    confidence=data["confidence"],
                    rationale=data["rationale"],
                    input_hash=data["input_hash"],
                    cached=True,
                )

            logger.info("Loaded %s cache entries from %s", len(cache_data), path)
        except FileNotFoundError:
            logger.debug("No cache file at %s", path)
        except Exception as exc:
            logger.warning("Failed to load cache from %s: %s", path, exc)

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

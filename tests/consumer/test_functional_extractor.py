"""Tests for FunctionalExtractor.

Verifies:
- Successful extraction with mock LLM response
- Graceful fallback when LLM unavailable
- Malformed response handling
- Confidence score bounds [0.0, 1.0]
- Advisory flag based on confidence threshold
- Prompt version tracked in output
- Invalid archetype defaults to 'unknown'
- Invalid problem_archetypes filtered out
"""

import os
import sys
import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from consumer.functional_extractor import (
    FunctionalExtractor,
    FunctionalSchema,
    EXTRACTOR_PROMPT_VERSION,
    VALID_ARCHETYPES,
    VALID_PROBLEM_ARCHETYPES,
)
from utils.circuit_breaker import CircuitBreaker


def _make_mock_response(result_dict: dict) -> MagicMock:
    """Create a mock Gemini API response."""
    mock = MagicMock()
    mock.text = json.dumps(result_dict)
    return mock


def _make_extractor(**kwargs) -> FunctionalExtractor:
    """Create extractor with test defaults (no real API key needed)."""
    defaults = {
        "api_key": "test-key",
        "confidence_threshold": 0.6,
    }
    defaults.update(kwargs)
    return FunctionalExtractor(**defaults)


SAMPLE_SIGNAL = {
    "title": "Acme Meal Kits",
    "source_api": "sec_edgar",
    "source_context": "Acme Inc filed Form D for a healthy meal kit delivery subscription service targeting busy families.",
}

SAMPLE_LLM_RESPONSE = {
    "problem_solved": "Busy families struggle to find healthy meal options",
    "customer": "Health-conscious parents with young children",
    "approach": "AI-powered meal planning with weekly kit delivery",
    "customer_archetype": "parents",
    "problem_archetypes": ["meal_delivery", "subscription"],
    "schema_confidence": 0.85,
}


class TestSuccessfulExtraction:
    """Tests for successful LLM extraction."""

    @pytest.mark.asyncio
    async def test_full_extraction(self):
        """Full extraction returns all fields."""
        extractor = _make_extractor()
        mock_response = _make_mock_response(SAMPLE_LLM_RESPONSE)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(
                SAMPLE_SIGNAL, company_id="comp-001", evidence_signal_ids=[1, 2]
            )

        assert schema is not None
        assert schema.company_id == "comp-001"
        assert schema.problem_solved_text == "Busy families struggle to find healthy meal options"
        assert schema.customer_text == "Health-conscious parents with young children"
        assert schema.approach_text == "AI-powered meal planning with weekly kit delivery"
        assert schema.customer_archetype == "parents"
        assert schema.problem_archetypes == ["meal_delivery", "subscription"]
        assert schema.schema_confidence == 0.85
        assert schema.is_advisory is False
        assert schema.evidence_signal_ids == [1, 2]
        assert schema.extraction_model == "gemini-2.0-flash"
        assert schema.extraction_prompt_version == EXTRACTOR_PROMPT_VERSION

    @pytest.mark.asyncio
    async def test_to_storage_dict(self):
        """to_storage_dict() produces correct format for SignalStore."""
        extractor = _make_extractor()
        mock_response = _make_mock_response(SAMPLE_LLM_RESPONSE)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-002")

        d = schema.to_storage_dict()
        assert d["company_id"] == "comp-002"
        assert d["customer_archetype"] == "parents"
        assert isinstance(d["problem_archetypes"], list)
        assert d["extraction_prompt_version"] == EXTRACTOR_PROMPT_VERSION

    @pytest.mark.asyncio
    async def test_prompt_version_tracked(self):
        """Extraction result includes prompt version."""
        extractor = _make_extractor()
        mock_response = _make_mock_response(SAMPLE_LLM_RESPONSE)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-003")

        assert schema.extraction_prompt_version == EXTRACTOR_PROMPT_VERSION
        assert "v1.0.0" in schema.extraction_prompt_version


class TestGracefulDegradation:
    """Tests for graceful fallback when LLM is unavailable."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_returns_none(self):
        """When circuit breaker is open, returns None."""
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=600)
        # Force circuit open
        cb._state = "open"

        extractor = _make_extractor(circuit_breaker=cb)
        schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-010")
        assert schema is None

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_returns_none(self):
        """When rate limit is exhausted, returns None."""
        mock_limiter = AsyncMock()
        mock_limiter.acquire = AsyncMock(side_effect=RuntimeError("Daily quota exhausted"))

        extractor = _make_extractor(rate_limiter=mock_limiter)
        schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-011")
        assert schema is None

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self):
        """When Gemini API raises, returns None."""
        extractor = _make_extractor()

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, side_effect=Exception("API down")):
            # Need to make circuit breaker pass through
            extractor._circuit_breaker._state = "closed"
            extractor._circuit_breaker._failure_count = 0
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-012")

        assert schema is None


class TestMalformedResponse:
    """Tests for malformed LLM response handling."""

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self):
        """Non-JSON response returns None."""
        extractor = _make_extractor()
        mock_response = MagicMock()
        mock_response.text = "This is not JSON at all"

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-020")

        assert schema is None

    @pytest.mark.asyncio
    async def test_markdown_wrapped_json_handled(self):
        """JSON wrapped in ```json code blocks should parse correctly."""
        extractor = _make_extractor()
        mock_response = MagicMock()
        mock_response.text = f"```json\n{json.dumps(SAMPLE_LLM_RESPONSE)}\n```"

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-021")

        assert schema is not None
        assert schema.customer_archetype == "parents"

    @pytest.mark.asyncio
    async def test_empty_response_returns_none(self):
        """Empty response text returns None."""
        extractor = _make_extractor()
        mock_response = MagicMock()
        mock_response.text = ""

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-022")

        assert schema is None

    @pytest.mark.asyncio
    async def test_missing_fields_handled(self):
        """Partial JSON (some fields missing) still produces a schema."""
        extractor = _make_extractor()
        mock_response = _make_mock_response({
            "customer_archetype": "foodies",
            "schema_confidence": 0.4,
        })

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-023")

        assert schema is not None
        assert schema.customer_archetype == "foodies"
        assert schema.problem_solved_text is None
        assert schema.customer_text is None
        assert schema.approach_text is None
        assert schema.problem_archetypes == []


class TestConfidenceBounds:
    """Tests for confidence score validation."""

    @pytest.mark.asyncio
    async def test_confidence_within_bounds(self):
        """Normal confidence value passes through."""
        extractor = _make_extractor()
        response = {**SAMPLE_LLM_RESPONSE, "schema_confidence": 0.72}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-030")

        assert schema.schema_confidence == 0.72

    @pytest.mark.asyncio
    async def test_confidence_clamped_above_one(self):
        """Confidence > 1.0 clamped to 1.0."""
        extractor = _make_extractor()
        response = {**SAMPLE_LLM_RESPONSE, "schema_confidence": 1.5}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-031")

        assert schema.schema_confidence == 1.0

    @pytest.mark.asyncio
    async def test_confidence_clamped_below_zero(self):
        """Confidence < 0.0 clamped to 0.0."""
        extractor = _make_extractor()
        response = {**SAMPLE_LLM_RESPONSE, "schema_confidence": -0.5}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-032")

        assert schema.schema_confidence == 0.0

    @pytest.mark.asyncio
    async def test_confidence_non_numeric_defaults_to_zero(self):
        """Non-numeric confidence defaults to 0.0."""
        extractor = _make_extractor()
        response = {**SAMPLE_LLM_RESPONSE, "schema_confidence": "high"}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-033")

        assert schema.schema_confidence == 0.0

    @pytest.mark.asyncio
    async def test_zero_confidence(self):
        """Confidence of exactly 0.0 is valid."""
        extractor = _make_extractor()
        response = {**SAMPLE_LLM_RESPONSE, "schema_confidence": 0.0}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-034")

        assert schema.schema_confidence == 0.0

    @pytest.mark.asyncio
    async def test_one_point_zero_confidence(self):
        """Confidence of exactly 1.0 is valid."""
        extractor = _make_extractor()
        response = {**SAMPLE_LLM_RESPONSE, "schema_confidence": 1.0}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-035")

        assert schema.schema_confidence == 1.0


class TestAdvisoryFlag:
    """Tests for is_advisory based on confidence threshold."""

    @pytest.mark.asyncio
    async def test_high_confidence_not_advisory(self):
        """Confidence >= threshold -> is_advisory = False."""
        extractor = _make_extractor(confidence_threshold=0.6)
        response = {**SAMPLE_LLM_RESPONSE, "schema_confidence": 0.85}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-040")

        assert schema.is_advisory is False

    @pytest.mark.asyncio
    async def test_low_confidence_is_advisory(self):
        """Confidence < threshold -> is_advisory = True."""
        extractor = _make_extractor(confidence_threshold=0.6)
        response = {**SAMPLE_LLM_RESPONSE, "schema_confidence": 0.3}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-041")

        assert schema.is_advisory is True

    @pytest.mark.asyncio
    async def test_exact_threshold_not_advisory(self):
        """Confidence == threshold -> is_advisory = False."""
        extractor = _make_extractor(confidence_threshold=0.6)
        response = {**SAMPLE_LLM_RESPONSE, "schema_confidence": 0.6}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-042")

        assert schema.is_advisory is False

    @pytest.mark.asyncio
    async def test_custom_threshold(self):
        """Custom threshold respected."""
        extractor = _make_extractor(confidence_threshold=0.8)
        response = {**SAMPLE_LLM_RESPONSE, "schema_confidence": 0.75}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-043")

        assert schema.is_advisory is True  # 0.75 < 0.8

    @pytest.mark.asyncio
    async def test_threshold_from_env_var(self, monkeypatch):
        """FUNCTIONAL_SCHEMA_CONFIDENCE_THRESHOLD env var configures threshold."""
        monkeypatch.setenv("FUNCTIONAL_SCHEMA_CONFIDENCE_THRESHOLD", "0.9")

        # Create extractor WITHOUT explicit threshold — should read env var
        extractor = FunctionalExtractor(api_key="test-key")
        assert extractor.confidence_threshold == 0.9

        response = {**SAMPLE_LLM_RESPONSE, "schema_confidence": 0.85}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-044")

        assert schema.is_advisory is True  # 0.85 < 0.9

    @pytest.mark.asyncio
    async def test_threshold_default_when_no_env_var(self, monkeypatch):
        """Default threshold is 0.6 when env var not set."""
        monkeypatch.delenv("FUNCTIONAL_SCHEMA_CONFIDENCE_THRESHOLD", raising=False)

        extractor = FunctionalExtractor(api_key="test-key")
        assert extractor.confidence_threshold == 0.6


class TestValidation:
    """Tests for archetype and problem validation."""

    @pytest.mark.asyncio
    async def test_invalid_archetype_defaults_to_unknown(self):
        """Invalid archetype string defaults to 'unknown'."""
        extractor = _make_extractor()
        response = {**SAMPLE_LLM_RESPONSE, "customer_archetype": "aliens"}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-050")

        assert schema.customer_archetype == "unknown"

    @pytest.mark.asyncio
    async def test_invalid_problem_archetypes_filtered(self):
        """Invalid problem archetypes are removed, valid ones kept."""
        extractor = _make_extractor()
        response = {**SAMPLE_LLM_RESPONSE, "problem_archetypes": ["meal_delivery", "alien_invasion", "wellness"]}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-051")

        assert schema.problem_archetypes == ["meal_delivery", "wellness"]

    @pytest.mark.asyncio
    async def test_problem_archetypes_not_list_defaults_empty(self):
        """Non-list problem_archetypes defaults to empty list."""
        extractor = _make_extractor()
        response = {**SAMPLE_LLM_RESPONSE, "problem_archetypes": "meal_delivery"}
        mock_response = _make_mock_response(response)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
            schema = await extractor.extract(SAMPLE_SIGNAL, company_id="comp-052")

        assert schema.problem_archetypes == []

    @pytest.mark.asyncio
    async def test_all_valid_archetypes_accepted(self):
        """Every archetype in VALID_ARCHETYPES is accepted."""
        for archetype in VALID_ARCHETYPES:
            extractor = _make_extractor()
            response = {**SAMPLE_LLM_RESPONSE, "customer_archetype": archetype}
            mock_response = _make_mock_response(response)

            with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response):
                schema = await extractor.extract(SAMPLE_SIGNAL, company_id=f"comp-val-{archetype}")

            assert schema.customer_archetype == archetype, f"Archetype {archetype} should be valid"


class TestContextTruncation:
    """Test that long context is truncated."""

    @pytest.mark.asyncio
    async def test_long_context_truncated(self):
        """Context longer than 500 chars should be truncated in prompt."""
        extractor = _make_extractor()
        long_signal = {
            "title": "Test Co",
            "source_api": "github",
            "source_context": "x" * 1000,
        }
        mock_response = _make_mock_response(SAMPLE_LLM_RESPONSE)

        with patch.object(extractor, '_call_gemini_api', new_callable=AsyncMock, return_value=mock_response) as mock_call:
            schema = await extractor.extract(long_signal, company_id="comp-060")

        assert schema is not None
        # Verify the prompt passed to API had truncated context
        call_args = mock_call.call_args[0][0]
        assert "x" * 500 in call_args
        assert "..." in call_args

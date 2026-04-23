"""Tests for LLMClassifierV2 with strict contract and caching."""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from consumer.llm_classifier_v2 import (
    LLMClassifierV2,
    ClassifierConfig,
    ClassificationLabel,
    ClassificationResult,
    SCHEMA_VERSION,
)
from telemetry.thesis_tracing import MemoryThesisTracer


class TestLLMClassifierV2:
    """Test suite for LLMClassifierV2."""

    @pytest.mark.asyncio
    async def test_classification_returns_valid_schema(self):
        """Classifier must return valid schema with all required fields."""

        config = ClassifierConfig(model="gemini-1.5-flash")
        classifier = LLMClassifierV2(config)

        mock_response = {
            "schema_version": "v1",
            "label": "pivot",
            "confidence": 0.85,
            "rationale": "Changed from B2C to B2B",
        }

        with patch.object(classifier, "_call_llm", return_value=mock_response):
            result = await classifier.classify(
                old_description="Consumer fitness app",
                new_description="Enterprise wellness platform",
            )

        assert result.schema_version == SCHEMA_VERSION
        assert result.label == ClassificationLabel.PIVOT
        assert 0 <= result.confidence <= 1
        assert result.rationale is not None
        assert result.input_hash is not None

    @pytest.mark.asyncio
    async def test_low_confidence_returns_needs_review(self):
        """Confidence < 0.7 should be labeled needs_review."""

        config = ClassifierConfig(min_confidence=0.7)
        classifier = LLMClassifierV2(config)

        mock_response = {
            "schema_version": "v1",
            "label": "pivot",
            "confidence": 0.55,
            "rationale": "Uncertain change",
        }

        with patch.object(classifier, "_call_llm", return_value=mock_response):
            result = await classifier.classify(
                old_description="App v1",
                new_description="App v2",
            )

        assert result.label == ClassificationLabel.NEEDS_REVIEW

    @pytest.mark.asyncio
    async def test_cache_prevents_duplicate_calls(self):
        """Same input should use cached result."""

        config = ClassifierConfig(cache_enabled=True)
        classifier = LLMClassifierV2(config)

        call_count = 0

        async def mock_llm(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "schema_version": "v1",
                "label": "minor",
                "confidence": 0.9,
                "rationale": "Test",
            }

        with patch.object(classifier, "_call_llm", side_effect=mock_llm):
            result1 = await classifier.classify("old", "new")
            result2 = await classifier.classify("old", "new")

        assert call_count == 1
        assert result1.cached is False
        assert result2.cached is True
        assert result1.input_hash == result2.input_hash

    @pytest.mark.asyncio
    async def test_different_inputs_not_cached(self):
        """Different inputs should make separate LLM calls."""

        config = ClassifierConfig(cache_enabled=True)
        classifier = LLMClassifierV2(config)

        call_count = 0

        async def mock_llm(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "schema_version": "v1",
                "label": "minor",
                "confidence": 0.9,
                "rationale": "Test",
            }

        with patch.object(classifier, "_call_llm", side_effect=mock_llm):
            await classifier.classify("old1", "new1")
            await classifier.classify("old2", "new2")

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_cache_can_be_persisted_and_loaded(self):
        """Cache should survive save/load cycle."""

        config = ClassifierConfig(cache_enabled=True)
        classifier = LLMClassifierV2(config)

        classifier._cache["test_hash"] = ClassificationResult(
            schema_version="v1",
            label=ClassificationLabel.PIVOT,
            confidence=0.9,
            rationale="Test",
            input_hash="test_hash",
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as file:
            cache_path = file.name

        try:
            classifier.save_cache(cache_path)

            new_classifier = LLMClassifierV2(config)
            new_classifier.load_cache(cache_path)

            assert "test_hash" in new_classifier._cache
            assert (
                new_classifier._cache["test_hash"].label
                == ClassificationLabel.PIVOT
            )
        finally:
            os.unlink(cache_path)

    @pytest.mark.asyncio
    async def test_all_labels_parsed_correctly(self):
        """All valid labels should be parsed correctly."""

        for label in ["pivot", "expansion", "rebrand", "minor", "needs_review"]:
            config = ClassifierConfig(cache_enabled=False)
            classifier = LLMClassifierV2(config)

            mock_response = {
                "schema_version": "v1",
                "label": label,
                "confidence": 0.9,
                "rationale": f"Test {label}",
            }

            with patch.object(classifier, "_call_llm", return_value=mock_response):
                result = await classifier.classify("old", "new")

            assert result.label == ClassificationLabel(label)

    @pytest.mark.asyncio
    async def test_invalid_fields_coerce_to_safe_defaults(self):
        """Schema validation should preserve the permissive fallback contract."""

        config = ClassifierConfig(cache_enabled=False)
        classifier = LLMClassifierV2(config)

        mock_response = {
            "schema_version": 17,
            "label": "mystery_label",
            "confidence": "1.7",
            "rationale": None,
        }

        with patch.object(classifier, "_call_llm", return_value=mock_response):
            result = await classifier.classify("old", "new")

        assert result.schema_version == SCHEMA_VERSION
        assert result.label == ClassificationLabel.NEEDS_REVIEW
        assert result.confidence == 1.0
        assert result.rationale == ""

    @pytest.mark.asyncio
    async def test_input_hash_is_deterministic(self):
        """Same inputs should always produce same hash."""

        config = ClassifierConfig()
        classifier = LLMClassifierV2(config)

        hash1 = classifier._compute_hash("old desc", "new desc")
        hash2 = classifier._compute_hash("old desc", "new desc")
        hash3 = classifier._compute_hash("different", "inputs")

        assert hash1 == hash2
        assert hash1 != hash3

    @pytest.mark.asyncio
    async def test_cache_disabled_makes_multiple_calls(self):
        """With cache disabled, same inputs should make multiple calls."""

        config = ClassifierConfig(cache_enabled=False)
        classifier = LLMClassifierV2(config)

        call_count = 0

        async def mock_llm(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "schema_version": "v1",
                "label": "minor",
                "confidence": 0.9,
                "rationale": "Test",
            }

        with patch.object(classifier, "_call_llm", side_effect=mock_llm):
            await classifier.classify("old", "new")
            await classifier.classify("old", "new")

        assert call_count == 2

    def test_compute_hash_includes_both_inputs(self):
        """Hash should change if either input changes."""

        config = ClassifierConfig()
        classifier = LLMClassifierV2(config)

        base = classifier._compute_hash("old", "new")
        changed_old = classifier._compute_hash("OLD", "new")
        changed_new = classifier._compute_hash("old", "NEW")

        assert base != changed_old
        assert base != changed_new
        assert changed_old != changed_new


class TestLLMClassifierV2Tracing:
    @pytest.mark.asyncio
    async def test_success_records_trace_span(self):
        tracer = MemoryThesisTracer()
        config = ClassifierConfig(tracer=tracer)
        classifier = LLMClassifierV2(config)

        mock_response = {
            "schema_version": "v1",
            "label": "pivot",
            "confidence": 0.85,
            "rationale": "Changed from B2C to B2B",
        }

        with patch.object(classifier, "_call_llm", return_value=mock_response):
            await classifier.classify(
                old_description="Consumer fitness app",
                new_description="Enterprise wellness platform",
            )

        assert len(tracer.finished_spans) == 1
        span = tracer.finished_spans[0]
        assert span.name == "thesis.llm.classify"
        assert span.attributes["component"] == "llm_classifier_v2"
        assert span.attributes["cache_hit"] is False
        assert span.attributes["label"] == "pivot"
        assert "latency_ms" in span.attributes
        assert span.errors == []

    @pytest.mark.asyncio
    async def test_cache_hit_records_trace_span(self):
        tracer = MemoryThesisTracer()
        config = ClassifierConfig(tracer=tracer, cache_enabled=True)
        classifier = LLMClassifierV2(config)

        mock_response = {
            "schema_version": "v1",
            "label": "minor",
            "confidence": 0.9,
            "rationale": "Test",
        }

        with patch.object(classifier, "_call_llm", return_value=mock_response):
            await classifier.classify("old", "new")
            await classifier.classify("old", "new")

        assert len(tracer.finished_spans) == 2
        assert tracer.finished_spans[0].attributes["cache_hit"] is False
        assert tracer.finished_spans[1].attributes["cache_hit"] is True
        assert tracer.finished_spans[1].attributes["cached"] is True

    @pytest.mark.asyncio
    async def test_api_fallback_records_error_kind(self):
        tracer = MemoryThesisTracer()
        config = ClassifierConfig(tracer=tracer)
        classifier = LLMClassifierV2(config)

        with patch.object(
            classifier,
            "_call_llm",
            return_value={
                "schema_version": "v1",
                "label": "needs_review",
                "confidence": 0.0,
                "rationale": "API error: boom",
            },
        ):
            result = await classifier.classify("old", "new")

        assert result.label == ClassificationLabel.NEEDS_REVIEW
        assert len(tracer.finished_spans) == 1
        span = tracer.finished_spans[0]
        assert span.attributes["fallback_used"] is True
        assert span.errors[0]["error_kind"] == "api"
        assert "old" not in str(span.errors[0])

    @pytest.mark.asyncio
    async def test_parse_fallback_records_error_kind(self):
        tracer = MemoryThesisTracer()
        config = ClassifierConfig(tracer=tracer)
        classifier = LLMClassifierV2(config)

        with patch.object(
            classifier,
            "_call_llm",
            return_value={
                "schema_version": "v1",
                "label": "needs_review",
                "confidence": 0.0,
                "rationale": "Parse error: invalid json",
            },
        ):
            result = await classifier.classify("old", "new")

        assert result.label == ClassificationLabel.NEEDS_REVIEW
        assert len(tracer.finished_spans) == 1
        span = tracer.finished_spans[0]
        assert span.attributes["fallback_used"] is True
        assert span.errors[0]["error_kind"] == "parse"


class TestLLMClassifierV2StructuredOutput:
    @pytest.mark.asyncio
    async def test_instructor_success_path_skips_manual_json_fallback(self):
        classifier = LLMClassifierV2(ClassifierConfig(cache_enabled=False))
        classifier._client = MagicMock()

        response_model = MagicMock()
        response_model.model_dump.return_value = {
            "schema_version": "v1",
            "label": "expansion",
            "confidence": 0.93,
            "rationale": "Expanded into a new adjacent market.",
        }

        wrapped_client = MagicMock()
        wrapped_client.create_with_completion.return_value = (
            response_model,
            MagicMock(),
        )
        instructor_module = MagicMock()
        instructor_module.from_genai.return_value = wrapped_client
        instructor_module.Mode = SimpleNamespace(
            GENAI_STRUCTURED_OUTPUTS="structured_outputs"
        )
        deps = MagicMock(
            instructor=instructor_module,
            types=MagicMock(),
        )

        with patch("consumer.llm_classifier_v2.load_instructor_genai", return_value=deps):
            with patch.object(
                classifier,
                "_call_llm_with_manual_json",
                side_effect=AssertionError("manual JSON fallback should not run"),
            ) as manual_fallback:
                result = await classifier.classify("old", "new")

        manual_fallback.assert_not_called()
        assert result.label == ClassificationLabel.EXPANSION
        assert result.confidence == 0.93
        assert result.rationale == "Expanded into a new adjacent market."


class TestLLMClassifierV2Logging:
    def test_parse_failure_logs_redacted_response_summary(self):
        classifier = LLMClassifierV2(ClassifierConfig())
        secret_response = "top-secret provider output"
        classifier._client = MagicMock()
        classifier._client.models.generate_content.return_value = MagicMock(
            text=secret_response
        )

        with patch("consumer.llm_classifier_v2.logger.error") as error_log:
            result = classifier._call_llm_with_manual_json("prompt")

        assert result["rationale"] == "Parse error: JSONDecodeError"
        assert secret_response not in str(error_log.call_args)

    def test_instructor_fallback_warning_redacts_exception_text(self):
        classifier = LLMClassifierV2(ClassifierConfig())
        classifier._client = MagicMock()
        secret_error = "prompt=Healthy meal kit delivery startup"
        instructor_module = MagicMock()
        instructor_module.from_genai.side_effect = RuntimeError(secret_error)
        instructor_module.Mode = SimpleNamespace(
            GENAI_STRUCTURED_OUTPUTS="structured_outputs"
        )
        deps = MagicMock(
            instructor=instructor_module,
            types=MagicMock(),
        )

        with patch("consumer.llm_classifier_v2.load_instructor_genai", return_value=deps):
            with patch("consumer.llm_classifier_v2.logger.warning") as warning_log:
                result = classifier._call_llm_with_instructor("prompt")

        assert result is None
        assert secret_error not in str(warning_log.call_args)

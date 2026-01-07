"""Tests for LLMClassifierV2 with strict contract and caching."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os

from consumer.llm_classifier_v2 import (
    LLMClassifierV2,
    ClassifierConfig,
    ClassificationResult,
    ClassificationLabel,
    SCHEMA_VERSION,
)


class TestLLMClassifierV2:
    """Test suite for LLMClassifierV2."""

    @pytest.mark.asyncio
    async def test_classification_returns_valid_schema(self):
        """Classifier must return valid schema with all required fields."""
        config = ClassifierConfig(model="gemini-1.5-flash")
        classifier = LLMClassifierV2(config)

        # Mock the LLM response
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
            "confidence": 0.55,  # Below threshold
            "rationale": "Uncertain change",
        }

        with patch.object(classifier, "_call_llm", return_value=mock_response):
            result = await classifier.classify(
                old_description="App v1", new_description="App v2"
            )

        # Low confidence should override to needs_review
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

        assert call_count == 1  # Only called once
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

        assert call_count == 2  # Called twice for different inputs

    @pytest.mark.asyncio
    async def test_cache_can_be_persisted_and_loaded(self):
        """Cache should survive save/load cycle."""
        config = ClassifierConfig(cache_enabled=True)
        classifier = LLMClassifierV2(config)

        # Populate cache
        classifier._cache["test_hash"] = ClassificationResult(
            schema_version="v1",
            label=ClassificationLabel.PIVOT,
            confidence=0.9,
            rationale="Test",
            input_hash="test_hash",
        )

        # Save and load
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            cache_path = f.name

        try:
            classifier.save_cache(cache_path)

            new_classifier = LLMClassifierV2(config)
            new_classifier.load_cache(cache_path)

            assert "test_hash" in new_classifier._cache
            assert new_classifier._cache["test_hash"].label == ClassificationLabel.PIVOT
        finally:
            os.unlink(cache_path)

    @pytest.mark.asyncio
    async def test_all_labels_parsed_correctly(self):
        """All valid labels should be parsed correctly."""
        for label in ["pivot", "expansion", "rebrand", "minor", "needs_review"]:
            # Fresh classifier for each label to avoid cache interference
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

        assert call_count == 2  # Called twice without caching

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

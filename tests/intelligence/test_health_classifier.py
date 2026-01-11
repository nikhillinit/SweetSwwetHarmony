"""Tests for health-specific LLM classifier."""
import pytest
from intelligence.health_classifier import (
    HealthClassifier,
    HealthClassifierConfig,
    HealthClassificationResult,
    HealthCategory,
    HEALTH_CLASSIFIER_SYSTEM_PROMPT,
)


class TestHealthClassifierBasics:
    """Test basic HealthClassifier functionality."""

    def test_classifier_exists(self):
        """HealthClassifier should exist and be instantiable."""
        classifier = HealthClassifier()
        assert classifier is not None

    def test_config_has_defaults(self):
        """HealthClassifierConfig should have sensible defaults."""
        config = HealthClassifierConfig()
        assert config.model is not None
        assert config.min_confidence >= 0.0
        assert config.min_confidence <= 1.0


class TestHealthCategories:
    """Test HealthCategory enum values."""

    def test_has_consumer_device(self):
        assert HealthCategory.CONSUMER_DEVICE.value == "consumer_device"

    def test_has_consumer_service(self):
        assert HealthCategory.CONSUMER_SERVICE.value == "consumer_service"

    def test_has_health_it(self):
        assert HealthCategory.HEALTH_IT.value == "health_it"

    def test_has_wellness(self):
        assert HealthCategory.WELLNESS.value == "wellness"

    def test_has_out_of_scope(self):
        assert HealthCategory.OUT_OF_SCOPE.value == "out_of_scope"


class TestHealthClassifierPrompt:
    """Test health classifier prompt configuration."""

    def test_system_prompt_exists(self):
        """System prompt should be defined."""
        assert HEALTH_CLASSIFIER_SYSTEM_PROMPT is not None
        assert len(HEALTH_CLASSIFIER_SYSTEM_PROMPT) > 100

    def test_system_prompt_mentions_consumer_health(self):
        """System prompt should focus on consumer health."""
        assert "consumer health" in HEALTH_CLASSIFIER_SYSTEM_PROMPT.lower()

    def test_system_prompt_excludes_pharma(self):
        """System prompt should exclude pharmaceutical."""
        assert "pharmaceutical" in HEALTH_CLASSIFIER_SYSTEM_PROMPT.lower()
        assert "out of scope" in HEALTH_CLASSIFIER_SYSTEM_PROMPT.lower()


class TestHealthClassification:
    """Test health signal classification."""

    @pytest.mark.asyncio
    async def test_classify_returns_result(self):
        """classify should return HealthClassificationResult."""
        classifier = HealthClassifier()
        result = await classifier.classify("FDA-cleared wearable for heart monitoring")
        assert isinstance(result, HealthClassificationResult)
        assert isinstance(result.fit_score, float)
        assert isinstance(result.category, HealthCategory)

    @pytest.mark.asyncio
    async def test_classify_with_company_name(self):
        """classify should accept company_name parameter."""
        classifier = HealthClassifier()
        result = await classifier.classify(
            "New health monitoring device",
            company_name="Acme Health"
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_result_has_spec_required_fields(self):
        """HealthClassificationResult must have all spec-required fields."""
        classifier = HealthClassifier()
        result = await classifier.classify("Test content")
        # Spec-required fields
        assert hasattr(result, 'fit_score')
        assert hasattr(result, 'category')
        assert hasattr(result, 'sub_category')
        assert hasattr(result, 'thesis_alignment')
        assert hasattr(result, 'signals')
        assert hasattr(result, 'confidence')
        # Type checks
        assert isinstance(result.fit_score, float)
        assert isinstance(result.category, HealthCategory)
        assert result.sub_category is None or isinstance(result.sub_category, str)
        assert isinstance(result.thesis_alignment, str)
        assert isinstance(result.signals, list)
        assert isinstance(result.confidence, float)
        # fit_score should be 0-1 scale
        assert 0.0 <= result.fit_score <= 1.0
        assert 0.0 <= result.confidence <= 1.0

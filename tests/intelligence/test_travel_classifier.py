"""Tests for travel-specific LLM classifier with weighted thesis scoring."""
import pytest
from intelligence.travel_classifier import (
    TravelClassifier,
    TravelClassifierConfig,
    TravelClassificationResult,
    TravelCategory,
    TRAVEL_CLASSIFIER_SYSTEM_PROMPT,
)


class TestTravelClassifierConfig:
    """Test TravelClassifierConfig defaults and customization."""

    def test_default_config(self):
        """TravelClassifierConfig should have sensible defaults."""
        config = TravelClassifierConfig()
        assert config.model == "claude-3-haiku-20240307"
        assert config.min_confidence == 0.7
        assert config.temperature == 0.2
        assert config.max_tokens == 500
        assert config.api_key is None

    def test_custom_config(self):
        """TravelClassifierConfig should accept custom values."""
        config = TravelClassifierConfig(
            model="claude-3-sonnet-20240229",
            min_confidence=0.8,
            temperature=0.3,
            max_tokens=1000,
            api_key="test-key",
        )
        assert config.model == "claude-3-sonnet-20240229"
        assert config.min_confidence == 0.8
        assert config.temperature == 0.3
        assert config.max_tokens == 1000
        assert config.api_key == "test-key"


class TestTravelCategory:
    """Test TravelCategory enum values."""

    def test_categories_exist(self):
        """TravelCategory should have all expected categories."""
        assert TravelCategory.HOTEL_TECH.value == "hotel_tech"
        assert TravelCategory.BOOKING_PLATFORM.value == "booking_platform"
        assert TravelCategory.EXPERIENTIAL.value == "experiential"
        assert TravelCategory.TRAVEL_INFRASTRUCTURE.value == "travel_infrastructure"
        assert TravelCategory.RENTAL_TECH.value == "rental_tech"
        assert TravelCategory.OUT_OF_SCOPE.value == "out_of_scope"


class TestTravelClassificationResult:
    """Test TravelClassificationResult dataclass."""

    def test_result_fields(self):
        """TravelClassificationResult should have all required fields."""
        result = TravelClassificationResult(
            fit_score=0.85,
            category=TravelCategory.HOTEL_TECH,
            sub_category="property_management",
            thesis_alignment="Strong fit with hotel tech thesis",
            signals=["hotel tech", "property management"],
            confidence=0.9,
            is_tech_enabled=True,
            investment_stage_fit="seed",
            regulatory_stage=None,
        )
        assert result.fit_score == 0.85
        assert result.category == TravelCategory.HOTEL_TECH
        assert result.sub_category == "property_management"
        assert result.thesis_alignment == "Strong fit with hotel tech thesis"
        assert result.signals == ["hotel tech", "property management"]
        assert result.confidence == 0.9
        assert result.is_tech_enabled is True
        assert result.investment_stage_fit == "seed"
        assert result.regulatory_stage is None

    def test_result_default_values(self):
        """TravelClassificationResult should have sensible defaults."""
        result = TravelClassificationResult(
            fit_score=0.5,
            category=TravelCategory.OUT_OF_SCOPE,
            sub_category=None,
            thesis_alignment="Default alignment",
            signals=[],
            confidence=0.5,
        )
        assert result.is_tech_enabled is True
        assert result.investment_stage_fit == "not_fit"
        assert result.regulatory_stage is None


class TestTravelClassifier:
    """Test TravelClassifier initialization and basic functionality."""

    def test_classifier_initialization(self):
        """TravelClassifier should initialize with default config."""
        classifier = TravelClassifier()
        assert classifier is not None
        assert classifier.config is not None
        assert isinstance(classifier.config, TravelClassifierConfig)

    def test_classifier_with_custom_config(self):
        """TravelClassifier should accept custom configuration."""
        config = TravelClassifierConfig(
            model="claude-3-sonnet-20240229",
            min_confidence=0.9,
        )
        classifier = TravelClassifier(config=config)
        assert classifier.config.model == "claude-3-sonnet-20240229"
        assert classifier.config.min_confidence == 0.9

    @pytest.mark.asyncio
    async def test_classify_returns_result(self):
        """classify should return TravelClassificationResult."""
        classifier = TravelClassifier()
        result = await classifier.classify("Hotel property management software platform")
        assert isinstance(result, TravelClassificationResult)
        assert isinstance(result.fit_score, float)
        assert isinstance(result.category, TravelCategory)
        assert 0.0 <= result.fit_score <= 1.0
        assert 0.0 <= result.confidence <= 1.0

    def test_system_prompt_exists(self):
        """System prompt should be defined and substantive."""
        assert TRAVEL_CLASSIFIER_SYSTEM_PROMPT is not None
        assert len(TRAVEL_CLASSIFIER_SYSTEM_PROMPT) > 100
        assert "travel" in TRAVEL_CLASSIFIER_SYSTEM_PROMPT.lower()


class TestTravelClassifierWeightedScoring:
    """Test weighted scoring methods of TravelClassifier."""

    def test_compute_signal_score_distribution(self):
        """_compute_signal_score should detect distribution signals."""
        classifier = TravelClassifier()
        content = "We have a partnership with Marriott and Hilton hotels"
        score = classifier._compute_signal_score(content, "distribution")
        assert score > 0.0
        assert score <= 1.0

    def test_compute_signal_score_category(self):
        """_compute_signal_score should detect category signals."""
        classifier = TravelClassifier()
        content = "Our hotel tech platform provides revenue management and channel manager functionality"
        score = classifier._compute_signal_score(content, "category")
        assert score > 0.0
        assert score <= 1.0

    def test_compute_signal_score_negative(self):
        """_compute_signal_score should return 0 for no matches."""
        classifier = TravelClassifier()
        content = "This is a completely unrelated document about cars"
        score = classifier._compute_signal_score(content, "distribution")
        assert score == 0.0

    def test_compute_weighted_fit_score(self):
        """_compute_weighted_fit_score should compute weighted sum of signal scores."""
        classifier = TravelClassifier()
        content = """
        Our hotel tech startup is building a booking platform for Marriott and Hilton.
        We have strong bookings and revenue growth with hospitality veteran founders.
        """
        score = classifier._compute_weighted_fit_score(content)
        assert score >= 0.0
        assert score <= 1.0

    def test_compute_weighted_fit_score_with_negative_signals(self):
        """_compute_weighted_fit_score should penalize negative signals."""
        classifier = TravelClassifier()
        # Content with positive signals but also negative signals
        content_positive = "We have hotel tech with Marriott partnership and strong bookings"
        content_negative = "We are a Series D public company with legacy GDS and no tech differentiation"

        score_positive = classifier._compute_weighted_fit_score(content_positive)
        score_negative = classifier._compute_weighted_fit_score(content_negative)

        # Negative signals should result in lower score
        assert score_positive > score_negative

    def test_compute_weighted_fit_score_empty_content(self):
        """_compute_weighted_fit_score should handle empty content."""
        classifier = TravelClassifier()
        score = classifier._compute_weighted_fit_score("")
        assert score == 0.0


class TestTravelClassifierCategoryDetection:
    """Test category detection in TravelClassifier."""

    @pytest.mark.asyncio
    async def test_detect_hotel_tech_category(self):
        """Classifier should detect hotel tech category."""
        classifier = TravelClassifier()
        result = await classifier.classify("Hotel property management software for boutique hotels")
        assert result.category in [TravelCategory.HOTEL_TECH, TravelCategory.OUT_OF_SCOPE]

    @pytest.mark.asyncio
    async def test_detect_booking_platform_category(self):
        """Classifier should detect booking platform category."""
        classifier = TravelClassifier()
        result = await classifier.classify("Online booking platform for travel reservations")
        assert result.category in [TravelCategory.BOOKING_PLATFORM, TravelCategory.OUT_OF_SCOPE]

    @pytest.mark.asyncio
    async def test_detect_experiential_category(self):
        """Classifier should detect experiential travel category."""
        classifier = TravelClassifier()
        result = await classifier.classify("Tour operator platform for experiential travel adventures")
        assert result.category in [TravelCategory.EXPERIENTIAL, TravelCategory.OUT_OF_SCOPE]

    @pytest.mark.asyncio
    async def test_detect_rental_tech_category(self):
        """Classifier should detect rental tech category."""
        classifier = TravelClassifier()
        result = await classifier.classify("Vacation rental management software for VRBO hosts")
        assert result.category in [TravelCategory.RENTAL_TECH, TravelCategory.OUT_OF_SCOPE]


class TestTravelClassifierTechEnabled:
    """Test tech-enabled detection in TravelClassifier."""

    @pytest.mark.asyncio
    async def test_detect_tech_enabled_company(self):
        """Classifier should detect tech-enabled companies."""
        classifier = TravelClassifier()
        result = await classifier.classify("AI-powered SaaS platform for hotel booking with API integration")
        assert result.is_tech_enabled is True

    @pytest.mark.asyncio
    async def test_detect_non_tech_company(self):
        """Classifier should detect non-tech companies."""
        classifier = TravelClassifier()
        result = await classifier.classify("Traditional travel agency with brick and mortar locations only")
        # Non-tech signals should set is_tech_enabled to False
        assert result.is_tech_enabled is False


class TestTravelClassifierStageDetection:
    """Test investment stage detection in TravelClassifier."""

    @pytest.mark.asyncio
    async def test_detect_seed_stage(self):
        """Classifier should detect seed stage."""
        classifier = TravelClassifier()
        result = await classifier.classify("Seed-stage hotel tech startup building property management software")
        assert result.investment_stage_fit in ["seed", "not_fit"]

    @pytest.mark.asyncio
    async def test_detect_series_a_stage(self):
        """Classifier should detect Series A stage."""
        classifier = TravelClassifier()
        result = await classifier.classify("Series A travel platform with strong traction")
        assert result.investment_stage_fit in ["series_a", "not_fit"]

    @pytest.mark.asyncio
    async def test_detect_excluded_stage(self):
        """Classifier should detect excluded stages."""
        classifier = TravelClassifier()
        result = await classifier.classify("Series C travel company preparing for IPO")
        assert result.investment_stage_fit == "stage_mismatch"

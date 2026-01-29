"""
Tests for News Digest Generator

Tests for:
- Digest generation from news signals
- Gemini LLM integration for summarization
- Thesis category grouping
- Output formatting
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from utils.news_digest import (
    NewsDigestGenerator,
    DigestConfig,
    NewsDigest,
    DigestSection,
    format_digest_markdown,
    format_digest_slack,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def sample_signals():
    """Create sample news signals for testing."""
    from verification.verification_gate_v2 import Signal

    now = datetime.now(timezone.utc)
    return [
        Signal(
            id="sig-1",
            signal_type="funding_announcement",
            confidence=0.75,
            source_api="news_api",
            detected_at=now - timedelta(hours=2),
            raw_data={
                "title": "HealthyMeals raises $10M Series A for meal delivery",
                "description": "Consumer food-tech startup expands nationwide",
                "source": "TechCrunch",
                "url": "https://techcrunch.com/healthymeals",
                "company_name": "HealthyMeals",
            },
        ),
        Signal(
            id="sig-2",
            signal_type="product_launch",
            confidence=0.60,
            source_api="rss_feeds",
            detected_at=now - timedelta(hours=5),
            raw_data={
                "title": "FitTrack launches AI-powered fitness app",
                "description": "Wellness startup enters crowded fitness market",
                "source": "VentureBeat",
                "url": "https://venturebeat.com/fittrack",
                "company_name": "FitTrack",
            },
        ),
        Signal(
            id="sig-3",
            signal_type="news_mention",
            confidence=0.50,
            source_api="news_api",
            detected_at=now - timedelta(hours=8),
            raw_data={
                "title": "BeautyBox expands to UK market",
                "description": "D2C beauty brand opens London fulfillment center",
                "source": "Business Insider",
                "url": "https://businessinsider.com/beautybox",
                "company_name": "BeautyBox",
            },
        ),
    ]


@pytest.fixture
def config():
    """Create test digest config."""
    return DigestConfig(
        max_items_per_section=5,
        include_urls=True,
        include_confidence=True,
    )


# =============================================================================
# DIGEST CONFIG TESTS
# =============================================================================

class TestDigestConfig:
    """Tests for DigestConfig dataclass."""

    def test_default_config(self):
        """Default config has sensible values."""
        config = DigestConfig()
        assert config.max_items_per_section > 0
        assert isinstance(config.include_urls, bool)
        assert isinstance(config.include_confidence, bool)

    def test_custom_config(self):
        """Custom config values are respected."""
        config = DigestConfig(
            max_items_per_section=10,
            include_urls=False,
            include_confidence=False,
        )
        assert config.max_items_per_section == 10
        assert config.include_urls is False
        assert config.include_confidence is False


# =============================================================================
# NEWS DIGEST GENERATOR TESTS
# =============================================================================

class TestNewsDigestGenerator:
    """Tests for NewsDigestGenerator class."""

    def test_init_default(self):
        """Generator initializes with defaults."""
        generator = NewsDigestGenerator()
        assert generator.config is not None

    def test_init_custom_config(self, config):
        """Generator accepts custom config."""
        generator = NewsDigestGenerator(config=config)
        assert generator.config.max_items_per_section == 5

    def test_categorize_signal_cpg(self, sample_signals):
        """CPG signals are categorized correctly."""
        generator = NewsDigestGenerator()
        # HealthyMeals is food-tech (CPG)
        category = generator._categorize_signal(sample_signals[0])
        assert category in ["cpg", "health_tech"]

    def test_categorize_signal_health_tech(self, sample_signals):
        """Health tech signals are categorized correctly."""
        generator = NewsDigestGenerator()
        # FitTrack is fitness (health tech)
        category = generator._categorize_signal(sample_signals[1])
        assert category == "health_tech"

    def test_categorize_signal_beauty(self, sample_signals):
        """Beauty signals are categorized as CPG."""
        generator = NewsDigestGenerator()
        # BeautyBox is beauty (CPG)
        category = generator._categorize_signal(sample_signals[2])
        assert category == "cpg"

    def test_group_by_category(self, sample_signals):
        """Signals are grouped by thesis category."""
        generator = NewsDigestGenerator()
        grouped = generator._group_by_category(sample_signals)

        # Should have at least one category
        assert len(grouped) > 0
        # All signals should be assigned
        total = sum(len(signals) for signals in grouped.values())
        assert total == len(sample_signals)

    def test_sort_by_importance(self, sample_signals):
        """Signals are sorted by importance (confidence + type)."""
        generator = NewsDigestGenerator()
        sorted_signals = generator._sort_by_importance(sample_signals)

        # Funding announcement should be first (highest weight)
        assert sorted_signals[0].signal_type == "funding_announcement"

    def test_generate_section_title(self):
        """Section titles are generated correctly."""
        generator = NewsDigestGenerator()

        assert "CPG" in generator._get_section_title("cpg")
        assert "Health" in generator._get_section_title("health_tech")
        assert "Travel" in generator._get_section_title("travel")
        assert "Marketplace" in generator._get_section_title("marketplace")


# =============================================================================
# DIGEST GENERATION TESTS
# =============================================================================

class TestDigestGeneration:
    """Tests for digest generation."""

    @pytest.mark.asyncio
    async def test_generate_digest_returns_digest(self, sample_signals, config):
        """Generate returns NewsDigest object."""
        generator = NewsDigestGenerator(config=config)

        with patch.object(generator, '_summarize_with_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "Test summary"
            digest = await generator.generate(sample_signals)

        assert isinstance(digest, NewsDigest)
        assert digest.generated_at is not None

    @pytest.mark.asyncio
    async def test_generate_digest_has_sections(self, sample_signals, config):
        """Generated digest has sections."""
        generator = NewsDigestGenerator(config=config)

        with patch.object(generator, '_summarize_with_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "Test summary"
            digest = await generator.generate(sample_signals)

        assert len(digest.sections) > 0

    @pytest.mark.asyncio
    async def test_generate_digest_sections_have_items(self, sample_signals, config):
        """Digest sections contain items."""
        generator = NewsDigestGenerator(config=config)

        with patch.object(generator, '_summarize_with_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "Test summary"
            digest = await generator.generate(sample_signals)

        total_items = sum(len(s.items) for s in digest.sections)
        assert total_items > 0

    @pytest.mark.asyncio
    async def test_generate_digest_respects_max_items(self, config):
        """Digest respects max items per section."""
        from verification.verification_gate_v2 import Signal

        # Create many signals
        now = datetime.now(timezone.utc)
        many_signals = [
            Signal(
                id=f"sig-{i}",
                signal_type="news_mention",
                confidence=0.5,
                source_api="news_api",
                detected_at=now,
                raw_data={
                    "title": f"Fitness startup {i} raises funding",
                    "description": "...",
                    "source": "TechCrunch",
                    "company_name": f"FitCo{i}",
                },
            )
            for i in range(20)
        ]

        config.max_items_per_section = 3
        generator = NewsDigestGenerator(config=config)

        with patch.object(generator, '_summarize_with_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "Test summary"
            digest = await generator.generate(many_signals)

        for section in digest.sections:
            assert len(section.items) <= 3

    @pytest.mark.asyncio
    async def test_generate_empty_signals(self, config):
        """Empty signal list produces empty digest."""
        generator = NewsDigestGenerator(config=config)
        digest = await generator.generate([])

        assert len(digest.sections) == 0

    @pytest.mark.asyncio
    async def test_generate_calls_llm_for_summary(self, sample_signals, config):
        """LLM is called to generate summary."""
        generator = NewsDigestGenerator(config=config)
        # Mock the model so generator thinks LLM is available
        generator._model = MagicMock()

        with patch.object(generator, '_summarize_with_llm', new_callable=AsyncMock) as mock_llm, \
             patch.object(generator, '_summarize_section', new_callable=AsyncMock) as mock_section:
            mock_llm.return_value = "AI-generated summary"
            mock_section.return_value = "Section summary"
            digest = await generator.generate(sample_signals)

        # LLM should be called at least once (for overall summary)
        assert mock_llm.called


# =============================================================================
# LLM INTEGRATION TESTS
# =============================================================================

class TestLLMIntegration:
    """Tests for Gemini LLM integration."""

    @pytest.mark.asyncio
    async def test_summarize_with_llm_called(self, sample_signals):
        """LLM summarization is attempted."""
        generator = NewsDigestGenerator()

        with patch('utils.news_digest.genai') as mock_genai:
            mock_model = MagicMock()
            mock_model.generate_content_async = AsyncMock(
                return_value=MagicMock(text="Summary from Gemini")
            )
            mock_genai.GenerativeModel.return_value = mock_model

            summary = await generator._summarize_with_llm(sample_signals)

        assert summary is not None

    @pytest.mark.asyncio
    async def test_summarize_handles_api_error(self, sample_signals):
        """Gracefully handles LLM API errors."""
        generator = NewsDigestGenerator()

        with patch('utils.news_digest.genai') as mock_genai:
            mock_model = MagicMock()
            mock_model.generate_content_async = AsyncMock(
                side_effect=Exception("API Error")
            )
            mock_genai.GenerativeModel.return_value = mock_model

            # Should not raise, returns fallback
            summary = await generator._summarize_with_llm(sample_signals)
            assert summary is not None

    @pytest.mark.asyncio
    async def test_summarize_without_api_key(self, sample_signals):
        """Works without API key (fallback summary)."""
        generator = NewsDigestGenerator()

        with patch.dict('os.environ', {}, clear=True):
            with patch('utils.news_digest.GOOGLE_API_KEY', None):
                summary = await generator._summarize_with_llm(sample_signals)

        # Should return fallback summary
        assert summary is not None


# =============================================================================
# OUTPUT FORMATTING TESTS
# =============================================================================

class TestOutputFormatting:
    """Tests for digest output formatting."""

    def test_format_markdown(self, sample_signals):
        """Markdown format is valid."""
        digest = NewsDigest(
            generated_at=datetime.now(timezone.utc),
            period_start=datetime.now(timezone.utc) - timedelta(days=1),
            period_end=datetime.now(timezone.utc),
            total_signals=3,
            sections=[
                DigestSection(
                    category="health_tech",
                    title="Health & Wellness",
                    items=[
                        {
                            "title": "FitTrack launches AI fitness app",
                            "company": "FitTrack",
                            "source": "TechCrunch",
                            "url": "https://example.com",
                            "confidence": 0.6,
                        }
                    ],
                    summary="Health tech news summary",
                )
            ],
            summary="Overall summary",
        )

        md = format_digest_markdown(digest)

        assert "# " in md  # Has header
        assert "Health" in md  # Has section
        assert "FitTrack" in md  # Has item

    def test_format_slack(self, sample_signals):
        """Slack format is valid."""
        digest = NewsDigest(
            generated_at=datetime.now(timezone.utc),
            period_start=datetime.now(timezone.utc) - timedelta(days=1),
            period_end=datetime.now(timezone.utc),
            total_signals=3,
            sections=[
                DigestSection(
                    category="cpg",
                    title="Consumer CPG",
                    items=[
                        {
                            "title": "BeautyBox expands",
                            "company": "BeautyBox",
                            "source": "Forbes",
                            "url": "https://example.com",
                            "confidence": 0.5,
                        }
                    ],
                    summary="CPG news summary",
                )
            ],
            summary="Overall summary",
        )

        slack = format_digest_slack(digest)

        assert isinstance(slack, dict)
        assert "blocks" in slack or "text" in slack

    def test_markdown_includes_urls_when_configured(self):
        """Markdown includes URLs when configured."""
        digest = NewsDigest(
            generated_at=datetime.now(timezone.utc),
            period_start=datetime.now(timezone.utc) - timedelta(days=1),
            period_end=datetime.now(timezone.utc),
            total_signals=1,
            sections=[
                DigestSection(
                    category="cpg",
                    title="CPG",
                    items=[{"title": "Test", "url": "https://test.com", "company": "TestCo", "source": "Test"}],
                    summary="Summary",
                )
            ],
            summary="Overall",
        )

        md = format_digest_markdown(digest, include_urls=True)
        assert "https://test.com" in md

    def test_markdown_excludes_urls_when_configured(self):
        """Markdown excludes URLs when configured."""
        digest = NewsDigest(
            generated_at=datetime.now(timezone.utc),
            period_start=datetime.now(timezone.utc) - timedelta(days=1),
            period_end=datetime.now(timezone.utc),
            total_signals=1,
            sections=[
                DigestSection(
                    category="cpg",
                    title="CPG",
                    items=[{"title": "Test", "url": "https://test.com", "company": "TestCo", "source": "Test"}],
                    summary="Summary",
                )
            ],
            summary="Overall",
        )

        md = format_digest_markdown(digest, include_urls=False)
        assert "https://test.com" not in md


# =============================================================================
# DIGEST DATACLASS TESTS
# =============================================================================

class TestNewsDigest:
    """Tests for NewsDigest dataclass."""

    def test_digest_creation(self):
        """Digest can be created with required fields."""
        digest = NewsDigest(
            generated_at=datetime.now(timezone.utc),
            period_start=datetime.now(timezone.utc) - timedelta(days=1),
            period_end=datetime.now(timezone.utc),
            total_signals=5,
            sections=[],
            summary="Test summary",
        )
        assert digest.total_signals == 5

    def test_digest_section_creation(self):
        """DigestSection can be created."""
        section = DigestSection(
            category="cpg",
            title="Consumer CPG",
            items=[{"title": "Test", "company": "TestCo"}],
            summary="Section summary",
        )
        assert section.category == "cpg"
        assert len(section.items) == 1

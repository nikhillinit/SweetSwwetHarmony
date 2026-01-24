"""
Tests for LLM-based Profile Extractor.

Uses mocked Gemini responses to test extraction logic.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone

from profilers.extractors.llm_extractor import (
    ProfileLLMExtractor,
    LLMExtractionResult,
    ExtractedFieldResult,
    EXTRACTOR_PROMPT_VERSION,
)


# =============================================================================
# MOCK GEMINI RESPONSES
# =============================================================================

MOCK_SUCCESSFUL_RESPONSE = {
    "problem_solved": {
        "value": "Helps meal kit companies reduce customer churn through personalized recommendations",
        "short_phrase": "subscription churn reduction",
        "confidence": 0.85,
        "evidence": "We reduce churn by 40% for meal kit brands"
    },
    "target_customer": {
        "value": "D2C subscription brands in food and beverage",
        "short_phrase": "D2C food subscription brands",
        "confidence": 0.9,
        "evidence": "Built for meal kit and grocery delivery companies"
    },
    "business_model": {
        "value": "B2B_SaaS",
        "short_phrase": "B2B SaaS",
        "confidence": 0.8,
        "evidence": None
    },
    "pricing_model": {
        "value": "usage_based",
        "short_phrase": "usage-based pricing",
        "confidence": 0.7,
        "evidence": "Pricing based on monthly active subscribers"
    },
    "company_name": {
        "value": "ChurnGuard",
        "short_phrase": "ChurnGuard",
        "confidence": 0.95,
        "evidence": None
    },
    "category_hints": ["Consumer CPG", "B2B SaaS"]
}

MOCK_PARTIAL_RESPONSE = {
    "problem_solved": {
        "value": "Helps with something",
        "short_phrase": "helps with something",
        "confidence": 0.5,
        "evidence": None
    },
    "target_customer": None,
    "business_model": None,
    "pricing_model": None,
    "company_name": {
        "value": "TestCo",
        "short_phrase": "TestCo",
        "confidence": 0.7,
        "evidence": None
    },
    "category_hints": []
}

MOCK_EMPTY_RESPONSE = {
    "problem_solved": None,
    "target_customer": None,
    "business_model": None,
    "pricing_model": None,
    "company_name": None,
    "category_hints": []
}


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_gemini_client():
    """Create a mock Gemini client."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '{"problem_solved": null}'
    mock_response.usage_metadata = MagicMock()
    mock_response.usage_metadata.prompt_token_count = 500
    mock_response.usage_metadata.candidates_token_count = 200
    mock_client.models.generate_content.return_value = mock_response
    return mock_client


@pytest.fixture
def extractor_with_mock(mock_gemini_client):
    """Create extractor with mocked client."""
    extractor = ProfileLLMExtractor(api_key="test-key")
    extractor._client = mock_gemini_client
    return extractor


# =============================================================================
# INITIALIZATION TESTS
# =============================================================================

class TestProfileLLMExtractorInit:
    """Tests for ProfileLLMExtractor initialization."""

    def test_default_model(self):
        extractor = ProfileLLMExtractor(api_key="test")
        assert extractor.model_name == "gemini-2.0-flash"

    def test_custom_model(self):
        extractor = ProfileLLMExtractor(api_key="test", model="gemini-pro")
        assert extractor.model_name == "gemini-pro"

    def test_default_temperature(self):
        extractor = ProfileLLMExtractor(api_key="test")
        assert extractor.temperature == 0.2

    def test_default_max_tokens(self):
        extractor = ProfileLLMExtractor(api_key="test")
        assert extractor.max_tokens == 800

    def test_default_max_input_chars(self):
        extractor = ProfileLLMExtractor(api_key="test")
        assert extractor.max_input_chars == 15000

    def test_api_key_from_env(self):
        with patch.dict('os.environ', {'GOOGLE_API_KEY': 'env-key'}):
            extractor = ProfileLLMExtractor()
            assert extractor.api_key == 'env-key'

    def test_api_key_from_gemini_env(self):
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'gemini-key'}, clear=True):
            extractor = ProfileLLMExtractor()
            assert extractor.api_key == 'gemini-key'


# =============================================================================
# FIELD PARSING TESTS
# =============================================================================

class TestParseField:
    """Tests for _parse_field method."""

    def test_parse_complete_field(self):
        extractor = ProfileLLMExtractor(api_key="test")
        field_data = {
            "value": "Test value",
            "short_phrase": "test",
            "confidence": 0.85,
            "evidence": "Some evidence"
        }
        result = extractor._parse_field(field_data)

        assert result is not None
        assert result.value == "Test value"
        assert result.short_phrase == "test"
        assert result.confidence == 0.85
        assert result.evidence == "Some evidence"

    def test_parse_field_without_evidence(self):
        extractor = ProfileLLMExtractor(api_key="test")
        field_data = {
            "value": "Test value",
            "short_phrase": "test",
            "confidence": 0.7,
            "evidence": None
        }
        result = extractor._parse_field(field_data)

        assert result is not None
        assert result.evidence is None

    def test_parse_null_field(self):
        extractor = ProfileLLMExtractor(api_key="test")
        result = extractor._parse_field(None)
        assert result is None

    def test_parse_field_with_null_value(self):
        extractor = ProfileLLMExtractor(api_key="test")
        field_data = {"value": None, "confidence": 0.5}
        result = extractor._parse_field(field_data)
        assert result is None

    def test_parse_field_default_short_phrase(self):
        extractor = ProfileLLMExtractor(api_key="test")
        field_data = {"value": "A very long value that exceeds fifty characters limit"}
        result = extractor._parse_field(field_data)

        assert result is not None
        assert len(result.short_phrase) <= 50

    def test_parse_field_default_confidence(self):
        extractor = ProfileLLMExtractor(api_key="test")
        field_data = {"value": "Test"}
        result = extractor._parse_field(field_data)

        assert result is not None
        assert result.confidence == 0.5  # Default


# =============================================================================
# EXTRACTION TESTS (WITH MOCKS)
# =============================================================================

class TestExtraction:
    """Tests for extract method with mocked Gemini."""

    @pytest.mark.asyncio
    async def test_successful_extraction(self, extractor_with_mock):
        """Test successful extraction returns ProfileExtractionResult."""
        import json

        # Configure mock response
        mock_response = MagicMock()
        mock_response.text = json.dumps(MOCK_SUCCESSFUL_RESPONSE)
        mock_response.usage_metadata = MagicMock()
        mock_response.usage_metadata.prompt_token_count = 500
        mock_response.usage_metadata.candidates_token_count = 200
        extractor_with_mock._client.models.generate_content.return_value = mock_response

        result = await extractor_with_mock.extract(
            combined_text="Homepage content here...",
            source_url="https://churnguard.com"
        )

        assert result is not None
        assert result.extraction_method == "llm"
        assert result.problem_solved is not None
        assert result.problem_solved.value == "Helps meal kit companies reduce customer churn through personalized recommendations"
        assert result.problem_solved.confidence == 0.85
        assert result.target_customer is not None
        assert result.company_name is not None
        assert "Consumer CPG" in result.category_hints

    @pytest.mark.asyncio
    async def test_partial_extraction(self, extractor_with_mock):
        """Test extraction with only some fields."""
        import json

        mock_response = MagicMock()
        mock_response.text = json.dumps(MOCK_PARTIAL_RESPONSE)
        mock_response.usage_metadata = None
        extractor_with_mock._client.models.generate_content.return_value = mock_response

        result = await extractor_with_mock.extract(
            combined_text="Some content...",
            source_url="https://testco.com"
        )

        assert result is not None
        assert result.problem_solved is not None
        assert result.target_customer is None
        assert result.company_name is not None

    @pytest.mark.asyncio
    async def test_empty_extraction(self, extractor_with_mock):
        """Test extraction with no fields."""
        import json

        mock_response = MagicMock()
        mock_response.text = json.dumps(MOCK_EMPTY_RESPONSE)
        mock_response.usage_metadata = None
        extractor_with_mock._client.models.generate_content.return_value = mock_response

        result = await extractor_with_mock.extract(
            combined_text="...",
            source_url="https://example.com"
        )

        assert result is not None
        assert result.problem_solved is None
        assert result.target_customer is None

    @pytest.mark.asyncio
    async def test_api_error_handling(self, extractor_with_mock):
        """Test graceful handling of API errors."""
        extractor_with_mock._client.models.generate_content.side_effect = Exception("API Error")

        result = await extractor_with_mock.extract(
            combined_text="Content...",
            source_url="https://example.com"
        )

        assert result is not None
        assert result.extraction_method == "llm_error"

    @pytest.mark.asyncio
    async def test_json_parse_error_handling(self, extractor_with_mock):
        """Test handling of invalid JSON response."""
        mock_response = MagicMock()
        mock_response.text = "Not valid JSON {"
        mock_response.usage_metadata = None
        extractor_with_mock._client.models.generate_content.return_value = mock_response

        result = await extractor_with_mock.extract(
            combined_text="Content...",
            source_url="https://example.com"
        )

        assert result is not None
        assert result.extraction_method == "llm_error"

    @pytest.mark.asyncio
    async def test_markdown_code_block_stripping(self, extractor_with_mock):
        """Test that markdown code blocks are stripped."""
        import json

        markdown_response = f"```json\n{json.dumps(MOCK_SUCCESSFUL_RESPONSE)}\n```"
        mock_response = MagicMock()
        mock_response.text = markdown_response
        mock_response.usage_metadata = None
        extractor_with_mock._client.models.generate_content.return_value = mock_response

        result = await extractor_with_mock.extract(
            combined_text="Content...",
            source_url="https://example.com"
        )

        assert result is not None
        assert result.problem_solved is not None

    @pytest.mark.asyncio
    async def test_input_truncation(self, extractor_with_mock):
        """Test that long input is truncated."""
        import json

        mock_response = MagicMock()
        mock_response.text = json.dumps(MOCK_EMPTY_RESPONSE)
        mock_response.usage_metadata = None
        extractor_with_mock._client.models.generate_content.return_value = mock_response

        # Set low max input
        extractor_with_mock.max_input_chars = 100

        long_content = "x" * 200
        await extractor_with_mock.extract(
            combined_text=long_content,
            source_url="https://example.com"
        )

        # Check that the call was made (prompt was built)
        assert extractor_with_mock._client.models.generate_content.called

    @pytest.mark.asyncio
    async def test_source_url_in_result(self, extractor_with_mock):
        """Test that source URL is propagated to fields."""
        import json

        mock_response = MagicMock()
        mock_response.text = json.dumps(MOCK_SUCCESSFUL_RESPONSE)
        mock_response.usage_metadata = None
        extractor_with_mock._client.models.generate_content.return_value = mock_response

        result = await extractor_with_mock.extract(
            combined_text="Content...",
            source_url="https://specific-url.com"
        )

        assert result.problem_solved.source_url == "https://specific-url.com"
        assert result.target_customer.source_url == "https://specific-url.com"

    @pytest.mark.asyncio
    async def test_extraction_method_set(self, extractor_with_mock):
        """Test that extraction_method is set to 'llm'."""
        import json

        mock_response = MagicMock()
        mock_response.text = json.dumps(MOCK_SUCCESSFUL_RESPONSE)
        mock_response.usage_metadata = None
        extractor_with_mock._client.models.generate_content.return_value = mock_response

        result = await extractor_with_mock.extract(
            combined_text="Content...",
            source_url="https://example.com"
        )

        assert result.extraction_method == "llm"
        assert result.problem_solved.extraction_method == "llm"


# =============================================================================
# LLM EXTRACTION RESULT TESTS
# =============================================================================

class TestLLMExtractionResult:
    """Tests for LLMExtractionResult dataclass."""

    def test_default_values(self):
        result = LLMExtractionResult()
        assert result.problem_solved is None
        assert result.target_customer is None
        assert result.category_hints == []
        assert result.prompt_version == EXTRACTOR_PROMPT_VERSION
        assert result.error is None

    def test_with_error(self):
        result = LLMExtractionResult(error="Something went wrong")
        assert result.error == "Something went wrong"


class TestExtractedFieldResult:
    """Tests for ExtractedFieldResult dataclass."""

    def test_creation(self):
        field = ExtractedFieldResult(
            value="test",
            short_phrase="test",
            confidence=0.8,
            evidence="evidence"
        )
        assert field.value == "test"
        assert field.confidence == 0.8

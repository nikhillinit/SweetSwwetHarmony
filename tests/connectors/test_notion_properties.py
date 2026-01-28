"""
Tests for NotionConnector property handling.

Covers:
- Field mapping (building Notion properties)
- Type conversion
- Multi-select handling
- Schema validation
- Property extraction
"""

import os
import sys
from typing import Any, Dict

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from connectors.notion_connector_v2 import (
    NotionConnector,
    ProspectPayload,
    InvestmentStage,
    DealStatus,
    ValidationResult,
    Sector,
)

from tests.connectors.conftest import MockNotionTransport


# =============================================================================
# FIELD MAPPING TESTS
# =============================================================================

class TestFieldMapping:
    """Tests for building Notion properties from ProspectPayload."""

    @pytest.mark.asyncio
    async def test_build_create_properties_maps_company_name(
        self,
        connector: NotionConnector,
        sample_prospect: ProspectPayload,
    ):
        """Company name should map to title property."""
        props = connector._build_create_properties(sample_prospect)

        assert "Company Name" in props
        title = props["Company Name"]["title"][0]["text"]["content"]
        assert title == "Acme Corp"

    @pytest.mark.asyncio
    async def test_build_create_properties_maps_investment_stage(
        self,
        connector: NotionConnector,
        sample_prospect: ProspectPayload,
    ):
        """Investment stage should map to select property."""
        props = connector._build_create_properties(sample_prospect)

        assert "Investment Stage" in props
        stage = props["Investment Stage"]["select"]["name"]
        assert stage == "Seed"

    @pytest.mark.asyncio
    async def test_build_create_properties_maps_website_url(
        self,
        connector: NotionConnector,
        sample_prospect: ProspectPayload,
    ):
        """Website should map to URL property."""
        props = connector._build_create_properties(sample_prospect)

        assert "Website" in props
        url = props["Website"]["url"]
        assert url == "https://acme.com"

    @pytest.mark.asyncio
    async def test_build_create_properties_maps_confidence_score(
        self,
        connector: NotionConnector,
        sample_prospect: ProspectPayload,
    ):
        """Confidence score should map to number property."""
        props = connector._build_create_properties(sample_prospect)

        assert "Confidence Score" in props
        score = props["Confidence Score"]["number"]
        assert score == 0.75

    @pytest.mark.asyncio
    async def test_build_create_properties_maps_rich_text(
        self,
        connector: NotionConnector,
        sample_prospect: ProspectPayload,
    ):
        """Text fields should map to rich_text property."""
        props = connector._build_create_properties(sample_prospect)

        assert "Why Now" in props
        text = props["Why Now"]["rich_text"][0]["text"]["content"]
        assert "Strong traction" in text


# =============================================================================
# TYPE CONVERSION TESTS
# =============================================================================

class TestTypeConversion:
    """Tests for type conversion in property building."""

    @pytest.mark.asyncio
    async def test_confidence_score_rounds_to_two_decimals(
        self,
        connector: NotionConnector,
    ):
        """Confidence score should be rounded."""
        prospect = ProspectPayload(
            discovery_id="disc-round",
            company_name="Round Corp",
            canonical_key="domain:round.com",
            stage=InvestmentStage.SEED,
            confidence_score=0.7532456,
        )

        props = connector._build_create_properties(prospect)

        assert props["Confidence Score"]["number"] == 0.75

    @pytest.mark.asyncio
    async def test_investment_stage_enum_to_string(
        self,
        connector: NotionConnector,
    ):
        """InvestmentStage enum should convert to string."""
        for stage in InvestmentStage:
            prospect = ProspectPayload(
                discovery_id=f"disc-{stage.name}",
                company_name="Stage Corp",
                canonical_key=f"domain:{stage.name}.com",
                stage=stage,
            )

            props = connector._build_create_properties(prospect)

            assert props["Investment Stage"]["select"]["name"] == stage.value


# =============================================================================
# MULTI-SELECT TESTS
# =============================================================================

class TestMultiSelect:
    """Tests for multi-select property handling."""

    @pytest.mark.asyncio
    async def test_signal_types_as_multiselect(
        self,
        connector: NotionConnector,
        sample_prospect: ProspectPayload,
    ):
        """Signal types should map to multi_select."""
        props = connector._build_create_properties(sample_prospect)

        assert "Signal Types" in props
        options = props["Signal Types"]["multi_select"]
        assert len(options) == 2
        assert options[0]["name"] == "funding"
        assert options[1]["name"] == "launch"

    @pytest.mark.asyncio
    async def test_signal_types_limited_to_five(
        self,
        connector: NotionConnector,
    ):
        """Signal types should be limited to 5."""
        prospect = ProspectPayload(
            discovery_id="disc-many",
            company_name="Many Signals Corp",
            canonical_key="domain:many.com",
            stage=InvestmentStage.SEED,
            signal_types=["a", "b", "c", "d", "e", "f", "g"],
        )

        props = connector._build_create_properties(prospect)

        options = props["Signal Types"]["multi_select"]
        assert len(options) == 5

    @pytest.mark.asyncio
    async def test_empty_signal_types_not_included(
        self,
        connector: NotionConnector,
    ):
        """Empty signal_types should not add property."""
        prospect = ProspectPayload(
            discovery_id="disc-empty-signals",
            company_name="No Signals Corp",
            canonical_key="domain:nosignals.com",
            stage=InvestmentStage.SEED,
            signal_types=[],
        )

        props = connector._build_create_properties(prospect)

        assert "Signal Types" not in props


# =============================================================================
# SCHEMA VALIDATION TESTS
# =============================================================================

class TestSchemaValidation:
    """Tests for schema validation."""

    @pytest.mark.asyncio
    async def test_validate_schema_passes_valid(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Valid schema should pass validation."""
        result = await connector.validate_schema(force_refresh=True)

        assert result.valid is True
        assert result.missing_properties == []
        assert result.missing_status_options == []

    @pytest.mark.asyncio
    async def test_validate_schema_detects_missing_property(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should detect missing required properties."""
        schema = mock_transport._default_schema()
        del schema["properties"]["Discovery ID"]
        mock_transport.set_schema(schema)

        result = await connector.validate_schema(force_refresh=True)

        assert result.valid is False
        assert "Discovery ID" in result.missing_properties

    @pytest.mark.asyncio
    async def test_validate_schema_detects_wrong_type(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should detect wrong property types."""
        schema = mock_transport._default_schema()
        # Change Confidence Score from number to text
        schema["properties"]["Confidence Score"] = {"type": "rich_text"}
        mock_transport.set_schema(schema)

        result = await connector.validate_schema(force_refresh=True)

        assert result.valid is False
        assert "Confidence Score" in result.wrong_property_types

    @pytest.mark.asyncio
    async def test_validate_schema_detects_missing_status_options(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should detect missing status options."""
        schema = mock_transport._default_schema()
        # Remove some status options
        schema["properties"]["Status"]["select"]["options"] = [
            {"name": "Source"},
            {"name": "Tracking"},
        ]
        mock_transport.set_schema(schema)

        result = await connector.validate_schema(force_refresh=True)

        assert result.valid is False
        assert len(result.missing_status_options) > 0
        assert "Passed" in result.missing_status_options


# =============================================================================
# PROPERTY EXTRACTION TESTS
# =============================================================================

class TestPropertyExtraction:
    """Tests for extracting values from Notion properties."""

    def test_extract_text_from_rich_text(self, connector: NotionConnector):
        """Should extract text from rich_text property."""
        prop = {"rich_text": [{"text": {"content": "Hello World"}}]}

        result = connector._extract_text(prop)

        assert result == "Hello World"

    def test_extract_text_empty_rich_text(self, connector: NotionConnector):
        """Should return None for empty rich_text."""
        prop = {"rich_text": []}

        result = connector._extract_text(prop)

        assert result is None

    def test_extract_title(self, connector: NotionConnector):
        """Should extract text from title property."""
        prop = {"title": [{"text": {"content": "Company Name"}}]}

        result = connector._extract_title(prop)

        assert result == "Company Name"

    def test_extract_select(self, connector: NotionConnector):
        """Should extract name from select property."""
        prop = {"select": {"name": "Source"}}

        result = connector._extract_select(prop)

        assert result == "Source"

    def test_extract_select_none(self, connector: NotionConnector):
        """Should return None for empty select."""
        prop = {"select": None}

        result = connector._extract_select(prop)

        assert result is None


# =============================================================================
# NORMALIZE HELPERS TESTS
# =============================================================================

class TestNormalizeHelpers:
    """Tests for normalization helper methods."""

    def test_normalize_website(self, connector: NotionConnector):
        """Should normalize website URLs."""
        test_cases = [
            ("https://www.example.com/", "example.com"),
            ("http://example.com", "example.com"),
            ("HTTPS://WWW.EXAMPLE.COM", "example.com"),
            ("https://example.com/path/page", "example.com"),
        ]

        for input_url, expected in test_cases:
            result = connector._normalize_website(input_url)
            assert result == expected, f"Failed for {input_url}"

    def test_normalize_canonical_key(self, connector: NotionConnector):
        """Should normalize canonical keys."""
        test_cases = [
            ("DOMAIN:EXAMPLE.COM", "domain:example.com"),
            ("  companies_house:12345  ", "companies_house:12345"),
            ("EIN:123456789", "ein:123456789"),
        ]

        for input_key, expected in test_cases:
            result = connector._normalize_canonical_key(input_key)
            assert result == expected, f"Failed for {input_key}"

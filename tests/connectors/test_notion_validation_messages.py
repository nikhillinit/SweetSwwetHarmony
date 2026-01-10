"""Test enhanced Notion validation error messages with actionable guidance"""

import pytest
from connectors.notion_connector_v2 import ValidationResult


@pytest.mark.asyncio
class TestEnhancedValidationMessages:
    """Test enhanced error messages with Notion UI instructions"""

    async def test_missing_required_property_error_message(self):
        """Missing required property shows Notion UI steps to add it"""
        result = ValidationResult(
            valid=False,
            missing_properties=["Discovery ID"],
            missing_optional_properties=[],
            missing_status_options=[],
            missing_stage_options=[],
            wrong_property_types={}
        )

        message = str(result)

        # Should contain severity emoji (critical = red circle)
        assert "🔴" in message or "❌" in message

        # Should mention the property
        assert "Discovery ID" in message

        # Should include Notion UI steps (Settings, Properties, Add)
        assert "Settings" in message or "⚙️" in message or "Properties" in message

        # Should mention Text property type
        assert "Text" in message or "text" in message.lower()

    async def test_wrong_property_type_error_message(self):
        """Wrong property type shows current vs expected with data loss warning"""
        result = ValidationResult(
            valid=False,
            missing_properties=[],
            missing_optional_properties=[],
            missing_status_options=[],
            missing_stage_options=[],
            wrong_property_types={"Status": "select"}  # Expected type is select
        )

        message = str(result)

        # Should contain critical severity emoji
        assert "🔴" in message or "❌" in message

        # Should mention Status property
        assert "Status" in message

        # Should mention the expected type (select)
        assert "select" in message.lower() or "Select" in message

        # Should warn about data loss or manual deletion
        assert "data" in message.lower() or "delete" in message.lower() or "manual" in message.lower()

    async def test_missing_select_option_error_message(self):
        """Missing select option shows steps to add option"""
        result = ValidationResult(
            valid=False,
            missing_properties=[],
            missing_optional_properties=[],
            missing_status_options=["Source", "Tracking"],
            missing_stage_options=[],
            wrong_property_types={}
        )

        message = str(result)

        # Should contain warning severity emoji
        assert "🟠" in message or "🟡" in message or "⚠️" in message

        # Should list missing Status options
        assert "Source" in message
        assert "Tracking" in message

        # Should mention Status select property
        assert "Status" in message

        # Should include steps to add options
        assert "option" in message.lower() or "Settings" in message

    async def test_missing_stage_option_error_message(self):
        """Missing stage option shows steps to add option"""
        result = ValidationResult(
            valid=False,
            missing_properties=[],
            missing_optional_properties=[],
            missing_status_options=[],
            missing_stage_options=["Pre-Seed", "Seed"],
            wrong_property_types={}
        )

        message = str(result)

        # Should contain warning severity emoji
        assert "🟠" in message or "🟡" in message or "⚠️" in message

        # Should list missing Stage options
        assert "Pre-Seed" in message
        assert "Seed" in message

        # Should mention Investment Stage property
        assert "Stage" in message or "Investment" in message

    async def test_multiple_issues_prioritization(self):
        """Multiple issues are prioritized: Critical missing → Type issues → Option warnings"""
        result = ValidationResult(
            valid=False,
            missing_properties=["Discovery ID", "Canonical Key"],  # CRITICAL
            missing_optional_properties=["Why Now"],  # Optional
            missing_status_options=["Source"],  # Important
            missing_stage_options=[],
            wrong_property_types={"Confidence Score": "number"}  # Type issue
        )

        message = str(result)

        # Should contain critical severity marker
        assert "🔴" in message or "❌" in message

        # Should list all properties
        assert "Discovery ID" in message
        assert "Canonical Key" in message
        assert "Source" in message

        # Missing critical properties should appear before optional
        missing_required_idx = message.find("Discovery ID")
        missing_optional_idx = message.find("Why Now")

        assert missing_required_idx != -1
        # If both exist, required should come before optional
        if missing_optional_idx != -1:
            assert missing_required_idx < missing_optional_idx

    async def test_valid_schema_success_message(self):
        """Valid schema shows success"""
        result = ValidationResult(
            valid=True,
            missing_properties=[],
            missing_optional_properties=[],
            missing_status_options=[],
            missing_stage_options=[],
            wrong_property_types={}
        )

        message = str(result)

        # Should contain success emoji
        assert "✅" in message or "✓" in message or "PASSED" in message

        # Should indicate success
        assert "valid" in message.lower() or "passed" in message.lower() or "success" in message.lower()

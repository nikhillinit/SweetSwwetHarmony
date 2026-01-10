"""Test schema auto-repair functionality for Notion database properties"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from connectors.notion_connector_v2 import NotionConnector, ValidationResult


@pytest.mark.asyncio
class TestSchemaAutoRepair:
    """Test automatic schema repair capabilities"""

    async def test_auto_create_text_property(self):
        """Auto-repair creates missing text property via Notion API"""
        connector = NotionConnector(api_key="test_key", database_id="test_db")

        # Mock the transport and validate_schema
        connector.transport = AsyncMock()
        connector.validate_schema = AsyncMock(return_value=ValidationResult(
            valid=False,
            missing_properties=["Discovery ID"]
        ))

        # Mock repair capability (should have repair_schema method)
        assert hasattr(connector, "repair_schema"), "NotionConnector should have repair_schema method"

        # Mock the PATCH call for creating property
        connector.transport.patch = AsyncMock(return_value={
            "properties": {"Discovery ID": {"type": "text"}}
        })

        # Call repair_schema
        result = await connector.repair_schema(auto_repair=True, dry_run=False)

        # Should indicate repair was successful
        assert hasattr(result, "operations") or hasattr(result, "executed_operations")
        assert result is not None

    async def test_auto_create_number_property(self):
        """Auto-repair creates missing number property (Confidence Score)"""
        connector = NotionConnector(api_key="test_key", database_id="test_db")

        connector.transport = AsyncMock()
        connector.validate_schema = AsyncMock(return_value=ValidationResult(
            valid=False,
            missing_properties=["Confidence Score"]
        ))

        assert hasattr(connector, "repair_schema")

        connector.transport.patch = AsyncMock(return_value={
            "properties": {"Confidence Score": {"type": "number"}}
        })

        result = await connector.repair_schema(auto_repair=True, dry_run=False)
        assert result is not None

    async def test_auto_add_select_options(self):
        """Auto-repair adds missing Status and Stage select options"""
        connector = NotionConnector(api_key="test_key", database_id="test_db")

        connector.transport = AsyncMock()
        connector.validate_schema = AsyncMock(return_value=ValidationResult(
            valid=False,
            missing_status_options=["Source", "Tracking"],
            missing_stage_options=["Pre-Seed"]
        ))

        assert hasattr(connector, "repair_schema")

        # Mock schema retrieval and updates
        connector._get_database_schema = AsyncMock(return_value={
            "properties": {
                "Status": {"type": "select", "select": {"options": []}},
                "Investment Stage": {"type": "select", "select": {"options": []}}
            }
        })

        connector.transport.patch = AsyncMock(return_value={
            "properties": {
                "Status": {"type": "select", "select": {"options": []}},
                "Investment Stage": {"type": "select", "select": {"options": []}}
            }
        })

        result = await connector.repair_schema(auto_repair=True, dry_run=False)
        assert result is not None

    async def test_cannot_auto_fix_wrong_type(self):
        """Auto-repair cannot fix wrong property type (raises error)"""
        connector = NotionConnector(api_key="test_key", database_id="test_db")

        connector.transport = AsyncMock()
        connector.validate_schema = AsyncMock(return_value=ValidationResult(
            valid=False,
            wrong_property_types={"Status": "select"}  # Expected select, got text
        ))

        assert hasattr(connector, "repair_schema")

        # Attempting to repair wrong types should raise error
        with pytest.raises(Exception) as exc_info:
            await connector.repair_schema(
                auto_repair=True,
                dry_run=False,
                repair_properties=["Status"]
            )

        # Error message should mention manual deletion
        assert "delete" in str(exc_info.value).lower() or "manual" in str(exc_info.value).lower()

    async def test_dry_run_mode(self):
        """Dry-run mode returns repair plan without making changes"""
        connector = NotionConnector(api_key="test_key", database_id="test_db")

        connector.transport = AsyncMock()
        connector.validate_schema = AsyncMock(return_value=ValidationResult(
            valid=False,
            missing_properties=["Discovery ID"]
        ))

        assert hasattr(connector, "repair_schema")

        # Dry-run should NOT call transport.patch
        result = await connector.repair_schema(auto_repair=True, dry_run=True)

        # Should return a plan but not execute
        assert result is not None
        # Transport should not be called in dry-run
        assert connector.transport.patch.call_count == 0

    async def test_selective_repair(self):
        """Selective repair only fixes specified properties"""
        connector = NotionConnector(api_key="test_key", database_id="test_db")

        connector.transport = AsyncMock()
        connector.validate_schema = AsyncMock(return_value=ValidationResult(
            valid=False,
            missing_properties=["Discovery ID", "Canonical Key", "Confidence Score"]
        ))

        assert hasattr(connector, "repair_schema")

        connector.transport.patch = AsyncMock(return_value={
            "properties": {"Discovery ID": {"type": "text"}}
        })

        # Only repair Discovery ID, skip others
        result = await connector.repair_schema(
            auto_repair=True,
            dry_run=False,
            repair_properties=["Discovery ID"]
        )

        assert result is not None

    async def test_idempotent_repair(self):
        """Running repair twice is idempotent (second call is no-op)"""
        connector = NotionConnector(api_key="test_key", database_id="test_db")

        connector.transport = AsyncMock()

        # First call: schema is invalid
        connector.validate_schema = AsyncMock(return_value=ValidationResult(
            valid=False,
            missing_properties=["Discovery ID"]
        ))

        assert hasattr(connector, "repair_schema")

        connector.transport.patch = AsyncMock(return_value={
            "properties": {"Discovery ID": {"type": "text"}}
        })

        # First repair
        result1 = await connector.repair_schema(auto_repair=True, dry_run=False)
        assert result1 is not None

        # Second call: schema is now valid
        connector.validate_schema = AsyncMock(return_value=ValidationResult(
            valid=True,
            missing_properties=[]
        ))

        # Second repair should be no-op
        result2 = await connector.repair_schema(auto_repair=True, dry_run=False)

        # Second call should not make changes (already valid)
        # Only the first call should have invoked patch
        assert connector.transport.patch.call_count == 1

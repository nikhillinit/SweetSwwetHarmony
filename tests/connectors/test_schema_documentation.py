"""Test schema documentation generation"""

import pytest
from unittest.mock import AsyncMock
from connectors.notion_connector_v2 import NotionConnector, ValidationResult


@pytest.mark.asyncio
class TestSchemaDocumentation:
    """Test schema documentation generation"""

    async def test_generate_schema_markdown(self):
        """Generate markdown documentation of Notion database schema"""
        connector = NotionConnector(api_key="test_key", database_id="test_db_123")

        # Mock schema retrieval
        connector._get_database_schema = AsyncMock(return_value={
            "id": "test_db_123",
            "properties": {
                "Name": {"type": "title"},
                "Status": {"type": "select", "select": {"options": []}},
                "Discovery ID": {"type": "rich_text"},
                "Confidence Score": {"type": "number"},
                "Investment Stage": {"type": "select", "select": {"options": []}}
            }
        })

        # Should have generate_schema_docs method
        assert hasattr(connector, "generate_schema_docs")
        assert callable(connector.generate_schema_docs)

        # Generate documentation
        docs = await connector.generate_schema_docs()

        # Should be markdown string
        assert isinstance(docs, str)
        assert len(docs) > 0

        # Should include database ID
        assert "test_db_123" in docs

        # Should mention properties
        assert "Discovery ID" in docs or "properties" in docs.lower()

    async def test_docs_include_select_options(self):
        """Documentation includes Status and Stage option values"""
        connector = NotionConnector(api_key="test_key", database_id="test_db_456")

        # Mock schema with actual select options
        connector._get_database_schema = AsyncMock(return_value={
            "id": "test_db_456",
            "properties": {
                "Status": {
                    "type": "select",
                    "select": {
                        "options": [
                            {"name": "Source", "color": "blue"},
                            {"name": "Tracking", "color": "green"}
                        ]
                    }
                },
                "Investment Stage": {
                    "type": "select",
                    "select": {
                        "options": [
                            {"name": "Pre-Seed", "color": "purple"},
                            {"name": "Seed", "color": "purple"}
                        ]
                    }
                }
            }
        })

        assert hasattr(connector, "generate_schema_docs")

        docs = await connector.generate_schema_docs()

        # Should include Status options
        assert "Source" in docs
        assert "Tracking" in docs

        # Should include Investment Stage options
        assert "Pre-Seed" in docs
        assert "Seed" in docs

    async def test_docs_highlight_missing(self):
        """Documentation highlights missing optional properties"""
        connector = NotionConnector(api_key="test_key", database_id="test_db_789")

        # Mock validation result with missing properties
        connector.validate_schema = AsyncMock(return_value=ValidationResult(
            valid=False,
            missing_properties=["Discovery ID"],
            missing_optional_properties=["Why Now", "Signal Types"]
        ))

        connector._get_database_schema = AsyncMock(return_value={
            "id": "test_db_789",
            "properties": {
                "Name": {"type": "title"}
            }
        })

        assert hasattr(connector, "generate_schema_docs")

        docs = await connector.generate_schema_docs(include_validation=True)

        # Should mention validation status
        assert "valid" in docs.lower() or "missing" in docs.lower()

        # Should list missing properties
        assert "Discovery ID" in docs or "missing" in docs.lower()
        assert "Why Now" in docs or "missing" in docs.lower()

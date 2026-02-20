"""
Shared fixtures for NotionConnector tests.

Uses mock transport to avoid real API calls.
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from connectors.notion_connector_v2 import (
    NotionConnector,
    ProspectPayload,
    InvestmentStage,
    DealStatus,
)


class MockNotionTransport:
    """Mock Notion transport for testing without real API calls."""

    def __init__(self):
        self.requests: List[Dict[str, Any]] = []
        self._responses: Dict[str, Any] = {}
        self._query_responses: List[Dict[str, Any]] = []
        self._pages: Dict[str, Dict[str, Any]] = {}
        self._database_schema: Dict[str, Any] = self._default_schema()

    def _default_schema(self) -> Dict[str, Any]:
        """Default valid database schema."""
        return {
            "properties": {
                "Company Name": {"type": "title"},
                "Status": {
                    "type": "select",
                    "select": {
                        "options": [
                            {"name": "Source"},
                            {"name": "Initial Meeting / Call"},
                            {"name": "Dilligence"},
                            {"name": "Tracking"},
                            {"name": "Committed"},
                            {"name": "Funded"},
                            {"name": "Passed"},
                            {"name": "Lost"},
                        ]
                    },
                },
                "Investment Stage": {
                    "type": "select",
                    "select": {
                        "options": [
                            {"name": "Pre-Seed"},
                            {"name": "Seed"},
                            {"name": "Seed +"},
                            {"name": "Series A"},
                        ]
                    },
                },
                "Website": {"type": "url"},
                "Discovery ID": {"type": "rich_text"},
                "Canonical Key": {"type": "rich_text"},
                "Confidence Score": {"type": "number"},
                "Signal Types": {"type": "multi_select", "multi_select": {"options": []}},
                "Why Now": {"type": "rich_text"},
                "Short Description": {"type": "rich_text"},
                "Sector": {"type": "rich_text"},
                "Proposed Sector": {"type": "rich_text"},
                "Taxonomy Status": {"type": "select", "select": {"options": []}},
                "Founder": {"type": "rich_text"},
                "Founder LinkedIn": {"type": "url"},
                "Location": {"type": "rich_text"},
                "Target Raise Amount": {"type": "rich_text"},
                "Watchlists Matched": {"type": "multi_select", "multi_select": {"options": []}},
            }
        }

    def set_response(self, path: str, response: Dict[str, Any]):
        """Set canned response for a specific path."""
        self._responses[path] = response

    def add_query_response(self, results: List[Dict[str, Any]], has_more: bool = False):
        """Add a query response to the queue."""
        self._query_responses.append({
            "results": results,
            "has_more": has_more,
            "next_cursor": "cursor" if has_more else None,
        })

    def add_page(self, page_id: str, page: Dict[str, Any]):
        """Add a page to the mock database."""
        self._pages[page_id] = page

    def set_schema(self, schema: Dict[str, Any]):
        """Override database schema."""
        self._database_schema = schema

    async def get(self, path: str) -> Dict[str, Any]:
        """Mock GET request."""
        self.requests.append({"method": "GET", "path": path})

        if "/databases/" in path and "/query" not in path:
            return self._database_schema

        if path in self._responses:
            return self._responses[path]

        return {}

    async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Mock POST request."""
        self.requests.append({"method": "POST", "path": path, "json": json})

        # Database query
        if "/query" in path:
            if self._query_responses:
                return self._query_responses.pop(0)
            return {"results": [], "has_more": False}

        # Page creation
        if path == "/pages":
            page_id = f"page-{len(self._pages) + 1}"
            page = {
                "id": page_id,
                "properties": json.get("properties", {}),
            }
            self._pages[page_id] = page
            return page

        if path in self._responses:
            return self._responses[path]

        return {"id": "mock-page-id"}

    async def patch(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Mock PATCH request."""
        self.requests.append({"method": "PATCH", "path": path, "json": json})

        # Page update
        if "/pages/" in path:
            page_id = path.split("/pages/")[1]
            if page_id in self._pages:
                self._pages[page_id]["properties"].update(json.get("properties", {}))
                return self._pages[page_id]
            return {"id": page_id, "properties": json.get("properties", {})}

        # Database update
        if "/databases/" in path:
            if "properties" in json:
                self._database_schema["properties"].update(json["properties"])
            return self._database_schema

        return {}


@pytest.fixture
def mock_transport() -> MockNotionTransport:
    """Create a mock transport for testing."""
    return MockNotionTransport()


@pytest.fixture
def connector(mock_transport: MockNotionTransport) -> NotionConnector:
    """Create NotionConnector with mock transport."""
    return NotionConnector(
        api_key="test-api-key",
        database_id="test-db-id",
        transport=mock_transport,
        validate_schema_on_init=False,
    )


@pytest.fixture
def sample_prospect() -> ProspectPayload:
    """Sample prospect payload for testing."""
    return ProspectPayload(
        discovery_id="disc-001",
        company_name="Acme Corp",
        canonical_key="domain:acme.com",
        stage=InvestmentStage.SEED,
        website="https://acme.com",
        confidence_score=0.75,
        signal_types=["funding", "launch"],
        why_now="Strong traction in consumer market",
    )


@pytest.fixture
def sample_prospect_stealth() -> ProspectPayload:
    """Stealth prospect without website."""
    return ProspectPayload(
        discovery_id="disc-002",
        company_name="Stealth Co",
        canonical_key="companies_house:12345678",
        stage=InvestmentStage.PRE_SEED,
        website="",  # No website - stealth mode
        confidence_score=0.6,
        signal_types=["incorporation"],
    )


@pytest.fixture
def existing_notion_page() -> Dict[str, Any]:
    """Sample existing Notion page."""
    return {
        "id": "existing-page-123",
        "properties": {
            "Company Name": {"title": [{"text": {"content": "Existing Corp"}}]},
            "Status": {"select": {"name": "Source"}},
            "Investment Stage": {"select": {"name": "Seed"}},
            "Website": {"url": "https://existing.com"},
            "Discovery ID": {"rich_text": [{"text": {"content": "disc-existing"}}]},
            "Canonical Key": {"rich_text": [{"text": {"content": "domain:existing.com"}}]},
        },
    }


@pytest.fixture
def suppression_page() -> Dict[str, Any]:
    """Page in suppression status."""
    return {
        "id": "suppressed-page-456",
        "properties": {
            "Company Name": {"title": [{"text": {"content": "Passed Corp"}}]},
            "Status": {"select": {"name": "Passed"}},
            "Investment Stage": {"select": {"name": "Seed"}},
            "Website": {"url": "https://passed.com"},
            "Discovery ID": {"rich_text": [{"text": {"content": "disc-passed"}}]},
            "Canonical Key": {"rich_text": [{"text": {"content": "domain:passed.com"}}]},
        },
    }

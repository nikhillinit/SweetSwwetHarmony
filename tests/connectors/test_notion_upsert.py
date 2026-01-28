"""
Tests for NotionConnector upsert operations.

Covers:
- upsert_prospect: Main upsert flow
- _create_page: Creating new deals
- _update_page: Updating existing deals
- Deduplication logic
- Suppression handling
"""

import os
import sys
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

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

from tests.connectors.conftest import MockNotionTransport


# =============================================================================
# CREATE PAGE TESTS
# =============================================================================

class TestCreatePage:
    """Tests for creating new Notion pages."""

    @pytest.mark.asyncio
    async def test_create_page_returns_created_status(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
    ):
        """upsert should return 'created' for new prospects."""
        result = await connector.upsert_prospect(sample_prospect)

        assert result["status"] == "created"
        assert result["page_id"] is not None
        assert "New deal created" in result["reason"]

    @pytest.mark.asyncio
    async def test_create_page_sets_all_required_fields(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
    ):
        """Created page should have all required fields."""
        await connector.upsert_prospect(sample_prospect)

        # Find the POST /pages request
        create_request = next(
            r for r in mock_transport.requests
            if r["method"] == "POST" and r["path"] == "/pages"
        )

        props = create_request["json"]["properties"]

        assert "Company Name" in props
        assert "Status" in props
        assert "Investment Stage" in props
        assert "Discovery ID" in props
        assert "Canonical Key" in props
        assert "Confidence Score" in props

    @pytest.mark.asyncio
    async def test_create_page_sets_default_status_source(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
    ):
        """New deals should default to 'Source' status."""
        await connector.upsert_prospect(sample_prospect)

        create_request = next(
            r for r in mock_transport.requests
            if r["method"] == "POST" and r["path"] == "/pages"
        )

        status = create_request["json"]["properties"]["Status"]["select"]["name"]
        assert status == "Source"

    @pytest.mark.asyncio
    async def test_create_page_respects_custom_status(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
    ):
        """Custom status should be used if provided."""
        sample_prospect.status = "Tracking"
        await connector.upsert_prospect(sample_prospect)

        create_request = next(
            r for r in mock_transport.requests
            if r["method"] == "POST" and r["path"] == "/pages"
        )

        status = create_request["json"]["properties"]["Status"]["select"]["name"]
        assert status == "Tracking"

    @pytest.mark.asyncio
    async def test_create_stealth_prospect_without_website(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect_stealth: ProspectPayload,
    ):
        """Stealth prospects without website should be created successfully."""
        result = await connector.upsert_prospect(sample_prospect_stealth)

        assert result["status"] == "created"

        create_request = next(
            r for r in mock_transport.requests
            if r["method"] == "POST" and r["path"] == "/pages"
        )

        # Website should not be in properties (or be empty)
        props = create_request["json"]["properties"]
        assert "Website" not in props or not props.get("Website", {}).get("url")


# =============================================================================
# UPDATE PAGE TESTS
# =============================================================================

class TestUpdatePage:
    """Tests for updating existing Notion pages."""

    @pytest.mark.asyncio
    async def test_update_by_discovery_id(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
        existing_notion_page: Dict[str, Any],
    ):
        """Should find and update by Discovery ID."""
        # Setup: existing page with same discovery ID
        sample_prospect.discovery_id = "disc-existing"
        mock_transport.add_query_response([existing_notion_page])

        result = await connector.upsert_prospect(sample_prospect)

        assert result["status"] == "updated"
        assert result["page_id"] == "existing-page-123"

    @pytest.mark.asyncio
    async def test_update_by_canonical_key(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
        existing_notion_page: Dict[str, Any],
    ):
        """Should find and update by canonical key."""
        # Different discovery ID but same canonical key
        sample_prospect.canonical_key = "domain:existing.com"
        mock_transport.add_query_response([])  # No match by discovery ID
        mock_transport.add_query_response([existing_notion_page])  # Match by canonical key

        result = await connector.upsert_prospect(sample_prospect)

        assert result["status"] == "updated"
        assert result["page_id"] == "existing-page-123"

    @pytest.mark.asyncio
    async def test_update_by_website_fallback(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
        existing_notion_page: Dict[str, Any],
    ):
        """Should fallback to website matching."""
        sample_prospect.website = "https://existing.com"
        mock_transport.add_query_response([])  # No match by discovery ID
        mock_transport.add_query_response([])  # No match by canonical key
        mock_transport.add_query_response([existing_notion_page])  # Match by website

        result = await connector.upsert_prospect(sample_prospect)

        assert result["status"] == "updated"

    @pytest.mark.asyncio
    async def test_update_only_discovery_owned_fields(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
        existing_notion_page: Dict[str, Any],
    ):
        """Update should only modify Discovery-owned fields."""
        sample_prospect.discovery_id = "disc-existing"
        mock_transport.add_query_response([existing_notion_page])
        mock_transport.add_page("existing-page-123", existing_notion_page)

        await connector.upsert_prospect(sample_prospect)

        patch_request = next(
            (r for r in mock_transport.requests if r["method"] == "PATCH"),
            None,
        )

        assert patch_request is not None
        updated_props = patch_request["json"]["properties"]

        # Should update Discovery fields
        assert "Discovery ID" in updated_props
        assert "Canonical Key" in updated_props
        assert "Confidence Score" in updated_props

        # Should NOT update user-editable fields
        assert "Company Name" not in updated_props
        assert "Status" not in updated_props
        assert "Investment Stage" not in updated_props


# =============================================================================
# SUPPRESSION TESTS
# =============================================================================

class TestSuppression:
    """Tests for suppression handling."""

    @pytest.mark.asyncio
    async def test_hard_suppress_passed_deals(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
        suppression_page: Dict[str, Any],
    ):
        """Passed deals should be hard suppressed."""
        # Setup suppression cache
        mock_transport.add_query_response([suppression_page])  # Suppression query

        # Warm up suppression cache
        await connector.get_suppression_list(force_refresh=True)

        # Try to upsert with matching canonical key
        sample_prospect.canonical_key = "domain:passed.com"

        result = await connector.upsert_prospect(sample_prospect)

        assert result["status"] == "skipped"
        assert "Hard suppressed" in result["reason"]

    @pytest.mark.asyncio
    async def test_soft_suppress_in_pipeline_deals(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
    ):
        """In-pipeline deals should be soft suppressed (updated, not skipped)."""
        # Create a "Source" deal (in pipeline, not hard suppressed)
        source_page = {
            "id": "source-page-789",
            "properties": {
                "Company Name": {"title": [{"text": {"content": "Source Corp"}}]},
                "Status": {"select": {"name": "Source"}},
                "Discovery ID": {"rich_text": [{"text": {"content": "disc-source"}}]},
                "Canonical Key": {"rich_text": [{"text": {"content": "domain:source.com"}}]},
            },
        }

        mock_transport.add_query_response([source_page])  # Suppression query
        await connector.get_suppression_list(force_refresh=True)

        sample_prospect.canonical_key = "domain:source.com"
        mock_transport.add_page("source-page-789", source_page)

        result = await connector.upsert_prospect(sample_prospect)

        assert result["status"] == "updated"
        assert "Updated in-pipeline" in result["reason"]

    @pytest.mark.asyncio
    async def test_suppression_by_discovery_id(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
        suppression_page: Dict[str, Any],
    ):
        """Should check suppression by discovery ID."""
        suppression_page["properties"]["Discovery ID"]["rich_text"][0]["text"]["content"] = "disc-match"
        mock_transport.add_query_response([suppression_page])
        await connector.get_suppression_list(force_refresh=True)

        sample_prospect.discovery_id = "disc-match"

        result = await connector.upsert_prospect(sample_prospect)

        assert result["status"] == "skipped"


# =============================================================================
# DEDUPLICATION TESTS
# =============================================================================

class TestDeduplication:
    """Tests for deduplication logic."""

    @pytest.mark.asyncio
    async def test_dedup_priority_discovery_id_first(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
    ):
        """Discovery ID should be checked first for deduplication."""
        page_by_id = {
            "id": "page-by-id",
            "properties": {
                "Discovery ID": {"rich_text": [{"text": {"content": "disc-001"}}]},
            },
        }

        mock_transport.add_query_response([page_by_id])  # Match by discovery ID

        result = await connector.upsert_prospect(sample_prospect)

        # Should match by discovery ID first
        assert result["page_id"] == "page-by-id"

    @pytest.mark.asyncio
    async def test_dedup_canonical_key_second(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
    ):
        """Canonical key should be checked second."""
        page_by_key = {
            "id": "page-by-key",
            "properties": {
                "Canonical Key": {"rich_text": [{"text": {"content": "domain:acme.com"}}]},
            },
        }

        mock_transport.add_query_response([])  # No match by discovery ID
        mock_transport.add_query_response([page_by_key])  # Match by canonical key

        result = await connector.upsert_prospect(sample_prospect)

        assert result["page_id"] == "page-by-key"

    @pytest.mark.asyncio
    async def test_dedup_website_fallback(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
    ):
        """Website should be checked as fallback."""
        page_by_website = {
            "id": "page-by-website",
            "properties": {
                "Website": {"url": "https://acme.com"},
            },
        }

        mock_transport.add_query_response([])  # No match by discovery ID
        mock_transport.add_query_response([])  # No match by canonical key
        mock_transport.add_query_response([page_by_website])  # Match by website

        result = await connector.upsert_prospect(sample_prospect)

        assert result["page_id"] == "page-by-website"


# =============================================================================
# RETRY TESTS
# =============================================================================

class TestUpsertWithRetry:
    """Tests for upsert_with_retry method."""

    @pytest.mark.asyncio
    async def test_upsert_with_retry_succeeds_first_try(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
    ):
        """Should succeed on first try."""
        result = await connector.upsert_with_retry(sample_prospect)

        assert result["status"] == "created"

    @pytest.mark.asyncio
    async def test_upsert_with_retry_on_transient_error(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
    ):
        """Should retry on transient errors."""
        upsert_attempt = 0

        original_upsert = connector.upsert_prospect

        async def failing_upsert(prospect):
            nonlocal upsert_attempt
            upsert_attempt += 1
            if upsert_attempt == 1:
                raise ConnectionError("Network error")
            return await original_upsert(prospect)

        connector.upsert_prospect = failing_upsert

        result = await connector.upsert_with_retry(
            sample_prospect,
            max_retries=3,
            initial_delay=0.01,
        )

        assert result["status"] == "created"
        assert upsert_attempt == 2  # Failed once, succeeded on retry


# =============================================================================
# EDGE CASES
# =============================================================================

class TestUpsertEdgeCases:
    """Edge cases for upsert operations."""

    @pytest.mark.asyncio
    async def test_upsert_with_empty_company_name(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should handle empty company name gracefully."""
        prospect = ProspectPayload(
            discovery_id="disc-empty",
            company_name="",  # Empty name
            canonical_key="domain:empty.com",
            stage=InvestmentStage.SEED,
        )

        result = await connector.upsert_prospect(prospect)

        # Should still create (Notion will show empty title)
        assert result["status"] == "created"

    @pytest.mark.asyncio
    async def test_upsert_with_unicode_company_name(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
    ):
        """Should handle Unicode company names."""
        prospect = ProspectPayload(
            discovery_id="disc-unicode",
            company_name="\u4e2d\u6587\u516c\u53f8",  # Chinese characters
            canonical_key="domain:chinese.com",
            stage=InvestmentStage.SEED,
        )

        result = await connector.upsert_prospect(prospect)

        assert result["status"] == "created"

    @pytest.mark.asyncio
    async def test_upsert_with_long_description(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
    ):
        """Should truncate long descriptions."""
        sample_prospect.short_description = "A" * 3000  # Over 2000 char limit

        await connector.upsert_prospect(sample_prospect)

        create_request = next(
            r for r in mock_transport.requests
            if r["method"] == "POST" and r["path"] == "/pages"
        )

        props = create_request["json"]["properties"]
        desc = props.get("Short Description", {}).get("rich_text", [{}])[0].get("text", {}).get("content", "")

        assert len(desc) <= 2000

    @pytest.mark.asyncio
    async def test_upsert_with_multiple_signal_types(
        self,
        connector: NotionConnector,
        mock_transport: MockNotionTransport,
        sample_prospect: ProspectPayload,
    ):
        """Should handle multiple signal types."""
        sample_prospect.signal_types = ["funding", "launch", "github", "press", "hiring"]

        await connector.upsert_prospect(sample_prospect)

        create_request = next(
            r for r in mock_transport.requests
            if r["method"] == "POST" and r["path"] == "/pages"
        )

        props = create_request["json"]["properties"]
        signal_types = props.get("Signal Types", {}).get("multi_select", [])

        # Should limit to 5 signal types
        assert len(signal_types) <= 5

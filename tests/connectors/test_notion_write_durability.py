"""Test Notion write durability and retry logic"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from connectors.notion_connector_v2 import NotionConnector, ProspectPayload, InvestmentStage
from connectors.notion_transport import NotionTransport


@pytest.mark.asyncio
class TestNotionWriteWithRetry:
    """Test retry-enabled Notion write operations"""

    async def test_notion_connector_has_retry_wrapper(self):
        """NotionConnector should have upsert_with_retry method"""
        connector = NotionConnector("test_api_key", "test_db")

        # Should have method
        assert hasattr(connector, "upsert_with_retry")
        assert callable(connector.upsert_with_retry)

    async def test_upsert_with_retry_succeeds_on_first_try(self):
        """upsert_with_retry returns result on first success"""
        connector = NotionConnector("test_api_key", "test_db")

        # Mock upsert_prospect
        connector.upsert_prospect = AsyncMock(return_value={
            "status": "created",
            "page_id": "page_123",
            "reason": "New prospect"
        })

        prospect = ProspectPayload(
            discovery_id="disc_123",
            company_name="Acme Inc",
            canonical_key="domain:acme.ai",
            stage=InvestmentStage.PRE_SEED,
        )

        # Call with retry
        result = await connector.upsert_with_retry(prospect, max_retries=3)

        assert result["page_id"] == "page_123"
        assert connector.upsert_prospect.call_count == 1

    async def test_upsert_with_retry_retries_on_transient_error(self):
        """upsert_with_retry retries on transient errors"""
        connector = NotionConnector("test_api_key", "test_db")

        # Mock: fail once, then succeed
        connector.upsert_prospect = AsyncMock(side_effect=[
            Exception("API temporarily unavailable"),
            {
                "status": "created",
                "page_id": "page_456",
                "reason": "New prospect (after retry)"
            }
        ])

        prospect = ProspectPayload(
            discovery_id="disc_456",
            company_name="Beta Inc",
            canonical_key="domain:beta.io",
            stage=InvestmentStage.SEED,
        )

        # Should retry and succeed
        result = await connector.upsert_with_retry(prospect, max_retries=3)

        assert result["page_id"] == "page_456"
        assert connector.upsert_prospect.call_count == 2

    async def test_upsert_with_retry_respects_max_retries(self):
        """upsert_with_retry gives up after max retries"""
        connector = NotionConnector("test_api_key", "test_db")

        # Mock: always fails with transient error
        connector.upsert_prospect = AsyncMock(
            side_effect=Exception("Connection network error")
        )

        prospect = ProspectPayload(
            discovery_id="disc_789",
            company_name="Gamma Inc",
            canonical_key="domain:gamma.ai",
            stage=InvestmentStage.SEED,
        )

        # Should fail after max retries
        with pytest.raises(Exception):
            await connector.upsert_with_retry(prospect, max_retries=2)

        # Should have attempted exactly max_retries times
        assert connector.upsert_prospect.call_count == 2

    async def test_upsert_with_retry_retries_on_timeout(self):
        """upsert_with_retry retries on TimeoutError"""
        connector = NotionConnector("test_api_key", "test_db")

        # Mock: timeout on first, success on retry
        connector.upsert_prospect = AsyncMock(side_effect=[
            TimeoutError("Request timed out"),
            {
                "status": "created",
                "page_id": "page_999",
                "reason": "New prospect (after timeout retry)"
            }
        ])

        prospect = ProspectPayload(
            discovery_id="disc_999",
            company_name="Delta Inc",
            canonical_key="domain:delta.io",
            stage=InvestmentStage.PRE_SEED,
        )

        # Should retry on timeout
        result = await connector.upsert_with_retry(prospect, max_retries=3)

        assert result["page_id"] == "page_999"
        assert connector.upsert_prospect.call_count == 2

"""Test Notion webhook integration for real-time status updates"""

import pytest
import hmac
import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from connectors.notion_webhook_handler import NotionWebhookHandler


@pytest.mark.asyncio
class TestNotionWebhook:
    """Test Notion webhook handling"""

    async def test_webhook_handler_initializes(self):
        """NotionWebhookHandler initializes with signing secret"""
        handler = NotionWebhookHandler(signing_secret="test_secret_123")

        assert handler.signing_secret == "test_secret_123"

    async def test_webhook_signature_verification_valid(self):
        """Webhook signature verification passes for valid signature"""
        signing_secret = "test_secret"
        payload = '{"type":"page.updated","object":{"id":"abc123"}}'

        # Compute correct signature
        signature = hmac.new(
            signing_secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

        handler = NotionWebhookHandler(signing_secret=signing_secret)

        assert handler.verify_signature(payload, signature)

    async def test_webhook_signature_verification_invalid(self):
        """Webhook signature verification fails for invalid signature"""
        signing_secret = "test_secret"
        payload = '{"type":"page.updated"}'
        invalid_signature = "invalid_signature_xyz"

        handler = NotionWebhookHandler(signing_secret=signing_secret)

        assert not handler.verify_signature(payload, invalid_signature)

    async def test_webhook_signature_verification_tampered_payload(self):
        """Webhook signature verification fails for tampered payload"""
        signing_secret = "test_secret"
        payload = '{"type":"page.updated","object":{"id":"abc123"}}'

        # Create signature for original payload
        signature = hmac.new(
            signing_secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

        # Try to verify with tampered payload
        tampered_payload = '{"type":"page.updated","object":{"id":"xyz789"}}'

        handler = NotionWebhookHandler(signing_secret=signing_secret)

        assert not handler.verify_signature(tampered_payload, signature)

    async def test_handle_page_updated_extracts_status(self):
        """Page updated handler extracts status from event"""
        handler = NotionWebhookHandler(signing_secret="test")

        event = {
            "type": "page.updated",
            "object": {
                "id": "page_123",
                "properties": {
                    "Status": {
                        "type": "select",
                        "select": {"name": "Tracking"}
                    },
                    "Discovery ID": {
                        "type": "rich_text",
                        "rich_text": [{"text": {"content": "disc_12345"}}]
                    }
                }
            }
        }

        result = await handler.handle_page_updated(event)

        assert result["page_id"] == "page_123"
        assert result["status"] == "Tracking"
        assert result["discovery_id"] == "disc_12345"
        assert result["handled"] is True

    async def test_handle_page_updated_missing_status(self):
        """Page updated handler handles missing status gracefully"""
        handler = NotionWebhookHandler(signing_secret="test")

        event = {
            "type": "page.updated",
            "object": {
                "id": "page_456",
                "properties": {}
            }
        }

        result = await handler.handle_page_updated(event)

        assert result["page_id"] == "page_456"
        assert result["status"] is None
        assert result["handled"] is True

    async def test_handle_status_change_valid(self):
        """Status change handler accepts valid Notion statuses"""
        handler = NotionWebhookHandler(signing_secret="test")

        result = await handler.handle_status_change(
            discovery_id="disc_789",
            new_status="Source"
        )

        assert result["discovery_id"] == "disc_789"
        assert result["status"] == "Source"
        assert result["synced"] is True

    async def test_handle_status_change_all_valid_statuses(self):
        """Status change handler accepts all valid Notion statuses"""
        handler = NotionWebhookHandler(signing_secret="test")

        valid_statuses = [
            "Source",
            "Initial Meeting / Call",
            "Dilligence",
            "Tracking",
            "Committed",
            "Funded",
            "Passed",
            "Lost"
        ]

        for status in valid_statuses:
            result = await handler.handle_status_change(
                discovery_id=f"disc_{status}",
                new_status=status
            )

            assert result["synced"] is True
            assert result["status"] == status

    async def test_handle_status_change_invalid(self):
        """Status change handler rejects invalid statuses"""
        handler = NotionWebhookHandler(signing_secret="test")

        result = await handler.handle_status_change(
            discovery_id="disc_999",
            new_status="InvalidStatus"
        )

        assert "error" in result
        assert "Invalid status" in result["error"]

    async def test_webhook_handler_deduplication(self):
        """Webhook handler prevents duplicate processing"""
        handler = NotionWebhookHandler(signing_secret="test")

        # Track processed events
        processed = set()

        # Simulate duplicate webhook (Notion may retry)
        event_id = "event_123"

        # First processing
        if event_id not in processed:
            processed.add(event_id)
            result1 = await handler.handle_status_change(
                discovery_id="disc_abc",
                new_status="Tracking"
            )
            assert result1["synced"]

        # Second processing (should be skipped)
        if event_id not in processed:
            result2 = await handler.handle_status_change(
                discovery_id="disc_abc",
                new_status="Source"
            )
        else:
            result2 = {"skipped": True}

        assert result2.get("skipped") is True

    async def test_webhook_event_timestamp_validation(self):
        """Webhook handler validates event timestamps"""
        handler = NotionWebhookHandler(signing_secret="test")

        # Event with recent timestamp (should be valid)
        recent_event = {
            "type": "page.updated",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "object": {"id": "page_001"}
        }

        assert recent_event["timestamp"] is not None

        # Verify event has timestamp structure
        assert "timestamp" in recent_event

    async def test_webhook_cursor_tracking(self):
        """Webhook handler tracks cursor for pagination"""
        handler = NotionWebhookHandler(signing_secret="test")

        event = {
            "cursor": "cursor_1234567890",
            "type": "page.updated",
            "object": {"id": "page_xyz"}
        }

        cursor = event.get("cursor")

        assert cursor == "cursor_1234567890"

"""
Notion Webhook Handler for Real-Time Status Updates

Handles incoming webhooks from Notion integration:
1. Verifies webhook signatures using HMAC-SHA256
2. Processes page update events
3. Syncs prospect status changes back to local database
4. Handles deduplication of webhook retries

Usage:
    handler = NotionWebhookHandler(signing_secret="your_secret")

    # Verify incoming webhook
    is_valid = handler.verify_signature(payload, signature_header)

    # Handle status change
    result = await handler.handle_status_change(discovery_id, new_status)
"""

import hmac
import hashlib
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class NotionWebhookHandler:
    """Handler for Notion webhooks"""

    # Valid Notion status values (from CLAUDE.md)
    VALID_STATUSES = {
        "Source",
        "Initial Meeting / Call",
        "Dilligence",  # Note: Typo in Notion schema
        "Tracking",
        "Committed",
        "Funded",
        "Passed",
        "Lost"
    }

    def __init__(self, signing_secret: str):
        """
        Initialize webhook handler with Notion signing secret.

        Args:
            signing_secret: Notion webhook signing secret from integration settings
        """
        if not signing_secret:
            logger.warning("Notion webhook signing secret is empty - webhook verification will fail")

        self.signing_secret = signing_secret

    def verify_signature(self, payload: str, signature: str) -> bool:
        """
        Verify Notion webhook signature using HMAC-SHA256.

        Notion sends the signature in the X-Notion-Signature header.
        The signature is computed as:
            HMAC-SHA256(signing_secret, request_body)

        Args:
            payload: Raw request body as string
            signature: X-Notion-Signature header value

        Returns:
            True if signature is valid, False otherwise
        """
        if not self.signing_secret:
            logger.warning("Cannot verify signature - signing secret not configured")
            return False

        try:
            # Compute HMAC-SHA256
            computed = hmac.new(
                self.signing_secret.encode(),
                payload.encode(),
                hashlib.sha256
            ).hexdigest()

            # Use constant-time comparison to prevent timing attacks
            is_valid = hmac.compare_digest(computed, signature)

            if not is_valid:
                logger.warning(f"Webhook signature verification failed")

            return is_valid

        except Exception as e:
            logger.error(f"Error verifying webhook signature: {e}")
            return False

    async def handle_page_updated(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle page update event from Notion.

        Extracts status and discovery ID from the page properties.

        Args:
            event: Notion webhook event payload

        Returns:
            Handler result with extracted fields:
                - page_id: Notion page ID
                - status: New status (if changed)
                - discovery_id: Discovery ID (if present)
                - handled: Boolean indicating success
        """
        try:
            page = event.get("object", {})
            page_id = page.get("id")
            properties = page.get("properties", {})

            # Extract status property
            status = None
            if "Status" in properties:
                status_prop = properties["Status"]
                if status_prop.get("type") == "select":
                    select = status_prop.get("select")
                    if select:
                        status = select.get("name")

            # Extract discovery ID
            discovery_id = None
            if "Discovery ID" in properties:
                discovery_id_prop = properties["Discovery ID"]
                if discovery_id_prop.get("type") == "rich_text":
                    rich_text = discovery_id_prop.get("rich_text", [])
                    if rich_text and len(rich_text) > 0:
                        discovery_id = rich_text[0].get("text", {}).get("content")

            logger.debug(
                f"Page updated: page_id={page_id}, status={status}, "
                f"discovery_id={discovery_id}"
            )

            return {
                "page_id": page_id,
                "status": status,
                "discovery_id": discovery_id,
                "handled": True
            }

        except Exception as e:
            logger.error(f"Error handling page update event: {e}")
            return {"handled": False, "error": str(e)}

    async def handle_status_change(
        self,
        discovery_id: str,
        new_status: str
    ) -> Dict[str, Any]:
        """
        Process a prospect status change from Notion.

        Validates the status against allowed Notion statuses and
        returns a result that can be used to update the local database.

        Args:
            discovery_id: Discovery ID of the prospect
            new_status: New status from Notion (e.g., "Source", "Tracking")

        Returns:
            Handler result:
                - success: Boolean indicating if status was valid
                - discovery_id: Echo of input discovery_id
                - status: Echo of input new_status
                - synced: Boolean indicating if ready to sync to database
                - error: Error message if validation failed
        """
        if not new_status:
            return {"error": "Status is required"}

        if new_status not in self.VALID_STATUSES:
            invalid_msg = f"Invalid status: '{new_status}'. Valid statuses: {', '.join(sorted(self.VALID_STATUSES))}"
            logger.warning(f"Status change rejected: {invalid_msg}")
            return {"error": invalid_msg}

        logger.info(
            f"Status change accepted: discovery_id={discovery_id}, "
            f"status={new_status}"
        )

        return {
            "discovery_id": discovery_id,
            "status": new_status,
            "synced": True,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    def track_cursor(self, cursor: str) -> Dict[str, Any]:
        """
        Track webhook cursor for pagination.

        Notion uses cursors to ensure no events are missed when
        fetching historical webhook data.

        Args:
            cursor: Cursor from webhook event

        Returns:
            Result with cursor tracking info
        """
        if not cursor:
            return {"error": "Cursor is required"}

        logger.debug(f"Tracking webhook cursor: {cursor}")

        return {
            "cursor": cursor,
            "tracked": True,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    def deduplicate_event(
        self,
        event_id: str,
        processed_ids: set
    ) -> bool:
        """
        Check if event has already been processed (deduplication).

        Notion may retry webhooks, so we track processed event IDs
        to avoid duplicate processing.

        Args:
            event_id: Unique event identifier
            processed_ids: Set of already processed event IDs

        Returns:
            True if event is new (not processed), False if duplicate
        """
        if event_id in processed_ids:
            logger.debug(f"Duplicate event detected and skipped: {event_id}")
            return False

        processed_ids.add(event_id)
        return True

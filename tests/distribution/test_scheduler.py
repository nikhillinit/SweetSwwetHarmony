"""
Tests for distribution/scheduler.py

Priority tests:
4. Outbox Idempotency - Same digest_date doesn't create duplicates
6. Event-Type Isolation - Only claims email_digest events
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from distribution.config import DistributionConfig
from distribution.scheduler import DigestScheduler, EVENT_TYPE


def make_config() -> DistributionConfig:
    """Create a test config."""
    return DistributionConfig(
        public_api_base_url="https://api.example.com",
        digest_from_email="deals@example.com",
        digest_to_emails=["gp1@example.com", "gp2@example.com"],
        email_transport="console",
    )


class TestOutboxIdempotency:
    """
    PRIORITY 4: Verify same digest_date doesn't create duplicates.

    Running scheduler twice on same day should not create duplicate events.
    """

    @pytest.mark.asyncio
    async def test_idempotency_key_format(self):
        """Idempotency key should be email_digest:{recipient}:{date}."""
        config = make_config()
        scheduler = DigestScheduler(config)

        mock_store = MagicMock()
        mock_store._db = MagicMock()

        # Track what idempotency keys are used
        enqueued_keys = []

        async def mock_enqueue(idempotency_key, payload, event_type):
            enqueued_keys.append(idempotency_key)
            return 1

        mock_store.enqueue_notion_write = mock_enqueue

        # Mock _event_exists to return False (event doesn't exist)
        scheduler._event_exists = AsyncMock(return_value=False)

        await scheduler.enqueue_digests(mock_store)

        # Check idempotency key format
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert f"email_digest:gp1@example.com:{today}" in enqueued_keys
        assert f"email_digest:gp2@example.com:{today}" in enqueued_keys

    @pytest.mark.asyncio
    async def test_duplicate_not_enqueued(self):
        """If event already exists, should not enqueue again."""
        config = make_config()
        scheduler = DigestScheduler(config)

        mock_store = MagicMock()
        enqueue_count = 0

        async def mock_enqueue(idempotency_key, payload, event_type):
            nonlocal enqueue_count
            enqueue_count += 1
            return 1

        mock_store.enqueue_notion_write = mock_enqueue

        # Mock _event_exists to return True for first recipient
        async def mock_exists(store, key):
            return "gp1@example.com" in key

        scheduler._event_exists = mock_exists

        result = await scheduler.enqueue_digests(mock_store)

        # Only gp2 should be enqueued (gp1 already exists)
        assert result == 1
        assert enqueue_count == 1

    @pytest.mark.asyncio
    async def test_unique_constraint_handled(self):
        """UNIQUE constraint error should be handled gracefully."""
        config = make_config()
        scheduler = DigestScheduler(config)

        mock_store = MagicMock()

        async def mock_enqueue(idempotency_key, payload, event_type):
            raise Exception("UNIQUE constraint failed")

        mock_store.enqueue_notion_write = mock_enqueue
        scheduler._event_exists = AsyncMock(return_value=False)

        # Should not raise, just log and continue
        result = await scheduler.enqueue_digests(mock_store)

        # Both failed due to UNIQUE constraint
        assert result == 0


class TestEventTypeIsolation:
    """
    PRIORITY 6: Verify only email_digest events are claimed.

    Notion worker should not steal digest events.
    """

    def test_event_type_constant(self):
        """EVENT_TYPE should be email_digest."""
        assert EVENT_TYPE == "email_digest"

    @pytest.mark.asyncio
    async def test_claims_only_email_digest_events(self):
        """process_pending should only claim email_digest events."""
        config = make_config()
        scheduler = DigestScheduler(config)

        mock_store = MagicMock()

        # Track what event_type is passed to claim_due_outbox
        claimed_event_type = None

        async def mock_claim(event_type, limit):
            nonlocal claimed_event_type
            claimed_event_type = event_type
            return []  # No events to process

        mock_store.claim_due_outbox = mock_claim

        await scheduler.process_pending(mock_store)

        # Should have claimed with email_digest event type
        assert claimed_event_type == "email_digest"

    @pytest.mark.asyncio
    async def test_notion_push_events_not_claimed(self):
        """Scheduler should not process notion_push events."""
        config = make_config()
        scheduler = DigestScheduler(config)

        mock_store = MagicMock()

        # Return a notion_push event (should not be processed)
        async def mock_claim(event_type, limit):
            if event_type == "email_digest":
                return []  # No digest events
            return [{"id": 1, "payload": {}, "event_type": "notion_push"}]

        mock_store.claim_due_outbox = mock_claim

        result = await scheduler.process_pending(mock_store)

        # No events processed (only claims email_digest)
        assert result == 0


class TestDigestSentLogging:
    """Tests for digest_sent action logging."""

    @pytest.mark.asyncio
    async def test_digest_sent_logged_after_send(self):
        """digest_sent action should be logged for each company after successful send."""
        config = make_config()
        scheduler = DigestScheduler(config)

        # Mock successful send
        mock_sender = MagicMock()
        mock_sender.send = AsyncMock(return_value=MagicMock(success=True, message_id="123"))
        scheduler.sender = mock_sender

        # Mock builder result
        mock_builder = MagicMock()
        mock_builder.build_weekly_digest = AsyncMock(return_value=MagicMock(
            html="<html>test</html>",
            text="test",
            company_count=2,
            company_keys=["domain:a.com", "domain:b.com"],
        ))
        scheduler.builder = mock_builder

        mock_store = MagicMock()
        mock_store.claim_due_outbox = AsyncMock(return_value=[
            {"id": 1, "payload": {"recipient": "gp@example.com"}}
        ])
        mock_store.finalize_outbox = AsyncMock()
        mock_store.log_company_action = AsyncMock()

        await scheduler.process_pending(mock_store)

        # Should log digest_sent for both companies
        assert mock_store.log_company_action.call_count == 2

        # Verify the calls
        calls = mock_store.log_company_action.call_args_list
        logged_keys = [call.kwargs["canonical_key"] for call in calls]
        assert "domain:a.com" in logged_keys
        assert "domain:b.com" in logged_keys

        # Verify actor is the recipient
        for call in calls:
            assert call.kwargs["actor"] == "gp@example.com"
            assert call.kwargs["action"] == "digest_sent"

    @pytest.mark.asyncio
    async def test_digest_sent_not_logged_on_failure(self):
        """digest_sent should NOT be logged if send fails."""
        config = make_config()
        scheduler = DigestScheduler(config)

        # Mock failed send
        mock_sender = MagicMock()
        mock_sender.send = AsyncMock(return_value=MagicMock(success=False, error="Send failed"))
        scheduler.sender = mock_sender

        mock_builder = MagicMock()
        mock_builder.build_weekly_digest = AsyncMock(return_value=MagicMock(
            html="<html>test</html>",
            text="test",
            company_count=2,
            company_keys=["domain:a.com", "domain:b.com"],
        ))
        scheduler.builder = mock_builder

        mock_store = MagicMock()
        mock_store.claim_due_outbox = AsyncMock(return_value=[
            {"id": 1, "payload": {"recipient": "gp@example.com"}}
        ])
        mock_store.finalize_outbox = AsyncMock()
        mock_store.log_company_action = AsyncMock()

        await scheduler.process_pending(mock_store)

        # Should NOT log digest_sent (send failed)
        mock_store.log_company_action.assert_not_called()

        # Should finalize with failure
        mock_store.finalize_outbox.assert_called_once()
        call_args = mock_store.finalize_outbox.call_args
        assert call_args.kwargs["success"] is False


class TestRunOnce:
    """Tests for run_once (full flow)."""

    @pytest.mark.asyncio
    async def test_run_once_returns_summary(self):
        """run_once should return summary with enqueued and sent counts."""
        config = make_config()
        scheduler = DigestScheduler(config)

        # Mock methods
        scheduler.enqueue_digests = AsyncMock(return_value=2)
        scheduler.process_pending = AsyncMock(return_value=2)

        mock_store = MagicMock()

        result = await scheduler.run_once(mock_store)

        assert result["enqueued"] == 2
        assert result["sent"] == 2
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_run_once_calls_enqueue_then_process(self):
        """run_once should call enqueue first, then process."""
        config = make_config()
        scheduler = DigestScheduler(config)

        call_order = []

        async def mock_enqueue(store):
            call_order.append("enqueue")
            return 0

        async def mock_process(store):
            call_order.append("process")
            return 0

        scheduler.enqueue_digests = mock_enqueue
        scheduler.process_pending = mock_process

        mock_store = MagicMock()

        await scheduler.run_once(mock_store)

        assert call_order == ["enqueue", "process"]

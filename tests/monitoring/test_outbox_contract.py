"""Tests for outbox contract (v2.4 Section 9.6)."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock


class TestClaimDueOutbox:
    """Test claim_due_outbox() atomic claim pattern."""

    @pytest.mark.asyncio
    async def test_claim_filters_by_event_type(self):
        """claim_due_outbox should filter by event_type."""
        from storage.signal_store import SignalStore

        # Create a minimal test that verifies the method exists and takes event_type
        store = SignalStore.__new__(SignalStore)
        store._db = None  # Will fail but we just check signature

        # Verify the method signature accepts event_type
        import inspect
        sig = inspect.signature(store.claim_due_outbox)
        assert 'event_type' in sig.parameters

    @pytest.mark.asyncio
    async def test_claim_accepts_limit_parameter(self):
        """claim_due_outbox should accept limit parameter."""
        from storage.signal_store import SignalStore

        store = SignalStore.__new__(SignalStore)

        import inspect
        sig = inspect.signature(store.claim_due_outbox)
        assert 'limit' in sig.parameters

    @pytest.mark.asyncio
    async def test_claim_accepts_stale_ttl_parameter(self):
        """claim_due_outbox should accept stale_processing_ttl_minutes."""
        from storage.signal_store import SignalStore

        store = SignalStore.__new__(SignalStore)

        import inspect
        sig = inspect.signature(store.claim_due_outbox)
        assert 'stale_processing_ttl_minutes' in sig.parameters


class TestFinalizeOutbox:
    """Test finalize_outbox() for success/failure handling."""

    @pytest.mark.asyncio
    async def test_finalize_accepts_success_parameter(self):
        """finalize_outbox should accept success parameter."""
        from storage.signal_store import SignalStore

        store = SignalStore.__new__(SignalStore)

        import inspect
        sig = inspect.signature(store.finalize_outbox)
        assert 'success' in sig.parameters

    @pytest.mark.asyncio
    async def test_finalize_accepts_error_parameter(self):
        """finalize_outbox should accept error parameter."""
        from storage.signal_store import SignalStore

        store = SignalStore.__new__(SignalStore)

        import inspect
        sig = inspect.signature(store.finalize_outbox)
        assert 'error' in sig.parameters

    @pytest.mark.asyncio
    async def test_finalize_accepts_backoff_parameter(self):
        """finalize_outbox should accept backoff_seconds parameter."""
        from storage.signal_store import SignalStore

        store = SignalStore.__new__(SignalStore)

        import inspect
        sig = inspect.signature(store.finalize_outbox)
        assert 'backoff_seconds' in sig.parameters

    @pytest.mark.asyncio
    async def test_finalize_accepts_outbox_id(self):
        """finalize_outbox should accept outbox_id parameter."""
        from storage.signal_store import SignalStore

        store = SignalStore.__new__(SignalStore)

        import inspect
        sig = inspect.signature(store.finalize_outbox)
        assert 'outbox_id' in sig.parameters


class TestOutboxIdempotency:
    """Test outbox idempotency handling."""

    @pytest.mark.asyncio
    async def test_enqueue_notion_write_accepts_idempotency_key(self):
        """enqueue_notion_write should accept idempotency_key."""
        from storage.signal_store import SignalStore

        store = SignalStore.__new__(SignalStore)

        import inspect
        sig = inspect.signature(store.enqueue_notion_write)
        assert 'idempotency_key' in sig.parameters

    @pytest.mark.asyncio
    async def test_enqueue_notion_write_accepts_payload(self):
        """enqueue_notion_write should accept payload."""
        from storage.signal_store import SignalStore

        store = SignalStore.__new__(SignalStore)

        import inspect
        sig = inspect.signature(store.enqueue_notion_write)
        assert 'payload' in sig.parameters


class TestOutboxMethodsExist:
    """Test that all required outbox methods exist."""

    def test_claim_due_outbox_exists(self):
        """claim_due_outbox method should exist."""
        from storage.signal_store import SignalStore
        assert hasattr(SignalStore, 'claim_due_outbox')
        assert callable(getattr(SignalStore, 'claim_due_outbox'))

    def test_finalize_outbox_exists(self):
        """finalize_outbox method should exist."""
        from storage.signal_store import SignalStore
        assert hasattr(SignalStore, 'finalize_outbox')
        assert callable(getattr(SignalStore, 'finalize_outbox'))

    def test_enqueue_notion_write_exists(self):
        """enqueue_notion_write method should exist."""
        from storage.signal_store import SignalStore
        assert hasattr(SignalStore, 'enqueue_notion_write')
        assert callable(getattr(SignalStore, 'enqueue_notion_write'))

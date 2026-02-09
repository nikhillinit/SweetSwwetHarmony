"""Tests for immutable audit event log (storage/audit_events.py)."""

import os
import tempfile

import pytest
import pytest_asyncio

from storage.signal_store import SignalStore
from storage.audit_events import (
    AuditEvent,
    count_events,
    get_event_by_id,
    get_events,
    record_event,
    record_event_from_context,
)


@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with temp file DB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = SignalStore(db_path=path)
    await store.initialize()
    yield store
    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


# ============================================================================
# record_event
# ============================================================================


class TestRecordEvent:
    @pytest.mark.asyncio
    async def test_basic_insert(self, store):
        event_id = await record_event(
            store,
            action_type="triage_approve",
            entity_type="signal",
            entity_id="42",
            actor_id="user-1",
            actor_email="gp@example.com",
        )
        assert isinstance(event_id, int)
        assert event_id > 0

    @pytest.mark.asyncio
    async def test_with_before_after_state(self, store):
        event_id = await record_event(
            store,
            action_type="triage_reject",
            entity_type="signal",
            entity_id="99",
            actor_id="analyst-1",
            before_state={"status": "pending"},
            after_state={"status": "rejected"},
            reason="B2B SaaS product",
        )
        event = await get_event_by_id(store, event_id)
        assert event is not None
        assert event.before_state == {"status": "pending"}
        assert event.after_state == {"status": "rejected"}
        assert event.reason == "B2B SaaS product"

    @pytest.mark.asyncio
    async def test_with_correlation_id(self, store):
        event_id = await record_event(
            store,
            action_type="batch_commit",
            entity_type="batch",
            entity_id="b-1",
            actor_id="admin-1",
            correlation_id="req-abc-123",
        )
        event = await get_event_by_id(store, event_id)
        assert event.correlation_id == "req-abc-123"

    @pytest.mark.asyncio
    async def test_with_metadata(self, store):
        event_id = await record_event(
            store,
            action_type="entity_merge",
            entity_type="company",
            entity_id="c-1",
            actor_id="admin-1",
            metadata={"winner": "c-1", "loser": "c-2", "affected_signals": 5},
        )
        event = await get_event_by_id(store, event_id)
        assert event.metadata["winner"] == "c-1"
        assert event.metadata["affected_signals"] == 5


# ============================================================================
# record_event_from_context
# ============================================================================


class TestRecordEventFromContext:
    @pytest.mark.asyncio
    async def test_with_operator_context(self, store):
        from api.auth.rbac import OperatorContext
        from api.auth.jwt_auth import Role

        ctx = OperatorContext(
            user_id="u1",
            email="gp@press.com",
            role=Role.GP,
            name="Test GP",
            request_id="req-555",
        )
        event_id = await record_event_from_context(
            store,
            action_type="triage_approve",
            entity_type="signal",
            entity_id="100",
            operator=ctx,
            reason="Strong thesis fit",
        )
        event = await get_event_by_id(store, event_id)
        assert event.actor_id == "u1"
        assert event.actor_email == "gp@press.com"
        assert event.actor_role == "gp"
        assert event.correlation_id == "req-555"
        assert event.reason == "Strong thesis fit"


# ============================================================================
# get_events
# ============================================================================


class TestGetEvents:
    @pytest.mark.asyncio
    async def test_empty_db(self, store):
        events = await get_events(store)
        assert events == []

    @pytest.mark.asyncio
    async def test_filter_by_entity(self, store):
        await record_event(
            store,
            action_type="approve",
            entity_type="signal",
            entity_id="1",
            actor_id="u1",
        )
        await record_event(
            store,
            action_type="approve",
            entity_type="batch",
            entity_id="b1",
            actor_id="u1",
        )
        signal_events = await get_events(store, entity_type="signal")
        assert len(signal_events) == 1
        assert signal_events[0].entity_id == "1"

    @pytest.mark.asyncio
    async def test_filter_by_action(self, store):
        await record_event(
            store,
            action_type="approve",
            entity_type="signal",
            entity_id="1",
            actor_id="u1",
        )
        await record_event(
            store,
            action_type="reject",
            entity_type="signal",
            entity_id="2",
            actor_id="u1",
        )
        rejects = await get_events(store, action_type="reject")
        assert len(rejects) == 1

    @pytest.mark.asyncio
    async def test_filter_by_actor(self, store):
        await record_event(
            store,
            action_type="approve",
            entity_type="signal",
            entity_id="1",
            actor_id="user-a",
        )
        await record_event(
            store,
            action_type="approve",
            entity_type="signal",
            entity_id="2",
            actor_id="user-b",
        )
        events = await get_events(store, actor_id="user-a")
        assert len(events) == 1
        assert events[0].actor_id == "user-a"

    @pytest.mark.asyncio
    async def test_limit(self, store):
        for i in range(10):
            await record_event(
                store,
                action_type="approve",
                entity_type="signal",
                entity_id=str(i),
                actor_id="u1",
            )
        events = await get_events(store, limit=3)
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_newest_first(self, store):
        await record_event(
            store,
            action_type="first",
            entity_type="signal",
            entity_id="1",
            actor_id="u1",
        )
        await record_event(
            store,
            action_type="second",
            entity_type="signal",
            entity_id="2",
            actor_id="u1",
        )
        events = await get_events(store)
        assert events[0].action_type == "second"


# ============================================================================
# count_events
# ============================================================================


class TestCountEvents:
    @pytest.mark.asyncio
    async def test_empty(self, store):
        assert await count_events(store) == 0

    @pytest.mark.asyncio
    async def test_count_by_action(self, store):
        for _ in range(5):
            await record_event(
                store,
                action_type="approve",
                entity_type="signal",
                entity_id="1",
                actor_id="u1",
            )
        for _ in range(3):
            await record_event(
                store,
                action_type="reject",
                entity_type="signal",
                entity_id="2",
                actor_id="u1",
            )
        assert await count_events(store) == 8
        assert await count_events(store, action_type="approve") == 5
        assert await count_events(store, action_type="reject") == 3


# ============================================================================
# Immutability
# ============================================================================


class TestImmutability:
    @pytest.mark.asyncio
    async def test_events_are_append_only(self, store):
        """Verify audit_events has no UPDATE/DELETE exposed in the API."""
        # The module exposes only record_event, get_events, get_event_by_id, count_events
        # No update/delete functions exist — this IS the immutability guarantee
        import storage.audit_events as mod
        public_funcs = [
            name for name in dir(mod) if not name.startswith("_") and callable(getattr(mod, name))
        ]
        for name in public_funcs:
            assert "update" not in name.lower(), f"Found update function: {name}"
            assert "delete" not in name.lower(), f"Found delete function: {name}"

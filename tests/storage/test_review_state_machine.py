"""
Tests for ReviewItem state machine (Task 7).

Covers:
- Valid state transitions succeed
- Invalid state transitions raise InvalidStateTransition
- create_review_item with ON CONFLICT DO NOTHING
- One-active-per-company enforcement
- get_review_queue filtering
- Audit trail on every transition
- Emergency halt (publish_queued -> rejected)
- Evidence bundle parsing
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore


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


@pytest_asyncio.fixture
async def store_with_review(store):
    """Store with one pending review item."""
    from storage.review_store import create_review_item

    review_id = await create_review_item(
        store, company_id="abc123", evidence_signal_ids=[1, 2, 3]
    )
    return store, review_id


# =============================================================================
# VALID TRANSITIONS
# =============================================================================

class TestValidTransitions:
    """Tests for allowed state transitions."""

    @pytest.mark.asyncio
    async def test_pending_to_approved(self, store_with_review):
        """pending -> approved is valid."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status

        await update_review_status(
            store, review_id, "approved",
            actor="analyst", reason="Looks good"
        )

        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?", (review_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == "approved"

    @pytest.mark.asyncio
    async def test_pending_to_rejected(self, store_with_review):
        """pending -> rejected is valid."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status

        await update_review_status(
            store, review_id, "rejected",
            actor="analyst", reason="Not a fit"
        )

        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?", (review_id,)
        )
        assert (await cursor.fetchone())[0] == "rejected"

    @pytest.mark.asyncio
    async def test_pending_to_deferred(self, store_with_review):
        """pending -> deferred is valid."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status

        await update_review_status(
            store, review_id, "deferred",
            actor="analyst", reason="Need more data"
        )

        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?", (review_id,)
        )
        assert (await cursor.fetchone())[0] == "deferred"

    @pytest.mark.asyncio
    async def test_approved_to_published(self, store_with_review):
        """approved -> published is valid."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status

        await update_review_status(store, review_id, "approved", actor="a", reason="ok")
        await update_review_status(store, review_id, "published", actor="pipeline", reason="pushed")

        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?", (review_id,)
        )
        assert (await cursor.fetchone())[0] == "published"

    @pytest.mark.asyncio
    async def test_approved_to_publish_queued(self, store_with_review):
        """approved -> publish_queued is valid."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status

        await update_review_status(store, review_id, "approved", actor="a", reason="ok")
        await update_review_status(store, review_id, "publish_queued", actor="batch", reason="queued")

        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?", (review_id,)
        )
        assert (await cursor.fetchone())[0] == "publish_queued"

    @pytest.mark.asyncio
    async def test_publish_queued_to_published(self, store_with_review):
        """publish_queued -> published is valid."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status

        await update_review_status(store, review_id, "approved", actor="a", reason="ok")
        await update_review_status(store, review_id, "publish_queued", actor="batch", reason="q")
        await update_review_status(store, review_id, "published", actor="pipeline", reason="done")

        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?", (review_id,)
        )
        assert (await cursor.fetchone())[0] == "published"

    @pytest.mark.asyncio
    async def test_emergency_halt(self, store_with_review):
        """publish_queued -> rejected (emergency halt) is valid."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status

        await update_review_status(store, review_id, "approved", actor="a", reason="ok")
        await update_review_status(store, review_id, "publish_queued", actor="batch", reason="q")
        await update_review_status(store, review_id, "rejected", actor="ops", reason="emergency halt")

        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?", (review_id,)
        )
        assert (await cursor.fetchone())[0] == "rejected"

    @pytest.mark.asyncio
    async def test_publish_queued_to_approved_abort_revert(self, store_with_review):
        """publish_queued -> approved (batch abort revert) is valid."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status

        await update_review_status(store, review_id, "approved", actor="a", reason="ok")
        await update_review_status(store, review_id, "publish_queued", actor="batch", reason="q")
        await update_review_status(store, review_id, "approved", actor="batch", reason="batch abort")

        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?", (review_id,)
        )
        assert (await cursor.fetchone())[0] == "approved"

    @pytest.mark.asyncio
    async def test_deferred_to_pending(self, store_with_review):
        """deferred -> pending (reopen) is valid."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status

        await update_review_status(store, review_id, "deferred", actor="a", reason="wait")
        await update_review_status(store, review_id, "pending", actor="a", reason="new evidence")

        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?", (review_id,)
        )
        assert (await cursor.fetchone())[0] == "pending"


# =============================================================================
# INVALID TRANSITIONS
# =============================================================================

class TestInvalidTransitions:
    """Tests for disallowed state transitions."""

    @pytest.mark.asyncio
    async def test_pending_to_published_invalid(self, store_with_review):
        """pending -> published should fail (must go through approved first)."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status, InvalidStateTransition

        with pytest.raises(InvalidStateTransition):
            await update_review_status(
                store, review_id, "published",
                actor="pipeline", reason="skip"
            )

    @pytest.mark.asyncio
    async def test_rejected_is_terminal(self, store_with_review):
        """rejected is terminal — no outbound transitions."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status, InvalidStateTransition

        await update_review_status(store, review_id, "rejected", actor="a", reason="no")

        with pytest.raises(InvalidStateTransition):
            await update_review_status(store, review_id, "pending", actor="a", reason="retry")

    @pytest.mark.asyncio
    async def test_published_is_terminal(self, store_with_review):
        """published is terminal — no outbound transitions."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status, InvalidStateTransition

        await update_review_status(store, review_id, "approved", actor="a", reason="ok")
        await update_review_status(store, review_id, "published", actor="p", reason="done")

        with pytest.raises(InvalidStateTransition):
            await update_review_status(store, review_id, "approved", actor="a", reason="retry")

    @pytest.mark.asyncio
    async def test_pending_to_publish_queued_invalid(self, store_with_review):
        """pending -> publish_queued should fail."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status, InvalidStateTransition

        with pytest.raises(InvalidStateTransition):
            await update_review_status(
                store, review_id, "publish_queued",
                actor="batch", reason="skip approval"
            )


# =============================================================================
# CREATE REVIEW ITEM
# =============================================================================

class TestCreateReviewItem:
    """Tests for review item creation with one-active-per-company."""

    @pytest.mark.asyncio
    async def test_create_review_returns_id(self, store):
        """create_review_item should return the new review ID."""
        from storage.review_store import create_review_item

        review_id = await create_review_item(
            store, company_id="comp1", evidence_signal_ids=[10, 20]
        )

        assert isinstance(review_id, int)
        assert review_id > 0

    @pytest.mark.asyncio
    async def test_create_review_stores_evidence_bundle(self, store):
        """Evidence bundle should contain signal_ids + schema_version."""
        from storage.review_store import create_review_item

        review_id = await create_review_item(
            store, company_id="comp1", evidence_signal_ids=[10, 20, 30]
        )

        cursor = await store._db.execute(
            "SELECT evidence_bundle FROM review_items WHERE id = ?", (review_id,)
        )
        bundle = json.loads((await cursor.fetchone())[0])
        assert bundle["signal_ids"] == [10, 20, 30]
        assert bundle["schema_version"] == 1

    @pytest.mark.asyncio
    async def test_one_active_per_company(self, store):
        """Second create for same company should return existing active review ID."""
        from storage.review_store import create_review_item

        id1 = await create_review_item(store, company_id="comp1", evidence_signal_ids=[1])
        id2 = await create_review_item(store, company_id="comp1", evidence_signal_ids=[2])

        assert id1 == id2, "Should return existing active review, not create duplicate"

    @pytest.mark.asyncio
    async def test_deferred_allows_new_review(self, store):
        """After deferring, a new review can be created for the same company."""
        from storage.review_store import create_review_item, update_review_status

        id1 = await create_review_item(store, company_id="comp1", evidence_signal_ids=[1])
        await update_review_status(store, id1, "deferred", actor="a", reason="wait")

        id2 = await create_review_item(store, company_id="comp1", evidence_signal_ids=[2])

        assert id2 != id1, "After deferral, new review should be created"

    @pytest.mark.asyncio
    async def test_rejected_allows_new_review(self, store):
        """After rejection, a new review can be created for the same company."""
        from storage.review_store import create_review_item, update_review_status

        id1 = await create_review_item(store, company_id="comp1", evidence_signal_ids=[1])
        await update_review_status(store, id1, "rejected", actor="a", reason="no")

        id2 = await create_review_item(store, company_id="comp1", evidence_signal_ids=[2])

        assert id2 != id1, "After rejection, new review should be created"

    @pytest.mark.asyncio
    async def test_different_companies_independent(self, store):
        """Different company_ids should get independent reviews."""
        from storage.review_store import create_review_item

        id1 = await create_review_item(store, company_id="comp1", evidence_signal_ids=[1])
        id2 = await create_review_item(store, company_id="comp2", evidence_signal_ids=[2])

        assert id1 != id2


# =============================================================================
# GET REVIEW QUEUE
# =============================================================================

class TestGetReviewQueue:
    """Tests for querying the review queue."""

    @pytest.mark.asyncio
    async def test_get_all_reviews(self, store):
        """get_review_queue() returns all reviews when no filter."""
        from storage.review_store import create_review_item, get_review_queue

        await create_review_item(store, "c1", [1])
        await create_review_item(store, "c2", [2])

        reviews = await get_review_queue(store)
        assert len(reviews) == 2

    @pytest.mark.asyncio
    async def test_filter_by_status(self, store):
        """get_review_queue(status=...) filters correctly."""
        from storage.review_store import create_review_item, update_review_status, get_review_queue

        id1 = await create_review_item(store, "c1", [1])
        await create_review_item(store, "c2", [2])
        await update_review_status(store, id1, "approved", actor="a", reason="ok")

        pending = await get_review_queue(store, status="pending")
        assert len(pending) == 1
        assert pending[0]["company_id"] == "c2"

        approved = await get_review_queue(store, status="approved")
        assert len(approved) == 1
        assert approved[0]["company_id"] == "c1"

    @pytest.mark.asyncio
    async def test_respects_limit(self, store):
        """get_review_queue(limit=N) caps results."""
        from storage.review_store import create_review_item, get_review_queue

        for i in range(5):
            await create_review_item(store, f"c{i}", [i])

        reviews = await get_review_queue(store, limit=3)
        assert len(reviews) == 3

    @pytest.mark.asyncio
    async def test_review_dict_fields(self, store):
        """Returned review dicts have expected fields."""
        from storage.review_store import create_review_item, get_review_queue

        await create_review_item(store, "comp1", [1, 2])

        reviews = await get_review_queue(store)
        assert len(reviews) == 1
        r = reviews[0]

        assert "id" in r
        assert "company_id" in r
        assert "status" in r
        assert "evidence_bundle" in r
        assert "created_at" in r
        assert r["company_id"] == "comp1"
        assert r["status"] == "pending"


# =============================================================================
# AUDIT TRAIL
# =============================================================================

class TestAuditTrail:
    """Tests for audit logging on state transitions."""

    @pytest.mark.asyncio
    async def test_transition_creates_audit_entry(self, store_with_review):
        """Every status transition should create an audit_log entry."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status

        await update_review_status(
            store, review_id, "approved",
            actor="analyst", reason="Strong signal"
        )

        cursor = await store._db.execute(
            """SELECT action_type, entity_type, entity_id, actor, details
               FROM audit_log
               WHERE entity_type = 'review_item' AND entity_id = ?""",
            (str(review_id),)
        )
        row = await cursor.fetchone()

        assert row is not None, "Audit entry should exist"
        assert row[0] == "status_transition"  # action_type
        assert row[1] == "review_item"  # entity_type
        assert row[2] == str(review_id)  # entity_id
        assert row[3] == "analyst"  # actor

        details = json.loads(row[4])
        assert details["reason"] == "Strong signal"

    @pytest.mark.asyncio
    async def test_halt_audit_action_type(self, store_with_review):
        """Emergency halt should capture before/after in audit details."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status

        await update_review_status(store, review_id, "approved", actor="a", reason="ok")
        await update_review_status(store, review_id, "publish_queued", actor="b", reason="q")
        await update_review_status(store, review_id, "rejected", actor="ops", reason="halt!")

        cursor = await store._db.execute(
            """SELECT action_type, details
               FROM audit_log
               WHERE entity_type = 'review_item' AND entity_id = ?
               ORDER BY id DESC LIMIT 1""",
            (str(review_id),)
        )
        row = await cursor.fetchone()
        assert row is not None

        details = json.loads(row[1])
        assert details["before"]["status"] == "publish_queued"
        assert details["after"]["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_timestamps_set_on_transition(self, store_with_review):
        """decided_at and decided_by should be set on terminal transitions."""
        store, review_id = store_with_review
        from storage.review_store import update_review_status

        await update_review_status(
            store, review_id, "approved",
            actor="analyst", reason="good"
        )

        cursor = await store._db.execute(
            "SELECT updated_at, decided_at, decided_by FROM review_items WHERE id = ?",
            (review_id,)
        )
        row = await cursor.fetchone()
        assert row[0] is not None, "updated_at should be set"
        assert row[1] is not None, "decided_at should be set"
        assert row[2] == "analyst", "decided_by should be the actor"

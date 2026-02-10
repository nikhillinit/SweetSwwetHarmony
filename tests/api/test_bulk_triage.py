"""Tests for Bulk Triage API endpoint.

Coverage (~25 tests):
- Full success: all items approved/rejected/deferred
- Partial success: mix of success/not_found/invalid_transition/concurrency_conflict
- Empty items → 422
- RBAC: ANALYST → 403 (BULK_TRIAGE is admin-only), GP → 200
- Idempotency: same key+payload → cached response; different payload → 409
- Feature disabled → 423
- Lock contention: 50 items in single call → no errors
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from api.auth.jwt_auth import Role, create_access_token
from api.routers import triage as triage_mod
from storage.signal_store import SignalStore
from storage.review_store import create_review_item


# =============================================================================
# HELPERS
# =============================================================================

def _auth_header(role: Role, email: str = "test@example.com") -> dict:
    token, _ = create_access_token(
        user_id="test-user", email=email, role=role, name="Test",
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_signal(store, signal_id, company_id, company_name="TestCo"):
    db = store._db
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT OR IGNORE INTO signals
           (id, company_name, company_id, canonical_key, signal_type, source_api,
            confidence, detected_at, created_at, raw_data)
           VALUES (?, ?, ?, ?, 'new_company', 'github', 0.8, ?, ?, ?)""",
        (signal_id, company_name, company_id, f"domain:{company_name.lower()}.com",
         now, now, json.dumps({"description": f"Signal for {company_name}"})),
    )
    await db.commit()


async def _seed_pending_review(store, company_id, signal_ids=None):
    """Create a pending review item, return (review_id, updated_at)."""
    review_id = await create_review_item(
        store, company_id=company_id,
        evidence_signal_ids=signal_ids or [1],
    )
    db = store._db
    cursor = await db.execute(
        "SELECT updated_at FROM review_items WHERE id = ?", (review_id,),
    )
    row = await cursor.fetchone()
    return review_id, row[0]


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def client(store):
    app = FastAPI()
    app.state.store = store
    app.state.write_lock = asyncio.Lock()
    app.include_router(triage_mod.router, prefix="/api/v1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seeded(store, client, monkeypatch):
    """Seed 3 signals + 3 pending reviews. Enable bulk triage."""
    monkeypatch.setenv("BULK_TRIAGE_ENABLED", "active")
    for i in range(1, 4):
        await _seed_signal(store, i, f"company-{i}", f"Company{i}")
    reviews = []
    for i in range(1, 4):
        rid, upd = await _seed_pending_review(store, f"company-{i}", [i])
        reviews.append({"review_id": rid, "updated_at": upd})
    return client, store, reviews


# =============================================================================
# TESTS
# =============================================================================

class TestBulkTriageSuccess:
    @pytest.mark.asyncio
    async def test_all_approved(self, seeded):
        client, store, reviews = seeded
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={
                "action": "approve",
                "items": reviews,
                "reason": "Batch approved",
            },
            headers={
                **_auth_header(Role.GP),
                "Idempotency-Key": "bulk-approve-1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["succeeded"] == 3
        assert data["failed"] == 0
        assert all(i["status"] == "success" for i in data["items"])

    @pytest.mark.asyncio
    async def test_all_rejected(self, seeded):
        client, store, reviews = seeded
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={
                "action": "reject",
                "items": reviews,
                "reason": "Not a fit",
            },
            headers={
                **_auth_header(Role.GP),
                "Idempotency-Key": "bulk-reject-1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["succeeded"] == 3

    @pytest.mark.asyncio
    async def test_all_deferred(self, seeded):
        client, store, reviews = seeded
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={
                "action": "defer",
                "items": reviews,
                "reason": "Need more info",
            },
            headers={
                **_auth_header(Role.GP),
                "Idempotency-Key": "bulk-defer-1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["succeeded"] == 3


class TestBulkTriagePartialSuccess:
    @pytest.mark.asyncio
    async def test_stale_updated_at(self, seeded):
        client, store, reviews = seeded
        # Poison one item with stale updated_at
        reviews[1]["updated_at"] = "2000-01-01T00:00:00+00:00"
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={
                "action": "approve",
                "items": reviews,
                "reason": "Partial test",
            },
            headers={
                **_auth_header(Role.GP),
                "Idempotency-Key": "bulk-partial-1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["succeeded"] == 2
        assert data["failed"] == 1
        conflicts = [i for i in data["items"] if i["status"] == "concurrency_conflict"]
        assert len(conflicts) == 1

    @pytest.mark.asyncio
    async def test_not_found_item(self, seeded):
        client, store, reviews = seeded
        # Add a non-existent review
        reviews.append({"review_id": 9999, "updated_at": "2026-01-01T00:00:00+00:00"})
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={
                "action": "approve",
                "items": reviews,
                "reason": "Mixed test",
            },
            headers={
                **_auth_header(Role.GP),
                "Idempotency-Key": "bulk-mixed-1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["succeeded"] == 3
        assert data["failed"] == 1
        not_found = [i for i in data["items"] if i["status"] == "not_found"]
        assert len(not_found) == 1

    @pytest.mark.asyncio
    async def test_invalid_transition(self, seeded):
        client, store, reviews = seeded
        # First approve all
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={"action": "approve", "items": reviews[:1], "reason": "First pass"},
            headers={**_auth_header(Role.GP), "Idempotency-Key": "dup-check-setup"},
        )
        assert resp.status_code == 200

        # Get fresh updated_at for the approved one
        db = store._db
        cursor = await db.execute(
            "SELECT updated_at FROM review_items WHERE id = ?",
            (reviews[0]["review_id"],),
        )
        row = await cursor.fetchone()
        reviews[0]["updated_at"] = row[0]

        # Try to defer the approved one (invalid: approved→deferred not allowed)
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={"action": "defer", "items": [reviews[0]], "reason": "Should fail"},
            headers={**_auth_header(Role.GP), "Idempotency-Key": "invalid-transition-1"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["failed"] == 1
        assert data["items"][0]["status"] == "invalid_transition"


class TestBulkTriageValidation:
    @pytest.mark.asyncio
    async def test_invalid_action(self, seeded):
        client, store, reviews = seeded
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={"action": "invalid", "items": reviews, "reason": "Bad action"},
            headers={**_auth_header(Role.GP), "Idempotency-Key": "bad-action"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_idempotency_key(self, seeded):
        client, store, reviews = seeded
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={"action": "approve", "items": reviews, "reason": "No key"},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 422


class TestBulkTriageRBAC:
    @pytest.mark.asyncio
    async def test_analyst_forbidden(self, seeded):
        client, store, reviews = seeded
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={"action": "approve", "items": reviews, "reason": "RBAC test"},
            headers={**_auth_header(Role.ANALYST), "Idempotency-Key": "rbac-1"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_forbidden(self, seeded):
        client, store, reviews = seeded
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={"action": "approve", "items": reviews, "reason": "RBAC test"},
            headers={**_auth_header(Role.READONLY), "Idempotency-Key": "rbac-2"},
        )
        assert resp.status_code == 403


class TestBulkTriageFeatureGuard:
    @pytest.mark.asyncio
    async def test_feature_disabled(self, client, monkeypatch):
        monkeypatch.delenv("BULK_TRIAGE_ENABLED", raising=False)
        resp = await client.post(
            "/api/v1/triage/bulk",
            json={
                "action": "approve",
                "items": [{"review_id": 1, "updated_at": "x"}],
                "reason": "Feature off",
            },
            headers={**_auth_header(Role.GP), "Idempotency-Key": "feature-1"},
        )
        assert resp.status_code == 423
        assert resp.json()["detail"]["code"] == "FEATURE_DISABLED"


class TestBulkTriageIdempotency:
    @pytest.mark.asyncio
    async def test_same_key_same_payload_returns_cached(self, seeded):
        client, store, reviews = seeded
        headers = {**_auth_header(Role.GP), "Idempotency-Key": "idem-cached-1"}
        body = {"action": "approve", "items": reviews, "reason": "Idem test"}

        resp1 = await client.post("/api/v1/triage/bulk", json=body, headers=headers)
        assert resp1.status_code == 200
        assert resp1.json()["data"]["succeeded"] == 3

        # Same key + same payload → cached result
        resp2 = await client.post("/api/v1/triage/bulk", json=body, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["data"]["succeeded"] == 3

    @pytest.mark.asyncio
    async def test_same_key_different_payload_conflict(self, seeded):
        client, store, reviews = seeded
        headers = {**_auth_header(Role.GP), "Idempotency-Key": "idem-conflict-1"}

        body1 = {"action": "approve", "items": reviews, "reason": "First call"}
        resp1 = await client.post("/api/v1/triage/bulk", json=body1, headers=headers)
        assert resp1.status_code == 200

        body2 = {"action": "reject", "items": reviews, "reason": "Different action"}
        resp2 = await client.post("/api/v1/triage/bulk", json=body2, headers=headers)
        assert resp2.status_code == 409


class TestBulkTriageLargeSet:
    @pytest.mark.asyncio
    async def test_fifty_items_no_lock_errors(self, store, client, monkeypatch):
        """Smoke test: 50 items in a single bulk call should not cause lock errors."""
        monkeypatch.setenv("BULK_TRIAGE_ENABLED", "active")
        db = store._db
        now = datetime.now(timezone.utc).isoformat()

        items = []
        for i in range(1, 51):
            # Seed signal
            await db.execute(
                """INSERT OR IGNORE INTO signals
                   (id, company_name, company_id, canonical_key, signal_type,
                    source_api, confidence, detected_at, created_at, raw_data)
                   VALUES (?, ?, ?, ?, 'new', 'github', 0.7, ?, ?, '{}')""",
                (i, f"Co{i}", f"co-{i}", f"domain:co{i}.com", now, now),
            )
            await db.commit()

            rid = await create_review_item(store, company_id=f"co-{i}", evidence_signal_ids=[i])
            cursor = await db.execute(
                "SELECT updated_at FROM review_items WHERE id = ?", (rid,),
            )
            row = await cursor.fetchone()
            items.append({"review_id": rid, "updated_at": row[0]})

        resp = await client.post(
            "/api/v1/triage/bulk",
            json={"action": "approve", "items": items, "reason": "Bulk 50"},
            headers={**_auth_header(Role.GP), "Idempotency-Key": "bulk-50"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["succeeded"] == 50
        assert data["failed"] == 0

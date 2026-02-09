"""Tests for Triage API Router.

Coverage (~30 tests):
- List: pagination, status/confidence/source/search filters, empty results
- Detail: found, not found, signals capped at 50, audit history
- Actions: approve/reject/defer success, RBAC enforcement, optimistic concurrency,
  idempotency cache hit, idempotency conflict, invalid transition, audit trail
"""

import asyncio
import json
import os
import sys
import tempfile

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


async def _seed_signal(
    store,
    signal_id=1,
    company_id="company-1",
    company_name="Acme Corp",
    canonical_key="domain:acme.com",
    source_api="github",
    confidence=0.8,
    detected_at="2026-01-15T12:00:00+00:00",
):
    db = store._db
    await db.execute(
        """INSERT OR IGNORE INTO signals
           (id, company_name, company_id, canonical_key, signal_type, source_api,
            confidence, detected_at, created_at, raw_data)
           VALUES (?, ?, ?, ?, 'new_company', ?, ?, ?, ?,  ?)""",
        (
            signal_id, company_name, company_id, canonical_key, source_api,
            confidence, detected_at, detected_at,
            json.dumps({"description": f"Test signal for {company_name}"}),
        ),
    )
    await db.commit()


async def _seed_review(store, company_id="company-1", signal_ids=None):
    """Create a pending review item. Returns (review_id, updated_at)."""
    review_id = await create_review_item(
        store, company_id=company_id, evidence_signal_ids=signal_ids or [1],
    )
    cursor = await store._db.execute(
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
async def seeded(store, client):
    """Seed one signal + one pending review. Returns (client, store, review_id, updated_at)."""
    await _seed_signal(store)
    review_id, updated_at = await _seed_review(store)
    return client, store, review_id, updated_at


# =============================================================================
# LIST TESTS
# =============================================================================

class TestTriageList:
    @pytest.mark.asyncio
    async def test_list_empty(self, client):
        resp = await client.get(
            "/api/v1/triage", headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"] == []
        assert data["meta"]["has_more"] is False

    @pytest.mark.asyncio
    async def test_list_returns_items(self, seeded):
        client, store, review_id, _ = seeded
        resp = await client.get(
            "/api/v1/triage", headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["review_id"] == review_id
        assert items[0]["company_id"] == "company-1"
        assert items[0]["status"] == "pending"
        assert items[0]["company_name"] == "Acme Corp"
        assert items[0]["signal_count"] == 1

    @pytest.mark.asyncio
    async def test_list_filters_by_status(self, seeded):
        client, *_ = seeded
        resp = await client.get(
            "/api/v1/triage?status=approved",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

        resp = await client.get(
            "/api/v1/triage?status=pending",
            headers=_auth_header(Role.ANALYST),
        )
        assert len(resp.json()["data"]) == 1

    @pytest.mark.asyncio
    async def test_list_pagination_with_cursor(self, store, client):
        # Seed 3 reviews
        for i in range(3):
            cid = f"company-{i}"
            await _seed_signal(
                store, signal_id=100 + i, company_id=cid,
                company_name=f"Co{i}", canonical_key=f"domain:co{i}.com",
            )
            await _seed_review(store, company_id=cid, signal_ids=[100 + i])

        # Page 1 (limit=2)
        resp = await client.get(
            "/api/v1/triage?limit=2",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        page1 = resp.json()
        assert len(page1["data"]) == 2
        assert page1["meta"]["has_more"] is True
        cursor = page1["meta"]["next_cursor"]
        assert cursor is not None

        # Page 2
        resp = await client.get(
            f"/api/v1/triage?limit=2&cursor={cursor}",
            headers=_auth_header(Role.ANALYST),
        )
        page2 = resp.json()
        assert len(page2["data"]) == 1
        assert page2["meta"]["has_more"] is False

    @pytest.mark.asyncio
    async def test_list_no_duplicates_across_pages(self, store, client):
        ids_seen = set()
        for i in range(5):
            cid = f"company-p{i}"
            await _seed_signal(
                store, signal_id=200 + i, company_id=cid,
                company_name=f"Page{i}", canonical_key=f"domain:page{i}.com",
            )
            await _seed_review(store, company_id=cid, signal_ids=[200 + i])

        cursor = None
        for _ in range(5):
            url = "/api/v1/triage?limit=2"
            if cursor:
                url += f"&cursor={cursor}"
            resp = await client.get(url, headers=_auth_header(Role.ANALYST))
            data = resp.json()
            for item in data["data"]:
                assert item["review_id"] not in ids_seen
                ids_seen.add(item["review_id"])
            cursor = data["meta"].get("next_cursor")
            if not data["meta"]["has_more"]:
                break

        assert len(ids_seen) == 5

    @pytest.mark.asyncio
    async def test_list_filters_by_min_confidence(self, store, client):
        await _seed_signal(store, signal_id=301, company_id="c-hi",
                           company_name="HiConf", confidence=0.9,
                           canonical_key="domain:hi.com")
        await _seed_signal(store, signal_id=302, company_id="c-lo",
                           company_name="LoConf", confidence=0.2,
                           canonical_key="domain:lo.com")
        await _seed_review(store, company_id="c-hi", signal_ids=[301])
        await _seed_review(store, company_id="c-lo", signal_ids=[302])

        resp = await client.get(
            "/api/v1/triage?min_confidence=0.5",
            headers=_auth_header(Role.ANALYST),
        )
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["company_name"] == "HiConf"

    @pytest.mark.asyncio
    async def test_list_filters_by_source(self, store, client):
        await _seed_signal(store, signal_id=401, company_id="c-gh",
                           company_name="GH", source_api="github",
                           canonical_key="domain:gh.com")
        await _seed_signal(store, signal_id=402, company_id="c-sec",
                           company_name="SEC", source_api="sec_edgar",
                           canonical_key="domain:sec.com")
        await _seed_review(store, company_id="c-gh", signal_ids=[401])
        await _seed_review(store, company_id="c-sec", signal_ids=[402])

        resp = await client.get(
            "/api/v1/triage?source_api=sec_edgar",
            headers=_auth_header(Role.ANALYST),
        )
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["company_name"] == "SEC"

    @pytest.mark.asyncio
    async def test_list_search_by_name(self, store, client):
        await _seed_signal(store, signal_id=501, company_id="c-acme",
                           company_name="Acme Foods",
                           canonical_key="domain:acmefoods.com")
        await _seed_signal(store, signal_id=502, company_id="c-beta",
                           company_name="Beta Health",
                           canonical_key="domain:betahealth.com")
        await _seed_review(store, company_id="c-acme", signal_ids=[501])
        await _seed_review(store, company_id="c-beta", signal_ids=[502])

        resp = await client.get(
            "/api/v1/triage?search=Foods",
            headers=_auth_header(Role.ANALYST),
        )
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["company_name"] == "Acme Foods"

    @pytest.mark.asyncio
    async def test_list_readonly_allowed(self, seeded):
        client, *_ = seeded
        resp = await client.get(
            "/api/v1/triage", headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200


# =============================================================================
# DETAIL TESTS
# =============================================================================

class TestTriageDetail:
    @pytest.mark.asyncio
    async def test_detail_found(self, seeded):
        client, store, review_id, _ = seeded
        resp = await client.get(
            f"/api/v1/triage/{review_id}",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["review_id"] == review_id
        assert data["company_id"] == "company-1"
        assert data["total_signal_count"] == 1
        assert len(data["signals"]) == 1
        assert data["signals"][0]["source_api"] == "github"

    @pytest.mark.asyncio
    async def test_detail_not_found(self, client):
        resp = await client.get(
            "/api/v1/triage/99999",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_detail_signals_capped_at_50(self, store, client):
        cid = "company-many"
        for i in range(60):
            await _seed_signal(
                store, signal_id=600 + i, company_id=cid,
                company_name="Many Signals Co",
                canonical_key="domain:many.com",
                source_api="github",
                detected_at=f"2026-01-{15 + (i % 15):02d}T{i % 24:02d}:00:00+00:00",
            )
        review_id, _ = await _seed_review(store, company_id=cid, signal_ids=[600])

        resp = await client.get(
            f"/api/v1/triage/{review_id}",
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["signals"]) == 50
        assert data["total_signal_count"] == 60

    @pytest.mark.asyncio
    async def test_detail_includes_audit_history(self, seeded):
        client, store, review_id, updated_at = seeded
        # Approve to create audit event
        resp = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "Good fit", "updated_at": updated_at},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200

        # Now check detail
        resp = await client.get(
            f"/api/v1/triage/{review_id}",
            headers=_auth_header(Role.ANALYST),
        )
        data = resp.json()["data"]
        assert len(data["audit_history"]) >= 1
        assert data["audit_history"][0]["action_type"] == "triage_approve"

    @pytest.mark.asyncio
    async def test_detail_readonly_allowed(self, seeded):
        client, _, review_id, _ = seeded
        resp = await client.get(
            f"/api/v1/triage/{review_id}",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200


# =============================================================================
# ACTION TESTS
# =============================================================================

class TestTriageActions:
    @pytest.mark.asyncio
    async def test_approve_success(self, seeded):
        client, store, review_id, updated_at = seeded
        resp = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "Strong consumer fit", "updated_at": updated_at},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["review_id"] == review_id
        assert data["action"] == "approve"
        assert data["new_status"] == "approved"
        assert data["audit_event_id"] > 0

        # Verify DB state
        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?", (review_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == "approved"

    @pytest.mark.asyncio
    async def test_reject_success(self, seeded):
        client, store, review_id, updated_at = seeded
        resp = await client.post(
            f"/api/v1/triage/{review_id}/reject",
            json={"reason": "B2B enterprise SaaS", "updated_at": updated_at},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["new_status"] == "rejected"

    @pytest.mark.asyncio
    async def test_defer_success(self, seeded):
        client, store, review_id, updated_at = seeded
        resp = await client.post(
            f"/api/v1/triage/{review_id}/defer",
            json={"reason": "Need more signals", "updated_at": updated_at},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["new_status"] == "deferred"

    @pytest.mark.asyncio
    async def test_approve_creates_audit_event(self, seeded):
        client, store, review_id, updated_at = seeded
        await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "Looks good", "updated_at": updated_at},
            headers=_auth_header(Role.ANALYST),
        )

        cursor = await store._db.execute(
            """SELECT action_type, entity_type, entity_id, actor_email, reason
               FROM audit_events
               WHERE entity_type = 'review_item' AND entity_id = ?""",
            (str(review_id),),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "triage_approve"
        assert row[1] == "review_item"
        assert row[3] == "test@example.com"
        assert row[4] == "Looks good"

    @pytest.mark.asyncio
    async def test_approve_readonly_forbidden(self, seeded):
        client, _, review_id, updated_at = seeded
        resp = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "try", "updated_at": updated_at},
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_approve_analyst_allowed(self, seeded):
        client, _, review_id, updated_at = seeded
        resp = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "consumer fit", "updated_at": updated_at},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_approve_unauthenticated_401(self, seeded):
        client, _, review_id, updated_at = seeded
        resp = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "try", "updated_at": updated_at},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_approve_missing_reason_422(self, seeded):
        client, _, review_id, updated_at = seeded
        resp = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"updated_at": updated_at},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_approve_short_reason_422(self, seeded):
        client, _, review_id, updated_at = seeded
        resp = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "ok", "updated_at": updated_at},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_approve_missing_updated_at_422(self, seeded):
        client, _, review_id, _ = seeded
        resp = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "Good consumer company"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_approve_wrong_updated_at_409(self, seeded):
        client, _, review_id, _ = seeded
        resp = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "Good fit", "updated_at": "1999-01-01T00:00:00"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "VERSION_MISMATCH"

    @pytest.mark.asyncio
    async def test_approve_not_found_404(self, client):
        resp = await client.post(
            "/api/v1/triage/99999/approve",
            json={"reason": "Good fit", "updated_at": "2026-01-01T00:00:00"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_invalid_transition_409(self, seeded):
        """After approving, approving again should fail."""
        client, _, review_id, updated_at = seeded
        # First approve
        resp = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "First approve", "updated_at": updated_at},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200

        # Get new updated_at
        new_updated_at = resp.json()["data"]["new_status"]  # need actual updated_at
        # Re-fetch from DB
        cursor = await seeded[1]._db.execute(
            "SELECT updated_at FROM review_items WHERE id = ?", (review_id,),
        )
        row = await cursor.fetchone()
        new_updated_at = row[0]

        # Second approve should fail (approved -> approved is invalid)
        resp = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "Second approve", "updated_at": new_updated_at},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "INVALID_TRANSITION"

    @pytest.mark.asyncio
    async def test_reject_after_approve_invalid(self, seeded):
        """After approving, rejecting should fail."""
        client, store, review_id, updated_at = seeded
        await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "Approve it", "updated_at": updated_at},
            headers=_auth_header(Role.ANALYST),
        )

        cursor = await store._db.execute(
            "SELECT updated_at FROM review_items WHERE id = ?", (review_id,),
        )
        new_updated_at = (await cursor.fetchone())[0]

        resp = await client.post(
            f"/api/v1/triage/{review_id}/reject",
            json={"reason": "Changed mind", "updated_at": new_updated_at},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_idempotency_hit_returns_cached(self, seeded):
        client, _, review_id, updated_at = seeded
        headers = {
            **_auth_header(Role.ANALYST),
            "Idempotency-Key": "unique-key-123",
        }
        body = {"reason": "Idempotent action", "updated_at": updated_at}

        resp1 = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json=body, headers=headers,
        )
        assert resp1.status_code == 200
        data1 = resp1.json()["data"]

        # Second call with same key returns cached
        resp2 = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json=body, headers=headers,
        )
        assert resp2.status_code == 200
        data2 = resp2.json()["data"]
        assert data2["audit_event_id"] == data1["audit_event_id"]

    @pytest.mark.asyncio
    async def test_idempotency_conflict_returns_409(self, seeded):
        client, _, review_id, updated_at = seeded
        key = "conflict-key-456"
        headers_a = {
            **_auth_header(Role.ANALYST),
            "Idempotency-Key": key,
        }

        # First call succeeds
        resp1 = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "First payload", "updated_at": updated_at},
            headers=headers_a,
        )
        assert resp1.status_code == 200

        # Same key, different payload -> 409
        resp2 = await client.post(
            f"/api/v1/triage/{review_id}/approve",
            json={"reason": "Different payload", "updated_at": updated_at},
            headers=headers_a,
        )
        assert resp2.status_code == 409

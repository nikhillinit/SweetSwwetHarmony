"""Tests for Batch Publish API Router.

Coverage (~25 tests):
- List: empty, with batches, status filter, RBAC
- Create: success, no approved reviews, multiple items, RBAC
- Preview: success, not found, items_hash matches create, RBAC
- Commit: dry-run, TOCTOU hash mismatch, not found, RBAC, delivery policy
- Abort: success, review revert, not found, double abort 409, RBAC
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
from api.routers import batch as batch_mod
from storage.signal_store import SignalStore
from storage.review_store import create_review_item, update_review_status


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
           VALUES (?, ?, ?, ?, 'new_company', ?, ?, ?, ?, ?)""",
        (
            signal_id, company_name, company_id, canonical_key, source_api,
            confidence, detected_at, detected_at,
            json.dumps({"description": f"Test signal for {company_name}"}),
        ),
    )
    await db.commit()


async def _seed_approved_review(store, company_id="company-1", signal_ids=None):
    """Create a pending review item and approve it. Returns review_id."""
    review_id = await create_review_item(
        store, company_id=company_id,
        evidence_signal_ids=signal_ids or [1],
    )
    await update_review_status(
        store, review_id, "approved",
        actor="test-seed", reason="Seeded for batch test",
    )
    return review_id


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
    app.include_router(batch_mod.router, prefix="/api/v1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seeded(store, client):
    """Seed one signal + one approved review. Returns (client, store)."""
    await _seed_signal(store)
    await _seed_approved_review(store)
    return client, store


# =============================================================================
# LIST TESTS
# =============================================================================

class TestBatchList:
    @pytest.mark.asyncio
    async def test_list_empty(self, client):
        resp = await client.get(
            "/api/v1/batches", headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"] == []

    @pytest.mark.asyncio
    async def test_list_after_create(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/batches",
            json={"limit": 10},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200

        resp = await client.get(
            "/api/v1/batches", headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["status"] == "draft"

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, seeded):
        client, store = seeded
        await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )

        resp = await client.get(
            "/api/v1/batches?status=draft",
            headers=_auth_header(Role.GP),
        )
        assert len(resp.json()["data"]) == 1

        resp = await client.get(
            "/api/v1/batches?status=committed",
            headers=_auth_header(Role.GP),
        )
        assert len(resp.json()["data"]) == 0

    @pytest.mark.asyncio
    async def test_list_readonly_allowed(self, client):
        resp = await client.get(
            "/api/v1/batches", headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_analyst_allowed(self, client):
        resp = await client.get(
            "/api/v1/batches", headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 200


# =============================================================================
# CREATE TESTS
# =============================================================================

class TestBatchCreate:
    @pytest.mark.asyncio
    async def test_create_success(self, seeded):
        client, store = seeded
        resp = await client.post(
            "/api/v1/batches",
            json={"limit": 10},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["batch_id"].startswith("batch-")
        assert data["item_count"] == 1
        assert "items_hash" in data
        assert len(data["items_hash"]) == 16

    @pytest.mark.asyncio
    async def test_create_no_approved_reviews(self, client):
        resp = await client.post(
            "/api/v1/batches",
            json={"limit": 10},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_multiple_items(self, store, client):
        for i in range(3):
            cid = f"company-{i}"
            await _seed_signal(
                store, signal_id=100 + i, company_id=cid,
                company_name=f"Co{i}", canonical_key=f"domain:co{i}.com",
            )
            await _seed_approved_review(store, company_id=cid, signal_ids=[100 + i])

        resp = await client.post(
            "/api/v1/batches",
            json={"limit": 10},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["item_count"] == 3

    @pytest.mark.asyncio
    async def test_create_gp_allowed(self, seeded):
        client, _ = seeded
        resp = await client.post(
            "/api/v1/batches",
            json={"limit": 10},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_analyst_forbidden(self, seeded):
        client, _ = seeded
        resp = await client.post(
            "/api/v1/batches",
            json={"limit": 10},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_readonly_forbidden(self, seeded):
        client, _ = seeded
        resp = await client.post(
            "/api/v1/batches",
            json={"limit": 10},
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_unauthenticated_401(self, seeded):
        client, _ = seeded
        resp = await client.post(
            "/api/v1/batches", json={"limit": 10},
        )
        assert resp.status_code == 401


# =============================================================================
# PREVIEW TESTS
# =============================================================================

class TestBatchPreview:
    @pytest.mark.asyncio
    async def test_preview_success(self, seeded):
        client, store = seeded
        create_resp = await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )
        batch_id = create_resp.json()["data"]["batch_id"]

        resp = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["batch_id"] == batch_id
        assert data["status"] == "draft"
        assert data["item_count"] == 1
        assert len(data["items"]) == 1
        assert "items_hash" in data

    @pytest.mark.asyncio
    async def test_preview_item_details(self, seeded):
        client, store = seeded
        create_resp = await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )
        batch_id = create_resp.json()["data"]["batch_id"]

        resp = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers=_auth_header(Role.GP),
        )
        item = resp.json()["data"]["items"][0]
        assert item["company_id"] == "company-1"
        assert item["status"] == "pending"

    @pytest.mark.asyncio
    async def test_preview_not_found(self, client):
        resp = await client.get(
            "/api/v1/batches/batch-nonexistent",
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_preview_readonly_allowed(self, seeded):
        client, store = seeded
        create_resp = await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )
        batch_id = create_resp.json()["data"]["batch_id"]

        resp = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_preview_items_hash_matches_create(self, seeded):
        client, store = seeded
        create_resp = await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )
        create_data = create_resp.json()["data"]
        batch_id = create_data["batch_id"]
        create_hash = create_data["items_hash"]

        resp = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers=_auth_header(Role.GP),
        )
        preview_hash = resp.json()["data"]["items_hash"]
        assert preview_hash == create_hash


# =============================================================================
# COMMIT TESTS
# =============================================================================

class TestBatchCommit:
    @pytest.mark.asyncio
    async def test_commit_dry_run_success(self, seeded):
        client, store = seeded
        create_resp = await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )
        batch_id = create_resp.json()["data"]["batch_id"]
        items_hash = create_resp.json()["data"]["items_hash"]

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": True},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["dry_run"] is True
        assert data["batch_id"] == batch_id

    @pytest.mark.asyncio
    async def test_commit_dry_run_batch_stays_draft(self, seeded):
        """Dry-run commit should not change batch status."""
        client, store = seeded
        create_resp = await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )
        batch_id = create_resp.json()["data"]["batch_id"]
        items_hash = create_resp.json()["data"]["items_hash"]

        await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": True},
            headers=_auth_header(Role.GP),
        )

        # Verify batch is still draft
        resp = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers=_auth_header(Role.GP),
        )
        assert resp.json()["data"]["status"] == "draft"

    @pytest.mark.asyncio
    async def test_commit_toctou_hash_mismatch(self, seeded):
        client, store = seeded
        create_resp = await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )
        batch_id = create_resp.json()["data"]["batch_id"]

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": "0000000000000000", "dry_run": True},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "BATCH_ITEMS_CHANGED"

    @pytest.mark.asyncio
    async def test_commit_not_found(self, client):
        resp = await client.post(
            "/api/v1/batches/batch-nonexistent/commit",
            json={"expected_items_hash": "abc", "dry_run": True},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_commit_real_delivery_policy_blocked(self, seeded):
        """Real commit (dry_run=False) with DELIVERY_MODE=staging_only should 403."""
        client, store = seeded
        create_resp = await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )
        batch_id = create_resp.json()["data"]["batch_id"]
        items_hash = create_resp.json()["data"]["items_hash"]

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": False},
            headers=_auth_header(Role.GP),
        )
        # staging_only blocks real commits
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_commit_analyst_forbidden(self, seeded):
        client, _ = seeded
        resp = await client.post(
            "/api/v1/batches/batch-x/commit",
            json={"expected_items_hash": "abc", "dry_run": True},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_commit_readonly_forbidden(self, seeded):
        client, _ = seeded
        resp = await client.post(
            "/api/v1/batches/batch-x/commit",
            json={"expected_items_hash": "abc", "dry_run": True},
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_commit_missing_hash_422(self, seeded):
        client, _ = seeded
        resp = await client.post(
            "/api/v1/batches/batch-x/commit",
            json={"dry_run": True},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 422


# =============================================================================
# ABORT TESTS
# =============================================================================

class TestBatchAbort:
    @pytest.mark.asyncio
    async def test_abort_success(self, seeded):
        client, store = seeded
        create_resp = await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )
        batch_id = create_resp.json()["data"]["batch_id"]

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/abort",
            json={"reason": "Changed my mind"},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["batch_id"] == batch_id
        assert data["reverted_count"] == 1

    @pytest.mark.asyncio
    async def test_abort_reviews_revert_to_approved(self, seeded):
        """After abort, reviews should revert from publish_queued to approved."""
        client, store = seeded
        create_resp = await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )
        batch_id = create_resp.json()["data"]["batch_id"]

        # Verify review is publish_queued after batch create
        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE company_id = 'company-1'"
        )
        row = await cursor.fetchone()
        assert row[0] == "publish_queued"

        # Abort
        await client.post(
            f"/api/v1/batches/{batch_id}/abort",
            json={"reason": "Reverting"},
            headers=_auth_header(Role.GP),
        )

        # Verify review reverted to approved
        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE company_id = 'company-1'"
        )
        row = await cursor.fetchone()
        assert row[0] == "approved"

    @pytest.mark.asyncio
    async def test_abort_batch_shows_aborted_status(self, seeded):
        """After abort, batch status should be aborted."""
        client, store = seeded
        create_resp = await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )
        batch_id = create_resp.json()["data"]["batch_id"]

        await client.post(
            f"/api/v1/batches/{batch_id}/abort",
            json={"reason": "Done"},
            headers=_auth_header(Role.GP),
        )

        resp = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers=_auth_header(Role.GP),
        )
        assert resp.json()["data"]["status"] == "aborted"

    @pytest.mark.asyncio
    async def test_abort_not_found(self, client):
        resp = await client.post(
            "/api/v1/batches/batch-nonexistent/abort",
            json={"reason": "Gone"},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_abort_double_abort_409(self, seeded):
        """Aborting an already-aborted batch should fail."""
        client, store = seeded
        create_resp = await client.post(
            "/api/v1/batches", json={}, headers=_auth_header(Role.GP),
        )
        batch_id = create_resp.json()["data"]["batch_id"]

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/abort",
            json={"reason": "First abort"},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 200

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/abort",
            json={"reason": "Second abort"},
            headers=_auth_header(Role.GP),
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_abort_analyst_forbidden(self, seeded):
        client, _ = seeded
        resp = await client.post(
            "/api/v1/batches/batch-x/abort",
            json={"reason": "try"},
            headers=_auth_header(Role.ANALYST),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_abort_readonly_forbidden(self, seeded):
        client, _ = seeded
        resp = await client.post(
            "/api/v1/batches/batch-x/abort",
            json={},
            headers=_auth_header(Role.READONLY),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_abort_unauthenticated_401(self, seeded):
        client, _ = seeded
        resp = await client.post(
            "/api/v1/batches/batch-x/abort",
            json={},
        )
        assert resp.status_code == 401

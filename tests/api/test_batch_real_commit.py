"""Tests for batch commit real path (M3.2).

Coverage:
- dry_run=True remains non-mutating (regression)
- dry_run=False + DELIVERY_MODE=staging_only -> 423 FEATURE_DISABLED
- dry_run=False + DELIVERY_MODE=batch_publish + mock pusher -> items pushed
- dry_run=False + notion_connector=None -> 503 NOTION_NOT_CONFIGURED
- dry_run=False + pusher error on one item -> committed_with_errors
- TOCTOU: stale hash -> 409 BATCH_ITEMS_CHANGED
- Batch status transitions: draft -> committing -> committed
- Error envelope shape verification
"""

import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from api.auth.jwt_auth import Role, create_access_token
from api.routers import batch as batch_mod
from storage.signal_store import SignalStore
from storage.review_store import create_review_item, update_review_status
from verification.verification_gate_v2 import PushDecision
from workflows.notion_pusher import PushResult


# =============================================================================
# HELPERS
# =============================================================================

def _auth_header(role: Role = Role.GP) -> dict:
    token, _ = create_access_token(
        user_id="test-user", email="test@example.com", role=role, name="Test",
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_signal(store, signal_id=1, company_id="company-1",
                       canonical_key="domain:acme.com", company_name="Acme Corp",
                       confidence=0.8):
    db = store._db
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT OR IGNORE INTO signals
           (id, company_name, company_id, canonical_key, signal_type, source_api,
            confidence, detected_at, created_at, raw_data)
           VALUES (?, ?, ?, ?, 'new_company', 'github', ?, ?, ?, ?)""",
        (signal_id, company_name, company_id, canonical_key,
         confidence, now, now,
         json.dumps({"description": f"Test signal for {company_name}"})),
    )
    # company_files for canonical_key lookup in batch
    await db.execute(
        """INSERT OR IGNORE INTO company_files
           (company_id, company_name, canonical_key, status, source_apis,
            first_seen_at, last_seen_at)
           VALUES (?, ?, ?, 'promoted', '["github"]', ?, ?)""",
        (company_id, company_name, canonical_key, now, now),
    )
    await db.commit()


async def _seed_approved_review(store, company_id="company-1", signal_ids=None):
    review_id = await create_review_item(
        store, company_id=company_id,
        evidence_signal_ids=signal_ids or [1],
    )
    await update_review_status(
        store, review_id, "approved", actor="test-seed",
    )
    return review_id


def _make_push_result(canonical_key="domain:acme.com", pushed=True,
                      notion_page_id="page-123", confidence=0.8):
    return PushResult(
        canonical_key=canonical_key,
        company_name="Acme Corp",
        decision=PushDecision.AUTO_PUSH,
        confidence=confidence,
        pushed=pushed,
        notion_page_id=notion_page_id,
    )


def _make_error_push_result(canonical_key="domain:acme.com", error="API timeout"):
    return PushResult(
        canonical_key=canonical_key,
        company_name="Acme Corp",
        decision=PushDecision.REJECT,
        confidence=0.0,
        pushed=False,
        error=error,
    )


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
async def app_no_notion(store):
    """FastAPI app with notion_connector=None (simulates missing env vars)."""
    app = FastAPI()
    app.state.store = store
    app.state.write_lock = asyncio.Lock()
    app.state.notion_connector = None
    app.state.notion_transport = None
    app.include_router(batch_mod.router, prefix="/api/v1")
    return app


@pytest_asyncio.fixture
async def app_with_notion(store):
    """FastAPI app with a mock notion_connector."""
    app = FastAPI()
    app.state.store = store
    app.state.write_lock = asyncio.Lock()
    app.state.notion_connector = MagicMock()  # non-None sentinel
    app.state.notion_transport = MagicMock()
    app.include_router(batch_mod.router, prefix="/api/v1")
    return app


@pytest_asyncio.fixture
async def client_no_notion(app_no_notion):
    transport = httpx.ASGITransport(app=app_no_notion)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def client_with_notion(app_with_notion):
    transport = httpx.ASGITransport(app=app_with_notion)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seeded_no_notion(store, client_no_notion):
    await _seed_signal(store)
    await _seed_approved_review(store)
    return client_no_notion, store


@pytest_asyncio.fixture
async def seeded_with_notion(store, client_with_notion):
    await _seed_signal(store)
    await _seed_approved_review(store)
    return client_with_notion, store


async def _create_batch(client):
    """Helper: create a batch and return (batch_id, items_hash)."""
    resp = await client.post(
        "/api/v1/batches", json={"limit": 50}, headers=_auth_header(),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    return data["batch_id"], data["items_hash"]


# =============================================================================
# DRY RUN REGRESSION
# =============================================================================

class TestDryRunRegression:
    """dry_run=True remains non-mutating regardless of connector presence."""

    @pytest.mark.asyncio
    async def test_dry_run_no_notion_still_works(self, seeded_no_notion):
        """Dry-run should succeed even with notion_connector=None."""
        client, store = seeded_no_notion
        batch_id, items_hash = await _create_batch(client)

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": True},
            headers=_auth_header(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["dry_run"] is True
        assert data["pending_count"] == 1

    @pytest.mark.asyncio
    async def test_dry_run_with_notion_still_works(self, seeded_with_notion):
        """Dry-run should succeed with notion_connector present."""
        client, store = seeded_with_notion
        batch_id, items_hash = await _create_batch(client)

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": True},
            headers=_auth_header(),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["dry_run"] is True

    @pytest.mark.asyncio
    async def test_dry_run_leaves_batch_draft(self, seeded_no_notion):
        """Batch status should remain draft after dry-run."""
        client, store = seeded_no_notion
        batch_id, items_hash = await _create_batch(client)

        await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": True},
            headers=_auth_header(),
        )

        # Verify batch still draft
        resp = await client.get(
            f"/api/v1/batches/{batch_id}", headers=_auth_header(),
        )
        assert resp.json()["data"]["status"] == "draft"


# =============================================================================
# NOTION NOT CONFIGURED (503)
# =============================================================================

class TestNotionNotConfigured:
    """dry_run=False with notion_connector=None -> 503."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"})
    async def test_real_commit_no_notion_returns_503(self, seeded_no_notion):
        client, store = seeded_no_notion
        batch_id, items_hash = await _create_batch(client)

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": False},
            headers=_auth_header(),
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "NOTION_NOT_CONFIGURED"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"})
    async def test_503_error_envelope_shape(self, seeded_no_notion):
        """Verify error envelope matches contracts.py pattern."""
        client, store = seeded_no_notion
        batch_id, items_hash = await _create_batch(client)

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": False},
            headers=_auth_header(),
        )
        body = resp.json()
        assert "detail" in body
        assert body["detail"]["error"] == "service_unavailable"
        assert body["detail"]["code"] == "NOTION_NOT_CONFIGURED"
        assert "message" in body["detail"]


# =============================================================================
# DELIVERY POLICY (423)
# =============================================================================

class TestDeliveryPolicyBlocks:
    """DELIVERY_MODE=staging_only blocks real commits."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "staging_only"})
    async def test_staging_blocks_real_commit_423(self, seeded_with_notion):
        """Real commit in staging_only -> 423 FEATURE_DISABLED."""
        client, store = seeded_with_notion
        batch_id, items_hash = await _create_batch(client)

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": False},
            headers=_auth_header(),
        )
        assert resp.status_code == 423
        assert resp.json()["detail"]["code"] == "FEATURE_DISABLED"


# =============================================================================
# TOCTOU GUARD (409)
# =============================================================================

class TestTOCTOU:
    """Stale items_hash -> 409 BATCH_ITEMS_CHANGED."""

    @pytest.mark.asyncio
    async def test_stale_hash_returns_409(self, seeded_with_notion):
        client, store = seeded_with_notion
        batch_id, _ = await _create_batch(client)

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": "0000000000000000", "dry_run": False},
            headers=_auth_header(),
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "BATCH_ITEMS_CHANGED"


# =============================================================================
# REAL COMMIT WITH MOCK PUSHER
# =============================================================================

class TestRealCommitMockPusher:
    """dry_run=False + DELIVERY_MODE=batch_publish + mock pusher."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"})
    async def test_successful_push_stores_notion_page_id(self, seeded_with_notion):
        """Successful push should store notion_page_id on batch_item."""
        client, store = seeded_with_notion
        batch_id, items_hash = await _create_batch(client)

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = AsyncMock(
            return_value=_make_push_result(),
        )

        with patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            with patch("verification.verification_gate_v2.VerificationGate"):
                resp = await client.post(
                    f"/api/v1/batches/{batch_id}/commit",
                    json={"expected_items_hash": items_hash, "dry_run": False},
                    headers=_auth_header(),
                )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["dry_run"] is False
        assert data["pushed_count"] == 1
        assert data["error_count"] == 0
        assert data["final_status"] == "committed"

        # Verify notion_page_id stored on batch_item
        cursor = await store._db.execute(
            "SELECT notion_page_id FROM batch_items WHERE batch_id = ?",
            (batch_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == "page-123"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"})
    async def test_batch_status_transitions_to_committed(self, seeded_with_notion):
        """Batch status should transition from draft to committed."""
        client, store = seeded_with_notion
        batch_id, items_hash = await _create_batch(client)

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = AsyncMock(
            return_value=_make_push_result(),
        )

        with patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            with patch("verification.verification_gate_v2.VerificationGate"):
                await client.post(
                    f"/api/v1/batches/{batch_id}/commit",
                    json={"expected_items_hash": items_hash, "dry_run": False},
                    headers=_auth_header(),
                )

        cursor = await store._db.execute(
            "SELECT status FROM publish_batches WHERE id = ?", (batch_id,),
        )
        assert (await cursor.fetchone())[0] == "committed"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"})
    async def test_multiple_items_all_pushed(self, store, client_with_notion):
        """Multiple items should all be pushed successfully."""
        for i in range(3):
            cid = f"company-{i}"
            await _seed_signal(
                store, signal_id=100 + i, company_id=cid,
                canonical_key=f"domain:co{i}.com", company_name=f"Co{i}",
            )
            await _seed_approved_review(store, company_id=cid, signal_ids=[100 + i])

        batch_id, items_hash = await _create_batch(client_with_notion)

        call_count = 0

        async def _mock_push(canonical_key, intent=None):
            nonlocal call_count
            call_count += 1
            return _make_push_result(
                canonical_key=canonical_key,
                notion_page_id=f"page-{call_count}",
            )

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = _mock_push

        with patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            with patch("verification.verification_gate_v2.VerificationGate"):
                resp = await client_with_notion.post(
                    f"/api/v1/batches/{batch_id}/commit",
                    json={"expected_items_hash": items_hash, "dry_run": False},
                    headers=_auth_header(),
                )

        data = resp.json()["data"]
        assert data["pushed_count"] == 3
        assert data["error_count"] == 0
        assert data["final_status"] == "committed"


# =============================================================================
# PARTIAL FAILURE (committed_with_errors)
# =============================================================================

class TestPartialFailure:
    """Pusher error on one item -> committed_with_errors."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"})
    async def test_one_error_produces_committed_with_errors(self, store, client_with_notion):
        """If one item fails, batch status should be committed_with_errors."""
        # Seed 2 items
        for i in range(2):
            cid = f"company-{i}"
            await _seed_signal(
                store, signal_id=200 + i, company_id=cid,
                canonical_key=f"domain:partial{i}.com", company_name=f"Partial{i}",
            )
            await _seed_approved_review(store, company_id=cid, signal_ids=[200 + i])

        batch_id, items_hash = await _create_batch(client_with_notion)

        call_count = 0

        async def _mock_push(canonical_key, intent=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_push_result(
                    canonical_key=canonical_key,
                    notion_page_id="page-ok",
                )
            else:
                raise RuntimeError("Notion API timeout")

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = _mock_push

        with patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            with patch("verification.verification_gate_v2.VerificationGate"):
                resp = await client_with_notion.post(
                    f"/api/v1/batches/{batch_id}/commit",
                    json={"expected_items_hash": items_hash, "dry_run": False},
                    headers=_auth_header(),
                )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["pushed_count"] == 1
        assert data["error_count"] == 1
        assert data["final_status"] == "committed_with_errors"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"})
    async def test_error_item_stores_error_message(self, store, client_with_notion):
        """Failed item should have error_message stored."""
        await _seed_signal(store, signal_id=300, company_id="err-co",
                           canonical_key="domain:err.com", company_name="ErrCorp")
        await _seed_approved_review(store, company_id="err-co", signal_ids=[300])

        batch_id, items_hash = await _create_batch(client_with_notion)

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = AsyncMock(
            side_effect=RuntimeError("Connection refused"),
        )

        with patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            with patch("verification.verification_gate_v2.VerificationGate"):
                resp = await client_with_notion.post(
                    f"/api/v1/batches/{batch_id}/commit",
                    json={"expected_items_hash": items_hash, "dry_run": False},
                    headers=_auth_header(),
                )

        assert resp.status_code == 200
        assert resp.json()["data"]["error_count"] == 1

        cursor = await store._db.execute(
            "SELECT error_message FROM batch_items WHERE batch_id = ?",
            (batch_id,),
        )
        row = await cursor.fetchone()
        assert "Connection refused" in row[0]


# =============================================================================
# PUSH RESULT: NOT PUSHED (decision=REJECT)
# =============================================================================

class TestPushNotPushed:
    """Pusher returns pushed=False (rejected by confidence, etc.)."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"})
    async def test_rejected_by_gate_recorded_as_error(self, seeded_with_notion):
        """If pusher returns pushed=False, item status should be error."""
        client, store = seeded_with_notion
        batch_id, items_hash = await _create_batch(client)

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = AsyncMock(
            return_value=_make_error_push_result(error="Insufficient confidence"),
        )

        with patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            with patch("verification.verification_gate_v2.VerificationGate"):
                resp = await client.post(
                    f"/api/v1/batches/{batch_id}/commit",
                    json={"expected_items_hash": items_hash, "dry_run": False},
                    headers=_auth_header(),
                )

        data = resp.json()["data"]
        assert data["pushed_count"] == 0
        assert data["error_count"] == 1
        assert data["final_status"] == "committed_with_errors"


# =============================================================================
# AUDIT LOG VERIFICATION
# =============================================================================

class TestAuditLog:
    """Verify audit trail for real commits."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"})
    async def test_real_commit_creates_audit_entry(self, seeded_with_notion):
        client, store = seeded_with_notion
        batch_id, items_hash = await _create_batch(client)

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = AsyncMock(
            return_value=_make_push_result(),
        )

        with patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            with patch("verification.verification_gate_v2.VerificationGate"):
                await client.post(
                    f"/api/v1/batches/{batch_id}/commit",
                    json={"expected_items_hash": items_hash, "dry_run": False},
                    headers=_auth_header(),
                )

        cursor = await store._db.execute(
            """SELECT action_type, details FROM audit_log
               WHERE entity_id = ? AND action_type = 'batch_commit'""",
            (batch_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        details = json.loads(row[1])
        assert details["pushed_count"] == 1
        assert details["final_status"] == "committed"

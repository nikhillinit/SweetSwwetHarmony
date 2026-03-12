"""Delivery mode progression integration test (M3.3).

Verifies the full flow across delivery modes:
- staging_only: blocks all real commits (423)
- manual_publish: allows single push, blocks batch commit (423)
- batch_publish: allows batch commit with mock pusher
- Error envelopes are consistent across all delivery policy violations
"""

import asyncio
import json
import os
import sys
import tempfile
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

def _auth_header() -> dict:
    token, _ = create_access_token(
        user_id="test-user", email="test@example.com", role=Role.GP, name="Test",
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed(store, count=1):
    """Seed count signals + approved reviews."""
    now = "2026-01-15T12:00:00+00:00"
    for i in range(count):
        cid = f"company-{i}"
        ckey = f"domain:prog{i}.com"
        await store._db.execute(
            """INSERT OR IGNORE INTO signals
               (id, company_name, company_id, canonical_key, signal_type, source_api,
                confidence, detected_at, created_at, raw_data)
               VALUES (?, ?, ?, ?, 'new_company', 'github', 0.8, ?, ?, ?)""",
            (500 + i, f"ProgCo{i}", cid, ckey, now, now,
             json.dumps({"test": True})),
        )
        await store._db.execute(
            """INSERT OR IGNORE INTO company_files
               (company_id, company_name, canonical_key, status, source_apis,
                first_seen_at, last_seen_at)
               VALUES (?, ?, ?, 'promoted', '["github"]', ?, ?)""",
            (cid, f"ProgCo{i}", ckey, now, now),
        )
    await store._db.commit()

    for i in range(count):
        cid = f"company-{i}"
        rid = await create_review_item(store, company_id=cid, evidence_signal_ids=[500 + i])
        await update_review_status(store, rid, "approved", actor="test")


def _make_push_result(canonical_key):
    return PushResult(
        canonical_key=canonical_key, company_name="Test",
        decision=PushDecision.AUTO_PUSH, confidence=0.8,
        pushed=True, notion_page_id=f"page-{canonical_key}",
    )


def _make_ready_gate_result():
    gate = MagicMock()
    gate.verdict = "ready"
    gate.to_dict.return_value = {"verdict": "ready"}
    return gate


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
    app.state.notion_connector = MagicMock()
    app.state.notion_transport = MagicMock()
    app.include_router(batch_mod.router, prefix="/api/v1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture(autouse=True)
async def ready_activation_gate():
    # Delivery-mode tests should isolate policy precedence from gate readiness.
    with patch(
        "monitoring.activation_gate.check_activation_readiness",
        new_callable=AsyncMock,
        return_value=_make_ready_gate_result(),
    ):
        yield


async def _create_batch(client):
    resp = await client.post(
        "/api/v1/batches", json={"limit": 50}, headers=_auth_header(),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    return data["batch_id"], data["items_hash"]


# =============================================================================
# TESTS
# =============================================================================

class TestDeliveryModeProgression:
    """Test delivery mode progression: staging_only -> manual_publish -> batch_publish."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "staging_only"})
    async def test_staging_only_blocks_batch_commit(self, store, client):
        """staging_only mode should block real batch commit with 423."""
        await _seed(store)
        batch_id, items_hash = await _create_batch(client)

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": False},
            headers=_auth_header(),
        )
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert detail["code"] == "FEATURE_DISABLED"
        assert detail["error"] == "locked"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "staging_only"})
    async def test_staging_only_allows_dry_run(self, store, client):
        """staging_only should still allow dry-run commits."""
        await _seed(store)
        batch_id, items_hash = await _create_batch(client)

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": True},
            headers=_auth_header(),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["dry_run"] is True

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "manual_publish"})
    async def test_manual_publish_blocks_batch_commit(self, store, client):
        """manual_publish allows MANUAL_PUSH but not BATCH_PUSH -> 423."""
        await _seed(store)
        batch_id, items_hash = await _create_batch(client)

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = AsyncMock(
            return_value=_make_push_result("domain:prog0.com"),
        )

        with patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            with patch("verification.verification_gate_v2.VerificationGate"):
                resp = await client.post(
                    f"/api/v1/batches/{batch_id}/commit",
                    json={"expected_items_hash": items_hash, "dry_run": False},
                    headers=_auth_header(),
                )

        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert detail["code"] == "FEATURE_DISABLED"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"})
    async def test_batch_publish_allows_batch_commit(self, store, client):
        """batch_publish mode should allow real batch commit."""
        await _seed(store)
        batch_id, items_hash = await _create_batch(client)

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = AsyncMock(
            return_value=_make_push_result("domain:prog0.com"),
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
        assert data["final_status"] == "committed"


class TestErrorEnvelopeConsistency:
    """All delivery policy violations should produce consistent error envelopes."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "staging_only"})
    async def test_423_envelope_has_required_fields(self, store, client):
        await _seed(store)
        batch_id, items_hash = await _create_batch(client)

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": False},
            headers=_auth_header(),
        )
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert "error" in detail
        assert "code" in detail
        assert "message" in detail

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DELIVERY_MODE": "manual_publish"})
    async def test_manual_publish_423_envelope_consistent(self, store, client):
        """manual_publish 423 should have same shape as staging_only 423."""
        await _seed(store)
        batch_id, items_hash = await _create_batch(client)

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = AsyncMock(
            return_value=_make_push_result("domain:prog0.com"),
        )

        with patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            with patch("verification.verification_gate_v2.VerificationGate"):
                resp = await client.post(
                    f"/api/v1/batches/{batch_id}/commit",
                    json={"expected_items_hash": items_hash, "dry_run": False},
                    headers=_auth_header(),
                )

        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert detail["error"] == "locked"
        assert detail["code"] == "FEATURE_DISABLED"
        assert "message" in detail

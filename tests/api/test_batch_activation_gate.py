"""
Tests for batch commit hard gate (Step 8).

The activation gate is HARD: non-ready verdicts raise ActivationGateError (HTTP 423)
unless override_reason is provided.

Tests:
- Real commit with ready gate includes gate metadata in response
- Canary fail without override returns 423 ACTIVATION_GATE_BLOCKED
- Canary fail with override_reason proceeds (200) + audit event
- No canary without override returns 423
- No canary with override proceeds (200)
- Timeout without override returns 423 (fail-closed)
- Dry-run skips gate entirely
- Gate runs after delivery policy check (error priority preserved)
- Override audit event contains verdict + gate_details
- HTTP 423 envelope matches error_response shape
"""

import asyncio
import json
import os
import sys
import tempfile
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
        user_id="test-user", email="gate@test.com", role=role, name="GateTest",
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_signal(store, signal_id=1, company_id="company-gate",
                       canonical_key="domain:gate-test.com", company_name="Gate Corp"):
    db = store._db
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT OR IGNORE INTO signals
           (id, company_name, company_id, canonical_key, signal_type, source_api,
            confidence, detected_at, created_at, raw_data)
           VALUES (?, ?, ?, ?, 'new_company', 'github', 0.8, ?, ?, ?)""",
        (signal_id, company_name, company_id, canonical_key,
         now, now, json.dumps({"description": f"Test signal for {company_name}"})),
    )
    await db.execute(
        """INSERT OR IGNORE INTO company_files
           (company_id, company_name, canonical_key, status, source_apis,
            first_seen_at, last_seen_at)
           VALUES (?, ?, ?, 'promoted', '["github"]', ?, ?)""",
        (company_id, company_name, canonical_key, now, now),
    )
    await db.commit()


async def _seed_approved_review(store, company_id="company-gate", signal_ids=None):
    review_id = await create_review_item(
        store, company_id=company_id,
        evidence_signal_ids=signal_ids or [1],
    )
    await update_review_status(store, review_id, "approved", actor="test-seed")
    return review_id


async def _insert_canary_run(store, verdict="fail", pass_rate=0.3):
    created_at = datetime.now(timezone.utc).isoformat()
    db = store._db
    run_id = f"run-gate-{verdict}"
    await db.execute(
        "INSERT OR IGNORE INTO run_history (id, run_type, status, started_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, "canary", "completed", created_at, created_at),
    )
    await db.execute(
        """INSERT INTO canary_runs
           (run_id, golden_set_size, golden_set_hash, total_scored, passed, failed,
            skipped, pass_rate, verdict, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, 10, "abc123", 10,
         int(pass_rate * 10), int((1 - pass_rate) * 10), 0,
         pass_rate, verdict, created_at),
    )
    await db.commit()


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
async def app(store):
    application = FastAPI()
    application.state.store = store
    application.state.write_lock = asyncio.Lock()
    application.state.notion_connector = MagicMock()
    application.state.notion_transport = MagicMock()
    application.include_router(batch_mod.router, prefix="/api/v1")
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seeded(store, client):
    await _seed_signal(store)
    await _seed_approved_review(store)
    return client, store


async def _create_batch(client):
    resp = await client.post(
        "/api/v1/batches", json={"limit": 50}, headers=_auth_header(),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    return data["batch_id"], data["items_hash"]


def _mock_pusher():
    """Create a mock NotionPusher that succeeds."""
    mock = MagicMock()
    mock.process_single_prospect = AsyncMock(return_value=PushResult(
        canonical_key="domain:gate-test.com",
        company_name="Gate Corp",
        decision=PushDecision.AUTO_PUSH,
        confidence=0.8,
        pushed=True,
        notion_page_id="page-gate-123",
    ))
    return mock


# =============================================================================
# TESTS
# =============================================================================

class TestBatchActivationGate:
    @pytest.mark.asyncio
    async def test_ready_gate_includes_metadata(self, seeded):
        """Real commit with ready gate includes activation_gate in response."""
        from monitoring.activation_gate import ActivationGateResult

        client, store = seeded
        batch_id, items_hash = await _create_batch(client)

        mock_gate = ActivationGateResult(verdict="ready", step=4, checked_at="now")

        with patch("workflows.notion_pusher.NotionPusher", return_value=_mock_pusher()):
            with patch("verification.verification_gate_v2.VerificationGate"):
                with patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"}):
                    with patch("monitoring.activation_gate.check_activation_readiness", new_callable=AsyncMock, return_value=mock_gate):
                        resp = await client.post(
                            f"/api/v1/batches/{batch_id}/commit",
                            json={"expected_items_hash": items_hash, "dry_run": False},
                            headers=_auth_header(),
                        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "activation_gate" in data
        assert data["activation_gate"]["verdict"] == "ready"

    @pytest.mark.asyncio
    async def test_canary_fail_blocks_without_override(self, seeded):
        """Canary fail without override_reason returns 423 ACTIVATION_GATE_BLOCKED."""
        client, store = seeded
        await _insert_canary_run(store, verdict="fail", pass_rate=0.3)
        batch_id, items_hash = await _create_batch(client)

        with patch("workflows.notion_pusher.NotionPusher", return_value=_mock_pusher()):
            with patch("verification.verification_gate_v2.VerificationGate"):
                with patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"}):
                    resp = await client.post(
                        f"/api/v1/batches/{batch_id}/commit",
                        json={"expected_items_hash": items_hash, "dry_run": False},
                        headers=_auth_header(),
                    )
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert detail["code"] == "ACTIVATION_GATE_BLOCKED"
        assert detail["error"] == "locked"

    @pytest.mark.asyncio
    async def test_canary_fail_proceeds_with_override(self, seeded):
        """Canary fail with override_reason proceeds (200) + audit event recorded."""
        client, store = seeded
        await _insert_canary_run(store, verdict="fail", pass_rate=0.3)
        batch_id, items_hash = await _create_batch(client)

        with patch("workflows.notion_pusher.NotionPusher", return_value=_mock_pusher()):
            with patch("verification.verification_gate_v2.VerificationGate"):
                with patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"}):
                    resp = await client.post(
                        f"/api/v1/batches/{batch_id}/commit",
                        json={
                            "expected_items_hash": items_hash,
                            "dry_run": False,
                            "override_reason": "manual check passed, canary test flaky",
                        },
                        headers=_auth_header(),
                    )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["activation_gate"]["verdict"] == "blocked"

        # Verify audit event
        db = store._db
        cursor = await db.execute(
            "SELECT reason, metadata FROM audit_events WHERE action_type = 'batch_commit_gate_override'"
        )
        row = await cursor.fetchone()
        assert row is not None, "audit event for gate override should exist"
        assert row[0] == "manual check passed, canary test flaky"
        meta = json.loads(row[1])
        assert meta["verdict"] == "blocked"
        assert "gate_details" in meta

    @pytest.mark.asyncio
    async def test_no_canary_blocks_without_override(self, seeded):
        """No canary data + step 4 policy -> 423 without override."""
        client, store = seeded
        batch_id, items_hash = await _create_batch(client)

        with patch("workflows.notion_pusher.NotionPusher", return_value=_mock_pusher()):
            with patch("verification.verification_gate_v2.VerificationGate"):
                with patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"}):
                    resp = await client.post(
                        f"/api/v1/batches/{batch_id}/commit",
                        json={"expected_items_hash": items_hash, "dry_run": False},
                        headers=_auth_header(),
                    )
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert detail["code"] == "ACTIVATION_GATE_BLOCKED"

    @pytest.mark.asyncio
    async def test_no_canary_proceeds_with_override(self, seeded):
        """No canary data + override_reason -> 200."""
        client, store = seeded
        batch_id, items_hash = await _create_batch(client)

        with patch("workflows.notion_pusher.NotionPusher", return_value=_mock_pusher()):
            with patch("verification.verification_gate_v2.VerificationGate"):
                with patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"}):
                    resp = await client.post(
                        f"/api/v1/batches/{batch_id}/commit",
                        json={
                            "expected_items_hash": items_hash,
                            "dry_run": False,
                            "override_reason": "bootstrapping batch publish",
                        },
                        headers=_auth_header(),
                    )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["activation_gate"]["verdict"] == "blocked"

    @pytest.mark.asyncio
    async def test_timeout_blocks_without_override(self, seeded):
        """Gate timeout returns 423 (fail-closed) without override."""
        client, store = seeded
        batch_id, items_hash = await _create_batch(client)

        async def _slow_gate(*args, **kwargs):
            await asyncio.sleep(10)  # will be cancelled by 2s timeout

        with patch("workflows.notion_pusher.NotionPusher", return_value=_mock_pusher()):
            with patch("verification.verification_gate_v2.VerificationGate"):
                with patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"}):
                    with patch("monitoring.activation_gate.check_activation_readiness", side_effect=_slow_gate):
                        resp = await client.post(
                            f"/api/v1/batches/{batch_id}/commit",
                            json={"expected_items_hash": items_hash, "dry_run": False},
                            headers=_auth_header(),
                        )
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert detail["code"] == "ACTIVATION_GATE_BLOCKED"

    @pytest.mark.asyncio
    async def test_timeout_proceeds_with_override(self, seeded):
        """Gate timeout with override proceeds (200)."""
        client, store = seeded
        batch_id, items_hash = await _create_batch(client)

        async def _slow_gate(*args, **kwargs):
            await asyncio.sleep(10)

        with patch("workflows.notion_pusher.NotionPusher", return_value=_mock_pusher()):
            with patch("verification.verification_gate_v2.VerificationGate"):
                with patch.dict(os.environ, {"DELIVERY_MODE": "batch_publish"}):
                    with patch("monitoring.activation_gate.check_activation_readiness", side_effect=_slow_gate):
                        resp = await client.post(
                            f"/api/v1/batches/{batch_id}/commit",
                            json={
                                "expected_items_hash": items_hash,
                                "dry_run": False,
                                "override_reason": "gate timed out, manual check passed",
                            },
                            headers=_auth_header(),
                        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["activation_gate"]["verdict"] == "timeout"

    @pytest.mark.asyncio
    async def test_dry_run_skips_gate(self, seeded):
        """Dry-run response has no activation_gate metadata."""
        client, store = seeded
        batch_id, items_hash = await _create_batch(client)

        resp = await client.post(
            f"/api/v1/batches/{batch_id}/commit",
            json={"expected_items_hash": items_hash, "dry_run": True},
            headers=_auth_header(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "activation_gate" not in data

    @pytest.mark.asyncio
    async def test_delivery_policy_error_takes_precedence(self, seeded):
        """DeliveryPolicyError (staging_only) fires before gate check."""
        client, store = seeded
        batch_id, items_hash = await _create_batch(client)

        with patch("workflows.notion_pusher.NotionPusher", return_value=_mock_pusher()):
            with patch("verification.verification_gate_v2.VerificationGate"):
                with patch.dict(os.environ, {"DELIVERY_MODE": "staging_only"}):
                    resp = await client.post(
                        f"/api/v1/batches/{batch_id}/commit",
                        json={"expected_items_hash": items_hash, "dry_run": False},
                        headers=_auth_header(),
                    )
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert detail["code"] == "FEATURE_DISABLED"

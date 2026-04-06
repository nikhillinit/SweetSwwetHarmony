"""Tests for governance/cli.py — delivery-mode, errors, --direct-db, resolve_db_path."""

import json
import os
import tempfile

import pytest
import pytest_asyncio

from api.auth.jwt_auth import Role
from api.auth.rbac import OperatorContext
from governance.cli import resolve_db_path_for_governance
from storage.signal_store import SignalStore


@pytest_asyncio.fixture
async def gov_db(tmp_path):
    """Create a temp DB with schema initialized."""
    db_path = str(tmp_path / "gov_test.db")
    s = SignalStore(db_path=db_path)
    await s.initialize()
    await s.close()
    return db_path


@pytest.fixture
def operator():
    return OperatorContext(
        user_id="test-user",
        email="test@press.com",
        role=Role.GP,
        name="Test GP",
        request_id="req-cli-test",
    )


# ── resolve_db_path_for_governance ──────────────────────────────────────


class TestResolveDbPath:
    def test_existing_file_resolves(self, gov_db):
        result = resolve_db_path_for_governance(gov_db)
        assert result == gov_db

    def test_missing_file_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            resolve_db_path_for_governance(str(tmp_path / "nope.db"))

    def test_env_fallback(self, gov_db, monkeypatch):
        monkeypatch.setenv("DISCOVERY_DB_PATH", gov_db)
        result = resolve_db_path_for_governance(None)
        assert result == gov_db

    def test_env_missing_file_exits(self, monkeypatch):
        monkeypatch.setenv("DISCOVERY_DB_PATH", "/nonexistent/path.db")
        with pytest.raises(SystemExit):
            resolve_db_path_for_governance(None)


# ── DELIVERY_MODE promote via direct DB ─────────────────────────────────


class TestDeliveryModePromote:
    @pytest.mark.asyncio
    async def test_delivery_mode_promote_direct_db(self, gov_db, monkeypatch):
        """Full promote flow for DELIVERY_MODE through CLI internals."""
        from governance.cli import _cmd_promote
        import argparse

        monkeypatch.setenv("GOV_ACTOR_ID", "test-cli-user")
        monkeypatch.delenv("DISCOVERY_API_URL", raising=False)

        args = argparse.Namespace(
            flag="DELIVERY_MODE",
            from_state="manual_publish",
            to_state="batch_publish",
            regret_check_date="2026-04-01",
            effective_at=None,
            repair_source=None,
            reason="Step 4A promotion",
            direct_db=gov_db,
        )

        await _cmd_promote(args)

        # Verify event was written
        from storage.audit_events import get_events

        store = SignalStore(db_path=gov_db)
        await store.initialize()
        try:
            events = await get_events(store, action_type="feature_promote")
            assert len(events) == 1
            assert events[0].entity_id == "DELIVERY_MODE"
            assert events[0].metadata["from_state"] == "manual_publish"
            assert events[0].metadata["to_state"] == "batch_publish"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_retroactive_merge_write_promote_direct_db(
        self, gov_db, monkeypatch
    ):
        """Retroactive promotion repair metadata survives the CLI path."""
        from governance.cli import _cmd_promote
        import argparse

        monkeypatch.setenv("GOV_ACTOR_ID", "test-cli-user")
        monkeypatch.setenv("MERGE_WRITES_ENABLED", "active")
        monkeypatch.delenv("DISCOVERY_API_URL", raising=False)

        args = argparse.Namespace(
            flag="MERGE_WRITES_ENABLED",
            from_state="shadow",
            to_state="active",
            regret_check_date="2026-04-18",
            effective_at="2026-04-04T00:00:00Z",
            repair_source="artifacts/regret-check/step4b-baseline-2026-04-05/summary.md",
            reason="Repair missing Step 4B governance event",
            direct_db=gov_db,
        )

        await _cmd_promote(args)

        from storage.audit_events import get_events

        store = SignalStore(db_path=gov_db)
        await store.initialize()
        try:
            events = await get_events(store, action_type="feature_promote")
            assert len(events) == 1
            assert events[0].entity_id == "MERGE_WRITES_ENABLED"
            assert events[0].metadata["effective_at"] == "2026-04-04T00:00:00Z"
            assert events[0].metadata["repair_source"].endswith("summary.md")
        finally:
            await store.close()


# ── Error handling ───────────────────────────────────────────────────────


class TestCliErrors:
    @pytest.mark.asyncio
    async def test_wrong_direction_exits(self, gov_db, monkeypatch):
        """Promoting downward should raise GovernanceStatePolicyError."""
        from governance.cli import _cmd_promote
        from governance.state_policies import GovernanceStatePolicyError
        import argparse

        monkeypatch.setenv("GOV_ACTOR_ID", "test-cli-user")
        monkeypatch.delenv("DISCOVERY_API_URL", raising=False)

        args = argparse.Namespace(
            flag="DELIVERY_MODE",
            from_state="batch_publish",
            to_state="manual_publish",
            regret_check_date="2026-04-01",
            effective_at=None,
            repair_source=None,
            reason="should fail",
            direct_db=gov_db,
        )

        with pytest.raises(GovernanceStatePolicyError, match="wrong direction"):
            await _cmd_promote(args)

    @pytest.mark.asyncio
    async def test_wrong_case_hint(self, gov_db, monkeypatch):
        """lowercase 'delivery_mode' gives case hint."""
        from governance.cli import _cmd_promote
        from governance.state_policies import GovernanceStatePolicyError
        import argparse

        monkeypatch.setenv("GOV_ACTOR_ID", "test-cli-user")
        monkeypatch.delenv("DISCOVERY_API_URL", raising=False)

        args = argparse.Namespace(
            flag="delivery_mode",
            from_state="manual_publish",
            to_state="batch_publish",
            regret_check_date="2026-04-01",
            effective_at=None,
            repair_source=None,
            reason="should fail with hint",
            direct_db=gov_db,
        )

        with pytest.raises(GovernanceStatePolicyError, match="Did you mean 'DELIVERY_MODE'"):
            await _cmd_promote(args)

    @pytest.mark.asyncio
    async def test_feature_prefix_hint(self, gov_db, monkeypatch):
        """FEATURE_BOILERPLATE_DEFENSE gives hint to use 'boilerplate_defense'."""
        from governance.cli import _cmd_promote
        from governance.state_policies import GovernanceStatePolicyError
        import argparse

        monkeypatch.setenv("GOV_ACTOR_ID", "test-cli-user")
        monkeypatch.delenv("DISCOVERY_API_URL", raising=False)

        args = argparse.Namespace(
            flag="FEATURE_BOILERPLATE_DEFENSE",
            from_state="off",
            to_state="shadow",
            regret_check_date="2026-04-01",
            effective_at=None,
            repair_source=None,
            reason="should fail with hint",
            direct_db=gov_db,
        )

        with pytest.raises(GovernanceStatePolicyError, match="Did you mean 'boilerplate_defense'"):
            await _cmd_promote(args)

    def test_api_url_without_token_exits(self, monkeypatch):
        """DISCOVERY_API_URL set without DISCOVERY_API_TOKEN exits non-zero."""
        monkeypatch.setenv("DISCOVERY_API_URL", "http://localhost:8000")
        monkeypatch.delenv("DISCOVERY_API_TOKEN", raising=False)

        import argparse
        from governance.cli import _use_api

        args = argparse.Namespace(direct_db=None)
        with pytest.raises(SystemExit):
            _use_api(args)

    def test_direct_db_bypasses_api(self, gov_db, monkeypatch):
        """--direct-db forces direct DB even if DISCOVERY_API_URL is set."""
        monkeypatch.setenv("DISCOVERY_API_URL", "http://localhost:8000")
        monkeypatch.setenv("DISCOVERY_API_TOKEN", "tok")

        import argparse
        from governance.cli import _use_api

        args = argparse.Namespace(direct_db=gov_db)
        result = _use_api(args)
        assert result is None  # Direct DB, not API


# ── Snapshot override for env-backed flags ───────────────────────────────


class TestSnapshotOverride:
    @pytest.mark.asyncio
    async def test_env_backed_snapshot_has_override(self, gov_db, monkeypatch):
        """Promote DELIVERY_MODE → snapshot should reflect the target state."""
        from governance.cli import _cmd_promote
        from storage.audit_events import get_events
        import argparse

        monkeypatch.setenv("GOV_ACTOR_ID", "test-cli-user")
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        monkeypatch.delenv("DISCOVERY_API_URL", raising=False)

        args = argparse.Namespace(
            flag="DELIVERY_MODE",
            from_state="manual_publish",
            to_state="batch_publish",
            regret_check_date="2026-04-01",
            effective_at=None,
            repair_source=None,
            reason="snapshot test",
            direct_db=gov_db,
        )

        await _cmd_promote(args)

        store = SignalStore(db_path=gov_db)
        await store.initialize()
        try:
            events = await get_events(store, action_type="feature_promote")
            flags = events[0].metadata["config_snapshot_flags"]
            assert flags["DELIVERY_MODE"] == "batch_publish"
        finally:
            await store.close()

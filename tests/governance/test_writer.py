"""Tests for governance writer — validates events are correctly written."""

import os
import tempfile

import pytest
import pytest_asyncio
from pydantic import ValidationError

from api.auth.jwt_auth import Role
from api.auth.rbac import OperatorContext
from governance.writer import (
    record_feature_demote,
    record_feature_promote,
    record_regret_check,
)
from storage.audit_events import get_events
from storage.signal_store import SignalStore


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


@pytest.fixture
def operator():
    return OperatorContext(
        user_id="test-user",
        email="test@press.com",
        role=Role.GP,
        name="Test GP",
        request_id="req-gov-test",
    )


class TestRecordFeaturePromote:
    @pytest.mark.asyncio
    async def test_writes_valid_promote(self, store, operator):
        event_id = await record_feature_promote(
            store, operator,
            feature_name="boilerplate_defense",
            from_state="shadow",
            to_state="active",
            regret_due_at="2026-03-14T00:00:00+00:00",
            reason="Canary stable for 48h",
            config_snapshot_hash="abc123",
        )
        assert event_id > 0
        events = await get_events(store, action_type="feature_promote")
        assert len(events) == 1
        assert events[0].entity_id == "boilerplate_defense"
        assert events[0].entity_type == "feature_flag"
        assert events[0].metadata["regret_due_at"] == "2026-03-14T00:00:00+00:00"
        assert events[0].metadata["config_snapshot_hash"] == "abc123"
        assert events[0].actor_id == "test-user"
        assert events[0].correlation_id == "req-gov-test"

    @pytest.mark.asyncio
    async def test_with_snapshot_flags(self, store, operator):
        await record_feature_promote(
            store, operator,
            feature_name="thesis_match",
            from_state="off",
            to_state="shadow",
            regret_due_at="2026-04-01",
            reason="Initial shadow deploy",
            config_snapshot_hash="def456",
            config_snapshot_flags={"LLM_THESIS_MODE": "shadow"},
        )
        events = await get_events(store, action_type="feature_promote")
        assert events[0].metadata["config_snapshot_flags"] == {
            "LLM_THESIS_MODE": "shadow",
        }

    @pytest.mark.asyncio
    async def test_invalid_state_raises_validation_error(self, store, operator):
        with pytest.raises(ValidationError):
            await record_feature_promote(
                store, operator,
                feature_name="x",
                from_state="invalid",
                to_state="active",
                regret_due_at="2026-03-14",
                reason="test",
                config_snapshot_hash="abc",
            )
        # No event should have been written
        events = await get_events(store, action_type="feature_promote")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_tx_aware_path(self, store, operator):
        """Writer works inside transaction_immediate() without committing."""
        async with store.transaction_immediate() as tx:
            event_id = await record_feature_promote(
                tx, operator,
                feature_name="thesis_match",
                from_state="off",
                to_state="shadow",
                regret_due_at="2026-04-01",
                reason="Initial shadow deploy",
                config_snapshot_hash="def456",
            )
        assert event_id > 0
        events = await get_events(store, action_type="feature_promote")
        assert len(events) == 1


class TestRecordRegretCheck:
    @pytest.mark.asyncio
    async def test_writes_valid_regret_check(self, store, operator):
        event_id = await record_regret_check(
            store, operator,
            feature_name="boilerplate_defense",
            verdict="pass",
            canary_verdict="pass",
            drift_status="in_control",
            reason="No regressions in 14-day window",
        )
        assert event_id > 0
        events = await get_events(store, action_type="regret_check")
        assert len(events) == 1
        assert events[0].metadata["verdict"] == "pass"
        assert events[0].metadata["window_days"] == 14

    @pytest.mark.asyncio
    async def test_fail_verdict(self, store, operator):
        await record_regret_check(
            store, operator,
            feature_name="thesis_match",
            verdict="fail",
            canary_verdict="fail",
            drift_status="critical",
            reason="FP rate spiked",
            window_days=7,
        )
        events = await get_events(store, action_type="regret_check")
        assert events[0].metadata["verdict"] == "fail"
        assert events[0].metadata["window_days"] == 7

    @pytest.mark.asyncio
    async def test_invalid_verdict_raises(self, store, operator):
        with pytest.raises(ValidationError):
            await record_regret_check(
                store, operator,
                feature_name="x",
                verdict="maybe",
                canary_verdict="pass",
                drift_status="in_control",
                reason="test",
            )


class TestRecordFeatureDemote:
    @pytest.mark.asyncio
    async def test_writes_valid_demote(self, store, operator):
        event_id = await record_feature_demote(
            store, operator,
            feature_name="boilerplate_defense",
            from_state="active",
            to_state="shadow",
            reason="FP rate increased",
            rollback_ticket="JIRA-789",
        )
        assert event_id > 0
        events = await get_events(store, action_type="feature_demote")
        assert len(events) == 1
        assert events[0].metadata["rollback_ticket"] == "JIRA-789"

    @pytest.mark.asyncio
    async def test_without_optional_fields(self, store, operator):
        await record_feature_demote(
            store, operator,
            feature_name="thesis_match",
            from_state="active",
            to_state="off",
            reason="Shutting down experiment",
        )
        events = await get_events(store, action_type="feature_demote")
        assert events[0].metadata["rollback_ticket"] is None

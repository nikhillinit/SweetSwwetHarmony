"""Tests for run/job abstraction (workflows/run_manager.py)."""

import os
import tempfile

import pytest
import pytest_asyncio

from storage.signal_store import SignalStore
from workflows.run_manager import (
    RunRecord,
    RunStatus,
    RunType,
    cancel_run,
    complete_run,
    create_run,
    fail_run,
    get_run,
    list_runs,
    start_run,
    update_progress,
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
# create_run
# ============================================================================


class TestCreateRun:
    @pytest.mark.asyncio
    async def test_basic_create(self, store):
        run = await create_run(store, run_type=RunType.HUNTER.value)
        assert run.id is not None
        assert len(run.id) == 16
        assert run.status == RunStatus.QUEUED
        assert run.run_type == RunType.HUNTER.value
        assert run.created_at is not None

    @pytest.mark.asyncio
    async def test_create_with_actor(self, store):
        run = await create_run(
            store,
            run_type=RunType.CANARY.value,
            actor_id="u1",
            actor_email="gp@press.com",
            inputs_hash="abc123",
        )
        assert run.actor_id == "u1"
        assert run.actor_email == "gp@press.com"
        assert run.inputs_hash == "abc123"

    @pytest.mark.asyncio
    async def test_create_with_inputs(self, store):
        run = await create_run(
            store,
            run_type=RunType.ACH_BUILD.value,
            inputs_summary={"company_id": "c-1", "signal_count": 5},
        )
        assert run.run_type == RunType.ACH_BUILD.value


# ============================================================================
# Lifecycle transitions
# ============================================================================


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_queued_to_running(self, store):
        run = await create_run(store, run_type="hunter")
        await start_run(store, run.id)
        updated = await get_run(store, run.id)
        assert updated.status == RunStatus.RUNNING
        assert updated.started_at is not None

    @pytest.mark.asyncio
    async def test_running_to_completed(self, store):
        run = await create_run(store, run_type="hunter")
        await start_run(store, run.id)
        await complete_run(store, run.id, result={"found": 5})
        updated = await get_run(store, run.id)
        assert updated.status == RunStatus.COMPLETED
        assert updated.result == {"found": 5}
        assert updated.completed_at is not None
        assert updated.progress_pct == 100

    @pytest.mark.asyncio
    async def test_running_to_failed(self, store):
        run = await create_run(store, run_type="canary")
        await start_run(store, run.id)
        await fail_run(store, run.id, error_message="Connection timeout")
        updated = await get_run(store, run.id)
        assert updated.status == RunStatus.FAILED
        assert updated.error_message == "Connection timeout"

    @pytest.mark.asyncio
    async def test_queued_to_failed(self, store):
        run = await create_run(store, run_type="ach_build")
        await fail_run(store, run.id, error_message="Invalid input")
        updated = await get_run(store, run.id)
        assert updated.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_cancel_queued(self, store):
        run = await create_run(store, run_type="hunter")
        await cancel_run(store, run.id)
        updated = await get_run(store, run.id)
        assert updated.status == RunStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_running(self, store):
        run = await create_run(store, run_type="hunter")
        await start_run(store, run.id)
        await cancel_run(store, run.id)
        updated = await get_run(store, run.id)
        assert updated.status == RunStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_start_already_running_noop(self, store):
        """Starting an already-running run should not change it."""
        run = await create_run(store, run_type="hunter")
        await start_run(store, run.id)
        first = await get_run(store, run.id)
        await start_run(store, run.id)  # Should be a no-op
        second = await get_run(store, run.id)
        assert first.started_at == second.started_at


# ============================================================================
# Progress tracking
# ============================================================================


class TestProgress:
    @pytest.mark.asyncio
    async def test_update_progress(self, store):
        run = await create_run(store, run_type="hunter")
        await start_run(store, run.id)
        await update_progress(store, run.id, 50)
        updated = await get_run(store, run.id)
        assert updated.progress_pct == 50

    @pytest.mark.asyncio
    async def test_clamp_progress(self, store):
        run = await create_run(store, run_type="hunter")
        await update_progress(store, run.id, 150)
        updated = await get_run(store, run.id)
        assert updated.progress_pct == 100

    @pytest.mark.asyncio
    async def test_clamp_progress_negative(self, store):
        run = await create_run(store, run_type="hunter")
        await update_progress(store, run.id, -10)
        updated = await get_run(store, run.id)
        assert updated.progress_pct == 0


# ============================================================================
# get_run / list_runs
# ============================================================================


class TestQueries:
    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store):
        assert await get_run(store, "nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_empty(self, store):
        runs = await list_runs(store)
        assert runs == []

    @pytest.mark.asyncio
    async def test_list_all(self, store):
        await create_run(store, run_type="hunter")
        await create_run(store, run_type="canary")
        await create_run(store, run_type="ach_build")
        runs = await list_runs(store)
        assert len(runs) == 3

    @pytest.mark.asyncio
    async def test_list_by_type(self, store):
        await create_run(store, run_type="hunter")
        await create_run(store, run_type="hunter")
        await create_run(store, run_type="canary")
        runs = await list_runs(store, run_type="hunter")
        assert len(runs) == 2

    @pytest.mark.asyncio
    async def test_list_by_status(self, store):
        r1 = await create_run(store, run_type="hunter")
        r2 = await create_run(store, run_type="hunter")
        await start_run(store, r1.id)
        runs = await list_runs(store, status=RunStatus.RUNNING.value)
        assert len(runs) == 1
        assert runs[0].id == r1.id

    @pytest.mark.asyncio
    async def test_list_limit(self, store):
        for _ in range(10):
            await create_run(store, run_type="hunter")
        runs = await list_runs(store, limit=3)
        assert len(runs) == 3

    @pytest.mark.asyncio
    async def test_list_newest_first(self, store):
        r1 = await create_run(store, run_type="hunter")
        r2 = await create_run(store, run_type="hunter")
        runs = await list_runs(store)
        # Most recent first
        assert runs[0].id == r2.id


# ============================================================================
# RunRecord model
# ============================================================================


class TestRunRecord:
    def test_status_enum(self):
        assert RunStatus.QUEUED.value == "queued"
        assert RunStatus.RUNNING.value == "running"
        assert RunStatus.COMPLETED.value == "completed"
        assert RunStatus.FAILED.value == "failed"
        assert RunStatus.CANCELLED.value == "cancelled"

    def test_run_type_enum(self):
        assert RunType.HUNTER.value == "hunter"
        assert RunType.CANARY.value == "canary"
        assert RunType.ACH_BUILD.value == "ach_build"

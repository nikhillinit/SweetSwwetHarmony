"""Phase 3 — FastAPI scheduler endpoint tests (TDD RED)."""

import pytest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from fastapi import FastAPI


def _mock_scheduler():
    """Create a mock PipelineScheduler with sensible defaults."""
    s = MagicMock()
    s.list_schedules.return_value = []
    s.get_schedule.return_value = None
    s.create_schedule.return_value = 1
    s.get_run_history.return_value = []
    s.get_schedule_status.return_value = {}
    return s


SAMPLE_SCHEDULE = {
    "id": 1,
    "name": "nightly-full",
    "cron_expression": "0 2 * * *",
    "collectors": '["github","sec_edgar"]',
    "mode": "full",
    "dry_run": 0,
    "enabled": 1,
    "max_retries": 0,
    "created_at": "2026-02-05T00:00:00",
    "updated_at": "2026-02-05T00:00:00",
}


SAMPLE_RUN = {
    "id": 1,
    "schedule_id": 1,
    "status": "success",
    "idempotency_key": "pipeline_run:1:2026-02-05T02:00",
    "started_at": "2026-02-05T02:00:00",
    "finished_at": "2026-02-05T02:05:00",
    "signals_found": 12,
    "signals_processed": 10,
    "signals_pushed": 8,
    "errors": 0,
    "error_message": None,
    "cost": 0.05,
    "created_at": "2026-02-05T02:00:00",
}


@pytest.fixture
def mock_sched():
    return _mock_scheduler()


@pytest.fixture
def app_with_scheduler(mock_sched):
    """Create a test FastAPI app with scheduler router."""
    from api.routers.scheduler import router, get_scheduler

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_scheduler] = lambda: mock_sched
    return app


@pytest.fixture
def client(app_with_scheduler):
    return TestClient(app_with_scheduler)


# =============================================================================
# LIST SCHEDULES
# =============================================================================


class TestListSchedules:
    def test_list_empty(self, client, mock_sched):
        """GET /schedules returns empty list when no schedules."""
        mock_sched.list_schedules.return_value = []
        resp = client.get("/schedules")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_schedules(self, client, mock_sched):
        """GET /schedules returns schedules list."""
        mock_sched.list_schedules.return_value = [SAMPLE_SCHEDULE]
        resp = client.get("/schedules")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "nightly-full"


# =============================================================================
# CREATE SCHEDULE
# =============================================================================


class TestCreateSchedule:
    def test_create_success(self, client, mock_sched):
        """POST /schedules creates a schedule and returns id."""
        mock_sched.create_schedule.return_value = 1
        mock_sched.get_schedule.return_value = SAMPLE_SCHEDULE

        resp = client.post("/schedules", json={
            "name": "nightly-full",
            "cron_expression": "0 2 * * *",
            "collectors": ["github", "sec_edgar"],
            "mode": "full",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == 1
        assert data["name"] == "nightly-full"
        mock_sched.create_schedule.assert_called_once()

    def test_create_minimal(self, client, mock_sched):
        """POST /schedules with only required fields."""
        mock_sched.create_schedule.return_value = 2
        mock_sched.get_schedule.return_value = {
            **SAMPLE_SCHEDULE,
            "id": 2,
            "name": "weekly",
            "cron_expression": "0 0 * * 0",
            "collectors": "[]",
        }

        resp = client.post("/schedules", json={
            "name": "weekly",
            "cron_expression": "0 0 * * 0",
        })
        assert resp.status_code == 201
        assert resp.json()["id"] == 2

    def test_create_invalid_cron(self, client, mock_sched):
        """POST /schedules rejects invalid cron expression."""
        resp = client.post("/schedules", json={
            "name": "bad-cron",
            "cron_expression": "not a cron",
        })
        assert resp.status_code == 422

    def test_create_missing_name(self, client, mock_sched):
        """POST /schedules rejects missing name."""
        resp = client.post("/schedules", json={
            "cron_expression": "0 2 * * *",
        })
        assert resp.status_code == 422

    def test_create_duplicate_name(self, client, mock_sched):
        """POST /schedules returns 409 on duplicate name."""
        from sqlite3 import IntegrityError
        mock_sched.create_schedule.side_effect = IntegrityError("UNIQUE constraint failed")

        resp = client.post("/schedules", json={
            "name": "nightly-full",
            "cron_expression": "0 2 * * *",
        })
        assert resp.status_code == 409


# =============================================================================
# GET SINGLE SCHEDULE
# =============================================================================


class TestGetSchedule:
    def test_get_existing(self, client, mock_sched):
        """GET /schedules/{id} returns schedule."""
        mock_sched.get_schedule.return_value = SAMPLE_SCHEDULE
        resp = client.get("/schedules/1")
        assert resp.status_code == 200
        assert resp.json()["name"] == "nightly-full"

    def test_get_not_found(self, client, mock_sched):
        """GET /schedules/{id} returns 404 for missing schedule."""
        mock_sched.get_schedule.return_value = None
        resp = client.get("/schedules/999")
        assert resp.status_code == 404


# =============================================================================
# PAUSE / RESUME
# =============================================================================


class TestPauseResume:
    def test_pause(self, client, mock_sched):
        """PUT /schedules/{id}/pause pauses the schedule."""
        mock_sched.get_schedule.return_value = SAMPLE_SCHEDULE
        resp = client.put("/schedules/1/pause")
        assert resp.status_code == 200
        mock_sched.pause_schedule.assert_called_once_with(1)
        assert resp.json()["message"] == "Schedule 1 paused"

    def test_pause_not_found(self, client, mock_sched):
        """PUT /schedules/{id}/pause returns 404 for missing."""
        mock_sched.pause_schedule.side_effect = ValueError("Schedule 999 not found")
        resp = client.put("/schedules/999/pause")
        assert resp.status_code == 404

    def test_resume(self, client, mock_sched):
        """PUT /schedules/{id}/resume resumes the schedule."""
        mock_sched.get_schedule.return_value = SAMPLE_SCHEDULE
        resp = client.put("/schedules/1/resume")
        assert resp.status_code == 200
        mock_sched.resume_schedule.assert_called_once_with(1)
        assert resp.json()["message"] == "Schedule 1 resumed"

    def test_resume_not_found(self, client, mock_sched):
        """PUT /schedules/{id}/resume returns 404 for missing."""
        mock_sched.resume_schedule.side_effect = ValueError("Schedule 999 not found")
        resp = client.put("/schedules/999/resume")
        assert resp.status_code == 404


# =============================================================================
# DELETE
# =============================================================================


class TestDeleteSchedule:
    def test_delete_success(self, client, mock_sched):
        """DELETE /schedules/{id} deletes the schedule."""
        resp = client.delete("/schedules/1")
        assert resp.status_code == 200
        mock_sched.delete_schedule.assert_called_once_with(1)

    def test_delete_not_found(self, client, mock_sched):
        """DELETE /schedules/{id} returns 404 for missing."""
        mock_sched.delete_schedule.side_effect = ValueError("Schedule 999 not found")
        resp = client.delete("/schedules/999")
        assert resp.status_code == 404


# =============================================================================
# TRIGGER (immediate run)
# =============================================================================


class TestTriggerRun:
    def test_trigger_success(self, client, mock_sched):
        """POST /schedules/{id}/trigger enqueues and returns run id."""
        mock_sched.get_schedule.return_value = SAMPLE_SCHEDULE
        mock_sched.enqueue_run.return_value = 42

        resp = client.post("/schedules/1/trigger")
        assert resp.status_code == 202
        data = resp.json()
        assert data["run_id"] == 42
        mock_sched.enqueue_run.assert_called_once_with(1)

    def test_trigger_not_found(self, client, mock_sched):
        """POST /schedules/{id}/trigger returns 404 for missing."""
        mock_sched.get_schedule.return_value = None
        resp = client.post("/schedules/999/trigger")
        assert resp.status_code == 404

    def test_trigger_disabled(self, client, mock_sched):
        """POST /schedules/{id}/trigger returns 409 for disabled schedule."""
        mock_sched.get_schedule.return_value = {**SAMPLE_SCHEDULE, "enabled": 0}
        mock_sched.enqueue_run.side_effect = ValueError("Schedule 1 is disabled or not found")
        resp = client.post("/schedules/1/trigger")
        assert resp.status_code == 409


# =============================================================================
# RUN HISTORY
# =============================================================================


class TestRunHistory:
    def test_history_empty(self, client, mock_sched):
        """GET /schedules/{id}/history returns empty list."""
        mock_sched.get_schedule.return_value = SAMPLE_SCHEDULE
        mock_sched.get_run_history.return_value = []
        resp = client.get("/schedules/1/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_history_with_runs(self, client, mock_sched):
        """GET /schedules/{id}/history returns run records."""
        mock_sched.get_schedule.return_value = SAMPLE_SCHEDULE
        mock_sched.get_run_history.return_value = [SAMPLE_RUN]
        resp = client.get("/schedules/1/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "success"
        assert data[0]["signals_found"] == 12

    def test_history_with_limit(self, client, mock_sched):
        """GET /schedules/{id}/history?limit=5 passes limit."""
        mock_sched.get_schedule.return_value = SAMPLE_SCHEDULE
        mock_sched.get_run_history.return_value = []
        resp = client.get("/schedules/1/history?limit=5")
        assert resp.status_code == 200
        mock_sched.get_run_history.assert_called_once_with(1, limit=5)

    def test_history_not_found(self, client, mock_sched):
        """GET /schedules/{id}/history returns 404 for missing schedule."""
        mock_sched.get_schedule.return_value = None
        resp = client.get("/schedules/999/history")
        assert resp.status_code == 404


# =============================================================================
# SCHEDULE STATUS (detailed status with stats)
# =============================================================================


class TestScheduleStatus:
    def test_status_success(self, client, mock_sched):
        """GET /schedules/{id}/status returns detailed status."""
        mock_sched.get_schedule_status.return_value = {
            "id": 1,
            "name": "nightly-full",
            "cron_expression": "0 2 * * *",
            "enabled": True,
            "last_run": SAMPLE_RUN,
            "next_run": "2026-02-06T02:00:00+00:00",
            "total_runs": 5,
            "success_rate": 80.0,
        }
        resp = client.get("/schedules/1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "nightly-full"
        assert data["total_runs"] == 5
        assert data["success_rate"] == 80.0

    def test_status_not_found(self, client, mock_sched):
        """GET /schedules/{id}/status returns 404 for missing."""
        mock_sched.get_schedule_status.side_effect = ValueError("Schedule 999 not found")
        resp = client.get("/schedules/999/status")
        assert resp.status_code == 404


# =============================================================================
# THREADPOOL USAGE
# =============================================================================


class TestThreadpool:
    def test_scheduler_endpoints_use_threadpool(self):
        """Verify scheduler router uses run_in_threadpool."""
        import inspect
        from api.routers import scheduler as sched_module
        source = inspect.getsource(sched_module)
        assert "run_in_threadpool" in source

"""Tests for the maint CLI commands (incident listing, show, repair)."""

import io
import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from ops.maintenance.incident import (
    MaintenanceIncident,
)


@pytest.fixture
def incidents_dir(tmp_path, monkeypatch):
    """Redirect ARTIFACTS_DIR to a temp directory."""
    monkeypatch.setattr("ops.maintenance.incident.ARTIFACTS_DIR", tmp_path)
    return tmp_path


def _create_test_incident(incidents_dir: Path, component: str, status: str = "open") -> MaintenanceIncident:
    """Helper: create a real incident on disk inside *incidents_dir*."""
    inc_id = f"{component}_20260205_120000"
    inc_dir = incidents_dir / inc_id
    inc_dir.mkdir(parents=True, exist_ok=True)
    inc = MaintenanceIncident(
        incident_id=inc_id,
        component=component,
        error_type="RuntimeError",
        error_message=f"Test error in {component}",
        status=status,
        artifact_dir=str(inc_dir),
    )
    with open(inc_dir / "incident.json", "w") as f:
        json.dump({
            "incident_id": inc.incident_id,
            "component": inc.component,
            "error_type": inc.error_type,
            "error_message": inc.error_message,
            "status": inc.status,
            "created_at": inc.created_at,
            "updated_at": inc.updated_at,
            "artifact_dir": inc.artifact_dir,
            "traceback_text": "",
            "context": {},
            "repair_attempts": [],
        }, f)
    return inc


def _capture(func, args_obj) -> str:
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        func(args_obj)
    finally:
        sys.stdout = old
    return buf.getvalue()


# ── list-incidents ───────────────────────────────────────────────────

class TestListIncidents:
    def test_empty(self, incidents_dir):
        from ops.cli import list_incidents_cmd

        class Args:
            status = None

        output = _capture(list_incidents_cmd, Args())
        assert "No incidents found" in output

    def test_with_data(self, incidents_dir):
        from ops.cli import list_incidents_cmd

        _create_test_incident(incidents_dir, "github", "open")
        _create_test_incident(incidents_dir, "sec_edgar", "resolved")

        class Args:
            status = None

        output = _capture(list_incidents_cmd, Args())
        assert "github" in output
        assert "sec_edgar" in output

    def test_status_filter(self, incidents_dir):
        from ops.cli import list_incidents_cmd

        _create_test_incident(incidents_dir, "github", "open")
        _create_test_incident(incidents_dir, "sec_edgar", "resolved")

        class Args:
            status = "open"

        output = _capture(list_incidents_cmd, Args())
        assert "github" in output
        assert "sec_edgar" not in output


# ── show ─────────────────────────────────────────────────────────────

class TestShowIncident:
    def test_valid(self, incidents_dir):
        from ops.cli import show_incident_cmd

        inc = _create_test_incident(incidents_dir, "github", "open")

        class Args:
            incident_id = inc.incident_id

        output = _capture(show_incident_cmd, Args())
        assert "github" in output
        assert "open" in output

    def test_not_found(self, incidents_dir):
        from ops.cli import show_incident_cmd

        class Args:
            incident_id = "nonexistent_id"

        with pytest.raises(SystemExit):
            show_incident_cmd(Args())


# ── repair-latest ────────────────────────────────────────────────────

class TestRepairLatest:
    def test_no_claude(self, incidents_dir):
        from ops.cli import repair_latest_cmd

        class Args:
            pass

        with patch("ops.maintenance.repair_agent.ClaudeCodeCLI") as MockCLI:
            MockCLI.return_value.available = False
            with pytest.raises(SystemExit):
                repair_latest_cmd(Args())

    def test_no_incidents(self, incidents_dir):
        from ops.cli import repair_latest_cmd

        class Args:
            pass

        with patch("ops.maintenance.repair_agent.ClaudeCodeCLI") as MockCLI:
            MockCLI.return_value.available = True
            mock_agent = MagicMock()
            mock_agent.available = True
            mock_agent.repair_latest.return_value = {"success": True, "message": "No open incidents"}
            with patch("ops.cli.repair_latest_cmd.__module__", "ops.cli"):
                with patch("ops.maintenance.repair_agent.RepairAgent", return_value=mock_agent):
                    output = _capture(repair_latest_cmd, Args())
        assert "No open incidents" in output


# ── repair ───────────────────────────────────────────────────────────

class TestRepairIncident:
    def test_mock_success(self, incidents_dir):
        from ops.cli import repair_cmd

        inc = _create_test_incident(incidents_dir, "github", "open")

        class Args:
            incident_id = inc.incident_id

        mock_agent = MagicMock()
        mock_agent.available = True
        mock_agent.repair_incident.return_value = {"success": True, "output": "Fixed the issue"}
        with patch("ops.maintenance.repair_agent.RepairAgent", return_value=mock_agent):
            output = _capture(repair_cmd, Args())
        assert "Repair completed" in output

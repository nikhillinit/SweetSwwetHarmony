"""Tests for RepairAgent and ClaudeCodeCLI."""

import json
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from ops.maintenance.claude_code_cli import ClaudeCodeCLI
from ops.maintenance.repair_agent import RepairAgent
from ops.maintenance.incident import MaintenanceIncident


# ── ClaudeCodeCLI ────────────────────────────────────────────────────

class TestClaudeCodeCLI:
    def test_not_available(self):
        with patch("shutil.which", return_value=None):
            cli = ClaudeCodeCLI()
        assert cli.available is False

    def test_not_available_returns_error(self):
        with patch("shutil.which", return_value=None):
            cli = ClaudeCodeCLI()
        result = cli.call("test prompt")
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_call_success(self):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            cli = ClaudeCodeCLI()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Fixed it", stderr="")
            result = cli.call("fix the bug")
        assert result["success"] is True
        assert result["output"] == "Fixed it"

    def test_call_timeout(self):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            cli = ClaudeCodeCLI(timeout=10)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=10)):
            result = cli.call("slow prompt")
        assert result["success"] is False
        assert "Timeout" in result["error"]

    def test_call_nonzero_exit(self):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            cli = ClaudeCodeCLI()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="API error")
            result = cli.call("bad prompt")
        assert result["success"] is False
        assert "API error" in result["error"]


# ── RepairAgent ──────────────────────────────────────────────────────

@pytest.fixture
def incidents_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("ops.maintenance.incident.ARTIFACTS_DIR", tmp_path)
    return tmp_path


def _make_incident(incidents_dir: Path, component: str, status: str = "open") -> MaintenanceIncident:
    inc_id = f"{component}_20260205_120000"
    inc_dir = incidents_dir / inc_id
    inc_dir.mkdir(parents=True, exist_ok=True)
    inc = MaintenanceIncident(
        incident_id=inc_id,
        component=component,
        error_type="RuntimeError",
        error_message="Something broke",
        status=status,
        artifact_dir=str(inc_dir),
        traceback_text="Traceback: line 42",
        context={"collector": component},
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
            "traceback_text": inc.traceback_text,
            "context": inc.context,
            "repair_attempts": [],
        }, f)
    return inc


class TestBuildRepairPrompt:
    def test_sanitization(self, incidents_dir):
        inc = _make_incident(incidents_dir, "github")
        with patch("shutil.which", return_value="/usr/bin/claude"):
            agent = RepairAgent()
        prompt = agent._build_repair_prompt(inc)
        assert "github" in prompt.lower()
        assert "RuntimeError" in prompt
        assert "Something broke" in prompt


class TestRepairIncident:
    def test_success_cycle(self, incidents_dir):
        """open -> investigating -> resolved."""
        inc = _make_incident(incidents_dir, "github", "open")
        with patch("shutil.which", return_value="/usr/bin/claude"):
            agent = RepairAgent()
        with patch.object(agent.cli, "call", return_value={"success": True, "output": "Applied fix"}):
            result = agent.repair_incident(inc.incident_id)
        assert result["success"] is True
        # Verify status on disk
        updated = json.loads((incidents_dir / inc.incident_id / "incident.json").read_text())
        assert updated["status"] == "resolved"

    def test_failure_cycle(self, incidents_dir):
        """open -> investigating -> open (on failure)."""
        inc = _make_incident(incidents_dir, "github", "open")
        with patch("shutil.which", return_value="/usr/bin/claude"):
            agent = RepairAgent()
        with patch.object(agent.cli, "call", return_value={"success": False, "output": "", "error": "API down"}):
            result = agent.repair_incident(inc.incident_id)
        assert result["success"] is False
        updated = json.loads((incidents_dir / inc.incident_id / "incident.json").read_text())
        assert updated["status"] == "open"

    def test_already_resolved(self, incidents_dir):
        inc = _make_incident(incidents_dir, "github", "resolved")
        with patch("shutil.which", return_value="/usr/bin/claude"):
            agent = RepairAgent()
        result = agent.repair_incident(inc.incident_id)
        assert result["success"] is True
        assert "already resolved" in result["message"].lower()

    def test_not_found(self, incidents_dir):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            agent = RepairAgent()
        result = agent.repair_incident("nonexistent_id")
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_cli_not_available(self, incidents_dir):
        with patch("shutil.which", return_value=None):
            agent = RepairAgent()
        result = agent.repair_incident("anything")
        assert result["success"] is False
        assert "not available" in result["error"].lower()


class TestRepairLatest:
    def test_no_incidents(self, incidents_dir):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            agent = RepairAgent()
        result = agent.repair_latest()
        assert result["success"] is True
        assert "no open incidents" in result["message"].lower()

    def test_picks_latest(self, incidents_dir):
        _make_incident(incidents_dir, "github", "open")
        with patch("shutil.which", return_value="/usr/bin/claude"):
            agent = RepairAgent()
        with patch.object(agent.cli, "call", return_value={"success": True, "output": "Fixed"}):
            result = agent.repair_latest()
        assert result["success"] is True

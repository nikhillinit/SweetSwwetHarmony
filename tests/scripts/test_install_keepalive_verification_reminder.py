from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "red-team-hybrid" / "install_keepalive_verification_reminder.ps1"


def _powershell() -> str:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("PowerShell is required for keepalive reminder installer tests")
    return exe


def _prepare_project_root(tmp_path: Path) -> Path:
    project_root = tmp_path / "harmonic-preview"
    (project_root / "scripts" / "red-team-hybrid").mkdir(parents=True)
    return project_root


def _run_generate_only(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    exe = _powershell()
    command = [exe, "-NoProfile"]
    if Path(exe).name.lower().startswith("powershell"):
        command.extend(["-ExecutionPolicy", "Bypass"])
    command.extend(
        [
            "-File",
            str(INSTALLER),
            "-ProjectRoot",
            str(project_root),
            "-GenerateOnly",
            *args,
        ]
    )
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_generate_only_writes_one_shot_composite_verifier_runner(tmp_path: Path) -> None:
    project_root = _prepare_project_root(tmp_path)

    result = _run_generate_only(
        project_root,
        "-KeepAliveNextRun",
        "2026-05-15T08:00:00Z",
        "-VerifyAt",
        "2026-05-15T08:30:00Z",
    )

    assert result.returncode == 0, result.stderr
    assert "GenerateOnly specified; skipping all ScheduledTasks cmdlets." in result.stdout
    assert "Expected artifact date: 2026-05-15" in result.stdout
    assert "Registering one-shot verification task" not in result.stdout

    runner = project_root / "scripts" / "red-team-hybrid" / "_HarmonicKeepAliveCompositeVerify.cmd"
    content = runner.read_text(encoding="ascii")

    assert "verify_keepalive_composite_artifact.py" in content
    assert '--task-name "HarmonicKeepAlive"' in content
    assert '--date "2026-05-15"' in content
    assert "2026-05-15-HarmonicKeepAlive-composite-verification.json" in content
    assert "composite-verification.ok.txt" in content
    assert "composite-verification.action-required.txt" in content
    assert "exit /b %VERIFY_EXIT%" in content

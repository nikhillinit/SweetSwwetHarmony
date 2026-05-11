from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "red-team-hybrid" / "install_keepalive_task.ps1"


def _powershell() -> str:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("PowerShell is required for install_keepalive_task.ps1 tests")
    return exe


def _prepare_project_root(tmp_path: Path) -> Path:
    project_root = tmp_path / "harmonic-preview"
    (project_root / "scripts" / "red-team-hybrid").mkdir(parents=True)
    return project_root


def _run_installer_generate_only(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
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


def test_generate_only_preserves_default_keepalive_runner(tmp_path: Path) -> None:
    project_root = _prepare_project_root(tmp_path)

    result = _run_installer_generate_only(project_root)

    assert result.returncode == 0, result.stderr
    assert "GenerateOnly specified; skipping all ScheduledTasks cmdlets." in result.stdout
    assert "Registering scheduled task" not in result.stdout
    assert "Task registered" not in result.stdout

    runner = project_root / "scripts" / "red-team-hybrid" / "_keepalive_daily.cmd"
    content = runner.read_text(encoding="ascii")

    assert "hacker_news,arxiv,rss_feeds,news_api" in content
    assert "--threshold-hours 36" in content
    assert "JOB_POSTING_DOMAINS" not in content
    assert "exit /b 0" not in content


def test_generate_only_writes_non_live_freeze_preview_runner(tmp_path: Path) -> None:
    project_root = _prepare_project_root(tmp_path)

    result = _run_installer_generate_only(
        project_root,
        "-TaskName",
        "HarmonicFreezeDrillPreview",
        "-Collectors",
        "job_postings,github",
        "-WatchdogOperational",
        "rss_feeds,greenhouse_jobs,ashby_jobs",
        "-WatchdogThresholdHours",
        "12",
        "-JobPostingDomains",
        "10beauty.com,cofertility.com,openai.com",
        "-IgnoreWatchdogExitCode",
    )

    assert result.returncode == 0, result.stderr
    assert "GenerateOnly specified; skipping all ScheduledTasks cmdlets." in result.stdout
    assert "HarmonicFreezeDrillPreview" in result.stdout
    assert "Registering scheduled task" not in result.stdout
    assert "Task registered" not in result.stdout

    runner = project_root / "scripts" / "red-team-hybrid" / "_keepalive_HarmonicFreezeDrillPreview.cmd"
    content = runner.read_text(encoding="ascii")

    assert 'set "JOB_POSTING_DOMAINS=10beauty.com,cofertility.com,openai.com"' in content
    assert "python run_pipeline.py collect --collectors job_postings,github" in content
    assert "--threshold-hours 12" in content
    assert "--operational rss_feeds,greenhouse_jobs,ashby_jobs" in content
    assert "exit /b 0" in content

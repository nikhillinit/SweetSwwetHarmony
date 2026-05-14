from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
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


def _run_installer(
    project_root: Path,
    *args: str,
    generate_only: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
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
            *args,
        ]
    )
    if generate_only:
        command.append("-GenerateOnly")
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _run_installer_generate_only(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run_installer(project_root, *args, generate_only=True)


def _run_installer_live(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run_installer(project_root, *args, generate_only=False)


def test_generate_only_preserves_default_keepalive_runner(tmp_path: Path) -> None:
    project_root = _prepare_project_root(tmp_path)

    result = _run_installer_generate_only(project_root)

    assert result.returncode == 0, result.stderr
    assert "GenerateOnly specified; skipping all ScheduledTasks cmdlets." in result.stdout
    assert "Registering scheduled task" not in result.stdout
    assert "Task registered" not in result.stdout

    runner = project_root / "scripts" / "red-team-hybrid" / "_keepalive_daily.cmd"
    content = runner.read_text(encoding="ascii")

    assert 'call "python" run_pipeline.py collect --collectors hacker_news,arxiv,rss_feeds,news_api' in content
    assert "--threshold-hours 36" in content
    assert "--min-created-at \"%KEEPALIVE_RUN_START_UTC%\"" in content
    assert "-HarmonicKeepAlive.json" in content
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
    assert 'call "python" run_pipeline.py collect --collectors job_postings,github' in content
    assert "--threshold-hours 12" in content
    assert "--operational rss_feeds,greenhouse_jobs,ashby_jobs" in content
    assert "--min-created-at \"%KEEPALIVE_RUN_START_UTC%\"" in content
    assert "-HarmonicFreezeDrillPreview.json" in content
    assert "exit /b 0" in content


def test_generate_only_writes_positive_peer_keepalive_trial_runner(tmp_path: Path) -> None:
    project_root = _prepare_project_root(tmp_path)

    result = _run_installer_generate_only(
        project_root,
        "-Collectors",
        "job_postings",
        "-WatchdogOperational",
        "greenhouse_jobs,ashby_jobs",
        "-WatchdogThresholdHours",
        "12",
        "-JobPostingDomains",
        "10beauty.com,cofertility.com,openai.com",
        "-MonitorPingUrlEnvVar",
        "HARMONIC_KEEPALIVE_PING_URL",
    )

    assert result.returncode == 0, result.stderr

    runner = project_root / "scripts" / "red-team-hybrid" / "_keepalive_daily.cmd"
    content = runner.read_text(encoding="ascii")

    assert 'set "JOB_POSTING_DOMAINS=10beauty.com,cofertility.com,openai.com"' in content
    assert 'call "python" run_pipeline.py collect --collectors job_postings' in content
    assert "--threshold-hours 12" in content
    assert "--operational greenhouse_jobs,ashby_jobs" in content
    assert "--min-created-at \"%KEEPALIVE_RUN_START_UTC%\"" in content
    assert "-HarmonicKeepAlive.json" in content
    assert "rss_feeds" not in content
    assert "scripts/red-team-hybrid/keepalive_monitor_ping.py" in content
    assert "--ping-url-env \"HARMONIC_KEEPALIVE_PING_URL\"" in content
    assert 'set "KEEPALIVE_WATCHDOG_EXIT=%ERRORLEVEL%"' in content
    assert 'if not "%KEEPALIVE_WATCHDOG_EXIT%"=="0" exit /b %KEEPALIVE_WATCHDOG_EXIT%' in content
    assert "exit /b 0" not in content


def test_live_keepalive_registration_requires_host_and_monitor_gates(tmp_path: Path) -> None:
    project_root = _prepare_project_root(tmp_path)

    missing_host = _run_installer_live(project_root)
    assert missing_host.returncode != 0
    assert "HostMode is required" in missing_host.stderr
    assert "Registering scheduled task" not in missing_host.stdout

    missing_monitor = _run_installer_live(project_root, "-HostMode", "LocalHost")
    assert missing_monitor.returncode != 0
    assert "MonitorPingUrlEnvVar is required" in missing_monitor.stderr
    assert "Registering scheduled task" not in missing_monitor.stdout

    missing_alert_verification = _run_installer_live(
        project_root,
        "-HostMode",
        "LocalHost",
        "-MonitorPingUrlEnvVar",
        "HARMONIC_KEEPALIVE_PING_URL_TEST_MISSING",
    )
    assert missing_alert_verification.returncode != 0
    assert "MonitorAlertVerified is required" in missing_alert_verification.stderr
    assert "Registering scheduled task" not in missing_alert_verification.stdout

    missing_ping_url = _run_installer_live(
        project_root,
        "-HostMode",
        "LocalHost",
        "-MonitorPingUrlEnvVar",
        "HARMONIC_KEEPALIVE_PING_URL_TEST_MISSING",
        "-MonitorAlertVerified",
    )
    assert missing_ping_url.returncode != 0
    assert "HARMONIC_KEEPALIVE_PING_URL_TEST_MISSING is not set" in missing_ping_url.stderr
    assert "Registering scheduled task" not in missing_ping_url.stdout


def test_generated_runner_quotes_python_exe_with_spaces(tmp_path: Path) -> None:
    project_root = _prepare_project_root(tmp_path)
    python_dir = tmp_path / "python bins"
    python_dir.mkdir()
    fake_python = python_dir / "fake python.cmd"
    fake_python.write_text(
        textwrap.dedent(
            r"""
            @echo off
            echo %*>> "%~dp0calls.txt"
            if "%~1"=="scripts/red-team-hybrid/freshness_watchdog.py" echo {"checked_at":"2026-05-13T15:10:00+00:00","threshold_hours":12,"exit_code":0,"status":"PASS","collectors":[],"failures":[]}
            exit /b 0
            """
        ).lstrip(),
        encoding="ascii",
    )

    result = _run_installer_generate_only(
        project_root,
        "-PythonExe",
        str(fake_python),
        "-Collectors",
        "job_postings",
        "-WatchdogOperational",
        "greenhouse_jobs,ashby_jobs",
        "-WatchdogThresholdHours",
        "12",
    )

    assert result.returncode == 0, result.stderr

    runner = project_root / "scripts" / "red-team-hybrid" / "_keepalive_daily.cmd"
    content = runner.read_text(encoding="ascii")
    assert f'call "{fake_python}" run_pipeline.py collect --collectors job_postings' in content
    assert f'call "{fake_python}" scripts/red-team-hybrid/freshness_watchdog.py' in content

    executed = subprocess.run(
        ["cmd.exe", "/c", str(runner)],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert executed.returncode == 0, executed.stderr
    calls = (python_dir / "calls.txt").read_text(encoding="ascii")
    assert "run_pipeline.py collect --collectors job_postings" in calls
    assert "scripts/red-team-hybrid/freshness_watchdog.py --json --threshold-hours 12" in calls

    artifact_names = {
        artifact.name for artifact in (project_root / "artifacts" / "keepalive").iterdir()
    }
    assert "+-HarmonicKeepAlive.json" not in artifact_names
    assert any(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}-HarmonicKeepAlive\.json", name)
        for name in artifact_names
    )

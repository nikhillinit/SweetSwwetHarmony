from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "red-team-hybrid" / "verify_keepalive_composite_artifact.py"


def _watchdog_payload(*, second_stale_reason: str = "no_post_run_rows") -> dict:
    return {
        "checked_at": "2026-05-15T15:05:00+00:00",
        "threshold_hours": 12,
        "min_created_at": "2026-05-15T15:00:00+00:00",
        "exit_code": 1,
        "status": "FAIL",
        "collectors": [
            {
                "source_api": "greenhouse_jobs",
                "category": "operational",
                "status": "STALE",
                "stale_reason": "no_post_run_rows",
                "required_after": "2026-05-15T15:00:00+00:00",
            },
            {
                "source_api": "ashby_jobs",
                "category": "operational",
                "status": "STALE",
                "stale_reason": second_stale_reason,
                "required_after": "2026-05-15T15:00:00+00:00",
            },
        ],
    }


def _write_composite(
    artifact_dir: Path,
    *,
    overall_status: str = "WARN_DUPLICATE_ONLY",
    db_progress_status: str = "WARN_DUPLICATE_ONLY",
    db_progress_reason: str | None = "no_post_run_rows",
    second_stale_reason: str = "no_post_run_rows",
) -> Path:
    artifact_dir.mkdir(parents=True)
    watchdog = _watchdog_payload(second_stale_reason=second_stale_reason)
    watchdog_path = artifact_dir / "2026-05-15-HarmonicKeepAlive.watchdog.json"
    watchdog_path.write_text(json.dumps(watchdog), encoding="utf-8")

    payload = {
        "kind": "harmonic_keepalive_composite",
        "schema_version": 1,
        "task_name": "HarmonicKeepAlive",
        "mode": "daily_heartbeat",
        "artifact": "2026-05-15-HarmonicKeepAlive.json",
        "watchdog_artifact": watchdog_path.name,
        "collector_exit_code": 0,
        "collector_exit_status": "PASS",
        "watchdog_exit_code": 1,
        "db_progress_status": db_progress_status,
        "db_progress_reason": db_progress_reason,
        "heartbeat_status": overall_status,
        "pre_monitor_exit_code": 0,
        "monitor_delivery_status": "PASS",
        "monitor_exit_code": 0,
        "overall_status": overall_status,
        "exit_code": 0,
        "completed_at": "2026-05-15T15:06:00+00:00",
        "watchdog": watchdog,
    }
    artifact_path = artifact_dir / "2026-05-15-HarmonicKeepAlive.json"
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    return artifact_path


def _run_verifier(artifact_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--artifact-dir",
            str(artifact_dir),
            "--task-name",
            "HarmonicKeepAlive",
            "--date",
            "2026-05-15",
            *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_verifier_accepts_final_warn_duplicate_only_composite(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    report_path = tmp_path / "report.json"
    _write_composite(artifact_dir)

    result = _run_verifier(artifact_dir, "--report", str(report_path))

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["overall_status"] == "WARN_DUPLICATE_ONLY"
    assert report["db_progress_reason"] == "no_post_run_rows"


def test_verifier_rejects_raw_watchdog_artifact(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    raw_path = artifact_dir / "2026-05-15-HarmonicKeepAlive.json"
    raw_path.write_text(json.dumps(_watchdog_payload()), encoding="utf-8")

    result = _run_verifier(artifact_dir)

    assert result.returncode == 1
    assert "artifact kind is not harmonic_keepalive_composite" in result.stdout


def test_verifier_rejects_warn_with_non_duplicate_operational_failure(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    _write_composite(artifact_dir, second_stale_reason="threshold_exceeded")

    result = _run_verifier(artifact_dir)

    assert result.returncode == 1
    assert "non-duplicate operational failures" in result.stdout

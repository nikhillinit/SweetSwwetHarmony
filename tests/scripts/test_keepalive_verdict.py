from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "red-team-hybrid" / "keepalive_verdict.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("keepalive_verdict", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _watchdog_payload(
    *,
    status: str = "OK",
    exit_code: int = 0,
    stale_reason: str | None = None,
    second_stale_reason: str | None = None,
) -> dict:
    source_status = "FRESH" if exit_code == 0 else "STALE"
    first = {
        "source_api": "greenhouse_jobs",
        "category": "operational",
        "last_created": "2026-05-13T15:00:22+00:00",
        "age_hours": 0.16,
        "status": source_status,
        "required_after": "2026-05-13T15:00:00+00:00",
    }
    second = {
        "source_api": "ashby_jobs",
        "category": "operational",
        "last_created": "2026-05-13T15:00:23+00:00",
        "age_hours": 0.15,
        "status": source_status,
        "required_after": "2026-05-13T15:00:00+00:00",
    }
    if stale_reason:
        first["stale_reason"] = stale_reason
    if second_stale_reason:
        second["stale_reason"] = second_stale_reason

    return {
        "checked_at": "2026-05-13T15:10:00+00:00",
        "threshold_hours": 12,
        "min_created_at": "2026-05-13T15:00:00+00:00",
        "exit_code": exit_code,
        "status": status,
        "collectors": [first, second],
        "failures": [] if exit_code == 0 else ["greenhouse_jobs: stale"],
    }


def _compose(module, watchdog: dict, *, mode: str = "daily_heartbeat", collector_exit: int = 0) -> dict:
    return module.compose_payload(
        watchdog,
        collector_exit_code=collector_exit,
        task_name="HarmonicKeepAlive",
        mode=mode,
        artifact_path=Path("2026-05-13-HarmonicKeepAlive.json"),
        watchdog_artifact_path=Path("2026-05-13-HarmonicKeepAlive.watchdog.json"),
        composed_at="2026-05-13T15:11:00+00:00",
    )


def test_daily_duplicate_only_becomes_warning_before_and_after_monitor_success() -> None:
    module = _load_module()
    composite = _compose(
        module,
        _watchdog_payload(
            status="FAIL",
            exit_code=1,
            stale_reason="no_post_run_rows",
            second_stale_reason="no_post_run_rows",
        ),
    )

    assert composite["db_progress_status"] == "WARN_DUPLICATE_ONLY"
    assert composite["db_progress_reason"] == "no_post_run_rows"
    assert composite["heartbeat_status"] == "WARN_DUPLICATE_ONLY"
    assert composite["pre_monitor_exit_code"] == 0

    finalized = module.finalize_payload(
        composite,
        monitor_exit_code=0,
        completed_at="2026-05-13T15:12:00+00:00",
    )

    assert finalized["monitor_delivery_status"] == "PASS"
    assert finalized["overall_status"] == "WARN_DUPLICATE_ONLY"
    assert finalized["exit_code"] == 0


def test_strict_duplicate_only_remains_failure() -> None:
    module = _load_module()
    composite = _compose(
        module,
        _watchdog_payload(
            status="FAIL",
            exit_code=1,
            stale_reason="no_post_run_rows",
            second_stale_reason="no_post_run_rows",
        ),
        mode="strict_write_proof",
    )

    assert composite["db_progress_status"] == "FAIL"
    assert composite["db_progress_reason"] == "no_post_run_rows"
    assert composite["heartbeat_status"] == "FAIL"
    assert composite["pre_monitor_exit_code"] == 1


def test_collector_failure_takes_precedence_even_when_watchdog_passes() -> None:
    module = _load_module()
    composite = _compose(module, _watchdog_payload(), collector_exit=7)

    assert composite["collector_exit_status"] == "FAIL"
    assert composite["db_progress_status"] == "PASS"
    assert composite["heartbeat_status"] == "FAIL"
    assert composite["pre_monitor_exit_code"] == 7


def test_mixed_db_failures_are_hard_failures_in_daily_mode() -> None:
    module = _load_module()
    composite = _compose(
        module,
        _watchdog_payload(
            status="FAIL",
            exit_code=1,
            stale_reason="no_post_run_rows",
            second_stale_reason="threshold_exceeded",
        ),
    )

    assert composite["db_progress_status"] == "FAIL"
    assert composite["db_progress_reason"] == "mixed_failures"
    assert composite["heartbeat_status"] == "FAIL"
    assert composite["pre_monitor_exit_code"] == 1


def test_monitor_failure_turns_otherwise_green_run_into_final_failure() -> None:
    module = _load_module()
    composite = _compose(module, _watchdog_payload())
    finalized = module.finalize_payload(composite, monitor_exit_code=3)

    assert finalized["monitor_delivery_status"] == "FAIL"
    assert finalized["overall_status"] == "FAIL"
    assert finalized["exit_code"] == 3


def test_cli_compose_and_finalize_write_artifact(tmp_path: Path) -> None:
    watchdog_path = tmp_path / "watchdog.json"
    artifact_path = tmp_path / "composite.json"
    watchdog_path.write_text(json.dumps(_watchdog_payload()), encoding="utf-8")

    composed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "compose",
            "--watchdog-json",
            str(watchdog_path),
            "--artifact",
            str(artifact_path),
            "--task-name",
            "HarmonicKeepAlive",
            "--collector-exit",
            "0",
            "--mode",
            "daily_heartbeat",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert composed.returncode == 0, composed.stderr
    composite = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert composite["heartbeat_status"] == "PASS"
    assert "monitor_delivery_status" not in composite

    finalized = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "finalize",
            "--artifact",
            str(artifact_path),
            "--monitor-exit",
            "0",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert finalized.returncode == 0, finalized.stderr
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["monitor_delivery_status"] == "PASS"
    assert payload["overall_status"] == "PASS"
    assert payload["exit_code"] == 0

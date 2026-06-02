from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from integrations.hermes.adapters import ExecutorResult
from integrations.hermes.locks import HermesLock
from integrations.hermes.run import RISK_ACK, run_hermes

from .conftest import minimal_config_dict


def _write_run_config(
    tmp_path: Path,
    *,
    gate_exit: int = 0,
    postflight_exit: int | None = None,
) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = [
        {
            "name": "preflight",
            "command": [
                sys.executable,
                "-c",
                f"import sys; print('preflight'); sys.exit({gate_exit})",
            ],
            "timeoutSeconds": 5,
        }
    ]
    if postflight_exit is not None:
        data["gates"]["postflight"] = [
            {
                "name": "postflight",
                "command": [
                    sys.executable,
                    "-c",
                    f"import sys; print('postflight'); sys.exit({postflight_exit})",
                ],
                "timeoutSeconds": 5,
            }
        ]
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _failure_event(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "failure_event.json").read_text(encoding="utf-8"))


class _FailingExecutor:
    async def execute(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> ExecutorResult:
        return ExecutorResult(
            executor="codex",
            success=False,
            exit_code=12,
            content="",
            duration_ms=1,
            error="executor boom",
        )


async def test_plan_only_returns_routing_plan_without_files(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="plan-only",
        config_path=config_path,
    )

    assert result.exit_code == 0
    assert result.run_id is None
    assert result.plan.recommended_executor == "codex"
    assert not (tmp_path / "ai-logs").exists()


async def test_dry_run_writes_complete_vertical_slice(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="dry-run",
        config_path=config_path,
    )

    assert result.exit_code == 0
    assert result.run_id is not None
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "ledger.json").exists()
    assert (run_dir / "plan.json").exists()
    assert (run_dir / "prompt.txt").exists()
    assert (run_dir / "summary.md").exists()
    assert (run_dir / "state" / "S0_initial.json").exists()
    assert (run_dir / "state" / "S1_routing.json").exists()
    assert (run_dir / "state" / "S2_preflight.json").exists()
    assert (run_dir / "state" / "S3_postflight.json").exists()
    assert "Dry Run" in (run_dir / "summary.md").read_text(encoding="utf-8")

    index_lines = (tmp_path / "ai-logs" / "hermes" / "index.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(index_lines) == 1
    assert json.loads(index_lines[0])["runId"] == result.run_id


async def test_preflight_only_stops_after_preflight_state(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="preflight-only",
        config_path=config_path,
    )

    run_dir = Path(result.run_dir or "")
    assert result.exit_code == 0
    assert (run_dir / "state" / "S2_preflight.json").exists()
    assert not (run_dir / "state" / "S3_postflight.json").exists()


async def test_failed_preflight_returns_gate_failure_exit_code(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path, gate_exit=4)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="dry-run",
        config_path=config_path,
    )

    assert result.exit_code == 4
    assert result.preflight is not None
    assert result.preflight.success is False


async def test_failed_preflight_writes_failure_event_artifact(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path, gate_exit=4)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="dry-run",
        config_path=config_path,
    )

    event = _failure_event(Path(result.run_dir or ""))
    assert event["artifactVersion"] == 1
    assert event["eventType"] == "hermes.run.failure"
    assert event["failureType"] == "preflight"
    assert event["exitCode"] == 4
    assert event["mode"] == "dry-run"
    assert event["runId"] == result.run_id
    assert event["routingPlan"]["task"] == "schema migration"
    assert event["statePaths"] == [
        "state/S0_initial.json",
        "state/S1_routing.json",
        "state/S2_preflight.json",
    ]
    assert event["details"]["gate"]["phase"] == "preflight"
    assert event["details"]["gate"]["name"] == "preflight"
    assert event["details"]["gate"]["returnCode"] == 4
    assert event["nextAction"] == "Fix the failing preflight gate and rerun Hermes."


async def test_failed_postflight_writes_failure_event_artifact(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path, postflight_exit=5)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="dry-run",
        config_path=config_path,
    )

    event = _failure_event(Path(result.run_dir or ""))
    assert event["failureType"] == "postflight"
    assert event["exitCode"] == 4
    assert event["details"]["gate"]["phase"] == "postflight"
    assert event["details"]["gate"]["name"] == "postflight"
    assert event["details"]["gate"]["returnCode"] == 5
    assert event["nextAction"] == "Fix the failing postflight gate and rerun Hermes."


async def test_execute_lock_held_writes_failure_event_artifact(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path)
    lock_path = tmp_path / "ai-logs" / "hermes" / "hermes.lock"
    lock = HermesLock(lock_path, mode="execute", run_id="existing-run")
    assert lock.acquire(timeout_seconds=0) is True

    try:
        result = await run_hermes(
            task="schema migration",
            phase="production",
            mode="execute",
            config_path=config_path,
            ack_risk=RISK_ACK,
        )
    finally:
        lock.release()

    event = _failure_event(Path(result.run_dir or ""))
    assert event["failureType"] == "lock-held"
    assert event["exitCode"] == 6
    assert event["details"]["holder"]["runId"] == "existing-run"
    assert event["statePaths"] == [
        "state/S0_initial.json",
        "state/S1_routing.json",
        "state/S2_lock_held.json",
    ]
    assert event["nextAction"] == "Wait for the current Hermes lock holder or force-unlock only after operator review."


async def test_execute_missing_ack_writes_failure_event_artifact(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="execute",
        config_path=config_path,
    )

    event = _failure_event(Path(result.run_dir or ""))
    assert event["failureType"] == "approval-required"
    assert event["exitCode"] == 75
    assert event["details"]["risk"] == "high"
    assert event["details"]["requiredAck"] == RISK_ACK
    assert event["details"]["providedAck"] is None
    assert event["statePaths"] == [
        "state/S0_initial.json",
        "state/S1_routing.json",
        "state/S2_preflight.json",
        "state/S3_approval_required.json",
    ]
    assert event["nextAction"] == "Rerun execute only with the exact high-risk acknowledgement after operator review."


async def test_executor_failure_writes_failure_event_artifact(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="execute",
        config_path=config_path,
        ack_risk=RISK_ACK,
        executor_factory=lambda _executor, _config: _FailingExecutor(),
    )

    event = _failure_event(Path(result.run_dir or ""))
    assert event["failureType"] == "executor"
    assert event["exitCode"] == 12
    assert event["details"]["executor"] == "codex"
    assert event["details"]["error"] == "executor boom"
    assert event["statePaths"] == [
        "state/S0_initial.json",
        "state/S1_routing.json",
        "state/S2_preflight.json",
        "state/S3_execution.json",
    ]
    assert event["nextAction"] == "Inspect the executor failure state, fix the cause, and rerun with the same routing plan."

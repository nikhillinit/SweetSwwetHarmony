from __future__ import annotations

import json
import sys
from pathlib import Path

from integrations.hermes.adapters import ExecutorResult
from integrations.hermes.locks import HermesLock
from integrations.hermes.run import (
    EXIT_GATE_FAILURE,
    EXIT_HIGH_RISK_ACK_REQUIRED,
    EXIT_LEDGER_FAILURE,
    EXIT_LOCK_HELD,
    run_hermes,
)

from .conftest import minimal_config_dict


class FakeExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, prompt: str, context_files=None) -> ExecutorResult:
        self.calls.append((prompt, context_files))
        return ExecutorResult(
            executor="codex",
            success=True,
            exit_code=0,
            content="executed",
            duration_ms=9,
        )


def _write_execute_config(tmp_path: Path, *, gate_exit: int = 0) -> Path:
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
    data["gates"]["postflight"] = [
        {
            "name": "postflight",
            "command": [sys.executable, "-c", "print('postflight')"],
            "timeoutSeconds": 5,
        }
    ]
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


async def test_execute_high_risk_without_ack_exits_75_and_records_state(
    tmp_path: Path,
) -> None:
    config_path = _write_execute_config(tmp_path)
    executor = FakeExecutor()

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="execute",
        config_path=config_path,
        manual_model="codex",
        executor_factory=lambda name, config: executor,
    )

    assert result.exit_code == EXIT_HIGH_RISK_ACK_REQUIRED
    assert executor.calls == []
    run_dir = Path(result.run_dir or "")
    approval = json.loads(
        (run_dir / "state" / "S3_approval_required.json").read_text(encoding="utf-8")
    )
    assert approval["requiredAck"] == "I-ACK-RISK"


async def test_execute_with_ack_invokes_adapter_and_records_postflight(
    tmp_path: Path,
) -> None:
    config_path = _write_execute_config(tmp_path)
    executor = FakeExecutor()

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="execute",
        config_path=config_path,
        manual_model="codex",
        ack_risk="I-ACK-RISK",
        executor_factory=lambda name, config: executor,
    )

    assert result.exit_code == 0
    assert len(executor.calls) == 1
    run_dir = Path(result.run_dir or "")
    execution = json.loads(
        (run_dir / "state" / "S3_execution.json").read_text(encoding="utf-8")
    )
    assert execution["executor"] == "codex"
    assert (run_dir / "state" / "S4_postflight.json").exists()


async def test_execute_preflight_failure_blocks_adapter(tmp_path: Path) -> None:
    config_path = _write_execute_config(tmp_path, gate_exit=2)
    executor = FakeExecutor()

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="execute",
        config_path=config_path,
        manual_model="codex",
        ack_risk="I-ACK-RISK",
        executor_factory=lambda name, config: executor,
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert executor.calls == []


async def test_execute_returns_lock_exit_when_lock_is_held(tmp_path: Path) -> None:
    config_path = _write_execute_config(tmp_path)
    lock_path = tmp_path / "ai-logs" / "hermes" / "hermes.lock"
    held = HermesLock(lock_path, mode="execute", run_id="held")
    assert held.acquire(timeout_seconds=0) is True

    try:
        result = await run_hermes(
            task="schema migration",
            phase="production",
            mode="execute",
            config_path=config_path,
            manual_model="codex",
            ack_risk="I-ACK-RISK",
            executor_factory=lambda name, config: FakeExecutor(),
        )
    finally:
        held.release()

    assert result.exit_code == EXIT_LOCK_HELD
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "state" / "S2_lock_held.json").exists()


async def test_ledger_write_failure_returns_exit_7(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    data = minimal_config_dict()
    data["ledger"]["root"] = str(blocked / "hermes")
    config_path = tmp_path / "model-routing.json"
    config_path.write_text(json.dumps(data), encoding="utf-8")

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="execute",
        config_path=config_path,
        manual_model="codex",
        ack_risk="I-ACK-RISK",
        executor_factory=lambda name, config: FakeExecutor(),
    )

    assert result.exit_code == EXIT_LEDGER_FAILURE
    assert result.run_id is None


def test_execute_cli_without_ack_exits_75(tmp_path: Path) -> None:
    config_path = _write_execute_config(tmp_path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["gates"]["preflight"] = []
    config_path.write_text(json.dumps(data), encoding="utf-8")

    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ops.cli",
            "hermes",
            "run",
            "--execute",
            "--config",
            str(config_path),
            "--phase",
            "production",
            "--task",
            "schema migration",
            "--codex",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == EXIT_HIGH_RISK_ACK_REQUIRED

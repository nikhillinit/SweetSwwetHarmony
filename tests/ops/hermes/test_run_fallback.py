from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from integrations.hermes.adapters import ExecutorResult
from integrations.hermes.run import RISK_ACK, run_hermes

from .conftest import minimal_config_dict

RATE_LIMIT_ERROR = "HTTP 429 Too Many Requests: retry after 2 minutes"


def _write_fallback_config(
    tmp_path: Path,
    *,
    runtime_fallback_enabled: bool = True,
) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = [
        {
            "name": "preflight",
            "command": [sys.executable, "-c", "print('ok')"],
            "timeoutSeconds": 5,
        }
    ]
    data["routing"]["runtimeFallbackEnabled"] = runtime_fallback_enabled
    data["rateLimits"] = {
        "enabled": True,
        "defaultCooldownMinutes": 60,
        "signatures": {
            "kimi": ["429", "rate limit"],
            "codex": ["429", "rate limit"],
        },
    }
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class _ScriptedExecutor:
    def __init__(
        self,
        name: str,
        *,
        success: bool = True,
        exit_code: int = 0,
        error: str | None = None,
        raises: Exception | None = None,
    ):
        self.name = name
        self.calls: list[str] = []
        self._success = success
        self._exit_code = exit_code
        self._error = error
        self._raises = raises

    async def execute(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> ExecutorResult:
        self.calls.append(prompt)
        if self._raises is not None:
            raise self._raises
        return ExecutorResult(
            executor=self.name,
            success=self._success,
            exit_code=self._exit_code,
            content="done" if self._success else "",
            duration_ms=1,
            error=self._error,
        )


class _Factory:
    def __init__(self, executors: dict[str, _ScriptedExecutor]):
        self.executors = executors
        self.built: list[str] = []

    def __call__(self, name: str, config: Any) -> _ScriptedExecutor:
        self.built.append(name)
        return self.executors[name]


def _execution_state(run_dir: str | None) -> dict[str, Any]:
    return json.loads(
        (Path(run_dir or "") / "state" / "S3_execution.json").read_text(
            encoding="utf-8"
        )
    )


def _provider_state(tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "ai-logs" / "hermes" / "provider-state.json"
    return json.loads(path.read_text(encoding="utf-8"))


async def test_rate_limited_recommended_falls_back_to_alternative(
    tmp_path: Path,
) -> None:
    config_path = _write_fallback_config(tmp_path)
    factory = _Factory(
        {
            "kimi": _ScriptedExecutor(
                "kimi", success=False, exit_code=1, error=RATE_LIMIT_ERROR
            ),
            "codex": _ScriptedExecutor("codex"),
        }
    )

    result = await run_hermes(
        task="thesis filter regression",
        phase="production",
        mode="execute",
        config_path=config_path,
        ack_risk=RISK_ACK,
        executor_factory=factory,
    )

    assert result.plan.recommended_executor == "kimi"
    assert result.exit_code == 0
    assert factory.built == ["kimi", "codex"]

    execution = _execution_state(result.run_dir)
    assert execution["requestedExecutor"] == "kimi"
    assert execution["selectedExecutor"] == "codex"
    statuses = [
        (entry["executor"], entry["status"])
        for entry in execution["providerDiagnostics"]
    ]
    assert ("kimi", "failed") in statuses
    assert ("codex", "fallback") in statuses

    run_dir = Path(result.run_dir or "")
    assert not (run_dir / "failure_event.json").exists()
    assert not (run_dir / "repair_prompt.md").exists()


async def test_rate_limited_failure_sets_provider_cooldown(tmp_path: Path) -> None:
    config_path = _write_fallback_config(tmp_path)
    factory = _Factory(
        {
            "kimi": _ScriptedExecutor(
                "kimi", success=False, exit_code=1, error=RATE_LIMIT_ERROR
            ),
            "codex": _ScriptedExecutor("codex"),
        }
    )

    await run_hermes(
        task="thesis filter regression",
        phase="production",
        mode="execute",
        config_path=config_path,
        ack_risk=RISK_ACK,
        executor_factory=factory,
    )

    state = _provider_state(tmp_path)
    until = datetime.fromisoformat(state["providers"]["kimi"]["coolingUntil"])
    assert until > datetime.now(timezone.utc)


async def test_fallback_disabled_keeps_rate_limited_failure_terminal(
    tmp_path: Path,
) -> None:
    config_path = _write_fallback_config(tmp_path, runtime_fallback_enabled=False)
    factory = _Factory(
        {
            "kimi": _ScriptedExecutor(
                "kimi", success=False, exit_code=1, error=RATE_LIMIT_ERROR
            ),
            "codex": _ScriptedExecutor("codex"),
        }
    )

    result = await run_hermes(
        task="thesis filter regression",
        phase="production",
        mode="execute",
        config_path=config_path,
        ack_risk=RISK_ACK,
        executor_factory=factory,
    )

    assert result.exit_code != 0
    assert factory.built == ["kimi"]
    event = json.loads(
        (Path(result.run_dir or "") / "failure_event.json").read_text(
            encoding="utf-8"
        )
    )
    assert event["failureType"] == "executor"
    assert event["details"]["failureKind"] == "rate_limited"
    # Cooldown is still recorded so the next run can avoid the provider.
    assert "kimi" in _provider_state(tmp_path)["providers"]


async def test_high_risk_plan_never_falls_back(tmp_path: Path) -> None:
    config_path = _write_fallback_config(tmp_path)
    factory = _Factory(
        {
            "codex": _ScriptedExecutor(
                "codex", success=False, exit_code=1, error=RATE_LIMIT_ERROR
            ),
            "kimi": _ScriptedExecutor("kimi"),
        }
    )

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="execute",
        config_path=config_path,
        ack_risk=RISK_ACK,
        executor_factory=factory,
    )

    assert result.exit_code != 0
    assert factory.built == ["codex"]
    execution = _execution_state(result.run_dir)
    statuses = [
        (entry["executor"], entry["status"])
        for entry in execution["providerDiagnostics"]
    ]
    assert ("codex", "blocked") in statuses


async def test_manual_pin_never_falls_back(tmp_path: Path) -> None:
    config_path = _write_fallback_config(tmp_path)
    factory = _Factory(
        {
            "kimi": _ScriptedExecutor(
                "kimi", success=False, exit_code=1, error=RATE_LIMIT_ERROR
            ),
            "codex": _ScriptedExecutor("codex"),
        }
    )

    result = await run_hermes(
        task="thesis filter regression",
        phase="production",
        mode="execute",
        config_path=config_path,
        manual_model="kimi",
        ack_risk=RISK_ACK,
        executor_factory=factory,
    )

    assert result.exit_code != 0
    assert factory.built == ["kimi"]


async def test_cooling_recommended_executor_is_skipped_at_selection(
    tmp_path: Path,
) -> None:
    config_path = _write_fallback_config(tmp_path)
    state_path = tmp_path / "ai-logs" / "hermes" / "provider-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    state_path.write_text(
        json.dumps({"providers": {"kimi": {"coolingUntil": until}}}),
        encoding="utf-8",
    )
    factory = _Factory(
        {
            "kimi": _ScriptedExecutor("kimi"),
            "codex": _ScriptedExecutor("codex"),
        }
    )

    result = await run_hermes(
        task="thesis filter regression",
        phase="production",
        mode="execute",
        config_path=config_path,
        ack_risk=RISK_ACK,
        executor_factory=factory,
    )

    assert result.exit_code == 0
    assert factory.built == ["codex"]
    execution = _execution_state(result.run_dir)
    assert execution["selectedExecutor"] == "codex"
    statuses = [
        (entry["executor"], entry["status"])
        for entry in execution["providerDiagnostics"]
    ]
    assert ("kimi", "skipped") in statuses


async def test_executor_exception_is_classified_and_falls_back(
    tmp_path: Path,
) -> None:
    config_path = _write_fallback_config(tmp_path)
    factory = _Factory(
        {
            "kimi": _ScriptedExecutor(
                "kimi",
                raises=FileNotFoundError(
                    "[WinError 2] The system cannot find the file specified: 'kimi-cli'"
                ),
            ),
            "codex": _ScriptedExecutor("codex"),
        }
    )

    result = await run_hermes(
        task="thesis filter regression",
        phase="production",
        mode="execute",
        config_path=config_path,
        ack_risk=RISK_ACK,
        executor_factory=factory,
    )

    assert result.exit_code == 0
    assert factory.built == ["kimi", "codex"]
    execution = _execution_state(result.run_dir)
    statuses = [
        (entry["executor"], entry["status"])
        for entry in execution["providerDiagnostics"]
    ]
    assert ("kimi", "failed") in statuses


async def test_executor_exception_without_fallback_writes_failure_event(
    tmp_path: Path,
) -> None:
    config_path = _write_fallback_config(tmp_path, runtime_fallback_enabled=False)
    factory = _Factory(
        {
            "kimi": _ScriptedExecutor(
                "kimi", raises=FileNotFoundError("No such file or directory")
            ),
            "codex": _ScriptedExecutor("codex"),
        }
    )

    result = await run_hermes(
        task="thesis filter regression",
        phase="production",
        mode="execute",
        config_path=config_path,
        ack_risk=RISK_ACK,
        executor_factory=factory,
    )

    assert result.exit_code == 1
    assert factory.built == ["kimi"]
    event = json.loads(
        (Path(result.run_dir or "") / "failure_event.json").read_text(
            encoding="utf-8"
        )
    )
    assert event["failureType"] == "executor"
    assert event["details"]["failureKind"] == "spawn_error"


async def test_nonzero_exit_never_falls_back(tmp_path: Path) -> None:
    config_path = _write_fallback_config(tmp_path)
    factory = _Factory(
        {
            "kimi": _ScriptedExecutor(
                "kimi", success=False, exit_code=12, error="executor boom"
            ),
            "codex": _ScriptedExecutor("codex"),
        }
    )

    result = await run_hermes(
        task="thesis filter regression",
        phase="production",
        mode="execute",
        config_path=config_path,
        ack_risk=RISK_ACK,
        executor_factory=factory,
    )

    assert result.exit_code == 12
    assert factory.built == ["kimi"]
    execution = _execution_state(result.run_dir)
    statuses = [
        (entry["executor"], entry["status"])
        for entry in execution["providerDiagnostics"]
    ]
    assert ("kimi", "blocked") in statuses

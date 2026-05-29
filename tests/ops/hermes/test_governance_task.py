from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from integrations.hermes.locks import HermesLock
from integrations.hermes.tasks.base import (
    EXIT_ACK_REQUIRED,
    EXIT_GATE_FAILURE,
    EXIT_LOCK_HELD,
    EXIT_TASK_FAILURE,
)
from integrations.hermes.tasks.registry import add_task_arguments, run_registered_task

from .conftest import minimal_config_dict


def _config_path(tmp_path: Path) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = []
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _args(
    tmp_path: Path,
    *,
    feature: str | None = "boilerplate_defense",
    from_state: str | None = "shadow",
    target_state: str | None = "active",
    reason: str | None = "Hermes governance test",
    mode: str = "preflight-only",
    ack_risk: str | None = None,
    state_source: Path | None = None,
    state_verify_attempts: int = 1,
    state_verify_delay_seconds: float = 0.0,
) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="governance",
        config=str(_config_path(tmp_path)),
        plan_only=mode == "plan-only",
        preflight_only=mode == "preflight-only",
        dry_run=mode == "dry-run",
        execute=mode == "execute",
        ack_risk=ack_risk,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="test",
        json_output=False,
        feature=feature,
        from_state=from_state,
        target_state=target_state,
        reason=reason,
        regret_check_date=None,
        effective_at=None,
        repair_source=None,
        rollback_ticket=None,
        incident_id=None,
        direct_db=None,
        state_source=str(state_source) if state_source else None,
        state_verify_attempts=state_verify_attempts,
        state_verify_delay_seconds=state_verify_delay_seconds,
    )


def test_plan_only_writes_ledger_artifacts_and_stays_non_mutating(
    tmp_path: Path,
) -> None:
    result = run_registered_task(
        _args(tmp_path, mode="plan-only"),
    )

    assert result.exit_code == 0
    assert result.status == "planned"
    assert result.plan["mutation"]["allowed"] is False
    assert result.plan["transition"]["action_type"] == "feature_promote"
    assert result.plan["transition"]["ack_risk_token"] == "GOVERNANCE_PROMOTE"
    assert result.plan["command"][1:4] == ["-m", "governance", "feature"]
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "task_plan.json").exists()
    assert (run_dir / "run_record.json").exists()
    assert (run_dir / "plan.md").exists()


def test_plan_records_inverse_rollback_command(tmp_path: Path) -> None:
    result = run_registered_task(_args(tmp_path, mode="plan-only"))

    rollback = result.plan["rollback"]["command"]

    assert result.exit_code == 0
    assert rollback[1:5] == ["-m", "governance", "feature", "demote"]
    assert rollback[rollback.index("--from") + 1] == "active"
    assert rollback[rollback.index("--to") + 1] == "shadow"


def test_registry_parser_exposes_state_verification_retry_options() -> None:
    parser = argparse.ArgumentParser()
    add_task_arguments(parser)

    args = parser.parse_args(
        [
            "governance",
            "--state-verify-attempts",
            "4",
            "--state-verify-delay-seconds",
            "0.25",
        ]
    )

    assert args.state_verify_attempts == 4
    assert args.state_verify_delay_seconds == 0.25


@pytest.mark.parametrize(
    "missing_field, overrides",
    [
        ("feature_declared", {"feature": None}),
        ("from_state_declared", {"from_state": None}),
        ("target_state_declared", {"target_state": None}),
    ],
)
def test_missing_required_transition_input_fails_preflight_safely(
    tmp_path: Path,
    missing_field: str,
    overrides: dict[str, None],
) -> None:
    result = run_registered_task(_args(tmp_path, **overrides))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    assert any(check.name == missing_field and not check.passed for check in result.checks)
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_unknown_feature_fails_preflight_through_state_policy(
    tmp_path: Path,
) -> None:
    result = run_registered_task(
        _args(tmp_path, feature="UNKNOWN_FEATURE", from_state="off", target_state="shadow")
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(check for check in result.checks if check.name == "feature_registered")
    assert check.passed is False
    assert "not registered for governance" in check.detail
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_invalid_transition_fails_preflight_safely(tmp_path: Path) -> None:
    result = run_registered_task(
        _args(tmp_path, feature="LLM_THESIS_MODE", from_state="off", target_state="active")
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(check for check in result.checks if check.name == "transition_allowed")
    assert check.passed is False
    assert "Skip-level" in check.detail
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_lock_conflict_on_governance_refuses_mutation(tmp_path: Path) -> None:
    config_path = _config_path(tmp_path)
    lock_path = tmp_path / "ai-logs" / "hermes" / "task-locks" / "governance.lock"
    lock = HermesLock(lock_path, mode="execute", run_id="held")
    assert lock.acquire(timeout_seconds=0) is True

    try:
        args = _args(tmp_path, mode="execute", ack_risk="GOVERNANCE_PROMOTE")
        args.config = str(config_path)
        result = run_registered_task(args)
    finally:
        lock.release()

    assert result.exit_code == EXIT_LOCK_HELD
    assert result.status == "lock_held"
    assert (Path(result.run_dir or "") / "lock_conflict.json").exists()


def test_dry_run_records_would_run_command_without_calling_governance_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_run_command(*_: Any, **__: Any) -> dict[str, Any]:
        raise AssertionError("dry-run must not call governance CLI")

    monkeypatch.setattr("integrations.hermes.tasks.governance.run_command", fail_run_command)

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    assert result.outputs["dryRun"] is True
    assert result.outputs["mutationCommitted"] is False
    assert result.outputs["command"][1:4] == ["-m", "governance", "feature"]
    assert result.outputs["command"][4] == "promote"
    assert (Path(result.run_dir or "") / "governance_command.json").exists()


def test_execute_promote_requires_promote_ack_before_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    state_source = tmp_path / "feature-state.json"
    state_source.write_text(
        json.dumps({"boilerplate_defense": {"state": "shadow"}}),
        encoding="utf-8",
    )

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        calls.append(command)
        return {"command": command, "returnCode": 0, "stdout": "", "stderr": "", "timedOut": False}

    monkeypatch.setattr("integrations.hermes.tasks.governance.run_command", fake_run_command)

    result = run_registered_task(
        _args(tmp_path, mode="execute", state_source=state_source)
    )

    assert result.exit_code == EXIT_ACK_REQUIRED
    assert result.status == "approval_required"
    assert calls == []
    assert (Path(result.run_dir or "") / "approval_required.json").exists()


def test_execute_demote_requires_rollback_ack_before_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    state_source = tmp_path / "feature-state.json"
    state_source.write_text(
        json.dumps({"boilerplate_defense": {"state": "active"}}),
        encoding="utf-8",
    )

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        calls.append(command)
        return {"command": command, "returnCode": 0, "stdout": "", "stderr": "", "timedOut": False}

    monkeypatch.setattr("integrations.hermes.tasks.governance.run_command", fake_run_command)

    result = run_registered_task(
        _args(
            tmp_path,
            from_state="active",
            target_state="shadow",
            mode="execute",
            state_source=state_source,
        )
    )

    assert result.exit_code == EXIT_ACK_REQUIRED
    assert result.status == "approval_required"
    assert calls == []
    assert (Path(result.run_dir or "") / "approval_required.json").exists()


def test_execute_with_ack_calls_live_shaped_command_and_records_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    state_source = tmp_path / "feature-state.json"
    state_source.write_text(
        json.dumps({"boilerplate_defense": {"state": "shadow"}}),
        encoding="utf-8",
    )

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        calls.append(command)
        state_source.write_text(
            json.dumps({"boilerplate_defense": {"state": "active"}}),
            encoding="utf-8",
        )
        return {
            "command": command,
            "returnCode": 0,
            "stdout": '{"event_id": 123}',
            "stderr": "",
            "timedOut": False,
        }

    monkeypatch.setattr("integrations.hermes.tasks.governance.run_command", fake_run_command)

    result = run_registered_task(
        _args(
            tmp_path,
            mode="execute",
            ack_risk="GOVERNANCE_PROMOTE",
            state_source=state_source,
        )
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    assert len(calls) == 1
    assert calls[0][1:5] == ["-m", "governance", "feature", "promote"]
    assert "boilerplate_defense" in calls[0]
    assert "--from" in calls[0]
    assert "--to" in calls[0]
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "governance_command.json").exists()
    assert (run_dir / "run_record.json").exists()


def test_execute_requires_readable_state_source_before_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        calls.append(command)
        return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("integrations.hermes.tasks.governance.run_command", fake_run_command)

    result = run_registered_task(
        _args(tmp_path, mode="execute", ack_risk="GOVERNANCE_PROMOTE")
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(check for check in result.checks if check.name == "state_source_readable")
    assert check.passed is False
    assert calls == []
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_execute_command_failure_emits_repair_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_source = tmp_path / "feature-state.json"
    state_source.write_text(
        json.dumps({"boilerplate_defense": {"state": "shadow"}}),
        encoding="utf-8",
    )

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        return {
            "command": command,
            "returnCode": 1,
            "stdout": "",
            "stderr": "governance failed",
            "timedOut": False,
        }

    monkeypatch.setattr("integrations.hermes.tasks.governance.run_command", fake_run_command)

    result = run_registered_task(
        _args(
            tmp_path,
            mode="execute",
            ack_risk="GOVERNANCE_PROMOTE",
            state_source=state_source,
        )
    )

    assert result.exit_code == EXIT_TASK_FAILURE
    assert result.status == "failed"
    assert "command failed" in (result.error or "")
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "execute_failure.json").exists()
    assert (run_dir / "repair_prompt.md").exists()


def test_postflight_catches_mismatched_final_state_when_state_source_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_source = tmp_path / "feature-state.json"
    state_source.write_text(
        json.dumps({"boilerplate_defense": {"state": "shadow"}}),
        encoding="utf-8",
    )

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        return {"command": command, "returnCode": 0, "stdout": "", "stderr": "", "timedOut": False}

    monkeypatch.setattr("integrations.hermes.tasks.governance.run_command", fake_run_command)

    result = run_registered_task(
        _args(
            tmp_path,
            mode="execute",
            ack_risk="GOVERNANCE_PROMOTE",
            state_source=state_source,
            state_verify_attempts=3,
        )
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "postflight_failed"
    check = next(check for check in result.checks if check.name == "final_state_matches_target")
    assert check.passed is False
    assert len(check.evidence["attempts"]) == 3
    assert check.evidence["actual_state"] == "shadow"
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_postflight_uses_planned_state_source_after_args_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planned_state_source = tmp_path / "planned-feature-state.json"
    planned_state_source.write_text(
        json.dumps({"boilerplate_defense": {"state": "shadow"}}),
        encoding="utf-8",
    )
    stale_args_state_source = tmp_path / "stale-args-feature-state.json"
    stale_args_state_source.write_text(
        json.dumps({"boilerplate_defense": {"state": "shadow"}}),
        encoding="utf-8",
    )
    args = _args(
        tmp_path,
        mode="execute",
        ack_risk="GOVERNANCE_PROMOTE",
        state_source=planned_state_source,
    )

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        args.state_source = str(stale_args_state_source)
        planned_state_source.write_text(
            json.dumps({"boilerplate_defense": {"state": "active"}}),
            encoding="utf-8",
        )
        return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("integrations.hermes.tasks.governance.run_command", fake_run_command)

    result = run_registered_task(args)

    assert result.exit_code == 0
    check = next(check for check in result.checks if check.name == "final_state_matches_target")
    assert check.passed is True
    assert check.evidence["path"] == str(planned_state_source)


def test_delayed_final_state_propagation_passes_after_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_source = tmp_path / "feature-state.json"
    state_source.write_text(
        json.dumps({"boilerplate_defense": {"state": "shadow"}}),
        encoding="utf-8",
    )
    sleeps: list[float] = []

    def fake_run_command(command: list[str], **_: Any) -> dict[str, Any]:
        return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

    def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        state_source.write_text(
            json.dumps({"boilerplate_defense": {"state": "active"}}),
            encoding="utf-8",
        )

    monkeypatch.setattr("integrations.hermes.tasks.governance.run_command", fake_run_command)
    monkeypatch.setattr("time.sleep", fake_sleep)

    result = run_registered_task(
        _args(
            tmp_path,
            mode="execute",
            ack_risk="GOVERNANCE_PROMOTE",
            state_source=state_source,
            state_verify_attempts=2,
            state_verify_delay_seconds=0.5,
        )
    )

    assert result.exit_code == 0
    check = next(check for check in result.checks if check.name == "final_state_matches_target")
    assert check.passed is True
    assert len(check.evidence["attempts"]) == 2
    assert sleeps == [0.5]

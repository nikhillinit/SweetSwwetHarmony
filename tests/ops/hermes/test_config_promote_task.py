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
)
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


def _base_config(tmp_path: Path) -> dict[str, Any]:
    data = minimal_config_dict()
    data["executors"]["codex"].pop("binary", None)
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = []
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _current_config_path(tmp_path: Path) -> Path:
    return _write_json(tmp_path / "model-routing.json", _base_config(tmp_path))


def _proposed_config_path(tmp_path: Path, current_path: Path) -> Path:
    data = json.loads(current_path.read_text(encoding="utf-8"))
    data["ledger"]["redactionPatterns"].append("config-promote-test-[A-Z]+")
    return _write_json(tmp_path / "proposed-model-routing.json", data)


def _args(
    tmp_path: Path,
    *,
    current_path: Path | None = None,
    proposed_path: Path | None = None,
    mode: str = "preflight-only",
    ack_risk: str | None = None,
    policy_evidence: list[str] | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="config-promote",
        config=str(current_path or _current_config_path(tmp_path)),
        plan_only=mode == "plan-only",
        preflight_only=mode == "preflight-only",
        dry_run=mode == "dry-run",
        execute=mode == "execute",
        ack_risk=ack_risk,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="test",
        json_output=False,
        proposed=str(proposed_path) if proposed_path else None,
        policy_evidence=policy_evidence or [],
    )


def test_plan_only_writes_ledger_artifacts_and_stays_non_mutating(
    tmp_path: Path,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed, mode="plan-only"),
    )

    assert result.exit_code == 0
    assert result.status == "planned"
    assert result.plan["mutation"]["allowed"] is False
    assert result.plan["mutation"]["external_systems"] == []
    assert result.plan["current_config"]["sha256"]
    assert result.plan["proposed_config"]["sha256"]
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "task_plan.json").exists()
    assert (run_dir / "run_record.json").exists()
    assert (run_dir / "plan.md").exists()


def test_missing_proposed_argument_fails_safely_and_emits_repair_prompt(
    tmp_path: Path,
) -> None:
    current = _current_config_path(tmp_path)

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=None),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    assert any(
        check.name == "proposed_argument_present" and not check.passed
        for check in result.checks
    )
    assert current.exists()
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_missing_proposed_file_fails_safely_and_emits_repair_prompt(
    tmp_path: Path,
) -> None:
    current = _current_config_path(tmp_path)
    missing = tmp_path / "missing-model-routing.json"

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=missing),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    assert any(
        check.name == "proposed_file_exists" and not check.passed
        for check in result.checks
    )
    assert missing.exists() is False
    assert current.exists()
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_missing_current_config_fails_preflight_without_fresh_create(
    tmp_path: Path,
) -> None:
    runtime = _current_config_path(tmp_path)
    current = tmp_path / "missing-model-routing.json"
    proposed = _proposed_config_path(tmp_path, runtime)

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    assert current.exists() is False
    check = next(check for check in result.checks if check.name == "current_config_readable")
    assert check.passed is False
    assert check.evidence["path"] == str(current)
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_malformed_proposed_json_fails_safely_and_emits_repair_prompt(
    tmp_path: Path,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = tmp_path / "proposed-model-routing.json"
    proposed.write_text("{not-json", encoding="utf-8")

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(check for check in result.checks if check.name == "proposed_json_valid")
    assert check.passed is False
    assert current.read_text(encoding="utf-8")
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_non_utf8_proposed_json_fails_safely_and_emits_repair_prompt(
    tmp_path: Path,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = tmp_path / "proposed-model-routing.json"
    proposed.write_bytes(b"\xff\xfe\x00")

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(check for check in result.checks if check.name == "proposed_json_valid")
    assert check.passed is False
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_invalid_proposed_schema_fails_safely(tmp_path: Path) -> None:
    current = _current_config_path(tmp_path)
    data = json.loads(current.read_text(encoding="utf-8"))
    data["routing"]["unknownTaskExecutor"] = "missing"
    proposed = _write_json(tmp_path / "invalid-model-routing.json", data)

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(
        check for check in result.checks if check.name == "proposed_config_schema_valid"
    )
    assert check.passed is False
    assert "unknown executor reference" in check.detail
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_provider_doctor_failure_fails_preflight_and_emits_repair_prompt(
    tmp_path: Path,
) -> None:
    current = _current_config_path(tmp_path)
    data = json.loads(current.read_text(encoding="utf-8"))
    data["executors"]["kimi"]["required"] = True
    proposed = _write_json(tmp_path / "provider-fail-model-routing.json", data)

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(
        check
        for check in result.checks
        if check.name == "provider_doctor_passes_for_required_executors"
    )
    assert check.passed is False
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_routing_policy_change_requires_explicit_evidence(tmp_path: Path) -> None:
    current = _current_config_path(tmp_path)
    data = json.loads(current.read_text(encoding="utf-8"))
    data["routing"]["fallbackOrder"] = ["kimi", "codex"]
    proposed = _write_json(tmp_path / "routing-change-model-routing.json", data)

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(
        check for check in result.checks if check.name == "policy_changes_have_evidence"
    )
    assert check.passed is False
    assert check.evidence["requires_evidence"] is True
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_dry_run_writes_diff_and_report_without_modifying_config(
    tmp_path: Path,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)
    before = current.read_text(encoding="utf-8")

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed, mode="dry-run"),
    )

    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    assert current.read_text(encoding="utf-8") == before
    assert result.outputs["dryRun"] is True
    assert result.outputs["mutationCommitted"] is False
    assert result.outputs["currentConfig"]["path"] == str(current)
    run_dir = Path(result.run_dir or "")
    diff = json.loads((run_dir / "config_promote_diff.json").read_text(encoding="utf-8"))
    report = json.loads(
        (run_dir / "config_promote_report.json").read_text(encoding="utf-8")
    )
    assert diff["currentConfig"]["path"] == str(current)
    assert report["currentConfig"]["path"] == str(current)


def test_execute_requires_config_promote_ack_before_mutation(
    tmp_path: Path,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)
    before = current.read_text(encoding="utf-8")

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed, mode="execute"),
    )

    assert result.exit_code == EXIT_ACK_REQUIRED
    assert result.status == "approval_required"
    assert current.read_text(encoding="utf-8") == before
    assert (Path(result.run_dir or "") / "approval_required.json").exists()


def test_execute_snapshots_previous_config_and_atomically_replaces_current(
    tmp_path: Path,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)
    before = current.read_text(encoding="utf-8")
    proposed_payload = json.loads(proposed.read_text(encoding="utf-8"))

    result = run_registered_task(
        _args(
            tmp_path,
            current_path=current,
            proposed_path=proposed,
            mode="execute",
            ack_risk="CONFIG_PROMOTE",
        ),
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    assert json.loads(current.read_text(encoding="utf-8")) == proposed_payload
    assert result.outputs["mutationCommitted"] is True
    run_dir = Path(result.run_dir or "")
    snapshot = run_dir / "snapshots" / "model-routing.previous.json"
    assert snapshot.read_text(encoding="utf-8") == before
    assert (run_dir / "config_promote_report.json").exists()


def test_execute_snapshot_failure_emits_structured_repair_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)
    before = current.read_text(encoding="utf-8")

    def fail_snapshot(*_: Any, **__: Any) -> Path:
        raise OSError("snapshot location unavailable")

    monkeypatch.setattr(
        "integrations.hermes.tasks.config_promote.copy_snapshot",
        fail_snapshot,
    )

    result = run_registered_task(
        _args(
            tmp_path,
            current_path=current,
            proposed_path=proposed,
            mode="execute",
            ack_risk="CONFIG_PROMOTE",
        ),
    )

    assert result.exit_code == 1
    assert result.status == "failed"
    assert current.read_text(encoding="utf-8") == before
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "execute_failure.json").exists()
    assert (run_dir / "repair_prompt.md").exists()
    assert "previous config snapshot failed" in (result.error or "")


def test_execute_post_replace_report_failure_emits_repair_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)
    proposed_payload = json.loads(proposed.read_text(encoding="utf-8"))

    def fail_report(*_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError("report write input failed")

    monkeypatch.setattr(
        "integrations.hermes.tasks.config_promote._report_payload",
        fail_report,
    )

    result = run_registered_task(
        _args(
            tmp_path,
            current_path=current,
            proposed_path=proposed,
            mode="execute",
            ack_risk="CONFIG_PROMOTE",
        ),
    )

    assert result.exit_code == 1
    assert result.status == "failed"
    assert json.loads(current.read_text(encoding="utf-8")) == proposed_payload
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "execute_failure.json").exists()
    assert (run_dir / "repair_prompt.md").exists()
    assert "config promotion execute failed" in (result.error or "")


def test_postflight_catches_hash_mismatch_after_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)
    before = current.read_text(encoding="utf-8")

    def fake_execute(_: Any, context: Any, plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "task": "config-promote",
            "runId": context.run.run_id,
            "dryRun": False,
            "mutationCommitted": True,
            "currentConfig": {
                "path": plan["current_config"]["path"],
                "sha256Before": plan["current_config"].get("sha256"),
                "sha256After": plan["current_config"].get("sha256"),
            },
            "proposedConfig": {
                "path": plan["proposed_config"]["path"],
                "sha256": plan["proposed_config"].get("sha256"),
            },
        }

    monkeypatch.setattr(
        "integrations.hermes.tasks.config_promote.ConfigPromoteTask.execute",
        fake_execute,
    )

    result = run_registered_task(
        _args(
            tmp_path,
            current_path=current,
            proposed_path=proposed,
            mode="execute",
            ack_risk="CONFIG_PROMOTE",
        ),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "postflight_failed"
    assert current.read_text(encoding="utf-8") == before
    failed_checks = {check.name for check in result.checks if not check.passed}
    assert "config_hash_matches_expected" in failed_checks
    assert "previous_config_snapshot_written" in failed_checks
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_postflight_catches_schema_mismatch_after_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)

    def fake_execute(_: Any, context: Any, plan: dict[str, Any]) -> dict[str, Any]:
        current_path = Path(plan["current_config"]["path"])
        current_path.write_text("{\"schemaVersion\": 1}", encoding="utf-8")
        return {
            "task": "config-promote",
            "runId": context.run.run_id,
            "dryRun": False,
            "mutationCommitted": True,
        }

    monkeypatch.setattr(
        "integrations.hermes.tasks.config_promote.ConfigPromoteTask.execute",
        fake_execute,
    )

    result = run_registered_task(
        _args(
            tmp_path,
            current_path=current,
            proposed_path=proposed,
            mode="execute",
            ack_risk="CONFIG_PROMOTE",
        ),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "postflight_failed"
    failed_checks = {check.name for check in result.checks if not check.passed}
    assert "current_config_schema_valid" in failed_checks
    assert "provider_doctor_postflight" in failed_checks
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_postflight_catches_provider_doctor_mismatch_after_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)

    class FakeReport:
        def __init__(self, *, success: bool) -> None:
            self.success = success

        def to_dict(self) -> dict[str, Any]:
            return {"success": self.success, "providers": {}, "systemChecks": []}

    calls = 0

    def fake_doctor(_: Any) -> Any:
        nonlocal calls
        calls += 1
        return FakeReport(success=calls == 1)

    monkeypatch.setattr(
        "integrations.hermes.tasks.config_promote.provider_doctor",
        fake_doctor,
    )

    result = run_registered_task(
        _args(
            tmp_path,
            current_path=current,
            proposed_path=proposed,
            mode="execute",
            ack_risk="CONFIG_PROMOTE",
        ),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "postflight_failed"
    failed_checks = {check.name for check in result.checks if not check.passed}
    assert "provider_doctor_postflight" in failed_checks
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_lock_conflict_refuses_mutation(tmp_path: Path) -> None:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)
    before = current.read_text(encoding="utf-8")
    lock_path = tmp_path / "ai-logs" / "hermes" / "task-locks" / "hermes-config.lock"
    lock = HermesLock(lock_path, mode="execute", run_id="held")
    assert lock.acquire(timeout_seconds=0) is True

    try:
        result = run_registered_task(
            _args(
                tmp_path,
                current_path=current,
                proposed_path=proposed,
                mode="execute",
                ack_risk="CONFIG_PROMOTE",
            ),
        )
    finally:
        lock.release()

    assert result.exit_code == EXIT_LOCK_HELD
    assert result.status == "lock_held"
    assert current.read_text(encoding="utf-8") == before
    assert (Path(result.run_dir or "") / "lock_conflict.json").exists()

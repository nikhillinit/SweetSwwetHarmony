from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from integrations.hermes.tasks.base import EXIT_GATE_FAILURE
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


def _config_path(tmp_path: Path) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = []
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="ledger-audit",
        config=str(_config_path(tmp_path)),
        plan_only=False,
        preflight_only=False,
        dry_run=True,
        execute=False,
        ack_risk=None,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="test",
        json_output=False,
        check="all",
        finding_severity_threshold="critical",
    )


def _ledger_root(tmp_path: Path) -> Path:
    return tmp_path / "ai-logs" / "hermes"


def _append_index(root: Path, row: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(row, separators=(",", ":"))
    with (root / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(payload + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _config_plan(
    *,
    current_sha256: str = "sha256:config-current",
    proposed_sha256: str = "sha256:config-proposed",
    contract_version: int = 2,
) -> dict[str, Any]:
    return {
        "contractVersion": contract_version,
        "task": "config-promote",
        "mode": "execute",
        "risk_level": "high",
        "planHash": "sha256:config-plan",
        "current_config": {
            "path": "config/model-routing.json",
            "sha256": current_sha256,
        },
        "proposed_config": {
            "path": "tmp/proposed-model-routing.json",
            "sha256": proposed_sha256,
        },
        "artifacts": {
            "config_diff": "config_promote_diff.json",
            "config_report": "config_promote_report.json",
            "previous_snapshot": "snapshots/model-routing.previous.json",
            "run_record": "run_record.json",
            "task_plan": "task_plan.json",
        },
        "mutation": {
            "allowed": True,
            "affected_files": ["config/model-routing.json"],
        },
    }


def _config_snapshot() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "executors": {},
        "routing": {},
    }


def _config_report(
    run_id: str,
    *,
    artifact_version: int | None = 1,
    current_sha256: str = "sha256:config-current",
    proposed_sha256: str = "sha256:config-proposed",
    previous_snapshot_ref: str | None = "snapshots/model-routing.previous.json",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "generatedAt": "2026-06-01T00:00:00+00:00",
        "task": "config-promote",
        "runId": run_id,
        "dryRun": False,
        "mutationCommitted": True,
        "currentConfig": {
            "path": "config/model-routing.json",
            "sha256Before": current_sha256,
            "sha256After": proposed_sha256,
        },
        "proposedConfig": {
            "path": "tmp/proposed-model-routing.json",
            "sha256": proposed_sha256,
        },
        "previousSnapshotRef": previous_snapshot_ref,
        "diffArtifact": "config_promote_diff.json",
        "policyReview": {
            "risky_changes": [],
            "requires_evidence": False,
            "evidence": [],
        },
    }
    if artifact_version is not None:
        payload["artifactVersion"] = artifact_version
    return payload


def _config_diff(
    *,
    artifact_version: int | None = 1,
    current_sha256: str = "sha256:config-current",
    proposed_sha256: str = "sha256:config-proposed",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "generatedAt": "2026-06-01T00:00:00+00:00",
        "currentConfig": {
            "path": "config/model-routing.json",
            "sha256": current_sha256,
        },
        "proposedConfig": {
            "path": "tmp/proposed-model-routing.json",
            "sha256": proposed_sha256,
        },
        "diff": {
            "sections_changed": [],
            "executors_added": [],
            "executors_removed": [],
            "executor_changes": [],
            "execute_support_changes": {},
            "routing_policy_changes": [],
            "unified_diff": [],
        },
        "policyReview": {
            "risky_changes": [],
            "requires_evidence": False,
            "evidence": [],
        },
    }
    if artifact_version is not None:
        payload["artifactVersion"] = artifact_version
    return payload


def _config_run_record(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "contract_version": 2,
        "task": "config-promote",
        "mode": "execute",
        "risk_level": "high",
        "status": "executed",
        "inputs": {
            "task_name": "config-promote",
            "proposed": "tmp/proposed-model-routing.json",
        },
        "outputs": _config_report(run_id),
        "plan_ref": "task_plan.json",
    }


def _governance_plan(
    *,
    feature: str = "boilerplate_defense",
    from_state: str = "shadow",
    target_state: str = "active",
) -> dict[str, Any]:
    return {
        "contractVersion": 2,
        "task": "governance",
        "mode": "execute",
        "risk_level": "high",
        "planHash": "sha256:governance-plan",
        "feature": feature,
        "from_state": from_state,
        "target_state": target_state,
        "transition": {
            "valid": True,
            "action_type": "feature_promote",
            "cli_subcommand": "promote",
            "ack_risk_token": "GOVERNANCE_PROMOTE",
        },
        "state_verification": {
            "attempts": 1,
            "delay_seconds": 0.0,
            "state_source": "tmp/feature-state.json",
        },
        "mutation": {
            "allowed": True,
            "affected_tables": ["audit_events"],
        },
    }


def _governance_command(
    *,
    artifact_version: int | None = 1,
    feature: str = "boilerplate_defense",
    from_state: str = "shadow",
    target_state: str = "active",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "command": [
            "python",
            "-m",
            "governance",
            "feature",
            "promote",
            feature,
            "--from",
            from_state,
            "--to",
            target_state,
            "--reason",
            "Hermes governance test",
        ],
        "result": {
            "returnCode": 0,
            "stdout": "",
            "stderr": "",
            "timedOut": False,
        },
    }
    if artifact_version is not None:
        payload["artifactVersion"] = artifact_version
    return payload


def _pre_governance_state() -> dict[str, Any]:
    return {
        "artifactVersion": 1,
        "path": "tmp/feature-state.json",
        "readable": True,
        "feature": "boilerplate_defense",
        "state": "shadow",
        "actual_state": "shadow",
        "detail": "state source readable",
    }


def _state_verification(
    *,
    artifact_version: int | None = 1,
    actual_state: str = "active",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": "tmp/feature-state.json",
        "feature": "boilerplate_defense",
        "target_state": "active",
        "actual_state": actual_state,
        "readable": True,
        "attempts_configured": 1,
        "delay_seconds": 0.0,
        "attempts": [
            {
                "path": "tmp/feature-state.json",
                "feature": "boilerplate_defense",
                "state": actual_state,
                "actual_state": actual_state,
                "readable": True,
                "detail": "state source readable",
                "attempt": 1,
            }
        ],
    }
    if artifact_version is not None:
        payload["artifactVersion"] = artifact_version
    return payload


def _governance_run_record(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "contract_version": 2,
        "task": "governance",
        "mode": "execute",
        "risk_level": "high",
        "status": "executed",
        "inputs": {
            "task_name": "governance",
            "feature": "boilerplate_defense",
            "from_state": "shadow",
            "target_state": "active",
        },
        "outputs": {
            "stateBefore": _pre_governance_state(),
            "result": {"returnCode": 0},
        },
        "plan_ref": "task_plan.json",
    }


def _write_config_ledger_run(
    root: Path,
    run_id: str,
    *,
    report: dict[str, Any] | str | None = None,
    diff: dict[str, Any] | str | None = None,
    missing: set[str] | None = None,
) -> Path:
    return _write_task_ledger_run(
        root,
        run_id,
        task="config-promote",
        artifacts={
            "task_plan.json": _config_plan(),
            "run_record.json": _config_run_record(run_id),
            "config_promote_report.json": _config_report(run_id)
            if report is None
            else report,
            "config_promote_diff.json": _config_diff() if diff is None else diff,
            "snapshots/model-routing.previous.json": _config_snapshot(),
        },
        missing=missing,
    )


def _write_governance_ledger_run(
    root: Path,
    run_id: str,
    *,
    command: dict[str, Any] | str | None = None,
    state_verification: dict[str, Any] | str | None = None,
) -> Path:
    return _write_task_ledger_run(
        root,
        run_id,
        task="governance",
        artifacts={
            "task_plan.json": _governance_plan(),
            "run_record.json": _governance_run_record(run_id),
            "pre_governance_state.json": _pre_governance_state(),
            "governance_command.json": _governance_command()
            if command is None
            else command,
            "state_verification.json": _state_verification()
            if state_verification is None
            else state_verification,
        },
        missing=None,
    )


def _write_task_ledger_run(
    root: Path,
    run_id: str,
    *,
    task: str,
    artifacts: dict[str, dict[str, Any] | str],
    missing: set[str] | None,
) -> Path:
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    missing_artifacts = missing or set()
    for relative_path, payload in artifacts.items():
        if relative_path in missing_artifacts:
            continue
        artifact_path = run_dir / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            artifact_path.write_text(payload, encoding="utf-8")
        else:
            artifact_path.write_text(json.dumps(payload), encoding="utf-8")

    _write_json(
        run_dir / "ledger.json",
        {
            "runId": run_id,
            "artifacts": {
                "ledger": "ledger.json",
                **{
                    artifact_name.removesuffix(".json"): artifact_name
                    for artifact_name in artifacts
                    if artifact_name not in missing_artifacts
                },
            },
        },
    )
    _append_index(
        root,
        {
            "runId": run_id,
            "createdAt": "2026-06-01T00:00:00Z",
            "task": task,
            "mode": "execute",
            "status": "executed",
            "runDir": str(run_dir),
        },
    )
    return run_dir


def _finding_codes(report: dict[str, Any]) -> set[str]:
    return {str(finding.get("code")) for finding in report.get("findings", [])}


def _governance_config_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    subsystem = report["subsystems"]["governance_config"]
    return subsystem["findings"]


def test_dry_run_reports_governance_config_subsystem_without_findings(
    tmp_path: Path,
) -> None:
    root = _ledger_root(tmp_path)
    _write_config_ledger_run(root, "config-ok")
    _write_governance_ledger_run(root, "governance-ok")

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == 0
    subsystem = result.outputs["subsystems"]["governance_config"]
    assert subsystem["subsystem"] == "governance_config"
    assert subsystem["ownerTask"] == "governance/config-promote"
    assert subsystem["runsChecked"] == 2
    assert subsystem["resourcesChecked"] == 10
    assert subsystem["findings"] == []


def test_dry_run_fails_closed_on_missing_governance_config_required_artifact(
    tmp_path: Path,
) -> None:
    run_dir = _write_config_ledger_run(
        _ledger_root(tmp_path),
        "config-missing-diff",
        missing={"config_promote_diff.json"},
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "missing_required_artifact" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _governance_config_findings(result.outputs)
        if item["code"] == "missing_required_artifact"
    )
    assert finding["severity"] == "critical"
    assert finding["subsystem"] == "governance_config"
    assert finding["resourceId"] == "config_promote.config_diff"
    assert finding["evidencePath"] == str(run_dir / "config_promote_diff.json")


def test_dry_run_fails_closed_on_malformed_governance_config_json(
    tmp_path: Path,
) -> None:
    _write_governance_ledger_run(
        _ledger_root(tmp_path),
        "governance-malformed-state",
        state_verification="{not-json",
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "malformed_json" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _governance_config_findings(result.outputs)
        if item["code"] == "malformed_json"
    )
    assert finding["subsystem"] == "governance_config"
    assert finding["resourceId"] == "governance.state_verification"


def test_dry_run_fails_closed_on_governance_config_unsupported_contract_version(
    tmp_path: Path,
) -> None:
    _write_config_ledger_run(
        _ledger_root(tmp_path),
        "config-unsupported-diff",
        diff=_config_diff(artifact_version=999),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "unsupported_contract_version" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _governance_config_findings(result.outputs)
        if item["code"] == "unsupported_contract_version"
    )
    assert finding["resourceId"] == "config_promote.config_diff.artifact_version"


def test_dry_run_fails_closed_on_config_promote_digest_mismatch(
    tmp_path: Path,
) -> None:
    _write_config_ledger_run(
        _ledger_root(tmp_path),
        "config-digest-mismatch",
        report=_config_report(
            "config-digest-mismatch",
            proposed_sha256="sha256:drifted-proposed",
        ),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "resource_digest_mismatch" in _finding_codes(result.outputs)
    resource_ids = {
        finding["resourceId"] for finding in _governance_config_findings(result.outputs)
    }
    assert "config_promote.report.proposed_config.sha256" in resource_ids


def test_dry_run_fails_closed_on_governance_state_mismatch(
    tmp_path: Path,
) -> None:
    _write_governance_ledger_run(
        _ledger_root(tmp_path),
        "governance-state-mismatch",
        state_verification=_state_verification(actual_state="shadow"),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "resource_digest_mismatch" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _governance_config_findings(result.outputs)
        if item["resourceId"] == "governance.state_verification.actual_state"
    )
    assert finding["expectedDigest"]
    assert finding["observedDigest"]


@pytest.mark.parametrize(
    "command_patch, expected_resource_id",
    [
        (
            lambda command: command.__setitem__(4, "demote"),
            "governance.governance_command.verb",
        ),
        (
            lambda command: command.__setitem__(2, "not-governance"),
            "governance.governance_command.prefix",
        ),
    ],
)
def test_dry_run_fails_closed_on_governance_command_shape_mismatch(
    tmp_path: Path,
    command_patch: Any,
    expected_resource_id: str,
) -> None:
    command = _governance_command()
    command_patch(command["command"])
    _write_governance_ledger_run(
        _ledger_root(tmp_path),
        "governance-command-mismatch",
        command=command,
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "dry_run_binding_mismatch" in _finding_codes(result.outputs)
    resource_ids = {
        finding["resourceId"] for finding in _governance_config_findings(result.outputs)
    }
    assert expected_resource_id in resource_ids


def test_dry_run_flags_legacy_governance_config_baseline_explicitly(
    tmp_path: Path,
) -> None:
    _write_governance_ledger_run(
        _ledger_root(tmp_path),
        "governance-legacy-baseline",
        command=_governance_command(artifact_version=None),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == 0
    finding = next(
        item
        for item in _governance_config_findings(result.outputs)
        if item["code"] == "genesis_baseline_missing"
    )
    assert finding["severity"] == "medium"
    assert finding["resourceId"] == "governance.governance_command.artifact_version"

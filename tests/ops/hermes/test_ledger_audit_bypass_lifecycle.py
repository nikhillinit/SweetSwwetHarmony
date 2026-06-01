from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.tasks.base import EXIT_GATE_FAILURE
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


PLAN_HASH = "sha256:" + ("a" * 64)
OTHER_PLAN_HASH = "sha256:" + ("b" * 64)


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
    with (root / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _task_plan(*, task: str = "deliberate", plan_hash: str = PLAN_HASH) -> dict[str, Any]:
    return {
        "contractVersion": 2,
        "task": task,
        "mode": "dry-run",
        "risk_level": "critical",
        "planHash": plan_hash,
        "mutation": {
            "allowed": False,
            "affected_files": ["signals.db.canary"],
            "affected_tables": ["signals"],
            "external_systems": [],
        },
    }


def _run_record(run_id: str, *, task: str = "deliberate") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "contract_version": 2,
        "task": task,
        "mode": "dry-run",
        "risk_level": "critical",
        "status": "dry_run_passed",
        "inputs": {},
        "outputs": {},
        "plan_ref": "task_plan.json",
    }


def _bypass_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "bypassId": "bypass-1",
        "kind": "emergency-override",
        "scope": "resource",
        "policyRef": "hermes.restore.approval",
        "reason": "temporary reviewer variance for canary restore rehearsal",
        "severity": "high",
        "affectedResources": [
            {
                "type": "sqlite",
                "id": "signals.db.canary",
                "task": "restore-db",
            }
        ],
        "operator": "operator@example.com",
        "authorizer": "incident-commander@example.com",
        "createdAt": "2026-06-01T00:00:00+00:00",
        "expiresAt": "2099-01-01T00:00:00+00:00",
        "deadline": "2099-01-02T00:00:00+00:00",
        "expectedRemediation": "rerun deliberation with standing quorum",
        "actualRemediationRunId": None,
        "status": "active",
        "planHash": PLAN_HASH,
        "evidence": {
            "path": "deliberation_record.json",
            "summary": "temporary bypass is ledgered and scoped to canary",
        },
    }
    record.update(overrides)
    return record


def _write_bypass_run(
    root: Path,
    run_id: str,
    *,
    task: str = "deliberate",
    plan: dict[str, Any] | None = None,
    bypass_payload: dict[str, Any] | str | None = None,
) -> Path:
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "task_plan.json", plan or _task_plan(task=task))
    _write_json(run_dir / "run_record.json", _run_record(run_id, task=task))
    if isinstance(bypass_payload, str):
        (run_dir / "bypass_record.json").write_text(bypass_payload, encoding="utf-8")
    else:
        _write_json(
            run_dir / "bypass_record.json",
            bypass_payload or _bypass_record(),
        )
    _write_json(
        run_dir / "ledger.json",
        {
            "runId": run_id,
            "artifacts": {
                "ledger": "ledger.json",
                "task_plan": "task_plan.json",
                "run_record": "run_record.json",
                "bypass_record": "bypass_record.json",
            },
        },
    )
    _append_index(
        root,
        {
            "runId": run_id,
            "createdAt": "2026-06-01T00:00:00Z",
            "task": task,
            "mode": "dry-run",
            "status": "dry_run_passed",
            "runDir": str(run_dir),
        },
    )
    return run_dir


def _write_incident_run(root: Path, run_id: str) -> Path:
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "task_plan.json", _task_plan(task="incident"))
    _write_json(run_dir / "run_record.json", _run_record(run_id, task="incident"))
    _write_json(
        run_dir / "incident_response_artifacts.json",
        {
            "incidentId": "incident-1",
            "phase": "repair-plan",
            "bypassId": "not-a-bypass-authorization",
            "statusAfter": "investigating",
        },
    )
    _write_json(
        run_dir / "ledger.json",
        {
            "runId": run_id,
            "artifacts": {
                "ledger": "ledger.json",
                "task_plan": "task_plan.json",
                "run_record": "run_record.json",
                "incident_response_artifacts": "incident_response_artifacts.json",
            },
        },
    )
    _append_index(
        root,
        {
            "runId": run_id,
            "createdAt": "2026-06-01T00:00:00Z",
            "task": "incident",
            "mode": "dry-run",
            "status": "dry_run_passed",
            "runDir": str(run_dir),
        },
    )
    return run_dir


def _bypass_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    return report["subsystems"]["bypass_lifecycle"]["findings"]


def _finding_codes(report: dict[str, Any]) -> set[str]:
    return {str(finding.get("code")) for finding in report.get("findings", [])}


def test_dry_run_reports_clean_active_bypass_lifecycle_without_findings(
    tmp_path: Path,
) -> None:
    _write_bypass_run(_ledger_root(tmp_path), "bypass-clean")

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == 0
    subsystem = result.outputs["subsystems"]["bypass_lifecycle"]
    assert subsystem["subsystem"] == "bypass_lifecycle"
    assert subsystem["ownerTask"] == "ledgered-bypass-artifacts"
    assert subsystem["runsChecked"] == 1
    assert subsystem["resourcesChecked"] == 1
    assert subsystem["activeRecords"] == 1
    assert subsystem["findings"] == []


def test_dry_run_flags_active_bypass_overdue_without_remediation(
    tmp_path: Path,
) -> None:
    run_dir = _write_bypass_run(
        _ledger_root(tmp_path),
        "bypass-overdue",
        bypass_payload=_bypass_record(
            expiresAt="2020-01-01T00:00:00+00:00",
            deadline="2020-01-02T00:00:00+00:00",
        ),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "bypass_overdue" in _finding_codes(result.outputs)
    finding = next(
        item for item in _bypass_findings(result.outputs) if item["code"] == "bypass_overdue"
    )
    assert finding["severity"] == "critical"
    assert finding["subsystem"] == "bypass_lifecycle"
    assert finding["resourceId"] == "bypass_lifecycle.bypass-1.deadline"
    assert finding["evidencePath"] == str(run_dir / "bypass_record.json")


def test_dry_run_fails_closed_on_malformed_bypass_record_json(tmp_path: Path) -> None:
    _write_bypass_run(
        _ledger_root(tmp_path),
        "bypass-malformed-json",
        bypass_payload="{not-json",
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "malformed_json" in _finding_codes(result.outputs)
    assert _bypass_findings(result.outputs)[0]["resourceId"] == "bypass_lifecycle.artifact"


def test_dry_run_fails_closed_on_invalid_bypass_scope(tmp_path: Path) -> None:
    _write_bypass_run(
        _ledger_root(tmp_path),
        "bypass-wrong-scope",
        bypass_payload=_bypass_record(scope="global"),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    finding = next(
        item for item in _bypass_findings(result.outputs) if item["code"] == "malformed_json"
    )
    assert finding["resourceId"] == "bypass_lifecycle.bypass-1.scope"
    assert "scope" in finding["detail"]


def test_dry_run_fails_closed_on_bypass_plan_hash_mismatch(tmp_path: Path) -> None:
    _write_bypass_run(
        _ledger_root(tmp_path),
        "bypass-plan-mismatch",
        bypass_payload=_bypass_record(planHash=OTHER_PLAN_HASH),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    finding = next(
        item
        for item in _bypass_findings(result.outputs)
        if item["code"] == "plan_hash_mismatch"
    )
    assert finding["resourceId"] == "bypass_lifecycle.bypass-1.planHash"


def test_dry_run_fails_closed_on_remediated_bypass_missing_run_id(
    tmp_path: Path,
) -> None:
    _write_bypass_run(
        _ledger_root(tmp_path),
        "bypass-remediation-missing",
        bypass_payload=_bypass_record(status="remediated", actualRemediationRunId=None),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    finding = next(
        item for item in _bypass_findings(result.outputs) if item["code"] == "malformed_json"
    )
    assert finding["resourceId"] == "bypass_lifecycle.bypass-1.actualRemediationRunId"
    assert "remediated" in finding["detail"]


@pytest.mark.parametrize(
    "affected_resources",
    [
        [{}],
        [123],
        [{"type": "sqlite"}],
        [{"id": "signals.db.canary"}],
    ],
)
def test_dry_run_fails_closed_on_malformed_affected_resources(
    tmp_path: Path,
    affected_resources: list[Any],
) -> None:
    _write_bypass_run(
        _ledger_root(tmp_path),
        "bypass-bad-resources",
        bypass_payload=_bypass_record(affectedResources=affected_resources),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    finding = next(
        item
        for item in _bypass_findings(result.outputs)
        if item["resourceId"] == "bypass_lifecycle.bypass-1.affectedResources"
    )
    assert finding["code"] == "malformed_json"
    assert "type and id" in finding["detail"]


def test_dry_run_fails_closed_on_naive_bypass_datetime(tmp_path: Path) -> None:
    _write_bypass_run(
        _ledger_root(tmp_path),
        "bypass-naive-datetime",
        bypass_payload=_bypass_record(deadline="2099-01-02T00:00:00"),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    finding = next(
        item
        for item in _bypass_findings(result.outputs)
        if item["resourceId"] == "bypass_lifecycle.bypass-1.deadline"
    )
    assert finding["code"] == "malformed_json"
    assert "timezone" in finding["detail"]


def test_incident_response_packets_are_not_bypass_authorization(
    tmp_path: Path,
) -> None:
    _write_incident_run(_ledger_root(tmp_path), "incident-not-bypass")

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == 0
    subsystem = result.outputs["subsystems"]["bypass_lifecycle"]
    assert subsystem["runsChecked"] == 0
    assert subsystem["resourcesChecked"] == 0
    assert subsystem["findings"] == []


def test_bypass_record_schema_accepts_structured_lifecycle_surface() -> None:
    schema_path = (
        PROJECT_ROOT
        / "integrations"
        / "hermes"
        / "schemas"
        / "bypass_record.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(_bypass_record())


def test_bypass_record_schema_rejects_naive_lifecycle_datetime() -> None:
    schema_path = (
        PROJECT_ROOT
        / "integrations"
        / "hermes"
        / "schemas"
        / "bypass_record.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(
            _bypass_record(deadline="2099-01-02T00:00:00")
        )

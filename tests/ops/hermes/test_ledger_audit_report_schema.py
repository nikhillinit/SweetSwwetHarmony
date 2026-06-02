from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


def _schema_path() -> Path:
    return (
        PROJECT_ROOT
        / "integrations"
        / "hermes"
        / "schemas"
        / "ledger_audit_report.schema.json"
    )


def _load_schema() -> dict[str, object]:
    return json.loads(_schema_path().read_text(encoding="utf-8"))


def _config_path(tmp_path: Path) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = []
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _args(tmp_path: Path, *, check: str = "index") -> argparse.Namespace:
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
        check=check,
    )


def _live_ledger_audit_report(
    tmp_path: Path,
    *,
    check: str = "index",
) -> dict[str, object]:
    result = run_registered_task(_args(tmp_path, check=check))
    run_dir = Path(result.run_dir or "")
    return json.loads((run_dir / "ledger_audit_report.json").read_text(encoding="utf-8"))


def test_ledger_audit_report_schema_matches_live_report_keys(tmp_path: Path) -> None:
    schema = _load_schema()
    report = _live_ledger_audit_report(tmp_path)

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)
    assert set(report) == set(schema["properties"])
    assert schema["required"] == [
        "auditId",
        "generatedAt",
        "dryRun",
        "mutationCommitted",
        "task",
        "ledgerRoot",
        "ledgerIndex",
        "checksRun",
        "summary",
        "operatorSummary",
        "findings",
        "subsystems",
        "rehearsals",
        "reportArtifacts",
    ]
    assert all(key in report for key in schema["required"])


def test_ledger_audit_report_schema_validates_rehearsal_report(
    tmp_path: Path,
) -> None:
    schema = _load_schema()
    report = _live_ledger_audit_report(tmp_path, check="rehearsals")

    Draft202012Validator(schema).validate(report)
    assert report["checksRun"] == ["rehearsals"]
    assert report["rehearsals"]["summary"]["rehearsedTasks"] > 0


def test_ledger_audit_report_schema_accepts_failed_rehearsal_payload(
    tmp_path: Path,
) -> None:
    schema = _load_schema()
    report = _live_ledger_audit_report(tmp_path, check="rehearsals")
    report["rehearsals"]["tasks"].append(
        {
            "task": "empty-modes",
            "description": "Empty supported modes test task.",
            "riskLevel": "not-a-risk",
            "supportedModes": [],
            "executeSupported": False,
            "requiredLocks": [],
            "ledgerBacked": False,
            "mutatesExternalSystems": False,
            "ackRiskRequired": False,
            "ackRiskToken": None,
            "status": "fail",
        }
    )
    report["rehearsals"]["summary"]["registeredTasks"] += 1
    report["rehearsals"]["summary"]["rehearsedTasks"] += 1
    report["rehearsals"]["summary"]["failedTasks"] += 1
    report["findings"].append(
        {
            "code": "task_contract_malformed",
            "severity": "critical",
            "subsystem": "cross_task_rehearsal",
            "resourceId": "empty-modes.supported_modes",
            "detail": "supported_modes must include at least one mode",
            "remediationHint": "Fix the registered Hermes task contract metadata.",
        }
    )
    report["rehearsals"]["findings"] = report["findings"]

    Draft202012Validator(schema).validate(report)
    failed = next(
        task for task in report["rehearsals"]["tasks"] if task["task"] == "empty-modes"
    )
    assert failed["status"] == "fail"
    assert failed["supportedModes"] == []


def test_ledger_audit_report_schema_tracks_live_camel_case_shape() -> None:
    schema = _load_schema()
    properties = schema["properties"]
    summary = properties["summary"]
    operator_summary = properties["operatorSummary"]
    subsystems = properties["subsystems"]
    rehearsals = properties["rehearsals"]
    report_artifacts = properties["reportArtifacts"]

    assert schema["additionalProperties"] is False
    assert "audit_id" not in properties
    assert "checks_run" not in properties
    assert "ledger_entries" not in properties
    assert "drifts" not in properties
    assert "checksRun" in properties
    assert "subsystems" in properties
    assert "rehearsals" in properties
    assert properties["checksRun"]["items"]["enum"] == [
        "index",
        "runs",
        "artifacts",
        "rehearsals",
    ]
    assert "checkedRunDirs" not in summary["properties"]
    assert "rawIndexRows" in summary["properties"]
    assert "uniqueRunDirsChecked" in summary["properties"]
    assert "validIndexEntries" in summary["properties"]
    assert "rehearsedTasks" in summary["properties"]
    assert operator_summary["required"] == [
        "status",
        "severityThreshold",
        "totalFindings",
        "blockingFindings",
        "severityCounts",
        "subsystemsWithFindings",
        "nextAction",
    ]
    assert operator_summary["properties"]["status"]["enum"] == [
        "pass",
        "action_required",
    ]
    assert operator_summary["properties"]["nextAction"]["enum"] == [
        "no_action_required",
        "review_non_blocking_findings",
        "review_blocking_findings",
    ]
    assert subsystems["required"] == [
        "restore_sqlite",
        "governance_config",
        "collector_promotion",
        "suppression_outbox",
        "bypass_lifecycle",
    ]
    assert subsystems["properties"]["restore_sqlite"]["required"] == [
        "subsystem",
        "ownerTask",
        "enabled",
        "runsChecked",
        "resourcesChecked",
        "findings",
    ]
    assert subsystems["properties"]["governance_config"]["required"] == [
        "subsystem",
        "ownerTask",
        "enabled",
        "runsChecked",
        "resourcesChecked",
        "findings",
    ]
    assert subsystems["properties"]["collector_promotion"]["required"] == [
        "subsystem",
        "ownerTask",
        "enabled",
        "runsChecked",
        "resourcesChecked",
        "findings",
    ]
    assert subsystems["properties"]["suppression_outbox"]["required"] == [
        "subsystem",
        "ownerTask",
        "enabled",
        "runsChecked",
        "resourcesChecked",
        "findings",
    ]
    assert subsystems["properties"]["bypass_lifecycle"]["required"] == [
        "subsystem",
        "ownerTask",
        "enabled",
        "runsChecked",
        "resourcesChecked",
        "activeRecords",
        "expiredRecords",
        "remediatedRecords",
        "revokedRecords",
        "findings",
    ]
    assert rehearsals["required"] == [
        "enabled",
        "summary",
        "tasks",
        "findings",
    ]
    assert rehearsals["properties"]["summary"]["required"] == [
        "registeredTasks",
        "rehearsedTasks",
        "executeCapableTasks",
        "ledgerBackedTasks",
        "failedTasks",
    ]
    assert rehearsals["properties"]["tasks"]["items"]["required"] == [
        "task",
        "description",
        "riskLevel",
        "supportedModes",
        "executeSupported",
        "requiredLocks",
        "ledgerBacked",
        "mutatesExternalSystems",
        "ackRiskRequired",
        "ackRiskToken",
        "status",
    ]
    task_properties = rehearsals["properties"]["tasks"]["items"]["properties"]
    assert task_properties["riskLevel"] == {"type": "string", "minLength": 1}
    assert task_properties["supportedModes"] == {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
    }
    assert report_artifacts["required"] == ["json", "markdown"]

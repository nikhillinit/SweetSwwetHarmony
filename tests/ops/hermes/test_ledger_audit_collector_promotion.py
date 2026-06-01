from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

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


def _database_evidence(
    *,
    status: str = "relevant",
    source_api: str = "github",
    query_collector: str = "github",
    updated_at: str = "2026-05-29T06:00:00+00:00",
) -> dict[str, Any]:
    return {
        "path": "signals.db",
        "exists": True,
        "openable": True,
        "detail": "hunter result found",
        "found": True,
        "result_id": 123,
        "status": status,
        "source_api": source_api,
        "query_collector": query_collector,
        "canonical_key": "domain:acme.ai",
        "promoted_signal_id": None,
        "updated_at": updated_at,
    }


def _collector_state_evidence() -> dict[str, Any]:
    return {
        "path": "state/collectors.json",
        "exists": True,
        "readable": True,
        "collector": "github",
        "collector_known": True,
        "entry": {
            "collector": "github",
            "configured_status": "enabled",
            "effective_status": "healthy",
        },
        "detail": "collector known",
        "schema_version": 2,
        "updated_at": "2026-05-28T00:00:00+00:00",
    }


def _collector_plan(
    *,
    mode: str = "dry-run",
    contract_version: int = 2,
) -> dict[str, Any]:
    database = _database_evidence()
    return {
        "contractVersion": contract_version,
        "task": "collector-promote",
        "mode": mode,
        "risk_level": "high",
        "planHash": "sha256:collector-plan",
        "collector": "github",
        "result_id": 123,
        "target_state": "active",
        "result_target_state": "promoted",
        "transition": {
            "valid": True,
            "requested": "active",
            "detail": "promote hunter result",
            "action_type": "hunter_promote",
            "ack_risk_token": "COLLECTOR_PROMOTE",
            "result_target_state": "promoted",
        },
        "database": database,
        "planned_result_updated_at": database["updated_at"],
        "collector_state": _collector_state_evidence(),
        "artifacts": {
            "record": "collector_promotion.json",
            "run_record": "run_record.json",
        },
        "mutation": {
            "allowed": mode == "execute",
            "affected_db": "signals.db" if mode == "execute" else None,
            "affected_tables": [
                "hunter_results",
                "signals",
                "audit_events",
                "idempotency_keys",
            ],
        },
    }


def _collector_run_record(
    run_id: str,
    *,
    mode: str = "dry-run",
    contract_version: int = 2,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "contract_version": contract_version,
        "task": "collector-promote",
        "mode": mode,
        "risk_level": "high",
        "status": "dry_run_passed" if mode == "dry-run" else "executed",
        "inputs": {
            "task_name": "collector-promote",
            "planHash": "sha256:collector-plan",
            "collector": "github",
            "result_id": 123,
            "target_state": "active",
        },
        "outputs": {},
        "plan_ref": "task_plan.json",
    }


def _collector_artifact(
    *,
    artifact_version: int | None = 1,
    mode: str = "dry-run",
    result_id: int = 123,
    target_state: str = "active",
    audit_database: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task": "collector-promote",
        "mode": mode,
        "dryRun": mode == "dry-run",
        "mutationCommitted": mode == "execute",
        "collector": "github",
        "resultId": result_id,
        "targetState": target_state,
        "resultTargetState": "promoted",
        "requestedResultStatus": "promoted",
        "actualResultStatus": "relevant" if mode == "dry-run" else "promoted",
        "requestedTargetReached": mode == "execute",
        "desiredOutcomeSatisfied": mode == "execute",
        "idempotent": False,
        "collision": False,
        "writesCommitted": mode == "execute",
        "transition": {
            "valid": True,
            "requested": target_state,
            "detail": "promote hunter result",
            "action_type": "hunter_promote",
            "ack_risk_token": "COLLECTOR_PROMOTE",
            "result_target_state": "promoted",
        },
        "promotionResult": None
        if mode == "dry-run"
        else {
            "success": True,
            "result_id": result_id,
            "status": "promoted",
            "message": "Hunter result promoted",
        },
        "artifactCommit": {
            "ledgerOnly": mode == "dry-run",
            "runtimeState": mode == "execute",
            "externalSystems": False,
        },
        "persistence": {
            "persisted": mode == "execute",
            "affectedDb": "signals.db" if mode == "execute" else None,
            "affectedTables": [
                "hunter_results",
                "signals",
                "audit_events",
                "idempotency_keys",
            ],
            "externalSystems": [],
        },
        "auditEvidence": {
            "planHash": "sha256:collector-plan",
            "plannedResultUpdatedAt": "2026-05-29T06:00:00+00:00",
            "database": audit_database or _database_evidence(),
            "collectorState": _collector_state_evidence(),
            "ackRiskToken": "COLLECTOR_PROMOTE",
        },
    }
    if artifact_version is not None:
        payload["artifactVersion"] = artifact_version
    return payload


def _dry_run_drift_artifact(
    *,
    artifact_version: int | None = 1,
) -> dict[str, Any]:
    stale_preview = _collector_artifact(artifact_version=artifact_version)
    payload: dict[str, Any] = {
        "task": "collector-promote",
        "mode": "dry-run",
        "dryRun": True,
        "mutationCommitted": False,
        "driftDetected": True,
        "drifts": [
            {
                "field": "updated_at",
                "planned": "2026-05-29T06:00:00+00:00",
                "observed": "2026-05-29T06:15:00+00:00",
                "resultId": 123,
            }
        ],
        "stalePreview": stale_preview,
        "observed": {
            "hunterResult": _database_evidence(
                status="promoted",
                updated_at="2026-05-29T06:15:00+00:00",
            )
        },
        "dryRunDriftArtifact": "dry_run_drift.json",
        "nextAction": "Refresh the collector promotion plan.",
    }
    if artifact_version is not None:
        payload["artifactVersion"] = artifact_version
    return payload


def _write_collector_ledger_run(
    root: Path,
    run_id: str,
    *,
    mode: str = "dry-run",
    plan: dict[str, Any] | str | None = None,
    record: dict[str, Any] | str | None = None,
    artifact: dict[str, Any] | str | None = None,
    drift: dict[str, Any] | str | None = None,
    missing: set[str] | None = None,
) -> Path:
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    missing_artifacts = missing or set()
    artifact_payloads: dict[str, dict[str, Any] | str] = {
        "task_plan.json": _collector_plan(mode=mode) if plan is None else plan,
        "run_record.json": _collector_run_record(run_id, mode=mode)
        if record is None
        else record,
    }
    if drift is not None:
        artifact_payloads["dry_run_drift.json"] = drift
    else:
        artifact_payloads["collector_promotion.json"] = (
            _collector_artifact(mode=mode) if artifact is None else artifact
        )

    for relative_path, payload in artifact_payloads.items():
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
                    for artifact_name in artifact_payloads
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
            "task": "collector-promote",
            "mode": mode,
            "status": "dry_run_passed" if mode == "dry-run" else "executed",
            "runDir": str(run_dir),
        },
    )
    return run_dir


def _finding_codes(report: dict[str, Any]) -> set[str]:
    return {str(finding.get("code")) for finding in report.get("findings", [])}


def _collector_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    subsystem = report["subsystems"]["collector_promotion"]
    return subsystem["findings"]


def test_dry_run_reports_collector_promotion_subsystem_without_findings(
    tmp_path: Path,
) -> None:
    _write_collector_ledger_run(_ledger_root(tmp_path), "collector-ok")

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == 0
    subsystem = result.outputs["subsystems"]["collector_promotion"]
    assert subsystem["subsystem"] == "collector_promotion"
    assert subsystem["ownerTask"] == "collector-promote"
    assert subsystem["runsChecked"] == 1
    assert subsystem["resourcesChecked"] == 3
    assert subsystem["findings"] == []


def test_dry_run_fails_closed_on_missing_collector_promotion_required_artifact(
    tmp_path: Path,
) -> None:
    run_dir = _write_collector_ledger_run(
        _ledger_root(tmp_path),
        "collector-missing-artifact",
        missing={"collector_promotion.json"},
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "missing_required_artifact" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _collector_findings(result.outputs)
        if item["code"] == "missing_required_artifact"
    )
    assert finding["severity"] == "critical"
    assert finding["subsystem"] == "collector_promotion"
    assert finding["resourceId"] == "collector_promotion.collector_promotion"
    assert finding["evidencePath"] == str(run_dir / "collector_promotion.json")


def test_dry_run_fails_closed_on_malformed_collector_promotion_json(
    tmp_path: Path,
) -> None:
    _write_collector_ledger_run(
        _ledger_root(tmp_path),
        "collector-malformed-artifact",
        artifact="{not-json",
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "malformed_json" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _collector_findings(result.outputs)
        if item["code"] == "malformed_json"
    )
    assert finding["subsystem"] == "collector_promotion"
    assert finding["resourceId"] == "collector_promotion.collector_promotion"


def test_dry_run_fails_closed_on_collector_promotion_unsupported_version(
    tmp_path: Path,
) -> None:
    _write_collector_ledger_run(
        _ledger_root(tmp_path),
        "collector-unsupported-version",
        artifact=_collector_artifact(artifact_version=999),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "unsupported_contract_version" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _collector_findings(result.outputs)
        if item["code"] == "unsupported_contract_version"
    )
    assert (
        finding["resourceId"]
        == "collector_promotion.collector_promotion.artifact_version"
    )


def test_dry_run_fails_closed_on_collector_promotion_target_binding_mismatch(
    tmp_path: Path,
) -> None:
    _write_collector_ledger_run(
        _ledger_root(tmp_path),
        "collector-target-mismatch",
        artifact=_collector_artifact(result_id=999, target_state="shadow"),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "dry_run_binding_mismatch" in _finding_codes(result.outputs)
    resource_ids = {finding["resourceId"] for finding in _collector_findings(result.outputs)}
    assert {
        "collector_promotion.result_id",
        "collector_promotion.target_state",
    }.issubset(resource_ids)


def test_dry_run_fails_closed_on_collector_promotion_result_digest_drift(
    tmp_path: Path,
) -> None:
    drifted_database = _database_evidence(
        status="pending",
        source_api="hacker_news",
        query_collector="hacker_news",
        updated_at="2026-05-29T06:15:00+00:00",
    )
    _write_collector_ledger_run(
        _ledger_root(tmp_path),
        "collector-result-drift",
        artifact=_collector_artifact(audit_database=drifted_database),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "resource_digest_mismatch" in _finding_codes(result.outputs)
    resource_ids = {finding["resourceId"] for finding in _collector_findings(result.outputs)}
    assert {
        "collector_promotion.database.status",
        "collector_promotion.database.source_api",
        "collector_promotion.database.query_collector",
        "collector_promotion.database.updated_at",
    }.issubset(resource_ids)


def test_dry_run_drift_artifact_is_audited_as_expected_collector_output(
    tmp_path: Path,
) -> None:
    _write_collector_ledger_run(
        _ledger_root(tmp_path),
        "collector-dry-run-drift",
        drift=_dry_run_drift_artifact(),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == 0
    subsystem = result.outputs["subsystems"]["collector_promotion"]
    assert subsystem["runsChecked"] == 1
    assert subsystem["resourcesChecked"] == 3
    assert subsystem["findings"] == []


def test_dry_run_flags_legacy_collector_promotion_baseline_explicitly(
    tmp_path: Path,
) -> None:
    _write_collector_ledger_run(
        _ledger_root(tmp_path),
        "collector-legacy-baseline",
        artifact=_collector_artifact(artifact_version=None),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == 0
    finding = next(
        item
        for item in _collector_findings(result.outputs)
        if item["code"] == "genesis_baseline_missing"
    )
    assert finding["severity"] == "medium"
    assert (
        finding["resourceId"]
        == "collector_promotion.collector_promotion.artifact_version"
    )

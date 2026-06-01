from __future__ import annotations

import argparse
import hashlib
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
    with (root / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _suppression_plan(
    *,
    mode: str = "dry-run",
    contract_version: int = 2,
    db_path: str = "signals.db",
    row_count: int = 2,
) -> dict[str, Any]:
    command = [
        "python",
        "-m",
        "workflows.suppression_sync",
        "--db-path",
        db_path,
        "--ttl-days",
        "7",
        "--dry-run",
        "--delete-stale",
    ]
    return {
        "contractVersion": contract_version,
        "task": "suppression-sync",
        "mode": mode,
        "risk_level": "medium",
        "planHash": "sha256:suppression-plan",
        "database": _suppression_state(db_path=db_path, row_count=row_count),
        "workflow": {"command": command},
        "delete_stale_requested": True,
        "expected_changes": {
            "upserts": "unknown_until_workflow_runs",
            "expired_removals": 1,
            "expired_removals_if_delete_stale": 1,
        },
        "mutation": {
            "allowed": mode == "execute",
            "affected_files": [db_path],
            "affected_tables": ["suppression_cache"],
            "external_systems": [],
        },
    }


def _suppression_state(
    *,
    artifact_version: int | None = 1,
    db_path: str = "signals.db",
    row_count: int = 2,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": db_path,
        "exists": True,
        "openable": True,
        "detail": "ok",
        "integrity_check": "ok",
        "table_exists": True,
        "schema_valid": True,
        "schema_detail": "valid suppression_cache schema",
        "row_count": row_count,
        "expired_count": 1,
        "duplicates": [],
        "missing_columns": [],
    }
    if artifact_version is not None:
        payload["artifactVersion"] = artifact_version
    return payload


def _suppression_command(
    *,
    artifact_version: int | None = 1,
    command: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "command": command
        or _suppression_plan()["workflow"]["command"],
        "returnCode": 0,
        "stdout": "dry run",
        "stderr": "",
        "timedOut": False,
    }
    if artifact_version is not None:
        payload["artifactVersion"] = artifact_version
    return payload


def _suppression_run_record(
    run_id: str,
    *,
    mode: str = "dry-run",
    contract_version: int = 2,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "contract_version": contract_version,
        "task": "suppression-sync",
        "mode": mode,
        "risk_level": "medium",
        "status": "dry_run_passed" if mode == "dry-run" else "executed",
        "inputs": {
            "task_name": "suppression-sync",
            "planHash": "sha256:suppression-plan",
        },
        "outputs": {},
        "plan_ref": "task_plan.json",
    }


def _outbox_plan(
    *,
    mode: str = "dry-run",
    contract_version: int = 2,
    candidate_hash: str | None = None,
    candidate_id_hash: str | None = None,
) -> dict[str, Any]:
    rows = _outbox_rows()
    ids = [7]
    return {
        "contractVersion": contract_version,
        "task": "outbox-purge",
        "mode": mode,
        "risk_level": "high",
        "planHash": "sha256:outbox-plan",
        "purge_criteria": {
            "status": "failed",
            "event_type": "notion_push",
            "age_days": 30,
        },
        "cutoff": {"created_at_lte": "2026-05-01T00:00:00+00:00"},
        "max_removals": 25,
        "candidates": {
            "count": 1,
            "ids": ids,
            "id_hash": candidate_id_hash or _hash_json(ids),
            "candidate_hash": candidate_hash or _hash_json(rows),
        },
        "mutation": {
            "allowed": mode == "execute",
            "affected_db": "signals.db" if mode == "execute" else None,
            "affected_files": ["signals.db"] if mode == "execute" else [],
            "affected_tables": ["notion_outbox"],
            "external_systems": [],
        },
    }


def _outbox_candidates(
    *,
    artifact_version: int | None = 1,
    candidate_hash: str | None = None,
    candidate_id_hash: str | None = None,
) -> dict[str, Any]:
    rows = _outbox_rows()
    ids = [7]
    payload: dict[str, Any] = {
        "candidateCount": 1,
        "candidateIds": ids,
        "candidateIdHash": candidate_id_hash or _hash_json(ids),
        "candidateHash": candidate_hash or _hash_json(rows),
        "rows": rows,
    }
    if artifact_version is not None:
        payload["artifactVersion"] = artifact_version
    return payload


def _outbox_result(
    *,
    artifact_version: int | None = 1,
    candidate_hash: str | None = None,
    candidate_id_hash: str | None = None,
) -> dict[str, Any]:
    rows = _outbox_rows()
    resolved_candidate_hash = candidate_hash or _hash_json(rows)
    resolved_candidate_id_hash = candidate_id_hash or _hash_json([7])
    payload: dict[str, Any] = {
        "dryRun": False,
        "mutationCommitted": True,
        "dbPath": "signals.db",
        "candidateCount": 1,
        "snapshotCandidateCount": 1,
        "candidateHash": resolved_candidate_hash,
        "candidateIdHash": resolved_candidate_id_hash,
        "candidateArtifact": "outbox_candidates.json",
        "purgeResultArtifact": "outbox_purge_result.json",
        "purgeCriteria": {
            "status": "failed",
            "event_type": "notion_push",
            "age_days": 30,
        },
        "before": {"matchingCount": 1, "candidateHash": resolved_candidate_hash},
        "after": {"matchingCount": 0, "candidateHash": "empty"},
        "deleteResult": {"success": True, "deletedCount": 1, "candidateIds": [7]},
    }
    if artifact_version is not None:
        payload["artifactVersion"] = artifact_version
    return payload


def _outbox_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 7,
            "idempotency_key": "stale",
            "payload_json": "{\"key\":\"stale\"}",
            "status": "failed",
            "attempts": 5,
            "next_attempt_at": None,
            "last_error": "stale",
            "created_at": "2026-04-01T00:00:00+00:00",
            "updated_at": "2026-04-01T00:00:00+00:00",
            "event_type": "notion_push",
            "max_attempts": 5,
            "created_by": "test",
        }
    ]


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _outbox_run_record(
    run_id: str,
    *,
    mode: str = "dry-run",
    contract_version: int = 2,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "contract_version": contract_version,
        "task": "outbox-purge",
        "mode": mode,
        "risk_level": "high",
        "status": "dry_run_passed" if mode == "dry-run" else "executed",
        "inputs": {
            "task_name": "outbox-purge",
            "planHash": "sha256:outbox-plan",
        },
        "outputs": {},
        "plan_ref": "task_plan.json",
    }


def _write_task_ledger_run(
    root: Path,
    run_id: str,
    *,
    task: str,
    mode: str,
    artifacts: dict[str, dict[str, Any] | str],
    missing: set[str] | None = None,
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
            "mode": mode,
            "status": "dry_run_passed" if mode == "dry-run" else "executed",
            "runDir": str(run_dir),
        },
    )
    return run_dir


def _write_suppression_ledger_run(
    root: Path,
    run_id: str,
    *,
    mode: str = "dry-run",
    plan: dict[str, Any] | str | None = None,
    record: dict[str, Any] | str | None = None,
    command: dict[str, Any] | str | None = None,
    pre_state: dict[str, Any] | str | None = None,
    missing: set[str] | None = None,
) -> Path:
    artifacts: dict[str, dict[str, Any] | str] = {
        "task_plan.json": _suppression_plan(mode=mode) if plan is None else plan,
        "run_record.json": _suppression_run_record(run_id, mode=mode)
        if record is None
        else record,
        "suppression_sync_command.json": _suppression_command()
        if command is None
        else command,
    }
    if mode == "execute":
        artifacts["pre_suppression_sync_state.json"] = (
            _suppression_state() if pre_state is None else pre_state
        )
    return _write_task_ledger_run(
        root,
        run_id,
        task="suppression-sync",
        mode=mode,
        artifacts=artifacts,
        missing=missing,
    )


def _write_outbox_ledger_run(
    root: Path,
    run_id: str,
    *,
    mode: str = "dry-run",
    plan: dict[str, Any] | str | None = None,
    record: dict[str, Any] | str | None = None,
    candidates: dict[str, Any] | str | None = None,
    result: dict[str, Any] | str | None = None,
    missing: set[str] | None = None,
) -> Path:
    artifacts: dict[str, dict[str, Any] | str] = {
        "task_plan.json": _outbox_plan(mode=mode) if plan is None else plan,
        "run_record.json": _outbox_run_record(run_id, mode=mode)
        if record is None
        else record,
        "outbox_candidates.json": _outbox_candidates()
        if candidates is None
        else candidates,
    }
    if mode == "execute":
        artifacts["outbox_purge_result.json"] = (
            _outbox_result() if result is None else result
        )
    return _write_task_ledger_run(
        root,
        run_id,
        task="outbox-purge",
        mode=mode,
        artifacts=artifacts,
        missing=missing,
    )


def _finding_codes(report: dict[str, Any]) -> set[str]:
    return {str(finding.get("code")) for finding in report.get("findings", [])}


def _suppression_outbox_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    subsystem = report["subsystems"]["suppression_outbox"]
    return subsystem["findings"]


def test_dry_run_reports_suppression_outbox_subsystem_without_findings(
    tmp_path: Path,
) -> None:
    root = _ledger_root(tmp_path)
    _write_suppression_ledger_run(root, "suppression-ok")
    _write_outbox_ledger_run(root, "outbox-ok")

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == 0
    subsystem = result.outputs["subsystems"]["suppression_outbox"]
    assert subsystem["subsystem"] == "suppression_outbox"
    assert subsystem["ownerTask"] == "suppression-sync/outbox-purge"
    assert subsystem["runsChecked"] == 2
    assert subsystem["resourcesChecked"] == 6
    assert subsystem["findings"] == []


def test_dry_run_fails_closed_on_missing_suppression_outbox_required_artifact(
    tmp_path: Path,
) -> None:
    run_dir = _write_outbox_ledger_run(
        _ledger_root(tmp_path),
        "outbox-missing-candidates",
        missing={"outbox_candidates.json"},
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "missing_required_artifact" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _suppression_outbox_findings(result.outputs)
        if item["code"] == "missing_required_artifact"
    )
    assert finding["severity"] == "critical"
    assert finding["subsystem"] == "suppression_outbox"
    assert finding["resourceId"] == "suppression_outbox.outbox_candidates"
    assert finding["evidencePath"] == str(run_dir / "outbox_candidates.json")


def test_dry_run_fails_closed_on_malformed_suppression_outbox_json(
    tmp_path: Path,
) -> None:
    _write_suppression_ledger_run(
        _ledger_root(tmp_path),
        "suppression-malformed-command",
        command="{not-json",
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "malformed_json" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _suppression_outbox_findings(result.outputs)
        if item["code"] == "malformed_json"
    )
    assert finding["subsystem"] == "suppression_outbox"
    assert finding["resourceId"] == "suppression_outbox.suppression_sync_command"


@pytest.mark.parametrize(
    "version_surface, expected_resource_id",
    [
        (
            "task_plan",
            "suppression_outbox.task_plan.contract_version",
        ),
        (
            "run_record",
            "suppression_outbox.run_record.contract_version",
        ),
        (
            "artifact",
            "suppression_outbox.outbox_candidates.artifact_version",
        ),
    ],
)
def test_dry_run_fails_closed_on_suppression_outbox_unsupported_version(
    tmp_path: Path,
    version_surface: str,
    expected_resource_id: str,
) -> None:
    plan = _outbox_plan(
        contract_version=999 if version_surface == "task_plan" else 2
    )
    record = _outbox_run_record(
        "outbox-unsupported-version",
        contract_version=999 if version_surface == "run_record" else 2,
    )
    candidates = _outbox_candidates(
        artifact_version=999 if version_surface == "artifact" else 1
    )
    _write_outbox_ledger_run(
        _ledger_root(tmp_path),
        "outbox-unsupported-version",
        plan=plan,
        record=record,
        candidates=candidates,
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "unsupported_contract_version" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _suppression_outbox_findings(result.outputs)
        if item["code"] == "unsupported_contract_version"
        and item["resourceId"] == expected_resource_id
    )
    assert finding["resourceId"] == expected_resource_id


def test_dry_run_fails_closed_on_suppression_command_binding_mismatch(
    tmp_path: Path,
) -> None:
    command = _suppression_command()
    command["command"] = [*command["command"]]
    command["command"][command["command"].index("--db-path") + 1] = "other.db"
    _write_suppression_ledger_run(
        _ledger_root(tmp_path),
        "suppression-command-mismatch",
        command=command,
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "dry_run_binding_mismatch" in _finding_codes(result.outputs)
    resource_ids = {
        finding["resourceId"] for finding in _suppression_outbox_findings(result.outputs)
    }
    assert "suppression_outbox.suppression_sync_command.command" in resource_ids


def test_dry_run_fails_closed_on_outbox_candidate_digest_drift(
    tmp_path: Path,
) -> None:
    _write_outbox_ledger_run(
        _ledger_root(tmp_path),
        "outbox-candidate-drift",
        candidates=_outbox_candidates(candidate_hash="drifted-candidate-hash"),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "resource_digest_mismatch" in _finding_codes(result.outputs)
    resource_ids = {
        finding["resourceId"] for finding in _suppression_outbox_findings(result.outputs)
    }
    assert "suppression_outbox.outbox_candidates.candidate_hash" in resource_ids


def test_dry_run_fails_closed_on_suppression_pre_state_database_drift(
    tmp_path: Path,
) -> None:
    _write_suppression_ledger_run(
        _ledger_root(tmp_path),
        "suppression-pre-state-drift",
        mode="execute",
        plan=_suppression_plan(mode="execute", row_count=2),
        record=_suppression_run_record("suppression-pre-state-drift", mode="execute"),
        pre_state=_suppression_state(row_count=3),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "resource_digest_mismatch" in _finding_codes(result.outputs)
    resource_ids = {
        finding["resourceId"] for finding in _suppression_outbox_findings(result.outputs)
    }
    assert "suppression_outbox.suppression_state.row_count" in resource_ids


def test_dry_run_flags_legacy_suppression_outbox_baseline_explicitly(
    tmp_path: Path,
) -> None:
    _write_outbox_ledger_run(
        _ledger_root(tmp_path),
        "outbox-legacy-baseline",
        candidates=_outbox_candidates(artifact_version=None),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == 0
    finding = next(
        item
        for item in _suppression_outbox_findings(result.outputs)
        if item["code"] == "genesis_baseline_missing"
    )
    assert finding["severity"] == "medium"
    assert (
        finding["resourceId"]
        == "suppression_outbox.outbox_candidates.artifact_version"
    )

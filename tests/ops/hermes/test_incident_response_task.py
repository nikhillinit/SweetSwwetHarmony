from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from integrations.hermes.locks import HermesLock
from integrations.hermes.tasks.base import EXIT_GATE_FAILURE, EXIT_LOCK_HELD
from integrations.hermes.tasks.registry import run_registered_task
from ops.maintenance import incident as incident_capsules
from ops.maintenance.incident import MaintenanceIncident

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
    mode: str = "preflight-only",
    incident_id: str | None = "github_20260528_010203",
    incident_phase: str | None = "freeze",
    artifact_root: Path | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="incident",
        config=str(_config_path(tmp_path)),
        plan_only=mode == "plan-only",
        preflight_only=mode == "preflight-only",
        dry_run=mode == "dry-run",
        execute=mode == "execute",
        ack_risk=None,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="test",
        json_output=False,
        incident_id=incident_id,
        incident_phase=incident_phase,
        artifact_root=str(artifact_root or tmp_path / "maintenance"),
    )


def _write_incident(
    artifact_root: Path,
    incident_id: str = "github_20260528_010203",
    *,
    status: str = "open",
) -> MaintenanceIncident:
    artifact_dir = artifact_root / incident_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    incident = MaintenanceIncident(
        incident_id=incident_id,
        component="github",
        error_type="RuntimeError",
        error_message="collector failed",
        status=status,
        artifact_dir=str(artifact_dir),
        traceback_text="Traceback: line 42",
        context={"collector": "github"},
    )
    (artifact_dir / "incident.json").write_text(
        json.dumps(
            {
                "incident_id": incident.incident_id,
                "component": incident.component,
                "error_type": incident.error_type,
                "error_message": incident.error_message,
                "status": incident.status,
                "created_at": incident.created_at,
                "updated_at": incident.updated_at,
                "artifact_dir": incident.artifact_dir,
                "traceback_text": incident.traceback_text,
                "context": incident.context,
                "repair_attempts": incident.repair_attempts,
            }
        ),
        encoding="utf-8",
    )
    return incident


def test_plan_only_writes_ledger_artifacts_and_stays_non_mutating(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "maintenance"
    _write_incident(artifact_root)

    result = run_registered_task(
        _args(tmp_path, mode="plan-only", artifact_root=artifact_root),
    )

    assert result.exit_code == 0
    assert result.status == "planned"
    assert result.plan["mutation"]["allowed"] is False
    assert result.plan["incident_id"] == "github_20260528_010203"
    assert result.plan["phase"] == "freeze"
    assert result.plan["capsule_contract"]["module"] == "ops.maintenance.incident"
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "task_plan.json").exists()
    assert (run_dir / "run_record.json").exists()
    assert (run_dir / "plan.md").exists()


@pytest.mark.parametrize(
    "overrides, expected_check",
    [
        ({"incident_id": None}, "incident_id_declared"),
        ({"incident_phase": "tribunal"}, "phase_valid"),
    ],
)
def test_missing_incident_id_or_invalid_phase_fails_preflight_safely(
    tmp_path: Path,
    overrides: dict[str, str | None],
    expected_check: str,
) -> None:
    artifact_root = tmp_path / "maintenance"
    _write_incident(artifact_root)

    result = run_registered_task(
        _args(tmp_path, artifact_root=artifact_root, **overrides),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    assert any(check.name == expected_check and not check.passed for check in result.checks)
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_dry_run_records_packet_plan_without_writing_incident_artifacts(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "maintenance"
    _write_incident(artifact_root)

    result = run_registered_task(
        _args(tmp_path, mode="dry-run", artifact_root=artifact_root),
    )

    packet_json = artifact_root / "github_20260528_010203" / "hermes_response_packet.json"
    packet_md = artifact_root / "github_20260528_010203" / "hermes_response_packet.md"
    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    assert result.outputs["dryRun"] is True
    assert result.outputs["mutationCommitted"] is False
    assert str(packet_json) in result.outputs["wouldWriteFiles"]
    assert not packet_json.exists()
    assert not packet_md.exists()
    assert (Path(result.run_dir or "") / "incident_response_dry_run.json").exists()


def test_lock_conflict_refuses_mutation_and_writes_lock_conflict(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "maintenance"
    _write_incident(artifact_root)
    config_path = _config_path(tmp_path)
    lock_path = tmp_path / "ai-logs" / "hermes" / "task-locks" / "incident-response.lock"
    lock = HermesLock(lock_path, mode="execute", run_id="held")
    assert lock.acquire(timeout_seconds=0) is True

    try:
        args = _args(tmp_path, mode="execute", artifact_root=artifact_root)
        args.config = str(config_path)
        result = run_registered_task(args)
    finally:
        lock.release()

    assert result.exit_code == EXIT_LOCK_HELD
    assert result.status == "lock_held"
    assert (Path(result.run_dir or "") / "lock_conflict.json").exists()
    assert not (artifact_root / "github_20260528_010203" / "hermes_response_packet.json").exists()


def test_execute_updates_capsule_and_writes_bounded_response_packet(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "maintenance"
    _write_incident(artifact_root)

    result = run_registered_task(
        _args(tmp_path, mode="execute", incident_phase="analyze", artifact_root=artifact_root),
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    assert result.outputs["mutationCommitted"] is True
    assert result.outputs["statusBefore"] == "open"
    assert result.outputs["statusAfter"] == "investigating"
    packet_json = artifact_root / "github_20260528_010203" / "hermes_response_packet.json"
    packet_md = artifact_root / "github_20260528_010203" / "hermes_response_packet.md"
    packet = json.loads(packet_json.read_text(encoding="utf-8"))
    updated = json.loads((artifact_root / "github_20260528_010203" / "incident.json").read_text(encoding="utf-8"))
    assert packet["incidentId"] == "github_20260528_010203"
    assert packet["phase"] == "analyze"
    assert packet["statusAfter"] == "investigating"
    assert updated["status"] == "investigating"
    assert packet_md.exists()
    assert (Path(result.run_dir or "") / "incident_response_artifacts.json").exists()
    assert (Path(result.run_dir or "") / "run_record.json").exists()


def test_execute_verify_phase_resolves_capsule_and_response_packet(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "maintenance"
    _write_incident(artifact_root, status="investigating")

    result = run_registered_task(
        _args(tmp_path, mode="execute", incident_phase="verify", artifact_root=artifact_root),
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    assert result.plan["expected_capsule_state"]["status_after"] == "resolved"
    assert result.outputs["statusBefore"] == "investigating"
    assert result.outputs["statusAfter"] == "resolved"
    packet_json = artifact_root / "github_20260528_010203" / "hermes_response_packet.json"
    packet = json.loads(packet_json.read_text(encoding="utf-8"))
    updated = json.loads((artifact_root / "github_20260528_010203" / "incident.json").read_text(encoding="utf-8"))
    assert packet["phase"] == "verify"
    assert packet["statusAfter"] == "resolved"
    assert updated["status"] == "resolved"


def test_execute_non_verify_phase_does_not_reopen_resolved_capsule(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "maintenance"
    _write_incident(artifact_root, status="resolved")

    result = run_registered_task(
        _args(tmp_path, mode="execute", incident_phase="analyze", artifact_root=artifact_root),
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    assert result.plan["expected_capsule_state"]["status_after"] == "resolved"
    assert result.outputs["statusBefore"] == "resolved"
    assert result.outputs["statusAfter"] == "resolved"
    updated = json.loads((artifact_root / "github_20260528_010203" / "incident.json").read_text(encoding="utf-8"))
    assert updated["status"] == "resolved"


def test_hermes_passes_artifact_root_explicitly_without_global_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "maintenance"
    _write_incident(artifact_root)
    sentinel_root = tmp_path / "global-maintenance"
    sentinel_root.mkdir()
    monkeypatch.setattr(incident_capsules, "ARTIFACTS_DIR", sentinel_root)
    original_load = incident_capsules.load_incident
    original_update = incident_capsules.update_incident_status
    calls: list[tuple[str, Path, Path | None]] = []

    def guarded_load(
        incident_id: str,
        *,
        artifacts_dir: Path | None = None,
    ) -> MaintenanceIncident | None:
        calls.append(("load", incident_capsules.ARTIFACTS_DIR, artifacts_dir))
        if incident_capsules.ARTIFACTS_DIR != sentinel_root:
            raise AssertionError("Hermes mutated the global maintenance artifact root")
        if artifacts_dir is None:
            raise AssertionError("Hermes did not pass an explicit maintenance artifact root")
        return original_load(incident_id, artifacts_dir=artifacts_dir)

    def guarded_update(
        incident_id: str,
        status: str,
        notes: str = "",
        *,
        artifacts_dir: Path | None = None,
    ) -> MaintenanceIncident | None:
        calls.append(("update", incident_capsules.ARTIFACTS_DIR, artifacts_dir))
        if incident_capsules.ARTIFACTS_DIR != sentinel_root:
            raise AssertionError("Hermes mutated the global maintenance artifact root")
        if artifacts_dir is None:
            raise AssertionError("Hermes did not pass an explicit maintenance artifact root")
        return original_update(
            incident_id,
            status,
            notes,
            artifacts_dir=artifacts_dir,
        )

    monkeypatch.setattr(incident_capsules, "load_incident", guarded_load)
    monkeypatch.setattr(incident_capsules, "update_incident_status", guarded_update)

    result = run_registered_task(
        _args(tmp_path, mode="execute", artifact_root=artifact_root),
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    assert calls
    assert all(call_root == sentinel_root for _, call_root, _ in calls)
    assert all(explicit_root == artifact_root for _, _, explicit_root in calls)


def test_postflight_catches_missing_packet_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "maintenance"
    _write_incident(artifact_root)

    def fake_write_packet(*_: Any, **__: Any) -> dict[str, Any]:
        return {
            "jsonPath": str(artifact_root / "github_20260528_010203" / "missing.json"),
            "markdownPath": str(artifact_root / "github_20260528_010203" / "missing.md"),
        }

    monkeypatch.setattr(
        "integrations.hermes.tasks.incident_response._write_response_packet",
        fake_write_packet,
    )

    result = run_registered_task(
        _args(tmp_path, mode="execute", artifact_root=artifact_root),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "postflight_failed"
    check = next(check for check in result.checks if check.name == "incident_packet_written")
    assert check.passed is False
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_postflight_catches_mismatched_expected_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "maintenance"
    _write_incident(artifact_root)

    def fake_update_incident_status(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(
        "integrations.hermes.tasks.incident_response.incident_capsules.update_incident_status",
        fake_update_incident_status,
    )

    result = run_registered_task(
        _args(tmp_path, mode="execute", artifact_root=artifact_root),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "postflight_failed"
    check = next(check for check in result.checks if check.name == "incident_status_matches_expected")
    assert check.passed is False
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()

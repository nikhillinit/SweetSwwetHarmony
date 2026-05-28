from __future__ import annotations

import argparse
import json
from pathlib import Path

from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.tasks.registry import run_registered_task
from ops.maintenance.incident import MaintenanceIncident

from .conftest import minimal_config_dict


def _schema_path() -> Path:
    return (
        PROJECT_ROOT
        / "integrations"
        / "hermes"
        / "schemas"
        / "hermes_response_packet.schema.json"
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


def _args(tmp_path: Path, artifact_root: Path) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="incident",
        config=str(_config_path(tmp_path)),
        plan_only=False,
        preflight_only=False,
        dry_run=False,
        execute=True,
        ack_risk=None,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="test",
        json_output=False,
        incident_id="github_20260528_010203",
        incident_phase="analyze",
        artifact_root=str(artifact_root),
    )


def _write_incident(artifact_root: Path) -> None:
    incident_id = "github_20260528_010203"
    artifact_dir = artifact_root / incident_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    incident = MaintenanceIncident(
        incident_id=incident_id,
        component="github",
        error_type="RuntimeError",
        error_message="collector failed",
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


def _live_response_packet(tmp_path: Path) -> dict[str, object]:
    artifact_root = tmp_path / "maintenance"
    _write_incident(artifact_root)

    result = run_registered_task(_args(tmp_path, artifact_root))

    assert result.exit_code == 0
    packet_path = artifact_root / "github_20260528_010203" / "hermes_response_packet.json"
    return json.loads(packet_path.read_text(encoding="utf-8"))


def test_incident_response_packet_schema_matches_live_packet_keys(
    tmp_path: Path,
) -> None:
    packet = _live_response_packet(tmp_path)
    schema = _load_schema()

    assert set(packet) == set(schema["properties"])
    assert schema["required"] == [
        "incidentId",
        "phase",
        "generatedAt",
        "runId",
        "statusBefore",
        "statusAfter",
        "capsule",
        "packetFiles",
    ]
    assert all(key in packet for key in schema["required"])


def test_incident_response_packet_schema_tracks_live_camel_case_shape() -> None:
    schema = _load_schema()
    properties = schema["properties"]

    assert schema["additionalProperties"] is False
    assert "incident_id" not in properties
    assert "packet_dir" not in properties
    assert "summary" not in properties
    assert "evidence" not in properties
    assert "incidentPacketTemplate" not in properties
    assert properties["packetFiles"]["items"]["type"] == "string"
    assert properties["capsule"]["type"] == ["object", "null"]

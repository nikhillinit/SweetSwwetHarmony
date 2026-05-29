"""Tests for maintenance incident capsule helper APIs."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from ops.maintenance.incident import (
    MaintenanceIncident,
    list_incidents,
    load_incident,
    update_incident_status,
)


def _write_incident(
    artifacts_dir: Path,
    incident_id: str,
    *,
    component: str = "github",
    status: str = "open",
) -> MaintenanceIncident:
    artifact_dir = artifacts_dir / incident_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    incident = MaintenanceIncident(
        incident_id=incident_id,
        component=component,
        error_type="RuntimeError",
        error_message=f"{component} failed",
        status=status,
        artifact_dir=str(artifact_dir),
        traceback_text="Traceback: line 42",
        context={"collector": component},
    )
    (artifact_dir / "incident.json").write_text(
        json.dumps(asdict(incident), indent=2),
        encoding="utf-8",
    )
    return incident


def test_explicit_artifact_dirs_keep_same_id_incidents_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident_id = "github_20260528_010203"
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    default_root = tmp_path / "default-root"
    _write_incident(root_a, incident_id, status="open")
    _write_incident(root_b, incident_id, status="open")
    _write_incident(default_root, incident_id, status="open")
    monkeypatch.setattr("ops.maintenance.incident.ARTIFACTS_DIR", default_root)

    updated_a = update_incident_status(
        incident_id,
        "resolved",
        "Root A verified",
        artifacts_dir=root_a,
    )
    updated_b = update_incident_status(
        incident_id,
        "investigating",
        "Root B still analyzing",
        artifacts_dir=root_b,
    )

    assert updated_a is not None
    assert updated_a.status == "resolved"
    assert updated_b is not None
    assert updated_b.status == "investigating"
    assert load_incident(incident_id, artifacts_dir=root_a).status == "resolved"
    assert load_incident(incident_id, artifacts_dir=root_b).status == "investigating"
    assert load_incident(incident_id).status == "open"
    assert [incident.incident_id for incident in list_incidents(artifacts_dir=root_a)] == [incident_id]
    assert [
        incident.incident_id
        for incident in list_incidents(status_filter="resolved", artifacts_dir=root_a)
    ] == [incident_id]
    assert list_incidents(status_filter="resolved", artifacts_dir=root_b) == []


def test_default_artifact_root_still_uses_global_artifacts_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident_id = "sec_edgar_20260528_010203"
    _write_incident(tmp_path, incident_id, component="sec_edgar", status="open")
    monkeypatch.setattr("ops.maintenance.incident.ARTIFACTS_DIR", tmp_path)

    loaded = load_incident(incident_id)
    updated = update_incident_status(incident_id, "resolved", "Default root verified")

    assert loaded is not None
    assert loaded.component == "sec_edgar"
    assert updated is not None
    assert updated.status == "resolved"
    assert [incident.incident_id for incident in list_incidents(status_filter="resolved")] == [
        incident_id
    ]

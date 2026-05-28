from __future__ import annotations

import argparse
import json
from pathlib import Path

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
        check="index",
    )


def _live_ledger_audit_report(tmp_path: Path) -> dict[str, object]:
    result = run_registered_task(_args(tmp_path))
    run_dir = Path(result.run_dir or "")
    return json.loads((run_dir / "ledger_audit_report.json").read_text(encoding="utf-8"))


def test_ledger_audit_report_schema_matches_live_report_keys(tmp_path: Path) -> None:
    schema = _load_schema()
    report = _live_ledger_audit_report(tmp_path)

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
        "findings",
        "reportArtifacts",
    ]
    assert all(key in report for key in schema["required"])


def test_ledger_audit_report_schema_tracks_live_camel_case_shape() -> None:
    schema = _load_schema()
    properties = schema["properties"]
    summary = properties["summary"]
    report_artifacts = properties["reportArtifacts"]

    assert schema["additionalProperties"] is False
    assert "audit_id" not in properties
    assert "checks_run" not in properties
    assert "ledger_entries" not in properties
    assert "drifts" not in properties
    assert "checksRun" in properties
    assert "validIndexEntries" in summary["properties"]
    assert report_artifacts["required"] == ["json", "markdown"]

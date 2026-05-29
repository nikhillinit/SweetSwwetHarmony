from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.tasks import config_promote
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


def _schema_path() -> Path:
    return (
        PROJECT_ROOT
        / "integrations"
        / "hermes"
        / "schemas"
        / "config_promote_report.schema.json"
    )


def _load_schema() -> dict[str, object]:
    return json.loads(_schema_path().read_text(encoding="utf-8"))


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
    data["ledger"]["redactionPatterns"].append("config-promote-schema-[A-Z]+")
    return _write_json(tmp_path / "proposed-model-routing.json", data)


def _args(
    tmp_path: Path,
    *,
    current_path: Path,
    proposed_path: Path,
    mode: str = "dry-run",
) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="config-promote",
        config=str(current_path),
        plan_only=False,
        preflight_only=False,
        dry_run=mode == "dry-run",
        execute=mode == "execute",
        ack_risk="CONFIG_PROMOTE" if mode == "execute" else None,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="test",
        json_output=False,
        proposed=str(proposed_path),
        policy_evidence=[],
    )


def _live_config_promote_report(tmp_path: Path) -> dict[str, object]:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed)
    )

    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    run_dir = Path(result.run_dir or "")
    return json.loads(
        (run_dir / config_promote.CONFIG_REPORT_ARTIFACT).read_text(encoding="utf-8")
    )


def _validate(schema: dict[str, object], artifact: dict[str, object]) -> None:
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(artifact)


def test_config_promote_report_schema_matches_live_report_keys(
    tmp_path: Path,
) -> None:
    report = _live_config_promote_report(tmp_path)
    schema = _load_schema()

    _validate(schema, report)
    assert report["task"] == "config-promote"
    assert report["dryRun"] is True
    assert report["mutationCommitted"] is False
    assert report["previousSnapshotRef"] is None
    assert report["diffArtifact"] == config_promote.CONFIG_DIFF_ARTIFACT
    assert set(report) == set(schema["properties"])
    assert schema["required"] == [
        "generatedAt",
        "task",
        "runId",
        "dryRun",
        "mutationCommitted",
        "currentConfig",
        "proposedConfig",
        "previousSnapshotRef",
        "diffArtifact",
        "policyReview",
    ]
    assert all(key in report for key in schema["required"])

    current_config = schema["properties"]["currentConfig"]
    proposed_config = schema["properties"]["proposedConfig"]
    policy_review = schema["properties"]["policyReview"]

    assert set(report["currentConfig"]) == set(current_config["properties"])
    assert current_config["required"] == ["path", "sha256Before", "sha256After"]
    assert set(report["proposedConfig"]) == set(proposed_config["properties"])
    assert proposed_config["required"] == ["path", "sha256"]
    assert set(report["policyReview"]) == set(policy_review["properties"])
    assert policy_review["required"] == [
        "risky_changes",
        "requires_evidence",
        "evidence",
    ]


def test_config_promote_report_schema_tracks_live_shape_not_overlay_stubs() -> None:
    schema = _load_schema()
    properties = schema["properties"]

    assert schema["additionalProperties"] is False
    assert "wouldReplace" not in properties
    assert "with" not in properties
    assert "diff" not in properties
    assert "current_config" not in properties
    assert "proposed_config" not in properties
    assert "previous_snapshot_ref" not in properties
    assert "new_config_hash" not in properties
    assert "old_config_hash" not in properties
    assert "configPromotionTemplate" not in properties
    assert "repairPromptTemplate" not in properties
    assert properties["task"] == {"const": "config-promote"}
    assert properties["diffArtifact"] == {"const": config_promote.CONFIG_DIFF_ARTIFACT}
    assert "previousSnapshotRef" in properties


def test_config_promote_report_schema_validates_execute_artifact(
    tmp_path: Path,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed, mode="execute")
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    report = json.loads(
        (
            Path(result.run_dir or "") / config_promote.CONFIG_REPORT_ARTIFACT
        ).read_text(encoding="utf-8")
    )
    _validate(_load_schema(), report)

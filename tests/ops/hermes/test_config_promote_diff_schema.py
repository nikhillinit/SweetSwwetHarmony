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
        / "config_promote_diff.schema.json"
    )


def _load_schema() -> dict[str, Any]:
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
    data["executors"]["codex"]["supportsExecute"] = False
    data["ledger"]["redactionPatterns"].append("config-promote-diff-[A-Z]+")
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


def _live_config_promote_diff(tmp_path: Path) -> dict[str, Any]:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed)
    )

    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    run_dir = Path(result.run_dir or "")
    return json.loads(
        (run_dir / config_promote.CONFIG_DIFF_ARTIFACT).read_text(encoding="utf-8")
    )


def _validate(schema: dict[str, Any], artifact: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(artifact)


def test_config_promote_diff_schema_matches_live_diff_keys(
    tmp_path: Path,
) -> None:
    artifact = _live_config_promote_diff(tmp_path)
    schema = _load_schema()

    _validate(schema, artifact)
    assert set(artifact) == set(schema["properties"])
    assert schema["required"] == [
        "generatedAt",
        "currentConfig",
        "proposedConfig",
        "diff",
        "policyReview",
    ]
    assert all(key in artifact for key in schema["required"])

    current_config = schema["properties"]["currentConfig"]
    proposed_config = schema["properties"]["proposedConfig"]
    diff = schema["properties"]["diff"]
    policy_review = schema["properties"]["policyReview"]

    assert set(artifact["currentConfig"]) == set(current_config["properties"])
    assert current_config["required"] == ["path", "sha256"]
    assert set(artifact["proposedConfig"]) == set(proposed_config["properties"])
    assert proposed_config["required"] == ["path", "sha256"]
    assert set(artifact["diff"]) == set(diff["properties"])
    assert diff["required"] == [
        "sections_changed",
        "executors_added",
        "executors_removed",
        "executor_changes",
        "execute_support_changes",
        "routing_policy_changes",
        "unified_diff",
    ]
    assert set(artifact["policyReview"]) == set(policy_review["properties"])
    assert policy_review["required"] == [
        "risky_changes",
        "requires_evidence",
        "evidence",
    ]


def test_config_promote_diff_schema_tracks_live_shape_not_overlay_stubs() -> None:
    schema = _load_schema()
    properties = schema["properties"]
    diff_properties = properties["diff"]["properties"]

    assert schema["additionalProperties"] is False
    assert "phase_changes" not in diff_properties
    assert "specialist_changes" not in diff_properties
    assert "risk_levels_lowered" not in diff_properties
    assert "blast_radius" not in properties
    assert "required_gates" not in properties
    assert "rollback" not in properties
    assert "configPromotionTemplate" not in properties
    assert "repairPromptTemplate" not in properties
    assert "current_config" not in properties
    assert "proposed_config" not in properties
    assert properties["generatedAt"] == {"type": "string", "format": "date-time"}


def test_config_promote_diff_schema_validates_execute_artifact(
    tmp_path: Path,
) -> None:
    current = _current_config_path(tmp_path)
    proposed = _proposed_config_path(tmp_path, current)

    result = run_registered_task(
        _args(tmp_path, current_path=current, proposed_path=proposed, mode="execute")
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    artifact = json.loads(
        (
            Path(result.run_dir or "") / config_promote.CONFIG_DIFF_ARTIFACT
        ).read_text(encoding="utf-8")
    )
    _validate(_load_schema(), artifact)

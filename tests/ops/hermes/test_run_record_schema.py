from __future__ import annotations

import argparse
import json
from pathlib import Path

from integrations.hermes.plan_contract import (
    CURRENT_CONTRACT_VERSION,
    canonical_plan_hash_from_plan,
)
from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


def _schema_path() -> Path:
    return PROJECT_ROOT / "integrations" / "hermes" / "schemas" / "run_record.schema.json"


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


def _ledger_audit_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="ledger-audit",
        config=str(_config_path(tmp_path)),
        plan_only=False,
        preflight_only=True,
        dry_run=False,
        execute=False,
        ack_risk=None,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="test",
        json_output=False,
        check="index",
    )


def _live_run_artifacts(
    tmp_path: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    result = run_registered_task(_ledger_audit_args(tmp_path))
    run_dir = Path(result.run_dir or "")
    plan = json.loads((run_dir / "task_plan.json").read_text(encoding="utf-8"))
    record = json.loads((run_dir / "run_record.json").read_text(encoding="utf-8"))
    return plan, record


def test_run_record_schema_matches_live_record_keys(tmp_path: Path) -> None:
    schema = _load_schema()
    _plan, record = _live_run_artifacts(tmp_path)

    assert set(record) == set(schema["properties"])
    assert "contract_version" in schema["properties"]
    assert "contract_version" not in schema["required"]
    assert schema["required"] == [
        "run_id",
        "task",
        "mode",
        "risk_level",
        "actor",
        "started_at",
        "updated_at",
        "status",
        "inputs",
        "locks",
        "ack_risk_token",
        "preflight",
        "outputs",
        "ledger",
        "plan_ref",
    ]
    assert all(key in record for key in schema["required"])


def test_live_task_plan_records_current_contract_version_and_hash(
    tmp_path: Path,
) -> None:
    plan, _record = _live_run_artifacts(tmp_path)

    assert plan["contractVersion"] == CURRENT_CONTRACT_VERSION
    assert plan["planHash"] == canonical_plan_hash_from_plan(plan)


def test_live_run_record_records_current_contract_version(tmp_path: Path) -> None:
    _plan, record = _live_run_artifacts(tmp_path)

    assert record["contract_version"] == CURRENT_CONTRACT_VERSION


def test_run_record_schema_uses_check_results_for_preflight() -> None:
    schema = _load_schema()
    properties = schema["properties"]
    preflight = properties["preflight"]
    preflight_properties = preflight["properties"]

    assert schema["additionalProperties"] is False
    assert preflight_properties["checks"]["items"] == {
        "$ref": "check_result.schema.json"
    }
    assert "mutation" not in properties
    assert "postflight" not in properties
    assert "repair_prompt_ref" not in properties

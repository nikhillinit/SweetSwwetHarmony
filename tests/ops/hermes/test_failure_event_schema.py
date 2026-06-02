from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.run import run_hermes

from .conftest import minimal_config_dict


def _schema_path() -> Path:
    return PROJECT_ROOT / "integrations" / "hermes" / "schemas" / "failure_event.schema.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(_schema_path().read_text(encoding="utf-8"))


def _write_failing_config(tmp_path: Path) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = [
        {
            "name": "preflight",
            "command": [
                sys.executable,
                "-c",
                "import sys; print('preflight'); sys.exit(4)",
            ],
            "timeoutSeconds": 5,
        }
    ]
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_failure_event_schema_contract() -> None:
    schema = _load_schema()

    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "artifactVersion",
        "eventType",
        "createdAt",
        "runId",
        "mode",
        "phase",
        "task",
        "failureType",
        "exitCode",
        "routingPlan",
        "statePaths",
        "artifacts",
        "details",
        "nextAction",
    ]
    assert schema["properties"]["failureType"]["enum"] == [
        "preflight",
        "postflight",
        "lock-held",
        "approval-required",
        "executor",
    ]


async def test_failure_event_schema_validates_live_artifact(tmp_path: Path) -> None:
    schema = _load_schema()
    config_path = _write_failing_config(tmp_path)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="dry-run",
        config_path=config_path,
    )
    event = json.loads(
        (Path(result.run_dir or "") / "failure_event.json").read_text(encoding="utf-8")
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(event)
    assert set(event) == set(schema["properties"])

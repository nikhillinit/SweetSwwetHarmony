from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.gates import GateBatch, GateResult


def _schema_path(name: str) -> Path:
    return PROJECT_ROOT / "integrations" / "hermes" / "schemas" / name


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads(_schema_path(name).read_text(encoding="utf-8"))


def _validate_gate_batch(
    schema: dict[str, Any],
    gate_result_schema: dict[str, Any],
    artifact: dict[str, Any],
) -> None:
    registry = Registry().with_resources(
        [
            (
                str(gate_result_schema["$id"]),
                Resource.from_contents(gate_result_schema),
            )
        ]
    )
    Draft202012Validator.check_schema(gate_result_schema)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, registry=registry).validate(artifact)


def _live_gate_batch_artifact() -> dict[str, Any]:
    return GateBatch(
        phase="postflight",
        results=(
            GateResult(
                name="shadow-agreement",
                command=(
                    "python",
                    "-m",
                    "integrations.hermes.gate_runners.shadow_agreement",
                ),
                return_code=0,
                stdout='{"ok": true}',
                stderr="",
                duration_ms=12,
                timed_out=False,
            ),
        ),
    ).to_dict()


def test_gate_batch_schema_matches_live_batch_keys() -> None:
    schema = _load_schema("gate_batch.schema.json")
    gate_result_schema = _load_schema("gate_result.schema.json")
    artifact = _live_gate_batch_artifact()

    assert set(artifact) == set(schema["properties"])
    assert schema["required"] == ["phase", "success", "results"]
    assert all(key in artifact for key in schema["required"])
    assert schema["properties"]["results"]["items"]["$ref"] == gate_result_schema["$id"]
    _validate_gate_batch(schema, gate_result_schema, artifact)


def test_gate_batch_schema_is_strict_to_phase_artifact_output() -> None:
    schema = _load_schema("gate_batch.schema.json")

    assert schema["additionalProperties"] is False
    assert "checks" not in schema["properties"]
    assert "postflight" not in schema["properties"]

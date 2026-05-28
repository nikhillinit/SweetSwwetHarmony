from __future__ import annotations

import json
from pathlib import Path

from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.gates import GateResult


def _schema_path() -> Path:
    return PROJECT_ROOT / "integrations" / "hermes" / "schemas" / "gate_result.schema.json"


def _load_schema() -> dict[str, object]:
    return json.loads(_schema_path().read_text(encoding="utf-8"))


def test_gate_result_schema_matches_live_result_keys() -> None:
    schema = _load_schema()
    result = GateResult(
        name="tribunal-clean",
        command=("python", "-m", "integrations.hermes.gate_runners.tribunal_clean"),
        return_code=0,
        stdout="{\"ok\": true}",
        stderr="",
        duration_ms=12,
        timed_out=False,
    ).to_dict()

    assert set(result) == set(schema["properties"])
    assert schema["required"] == [
        "name",
        "command",
        "returnCode",
        "stdout",
        "stderr",
        "durationMs",
        "timedOut",
        "success",
    ]
    assert all(key in result for key in schema["required"])


def test_gate_result_schema_is_strict_to_live_gate_batch_output() -> None:
    schema = _load_schema()

    assert schema["additionalProperties"] is False
    assert "passed" not in schema["properties"]
    assert "detail" not in schema["properties"]
    assert "evidence" not in schema["properties"]

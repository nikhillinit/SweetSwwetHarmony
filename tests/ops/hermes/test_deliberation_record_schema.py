from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from integrations.hermes.adapters import ExecutorResult
from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


def _schema_path() -> Path:
    return (
        PROJECT_ROOT
        / "integrations"
        / "hermes"
        / "schemas"
        / "deliberation_record.schema.json"
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
        task_name="deliberate",
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
        plan=None,
        task_text="Review this Hermes plan.",
        panel="codex",
        rounds=1,
        synthesizer="codex",
        coding_pair=False,
    )


class _FakeReviewer:
    async def execute(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> ExecutorResult:
        assert "Do not mutate files" in prompt
        assert context_files is None
        return ExecutorResult(
            executor="codex",
            success=True,
            exit_code=0,
            content=json.dumps(
                {
                    "verdict": "approve",
                    "confidence": 0.95,
                    "concerns": [],
                    "required_changes": [],
                }
            ),
            duration_ms=12,
            token_usage={"total_tokens": 9},
        )


def _live_deliberation_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    monkeypatch.setattr(
        "integrations.hermes.tasks.deliberation.build_reviewer_executor",
        lambda *_args, **_kwargs: _FakeReviewer(),
    )
    result = run_registered_task(_args(tmp_path))
    run_dir = Path(result.run_dir or "")
    return json.loads((run_dir / "deliberation_record.json").read_text(encoding="utf-8"))


def test_deliberation_record_schema_matches_live_record_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = _load_schema()
    record = _live_deliberation_record(tmp_path, monkeypatch)

    assert set(record) == set(schema["properties"])
    assert schema["required"] == [
        "deliberationId",
        "task",
        "mode",
        "dryRun",
        "mutationCommitted",
        "artifactCommit",
        "input",
        "panel",
        "synthesizer",
        "consensus",
        "freshnessTtlSeconds",
    ]
    assert all(key in record for key in schema["required"])


def test_deliberation_record_schema_tracks_live_camel_case_shape() -> None:
    schema = _load_schema()
    properties = schema["properties"]
    panel_item = properties["panel"]["items"]
    consensus = properties["consensus"]

    assert schema["additionalProperties"] is False
    assert "deliberation_id" not in properties
    assert "input_plan_hash" not in properties
    assert "freshness_ttl_seconds" not in properties
    assert panel_item["properties"]["requiredChanges"]["type"] == "array"
    assert "required_changes" not in panel_item["properties"]
    assert consensus["properties"]["overrideAllowed"]["type"] == "boolean"
    assert "no_quorum" in consensus["properties"]["status"]["enum"]

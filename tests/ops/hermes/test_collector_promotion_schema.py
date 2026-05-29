from __future__ import annotations

import json
from pathlib import Path

import pytest

import integrations.hermes.tasks.collector_promote as collector_promote
from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.tasks.registry import run_registered_task

from .test_collector_promote_task import (
    _args,
    _patch_runtime,
    _patch_valid_preflight,
)


def _schema_path() -> Path:
    return (
        PROJECT_ROOT
        / "integrations"
        / "hermes"
        / "schemas"
        / "collector_promotion.schema.json"
    )


def _load_schema() -> dict[str, object]:
    return json.loads(_schema_path().read_text(encoding="utf-8"))


def _live_collector_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    _patch_valid_preflight(monkeypatch)
    _patch_runtime(monkeypatch)

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    assert result.exit_code == 0
    run_dir = Path(result.run_dir or "")
    return json.loads(
        (run_dir / collector_promote.COLLECTOR_PROMOTION_ARTIFACT).read_text(
            encoding="utf-8"
        )
    )


def test_collector_promotion_schema_matches_live_artifact_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _live_collector_promotion(tmp_path, monkeypatch)
    schema = _load_schema()

    assert artifact["task"] == "collector-promote"
    assert set(artifact) == set(schema["properties"])
    assert schema["required"] == [
        "task",
        "mode",
        "dryRun",
        "mutationCommitted",
        "collector",
        "resultId",
        "targetState",
        "resultTargetState",
        "requestedResultStatus",
        "actualResultStatus",
        "requestedTargetReached",
        "desiredOutcomeSatisfied",
        "idempotent",
        "collision",
        "writesCommitted",
        "transition",
        "promotionResult",
        "artifactCommit",
        "persistence",
    ]
    assert all(key in artifact for key in schema["required"])


def test_collector_promotion_schema_tracks_live_shape_not_overlay_stubs() -> None:
    schema = _load_schema()
    properties = schema["properties"]

    assert schema["additionalProperties"] is False
    assert "wouldPromote" not in properties
    assert "current_state" not in properties
    assert "target_state" not in properties
    assert "keyword_config_hash" not in properties
    assert "estimated_signal_volume_delta" not in properties
    assert "blast_radius" not in properties
    assert "required_gates" not in properties
    assert "rollback" not in properties
    assert "command" not in properties
    assert "result" not in properties
    assert "collectorPromotionTemplate" not in properties
    assert "repairPromptTemplate" not in properties
    assert properties["task"] == {"const": "collector-promote"}
    assert properties["mode"]["enum"] == ["dry-run", "execute"]
    assert "resultId" in properties
    assert "result_id" not in properties

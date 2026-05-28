from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import integrations.hermes.tasks.shadow_validate as shadow_validate
from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


def _schema_path() -> Path:
    return (
        PROJECT_ROOT
        / "integrations"
        / "hermes"
        / "schemas"
        / "shadow_validation.schema.json"
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
        task_name="shadow-validate",
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
        db_path=str(tmp_path / "signals.db"),
        max_signals=25,
        sample_rate=0.5,
        timeout_seconds=5.0,
        max_disagreements=50,
        min_similarity_threshold=0.85,
        max_suggestions=10,
        min_agreement_rate=0.95,
    )


@dataclass
class _FakeShadowRunConfig:
    max_signals_per_run: int
    sample_rate: float
    timeout_seconds: float
    max_disagreements_stored: int
    min_similarity_threshold: float
    max_suggestions_per_run: int


@dataclass
class _FakeShadowResult:
    status: str = "completed"
    total_signals: int = 12
    phase1a_groups: int = 4
    phase_g_groups: int = 4
    agreements: int = 11
    disagreements_count: int = 1
    agreement_rate: float = 0.98
    duration_ms: float = 17.0
    inputs_hash: str = "abc123"
    disagreements: list[dict[str, Any]] = field(default_factory=list)
    run_id: str = ""
    truncated: bool = False
    error_summary: str | None = None


class _FakeRuntime:
    async def __aenter__(self) -> tuple[str, str]:
        return "fake-store", "fake-ro-store"

    async def __aexit__(self, *_: object) -> None:
        return None


def _patch_valid_database(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    monkeypatch.setattr(
        shadow_validate,
        "_inspect_shadow_database",
        lambda _db_path: {
            "path": str(db_path),
            "exists": True,
            "openable": True,
            "detail": "ok",
            "signals_table": True,
            "signals_company_id_column": True,
            "signals_detail": "signals.company_id present",
            "shadow_tables": {
                "shadow_entity_runs": True,
                "shadow_disagreements": True,
            },
        },
    )
    monkeypatch.setattr(
        shadow_validate,
        "_runtime_import_check",
        lambda: {
            "available": True,
            "detail": "fake runtime importable",
        },
    )


def _patch_shadow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_shadow_comparison(
        _store: Any,
        _ro_identity_store: Any,
        _config: _FakeShadowRunConfig,
    ) -> _FakeShadowResult:
        return _FakeShadowResult()

    async def fake_store_shadow_run(
        _store: Any,
        _shadow_result: _FakeShadowResult,
    ) -> int:
        return 42

    async def fake_store_skipped_shadow_run(_store: Any, _reason: str) -> int:
        return 43

    monkeypatch.setattr(
        shadow_validate,
        "_load_shadow_evaluator",
        lambda: shadow_validate.ShadowEvaluatorBindings(
            ShadowRunConfig=_FakeShadowRunConfig,
            run_shadow_comparison=fake_run_shadow_comparison,
            store_shadow_run=fake_store_shadow_run,
            store_skipped_shadow_run=fake_store_skipped_shadow_run,
        ),
    )
    monkeypatch.setattr(
        shadow_validate,
        "_open_shadow_runtime",
        lambda _context, _db_path, *, writable: _FakeRuntime(),
    )


def _live_shadow_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    db_path = tmp_path / "signals.db"
    _patch_valid_database(monkeypatch, db_path)
    _patch_shadow_runtime(monkeypatch)

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == 0
    run_dir = Path(result.run_dir or "")
    return json.loads((run_dir / "shadow_validation.json").read_text(encoding="utf-8"))


def test_shadow_validation_schema_matches_live_artifact_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _live_shadow_validation(tmp_path, monkeypatch)
    schema = _load_schema()

    assert artifact["shadowRun"]["runId"] == ""
    assert set(artifact) == set(schema["properties"])
    assert schema["required"] == [
        "task",
        "mode",
        "dryRun",
        "mutationCommitted",
        "artifactCommit",
        "persistence",
        "database",
        "shadowConfig",
        "shadowRun",
        "rawResult",
    ]
    assert all(key in artifact for key in schema["required"])


def test_shadow_validation_schema_tracks_live_shape_not_overlay_stubs() -> None:
    schema = _load_schema()
    properties = schema["properties"]

    assert schema["additionalProperties"] is False
    assert "status" not in properties
    assert "persisted" not in properties
    assert "agreement_rate" not in properties
    assert "total_signals" not in properties
    assert "shadowValidateTemplate" not in properties
    assert "repairPromptTemplate" not in properties
    assert properties["task"] == {"const": "shadow-validate"}
    assert properties["mode"]["enum"] == ["dry-run", "execute"]
    assert properties["rawResult"]["type"] == "object"
    assert "agreementRate" in properties["shadowRun"]["properties"]
    assert "agreement_rate" not in properties["shadowRun"]["properties"]
    assert "minLength" not in properties["shadowRun"]["properties"]["runId"]

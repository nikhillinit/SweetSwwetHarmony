from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import integrations.hermes.tasks.shadow_validate as shadow_validate
from integrations.hermes.tasks.base import EXIT_GATE_FAILURE
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


def _config_path(tmp_path: Path) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = []
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _args(
    tmp_path: Path,
    *,
    mode: str = "preflight-only",
    db_path: Path | None = None,
    min_agreement_rate: float = 0.95,
) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="shadow-validate",
        config=str(_config_path(tmp_path)),
        plan_only=mode == "plan-only",
        preflight_only=mode == "preflight-only",
        dry_run=mode == "dry-run",
        execute=mode == "execute",
        ack_risk=None,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="test",
        json_output=False,
        db_path=str(db_path or tmp_path / "signals.db"),
        max_signals=25,
        sample_rate=0.5,
        timeout_seconds=5.0,
        max_disagreements=50,
        min_similarity_threshold=0.85,
        max_suggestions=10,
        min_agreement_rate=min_agreement_rate,
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
    def __init__(self, calls: dict[str, Any], writable: bool) -> None:
        self.calls = calls
        self.writable = writable

    async def __aenter__(self) -> tuple[str, str]:
        self.calls.setdefault("writable", []).append(self.writable)
        return "fake-store", "fake-ro-store"

    async def __aexit__(self, *_: object) -> None:
        self.calls["closed"] = True


def _valid_database_state(db_path: Path) -> dict[str, Any]:
    return {
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
    }


def _patch_valid_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        shadow_validate,
        "_inspect_shadow_database",
        lambda db_path: _valid_database_state(db_path),
    )
    monkeypatch.setattr(
        shadow_validate,
        "_runtime_import_check",
        lambda: {
            "available": True,
            "detail": "fake runtime importable",
        },
    )


def _patch_shadow_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: _FakeShadowResult | None = None,
) -> dict[str, Any]:
    calls: dict[str, Any] = {"run": [], "persist": []}
    result = result or _FakeShadowResult()

    async def fake_run_shadow_comparison(
        store: Any,
        ro_identity_store: Any,
        config: _FakeShadowRunConfig,
    ) -> _FakeShadowResult:
        calls["run"].append(
            {
                "store": store,
                "ro_identity_store": ro_identity_store,
                "config": config,
            }
        )
        return result

    async def fake_store_shadow_run(store: Any, shadow_result: _FakeShadowResult) -> int:
        calls["persist"].append({"store": store, "result": shadow_result})
        return 42

    async def fake_store_skipped_shadow_run(store: Any, reason: str) -> int:
        calls["skipped"] = {"store": store, "reason": reason}
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
        lambda _context, _db_path, *, writable: _FakeRuntime(calls, writable),
    )
    return calls


def test_plan_only_writes_ledger_artifacts_and_stays_non_mutating(
    tmp_path: Path,
) -> None:
    result = run_registered_task(_args(tmp_path, mode="plan-only"))

    assert result.exit_code == 0
    assert result.status == "planned"
    assert result.plan["mutation"]["allowed"] is False
    assert result.plan["mutation"]["external_systems"] == []
    assert result.plan["artifacts"]["record"] == "shadow_validation.json"
    assert result.plan["shadow_config"]["max_signals_per_run"] == 25
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "task_plan.json").exists()
    assert (run_dir / "run_record.json").exists()
    assert (run_dir / "plan.md").exists()


def test_missing_shadow_evaluator_dependency_fails_preflight_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_valid_database(monkeypatch)
    monkeypatch.setattr(
        shadow_validate,
        "_load_shadow_evaluator",
        lambda: (_ for _ in ()).throw(ImportError("shadow evaluator unavailable")),
    )

    result = run_registered_task(_args(tmp_path))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(check for check in result.checks if check.name == "shadow_evaluator_importable")
    assert check.passed is False
    assert "shadow evaluator unavailable" in check.detail
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_dry_run_invokes_evaluator_without_persistence_and_writes_shadow_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_valid_database(monkeypatch)
    calls = _patch_shadow_runtime(monkeypatch)

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    run_dir = Path(result.run_dir or "")
    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    assert calls["writable"] == [False]
    assert len(calls["run"]) == 1
    assert calls["run"][0]["config"].max_signals_per_run == 25
    assert calls["persist"] == []
    assert result.outputs["mutationCommitted"] is False
    assert result.outputs["persistence"]["persisted"] is False
    assert (run_dir / "shadow_validation.json").exists()
    assert str(run_dir).startswith(str(tmp_path))
    assert not (tmp_path / "signals.db").exists()


def test_execute_persists_shadow_rows_and_records_explicit_mutation_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_valid_database(monkeypatch)
    calls = _patch_shadow_runtime(monkeypatch)

    result = run_registered_task(_args(tmp_path, mode="execute"))

    assert result.exit_code == 0
    assert result.status == "executed"
    assert calls["writable"] == [True]
    assert len(calls["persist"]) == 1
    assert result.plan["mutation"]["allowed"] is True
    assert result.plan["mutation"]["affected_tables"] == [
        "shadow_entity_runs",
        "shadow_disagreements",
    ]
    assert result.plan["mutation"]["external_systems"] == []
    assert result.outputs["mutationCommitted"] is True
    assert result.outputs["persistence"] == {
        "persisted": True,
        "shadowRunId": 42,
        "affectedTables": ["shadow_entity_runs", "shadow_disagreements"],
        "externalSystems": [],
    }
    assert (Path(result.run_dir or "") / "execute.json").exists()
    assert (Path(result.run_dir or "") / "shadow_validation.json").exists()


def test_postflight_catches_agreement_rate_below_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_valid_database(monkeypatch)
    _patch_shadow_runtime(monkeypatch, result=_FakeShadowResult(agreement_rate=0.5))

    result = run_registered_task(
        _args(tmp_path, mode="dry-run", min_agreement_rate=0.9),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "dry_run_failed"
    check = next(
        check for check in result.checks if check.name == "agreement_rate_above_threshold"
    )
    assert check.passed is False
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_postflight_catches_missing_shadow_validation_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_valid_database(monkeypatch)
    _patch_shadow_runtime(monkeypatch)
    monkeypatch.setattr(shadow_validate, "_write_shadow_artifact", lambda *_: None)

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "dry_run_failed"
    check = next(
        check for check in result.checks if check.name == "shadow_validation_artifact_written"
    )
    assert check.passed is False
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()

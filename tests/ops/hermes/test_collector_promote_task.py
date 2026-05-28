from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

import integrations.hermes.tasks.collector_promote as collector_promote
from integrations.hermes.tasks.base import EXIT_ACK_REQUIRED, EXIT_GATE_FAILURE
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


def _collector_state(tmp_path: Path) -> Path:
    path = tmp_path / "state" / "collectors.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "updated_at": "2026-05-28T00:00:00+00:00",
                "collectors": {
                    "github": {
                        "schema_version": 2,
                        "collector": "github",
                        "configured_status": "enabled",
                        "last_run_status": "success",
                        "effective_status": "healthy",
                        "health": "ok",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _args(
    tmp_path: Path,
    *,
    mode: str = "preflight-only",
    collector: str | None = "github",
    result_id: int | None = 123,
    target_state: str | None = "active",
    ack_risk: str | None = None,
    idempotency_key: str | None = "idem-123",
) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="collector-promote",
        config=str(_config_path(tmp_path)),
        plan_only=mode == "plan-only",
        preflight_only=mode == "preflight-only",
        dry_run=mode == "dry-run",
        execute=mode == "execute",
        ack_risk=ack_risk,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="test",
        json_output=False,
        db_path=str(tmp_path / "signals.db"),
        collector=collector,
        result_id=result_id,
        target_state=target_state,
        collector_state=str(_collector_state(tmp_path)),
        collector_config=None,
        idempotency_key=idempotency_key,
        reason="operator decision",
    )


def _valid_collector_state(*_: Any) -> dict[str, Any]:
    return {
        "path": "state/collectors.json",
        "exists": True,
        "readable": True,
        "collector": "github",
        "collector_known": True,
        "entry": {
            "collector": "github",
            "configured_status": "enabled",
            "effective_status": "healthy",
        },
        "detail": "collector known",
    }


def _valid_hunter_result(*_: Any) -> dict[str, Any]:
    return {
        "path": "signals.db",
        "exists": True,
        "openable": True,
        "detail": "hunter result found",
        "found": True,
        "result_id": 123,
        "status": "relevant",
        "source_api": "github",
        "query_collector": "github",
        "canonical_key": "domain:acme.ai",
        "promoted_signal_id": None,
    }


def _patch_valid_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        collector_promote,
        "_inspect_collector_state",
        _valid_collector_state,
    )
    monkeypatch.setattr(
        collector_promote,
        "_inspect_hunter_result",
        _valid_hunter_result,
    )
    monkeypatch.setattr(
        collector_promote,
        "_promotion_bridge_import_check",
        lambda: {
            "available": True,
            "detail": "fake bridge importable",
            "module": "workflows.hunter_promotion",
        },
    )


class _FakeStoreContext:
    def __init__(self, calls: dict[str, Any], writable: bool) -> None:
        self.calls = calls
        self.writable = writable
        self.store = object()

    async def __aenter__(self) -> object:
        self.calls.setdefault("writable", []).append(self.writable)
        return self.store

    async def __aexit__(self, *_: object) -> None:
        self.calls["closed"] = True


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    promotion_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    calls: dict[str, Any] = {"promote": [], "update_status": []}

    def fake_open_signal_store(_db_path: Path, *, writable: bool) -> _FakeStoreContext:
        return _FakeStoreContext(calls, writable)

    async def fake_promote(
        store: object,
        result_id: int,
        *,
        actor: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        calls["promote"].append(
            {
                "store": store,
                "result_id": result_id,
                "actor": actor,
                "idempotency_key": idempotency_key,
            }
        )
        return promotion_result or {
            "success": True,
            "signal_id": 456,
            "result_id": result_id,
            "status": "promoted",
            "message": "promoted",
            "collision": False,
        }

    async def fake_update_status(
        store: object,
        result_id: int,
        new_status: str,
        *,
        operator_feedback: str | None,
        actor: str,
    ) -> None:
        calls["update_status"].append(
            {
                "store": store,
                "result_id": result_id,
                "new_status": new_status,
                "operator_feedback": operator_feedback,
                "actor": actor,
            }
        )

    monkeypatch.setattr(collector_promote, "_open_signal_store", fake_open_signal_store)
    monkeypatch.setattr(
        collector_promote,
        "_load_promote_hunter_result",
        lambda: fake_promote,
    )
    monkeypatch.setattr(
        collector_promote,
        "_load_update_result_status",
        lambda: fake_update_status,
    )
    return calls


def test_plan_only_writes_ledger_artifacts_and_stays_non_mutating(
    tmp_path: Path,
) -> None:
    result = run_registered_task(_args(tmp_path, mode="plan-only"))

    assert result.exit_code == 0
    assert result.status == "planned"
    assert result.plan["mutation"]["allowed"] is False
    assert result.plan["mutation"]["affected_files"] == []
    assert result.plan["mutation"]["external_systems"] == []
    assert result.plan["ack_risk_token"] == collector_promote.COLLECTOR_PROMOTE_ACK
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "task_plan.json").exists()
    assert (run_dir / "run_record.json").exists()


def test_missing_required_inputs_fail_preflight_safely_and_emit_repair_prompt(
    tmp_path: Path,
) -> None:
    result = run_registered_task(
        _args(tmp_path, collector=None, result_id=None, target_state=None),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    failed = {check.name for check in result.checks if not check.passed}
    assert {
        "collector_declared",
        "result_id_declared",
        "target_state_supported",
    }.issubset(failed)
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_dry_run_does_not_invoke_promotion_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_valid_preflight(monkeypatch)
    calls = _patch_runtime(monkeypatch)

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    run_dir = Path(result.run_dir or "")
    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    assert calls["promote"] == []
    assert "writable" not in calls
    assert result.outputs["mutationCommitted"] is False
    assert result.outputs["artifactCommit"] == {
        "ledgerOnly": True,
        "runtimeState": False,
        "externalSystems": False,
    }
    assert (run_dir / collector_promote.COLLECTOR_PROMOTION_ARTIFACT).exists()
    assert not (tmp_path / "signals.db").exists()


def test_execute_requires_ack_before_mutating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_valid_preflight(monkeypatch)
    calls = _patch_runtime(monkeypatch)

    result = run_registered_task(_args(tmp_path, mode="execute"))

    assert result.exit_code == EXIT_ACK_REQUIRED
    assert result.status == "approval_required"
    assert result.outputs["requiredAck"] == collector_promote.COLLECTOR_PROMOTE_ACK
    assert calls["promote"] == []
    assert "writable" not in calls


def test_execute_uses_promotion_bridge_and_records_mutation_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_valid_preflight(monkeypatch)
    calls = _patch_runtime(monkeypatch)

    result = run_registered_task(
        _args(
            tmp_path,
            mode="execute",
            ack_risk=collector_promote.COLLECTOR_PROMOTE_ACK,
        ),
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    assert calls["writable"] == [True]
    assert calls["promote"][0]["result_id"] == 123
    assert calls["update_status"] == []
    assert calls["promote"][0]["actor"] == "operator:test"
    assert calls["promote"][0]["idempotency_key"] == "idem-123"
    assert result.plan["mutation"] == {
        "allowed": True,
        "affected_db": str(tmp_path / "signals.db"),
        "affected_files": [str(tmp_path / "signals.db")],
        "affected_tables": collector_promote.PROMOTION_TABLES,
        "external_systems": [],
    }
    assert result.outputs["mutationCommitted"] is True
    assert result.outputs["persistence"] == {
        "persisted": True,
        "affectedDb": str(tmp_path / "signals.db"),
        "affectedTables": collector_promote.PROMOTION_TABLES,
        "externalSystems": [],
    }
    assert (Path(result.run_dir or "") / "execute.json").exists()
    assert (
        Path(result.run_dir or "") / collector_promote.COLLECTOR_PROMOTION_ARTIFACT
    ).exists()


def test_execute_demote_uses_status_bridge_and_demote_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_valid_preflight(monkeypatch)
    calls = _patch_runtime(monkeypatch)

    missing_ack = run_registered_task(
        _args(tmp_path, mode="execute", target_state="shadow"),
    )
    assert missing_ack.exit_code == EXIT_ACK_REQUIRED
    assert missing_ack.outputs["requiredAck"] == collector_promote.COLLECTOR_DEMOTE_ACK
    assert calls["update_status"] == []

    result = run_registered_task(
        _args(
            tmp_path,
            mode="execute",
            target_state="shadow",
            ack_risk=collector_promote.COLLECTOR_DEMOTE_ACK,
        ),
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    assert calls["writable"] == [True]
    assert calls["promote"] == []
    assert calls["update_status"][0] == {
        "store": calls["update_status"][0]["store"],
        "result_id": 123,
        "new_status": "not_relevant",
        "operator_feedback": "operator decision",
        "actor": "operator:test",
    }
    assert result.plan["mutation"]["affected_tables"] == collector_promote.DEMOTION_TABLES
    assert result.outputs["promotionResult"] == {
        "success": True,
        "result_id": 123,
        "status": "not_relevant",
        "message": "Hunter result demoted to not_relevant",
    }


def test_postflight_catches_missing_collector_promotion_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_valid_preflight(monkeypatch)
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(collector_promote, "_write_collector_artifact", lambda *_: None)

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "dry_run_failed"
    check = next(
        check
        for check in result.checks
        if check.name == "collector_promotion_artifact_written"
    )
    assert check.passed is False
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_postflight_catches_failed_promotion_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_valid_preflight(monkeypatch)
    _patch_runtime(
        monkeypatch,
        promotion_result={
            "success": False,
            "result_id": 123,
            "status": "failed",
            "message": "fake failure",
        },
    )

    result = run_registered_task(
        _args(
            tmp_path,
            mode="execute",
            ack_risk=collector_promote.COLLECTOR_PROMOTE_ACK,
        ),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "postflight_failed"
    check = next(
        check for check in result.checks if check.name == "promotion_result_success"
    )
    assert check.passed is False
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()

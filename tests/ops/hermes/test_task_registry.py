from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from integrations.hermes.locks import assert_canonical_lock_order
from integrations.hermes.plan_contract import CURRENT_CONTRACT_VERSION
from integrations.hermes.tasks.base import HermesTask
from integrations.hermes.tasks.registry import (
    TASK_REGISTRY,
    registered_task_names,
    run_registered_task,
)


def _args(**overrides: object) -> argparse.Namespace:
    payload = {
        "task_name": "contract-check",
        "plan_only": True,
        "preflight_only": False,
        "dry_run": False,
        "json_output": False,
    }
    payload.update(overrides)
    return argparse.Namespace(**payload)


def test_pr11_registered_tasks_add_config_promote_after_collector_promote() -> None:
    assert registered_task_names() == [
        "collector-promote",
        "config-promote",
        "contract-check",
        "deliberate",
        "governance",
        "incident",
        "ledger-audit",
        "outbox-purge",
        "restore-db",
        "shadow-validate",
        "suppression-sync",
        "thesis-eval",
    ]


def test_registered_task_lock_declarations_follow_canonical_order() -> None:
    for task_name, task_type in TASK_REGISTRY.items():
        assert_canonical_lock_order(task_type.required_locks, task_name=task_name)


def test_base_plan_rejects_non_canonical_lock_declarations() -> None:
    class BadLockOrderTask(HermesTask):
        name = "bad-lock-order"
        required_locks = ("suppression-cache", "signals.db")

    with pytest.raises(ValueError, match="canonical lock order"):
        BadLockOrderTask().plan(
            BadLockOrderTask().build_context(_args(), mode="plan-only")
        )


def test_plan_only_registered_task_is_non_mutating() -> None:
    result = run_registered_task(_args())

    assert result.exit_code == 0
    assert result.status == "planned"
    assert result.plan["contractVersion"] == CURRENT_CONTRACT_VERSION
    assert "contract_version" not in result.plan
    assert result.outputs == {}
    assert result.plan["mutation"] == {"allowed": False, "external_systems": []}


def test_preflight_registered_task_checks_contract_loaded() -> None:
    result = run_registered_task(
        _args(plan_only=False, preflight_only=True),
    )

    assert result.exit_code == 0
    assert result.status == "preflight_passed"
    assert [check.name for check in result.checks] == ["contract_loaded"]


def test_dry_run_registered_task_stays_non_mutating() -> None:
    result = run_registered_task(
        _args(plan_only=False, dry_run=True),
    )

    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    assert result.outputs == {"dryRun": True, "mutationCommitted": False}


def test_schema_template_surface_stays_narrow_after_gate_batch_schema() -> None:
    root = Path(__file__).resolve().parents[3] / "integrations" / "hermes"

    assert {path.name for path in (root / "schemas").glob("*.json")} == {
        "bypass_record.schema.json",
        "check_result.schema.json",
        "collector_promotion.schema.json",
        "config_promote_diff.schema.json",
        "config_promote_report.schema.json",
        "deliberation_record.schema.json",
        "failure_event.schema.json",
        "gate_batch.schema.json",
        "gate_result.schema.json",
        "hermes_response_packet.schema.json",
        "ledger_audit_report.schema.json",
        "outbox_candidates.schema.json",
        "run_record.schema.json",
        "shadow_validation.schema.json",
        "task_result.schema.json",
    }
    assert {path.name for path in (root / "templates").glob("*.j2")} == {
        "reviewer_prompt.md.j2",
    }

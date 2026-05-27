from __future__ import annotations

import argparse
from pathlib import Path

from integrations.hermes.tasks.registry import registered_task_names, run_registered_task


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


def test_contract_task_is_the_only_pr1_registered_task() -> None:
    assert registered_task_names() == ["contract-check"]


def test_plan_only_registered_task_is_non_mutating() -> None:
    result = run_registered_task(_args())

    assert result.exit_code == 0
    assert result.status == "planned"
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


def test_pr1_schema_template_surface_is_contract_only() -> None:
    root = Path(__file__).resolve().parents[3] / "integrations" / "hermes"

    assert {path.name for path in (root / "schemas").glob("*.json")} == {
        "check_result.schema.json",
        "task_result.schema.json",
    }
    assert {path.name for path in (root / "templates").glob("*.j2")} == {
        "reviewer_prompt.md.j2",
    }

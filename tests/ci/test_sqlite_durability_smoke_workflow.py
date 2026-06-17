from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "sqlite-durability-smoke.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step_by_name(name: str) -> dict:
    steps = _workflow()["jobs"]["sqlite-durability-smoke"]["steps"]
    return next(step for step in steps if step.get("name") == name)


def test_local_durability_fixture_opts_into_in_tree_db_guard() -> None:
    step = _step_by_name("Run local durability check")

    assert "artifacts/sqlite-durability-smoke/signals.db" in step["run"]
    assert step["env"]["HARMONIC_ALLOW_IN_TREE_DB"] == "true"
    assert step["env"]["STRICT_CONFIG_VALIDATION"] == "false"

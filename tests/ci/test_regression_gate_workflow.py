from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "regression-gate.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step_by_name(name: str) -> dict:
    steps = _workflow()["jobs"]["docker-build"]["steps"]
    return next(step for step in steps if step.get("name") == name)


def test_docker_smoke_uses_out_of_tree_database_path() -> None:
    """The runtime image keeps source under /app, so DB state must live elsewhere."""
    step = _step_by_name("Start container")

    assert "--tmpfs /tmp/harmonic:rw" in step["run"]
    assert "-e DISCOVERY_DB_PATH=/tmp/harmonic/signals.db" in step["run"]
    assert "/app/data/signals.db" not in step["run"]

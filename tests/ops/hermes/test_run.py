from __future__ import annotations

import json
import sys
from pathlib import Path

from integrations.hermes.run import run_hermes

from .conftest import minimal_config_dict


def _write_run_config(tmp_path: Path, *, gate_exit: int = 0) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = [
        {
            "name": "preflight",
            "command": [
                sys.executable,
                "-c",
                f"import sys; print('preflight'); sys.exit({gate_exit})",
            ],
            "timeoutSeconds": 5,
        }
    ]
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


async def test_plan_only_returns_routing_plan_without_files(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="plan-only",
        config_path=config_path,
    )

    assert result.exit_code == 0
    assert result.run_id is None
    assert result.plan.recommended_executor == "codex"
    assert not (tmp_path / "ai-logs").exists()


async def test_dry_run_writes_complete_vertical_slice(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="dry-run",
        config_path=config_path,
    )

    assert result.exit_code == 0
    assert result.run_id is not None
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "ledger.json").exists()
    assert (run_dir / "plan.json").exists()
    assert (run_dir / "prompt.txt").exists()
    assert (run_dir / "summary.md").exists()
    assert (run_dir / "state" / "S0_initial.json").exists()
    assert (run_dir / "state" / "S1_routing.json").exists()
    assert (run_dir / "state" / "S2_preflight.json").exists()
    assert (run_dir / "state" / "S3_postflight.json").exists()
    assert "Dry Run" in (run_dir / "summary.md").read_text(encoding="utf-8")

    index_lines = (tmp_path / "ai-logs" / "hermes" / "index.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(index_lines) == 1
    assert json.loads(index_lines[0])["runId"] == result.run_id


async def test_preflight_only_stops_after_preflight_state(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="preflight-only",
        config_path=config_path,
    )

    run_dir = Path(result.run_dir or "")
    assert result.exit_code == 0
    assert (run_dir / "state" / "S2_preflight.json").exists()
    assert not (run_dir / "state" / "S3_postflight.json").exists()


async def test_failed_preflight_returns_gate_failure_exit_code(tmp_path: Path) -> None:
    config_path = _write_run_config(tmp_path, gate_exit=4)

    result = await run_hermes(
        task="schema migration",
        phase="production",
        mode="dry-run",
        config_path=config_path,
    )

    assert result.exit_code == 4
    assert result.preflight is not None
    assert result.preflight.success is False

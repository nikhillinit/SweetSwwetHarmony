from __future__ import annotations

import json
import sys
from pathlib import Path

from integrations.hermes.config import GateSpec
from integrations.hermes.gates import PROJECT_ROOT, GateBatch, run_gates


async def test_run_gates_captures_successful_command(tmp_path: Path) -> None:
    specs = [
        GateSpec(
            name="ok",
            command=[sys.executable, "-c", "print('gate ok')"],
            timeoutSeconds=5,
        )
    ]

    batch = await run_gates(specs, phase="preflight", run_dir=tmp_path)

    assert batch.success is True
    assert batch.phase == "preflight"
    assert batch.results[0].name == "ok"
    assert batch.results[0].return_code == 0
    assert batch.results[0].stdout.strip() == "gate ok"
    assert batch.results[0].stderr == ""
    assert batch.results[0].timed_out is False


async def test_run_gates_captures_failure_without_shell(tmp_path: Path) -> None:
    specs = [
        GateSpec(
            name="bad",
            command=[
                sys.executable,
                "-c",
                "import sys; print('nope', file=sys.stderr); sys.exit(3)",
            ],
            timeoutSeconds=5,
        )
    ]

    batch = await run_gates(specs, phase="preflight", run_dir=tmp_path)

    assert batch.success is False
    assert batch.results[0].return_code == 3
    assert batch.results[0].stderr.strip() == "nope"


async def test_run_gates_records_timeout(tmp_path: Path) -> None:
    specs = [
        GateSpec(
            name="slow",
            command=[sys.executable, "-c", "import time; time.sleep(5)"],
            timeoutSeconds=1,
        )
    ]

    batch = await run_gates(specs, phase="preflight", run_dir=tmp_path)

    assert batch.success is False
    assert batch.results[0].timed_out is True
    assert batch.results[0].return_code == -1
    assert "timed out" in batch.results[0].stderr


async def test_run_gates_writes_phase_json_when_run_dir_exists(tmp_path: Path) -> None:
    specs = [
        GateSpec(
            name="ok",
            command=[sys.executable, "-c", "print('persisted')"],
            timeoutSeconds=5,
        )
    ]

    batch = await run_gates(specs, phase="postflight", run_dir=tmp_path)

    artifact = tmp_path / "gates" / "postflight.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload == batch.to_dict()
    assert payload["results"][0]["stdout"].strip() == "persisted"


async def test_run_gates_without_run_dir_creates_no_artifact(tmp_path: Path) -> None:
    specs = [
        GateSpec(
            name="ok",
            command=[sys.executable, "-c", "print('no artifact')"],
            timeoutSeconds=5,
        )
    ]

    batch = await run_gates(specs, phase="preflight", run_dir=None)

    assert batch.success is True
    assert not (tmp_path / "gates").exists()


def test_project_root_points_to_checkout() -> None:
    assert PROJECT_ROOT == Path(__file__).resolve().parents[3]
    assert (PROJECT_ROOT / "ops" / "cli.py").exists()
    assert isinstance(GateBatch(phase="preflight", results=()), GateBatch)

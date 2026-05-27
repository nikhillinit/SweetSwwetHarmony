from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import GateSpec

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class GateResult:
    name: str
    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.return_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "returnCode": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "durationMs": self.duration_ms,
            "timedOut": self.timed_out,
            "success": self.success,
        }


@dataclass(frozen=True)
class GateBatch:
    phase: str
    results: tuple[GateResult, ...]

    @property
    def success(self) -> bool:
        return all(result.success for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "success": self.success,
            "results": [result.to_dict() for result in self.results],
        }


async def run_gates(
    specs: list[GateSpec],
    phase: str,
    run_dir: Path | None,
) -> GateBatch:
    results = []
    for spec in specs:
        results.append(await _run_gate(spec))

    batch = GateBatch(phase=phase, results=tuple(results))
    if run_dir is not None:
        gates_dir = run_dir / "gates"
        gates_dir.mkdir(parents=True, exist_ok=True)
        (gates_dir / f"{phase}.json").write_text(
            json.dumps(batch.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
    return batch


async def _run_gate(spec: GateSpec) -> GateResult:
    start = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        *spec.command,
        cwd=str(PROJECT_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=spec.timeout_seconds,
        )
        return_code = process.returncode or 0
        timed_out = False
    except asyncio.TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        return_code = -1
        timed_out = True
        timeout_message = f"gate {spec.name!r} timed out after {spec.timeout_seconds}s"
        stderr_bytes = _append_stderr(stderr_bytes, timeout_message)

    duration_ms = int((time.perf_counter() - start) * 1000)
    return GateResult(
        name=spec.name,
        command=tuple(spec.command),
        return_code=return_code,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        duration_ms=duration_ms,
        timed_out=timed_out,
    )


def _append_stderr(stderr_bytes: bytes, message: str) -> bytes:
    if not stderr_bytes:
        return message.encode("utf-8")
    return stderr_bytes + b"\n" + message.encode("utf-8")

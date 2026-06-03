"""Advisory Hermes deliberation cross-check for the thesis gate (v1: never blocks)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def build_deliberation_argv(*, panel: str, rounds: int, synthesizer: str,
                            task_text: str) -> list[str]:
    return [
        "-m", "ops.cli", "hermes", "task", "deliberate",
        "--task-text", task_text,
        "--panel", panel,
        "--rounds", str(rounds),
        "--synthesizer", synthesizer,
    ]


def summarize(*, returncode: int, stdout: str, stderr: str) -> dict:
    return {
        "advisory": True,
        "ran": returncode == 0,
        "returncode": returncode,
        "stdout_tail": (stdout or "")[-500:],
        "stderr_tail": (stderr or "")[-500:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", default="codex,kimi")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--synthesizer", default="codex")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    cmd = [sys.executable] + build_deliberation_argv(
        panel=args.panel, rounds=args.rounds, synthesizer=args.synthesizer,
        task_text="cross-check borderline thesis golden-set classifications")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        summary = summarize(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    except Exception as exc:  # advisory: never fail the gate
        summary = summarize(returncode=1, stdout="", stderr=str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"advisory": True, "ran": summary["ran"]}))
    return 0  # advisory: always succeed


if __name__ == "__main__":
    raise SystemExit(main())

# scripts/ci/resolve_thesis_eval_mode.py
"""Resolve, auditably, how the thesis golden-set gate should run.

Modes:
  gold        - GOOGLE_API_KEY/GEMINI_API_KEY present; run the real classifier.
  hermes      - no API key, but `hermes route` yields an execute-capable executor.
  structural  - no API key and no execute-capable Hermes executor; live eval is BLOCKED.

Emits a decision JSON so the "no live eval" case is auditable, never a silent green.
Exit code is always 0; enforcement lives in check_thesis_gate_artifact.py.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

API_KEY_ENV = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
ROUTE_TASK = "thesis golden-set eval"


def resolve_api_key(env: dict[str, str]) -> str | None:
    for name in API_KEY_ENV:
        value = env.get(name)
        if value:
            return value
    return None


def route_executor(runner=subprocess.run) -> dict:
    """Return the parsed `hermes route --json` plan, or {} on any failure."""
    try:
        proc = runner(
            [sys.executable, "-m", "ops.cli", "hermes", "route",
             "--json", "--phase", "production", "--task", ROUTE_TASK],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return {}
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def decide(env: dict[str, str], plan: dict) -> dict:
    if resolve_api_key(env):
        return {"mode": "gold", "executor": None,
                "reason": "LLM API key present; running real classifier."}
    executor = plan.get("recommendedExecutor")
    meta = (plan.get("executorMetadata") or {}).get(executor or "", {})
    if executor and meta.get("supportsExecute"):
        return {"mode": "hermes", "executor": executor,
                "phase": plan.get("phase"), "risk": plan.get("risk"),
                "reason": f"No API key; Hermes routes to execute-capable '{executor}'."}
    return {"mode": "structural", "executor": None,
            "reason": ("No API key and no execute-capable Hermes executor; "
                       "live eval BLOCKED (structural checks only).")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True,
                        help="Path to write the decision JSON.")
    args = parser.parse_args(argv)
    decision = decide(dict(os.environ), route_executor())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(decision, indent=2), encoding="utf-8")
    print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

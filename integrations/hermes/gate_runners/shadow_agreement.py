from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.gate_runners._common import emit, latest_existing, load_json

ARTIFACT_NAMES = ("shadow_validation.json", "shadow_validate.json")
PASSING_STATUSES = {"completed", "passed", "pass"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check Hermes shadow validation agreement artifact"
    )
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--ledger-root", default="ai-logs/hermes")
    parser.add_argument("--min-rate", type=float, default=0.95)
    args = parser.parse_args(argv)

    artifact_path = _find_artifact(args.run_dir, args.ledger_root)
    if artifact_path is None:
        return emit(False, "shadow_validation.json not found")

    try:
        data = load_json(artifact_path)
    except Exception as exc:
        return emit(False, f"could not read shadow artifact: {exc}")
    if not isinstance(data, dict):
        return emit(False, "shadow artifact must be a JSON object")

    status = _status(data)
    rate = _agreement_rate(data)
    ok = status in PASSING_STATUSES and rate >= args.min_rate
    return emit(
        ok,
        "shadow agreement passed" if ok else "shadow agreement failed",
        {
            "artifact": str(artifact_path),
            "status": status,
            "agreementRate": rate,
            "minRate": args.min_rate,
        },
    )


def _find_artifact(run_dir: str | None, ledger_root: str) -> Path | None:
    if run_dir:
        run_path = Path(run_dir)
        if run_path.is_file():
            return run_path if run_path.name in ARTIFACT_NAMES else None
        return latest_existing([run_path / name for name in ARTIFACT_NAMES])

    root = Path(ledger_root)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    candidates: list[Path] = []
    for name in ARTIFACT_NAMES:
        candidates.extend(root.glob(f"runs/*/{name}"))
    return latest_existing(candidates)


def _status(data: dict[str, Any]) -> str:
    shadow_run = data.get("shadowRun")
    if isinstance(shadow_run, dict):
        return str(shadow_run.get("status") or "").lower()
    shadow_run = data.get("shadow_run")
    if isinstance(shadow_run, dict):
        return str(shadow_run.get("status") or "").lower()
    return str(data.get("status") or "").lower()


def _agreement_rate(data: dict[str, Any]) -> float:
    shadow_run = data.get("shadowRun")
    if isinstance(shadow_run, dict):
        return _float_value(shadow_run.get("agreementRate"))
    shadow_run = data.get("shadow_run")
    if isinstance(shadow_run, dict):
        return _float_value(shadow_run.get("agreement_rate"))
    return _float_value(data.get("agreementRate") or data.get("agreement_rate"))


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    sys.exit(main())

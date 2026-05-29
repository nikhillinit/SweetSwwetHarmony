from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.gate_runners._common import emit, latest_existing, load_json

ARTIFACT_NAMES = ("shadow_validation.json", "shadow_validate.json")
CANONICAL_PASSING_STATUS = "completed"
LEGACY_PASSING_STATUSES = {"passed", "pass"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check Hermes shadow validation agreement artifact"
    )
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--ledger-root", default="ai-logs/hermes")
    parser.add_argument("--min-rate", type=float, default=0.95)
    parser.add_argument(
        "--strict-status",
        action="store_true",
        help="Require the canonical completed status and reject legacy pass statuses",
    )
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
    status_compatibility = _status_compatibility(
        status,
        strict_status=bool(args.strict_status),
    )
    ok = bool(status_compatibility["accepted"]) and rate >= args.min_rate
    if ok and status_compatibility["deprecated"]:
        detail = "shadow agreement passed with deprecated status"
    elif ok:
        detail = "shadow agreement passed"
    else:
        detail = "shadow agreement failed"
    return emit(
        ok,
        detail,
        {
            "artifact": str(artifact_path),
            "status": status,
            "statusCompatibility": status_compatibility,
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
        return str(shadow_run.get("status") or "").strip().lower()
    shadow_run = data.get("shadow_run")
    if isinstance(shadow_run, dict):
        return str(shadow_run.get("status") or "").strip().lower()
    return str(data.get("status") or "").strip().lower()


def _status_compatibility(
    status: str,
    *,
    strict_status: bool,
) -> dict[str, Any]:
    deprecated = status in LEGACY_PASSING_STATUSES
    accepted = status == CANONICAL_PASSING_STATUS or (deprecated and not strict_status)
    detail = None
    if deprecated:
        detail = f"status {status!r} is deprecated; emit 'completed'"
    return {
        "accepted": accepted,
        "canonicalStatus": CANONICAL_PASSING_STATUS,
        "deprecated": deprecated,
        "deprecationDetail": detail,
        "strictStatus": strict_status,
    }


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

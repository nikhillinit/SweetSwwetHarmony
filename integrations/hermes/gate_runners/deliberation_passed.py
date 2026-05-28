from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.gate_runners._common import emit, latest_existing, load_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check latest Hermes deliberation artifact"
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Specific run directory containing deliberation_record.json",
    )
    parser.add_argument("--ledger-root", default="ai-logs/hermes")
    parser.add_argument("--plan-hash", default=None)
    parser.add_argument("--max-age-seconds", type=int, default=86400)
    args = parser.parse_args(argv)

    record_path = _find_record(args.run_dir, args.ledger_root)
    if record_path is None:
        return emit(False, "no deliberation_record.json found")

    try:
        record = load_json(record_path)
    except Exception as exc:
        return emit(False, f"could not read deliberation record: {exc}")
    if not isinstance(record, dict):
        return emit(False, "deliberation record must be a JSON object")

    plan_hash_ok = _plan_hash_matches(record, args.plan_hash)
    age_ok, age_seconds, max_age_seconds = _freshness_status(
        record,
        record_path,
        args.max_age_seconds,
    )
    consensus = _dict_value(record.get("consensus"))
    blockers = list(consensus.get("blockers") or [])
    dissent_present = _dissent_present(consensus.get("dissent"))
    status_ok = consensus.get("status") == "approved"
    ok = plan_hash_ok and age_ok and status_ok and not blockers and not dissent_present

    return emit(
        ok,
        "deliberation passed" if ok else "deliberation failed",
        {
            "record": str(record_path),
            "planHashOk": plan_hash_ok,
            "ageOk": age_ok,
            "ageSeconds": age_seconds,
            "maxAgeSeconds": max_age_seconds,
            "consensus": consensus,
        },
    )


def _find_record(run_dir: str | None, ledger_root: str) -> Path | None:
    if run_dir:
        path = Path(run_dir)
        candidate = path if path.is_file() else path / "deliberation_record.json"
        return candidate if candidate.exists() else None

    root = Path(ledger_root)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return latest_existing(list(root.glob("runs/*/deliberation_record.json")))


def _plan_hash_matches(record: dict[str, Any], expected: str | None) -> bool:
    if expected is None:
        return True

    actual = _record_plan_hash(record)
    if actual is None:
        return False
    return _normalize_hash(actual) == _normalize_hash(expected)


def _record_plan_hash(record: dict[str, Any]) -> str | None:
    input_payload = _dict_value(record.get("input"))
    value = (
        input_payload.get("planHash")
        or input_payload.get("plan_hash")
        or record.get("input_plan_hash")
    )
    return str(value) if value else None


def _normalize_hash(value: str) -> str:
    return value.removeprefix("sha256:")


def _freshness_status(
    record: dict[str, Any],
    record_path: Path,
    max_age_seconds: int,
) -> tuple[bool, int, int]:
    ttl_seconds = _int_value(
        record.get("freshnessTtlSeconds")
        or record.get("freshness_ttl_seconds")
        or max_age_seconds,
        default=max_age_seconds,
    )
    effective_max_age = max(0, min(ttl_seconds, max_age_seconds))
    modified_at = datetime.fromtimestamp(record_path.stat().st_mtime, timezone.utc)
    age_seconds = int((datetime.now(timezone.utc) - modified_at).total_seconds())
    return age_seconds <= effective_max_age, age_seconds, effective_max_age


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dissent_present(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("present"))
    return bool(value)


def _int_value(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import re
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
    parser.add_argument("--allow-unbound", action="store_true")
    parser.add_argument("--allow-mtime-freshness", action="store_true")
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

    plan_binding = _plan_binding_evidence(args.plan_hash, args.allow_unbound)
    if args.plan_hash is None and not args.allow_unbound:
        return emit(
            False,
            "plan hash required",
            {
                "record": str(record_path),
                "planBinding": plan_binding,
            },
        )

    plan_hash_ok = _plan_hash_matches(record, args.plan_hash)
    age_ok, age_seconds, max_age_seconds, freshness_source = _freshness_status(
        record,
        record_path,
        args.max_age_seconds,
        allow_mtime_freshness=args.allow_mtime_freshness,
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
            "planBinding": plan_binding,
            "ageOk": age_ok,
            "ageSeconds": age_seconds,
            "maxAgeSeconds": max_age_seconds,
            "freshnessSource": freshness_source,
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


def _plan_binding_evidence(
    expected_hash: str | None,
    allow_unbound: bool,
) -> dict[str, Any]:
    if expected_hash is not None:
        return {
            "mode": "bound",
            "unsafe": False,
            "required": True,
        }
    return {
        "mode": "unbound" if allow_unbound else "required",
        "unsafe": bool(allow_unbound),
        "required": not allow_unbound,
    }


def _freshness_status(
    record: dict[str, Any],
    record_path: Path,
    max_age_seconds: int,
    *,
    allow_mtime_freshness: bool = False,
) -> tuple[bool, int | None, int, str | None]:
    ttl_seconds = _int_value(
        record.get("freshnessTtlSeconds")
        or record.get("freshness_ttl_seconds")
        or max_age_seconds,
        default=max_age_seconds,
    )
    effective_max_age = max(0, min(ttl_seconds, max_age_seconds))
    source_time, source = _freshness_source(record)
    if source_time is None and allow_mtime_freshness:
        source_time = datetime.fromtimestamp(record_path.stat().st_mtime, timezone.utc)
        source = "mtime"
    if source_time is None:
        return False, None, effective_max_age, None

    age_seconds = max(
        0,
        int((datetime.now(timezone.utc) - source_time).total_seconds()),
    )
    return age_seconds <= effective_max_age, age_seconds, effective_max_age, source


def _freshness_source(record: dict[str, Any]) -> tuple[datetime | None, str | None]:
    created_at = _parse_iso_datetime(record.get("createdAt") or record.get("created_at"))
    if created_at is not None:
        return created_at, "createdAt"

    deliberation_id_time = _parse_deliberation_id_timestamp(
        str(record.get("deliberationId") or record.get("deliberation_id") or "")
    )
    if deliberation_id_time is not None:
        return deliberation_id_time, "deliberationId"

    return None, None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_deliberation_id_timestamp(value: str) -> datetime | None:
    match = re.search(r"deliberate-(\d{8}T\d{6}Z)", value)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


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

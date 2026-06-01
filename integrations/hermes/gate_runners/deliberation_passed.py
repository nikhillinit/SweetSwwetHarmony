from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.deliberation_policy import evaluate_record_reviewer_policy
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
    parser.add_argument("--restore-plan")
    parser.add_argument("--restore-readiness")
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
    reviewer_policy = evaluate_record_reviewer_policy(record)
    reviewer_policy_ok = reviewer_policy.get("status") == "satisfied"
    restore_readiness = _restore_readiness_evidence(
        args.restore_plan,
        args.restore_readiness,
    )
    restore_readiness_ok = restore_readiness.get("ok") is not False
    status_ok = consensus.get("status") == "approved"
    ok = (
        plan_hash_ok
        and age_ok
        and status_ok
        and not blockers
        and not dissent_present
        and reviewer_policy_ok
        and restore_readiness_ok
    )

    evidence = {
        "record": str(record_path),
        "planHashOk": plan_hash_ok,
        "planBinding": plan_binding,
        "ageOk": age_ok,
        "ageSeconds": age_seconds,
        "maxAgeSeconds": max_age_seconds,
        "freshnessSource": freshness_source,
        "consensus": consensus,
        "reviewerPolicy": reviewer_policy,
    }
    if restore_readiness.get("required"):
        evidence["restoreReadiness"] = restore_readiness

    return emit(
        ok,
        "deliberation passed" if ok else "deliberation failed",
        evidence,
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


def _restore_readiness_evidence(
    restore_plan: str | None,
    restore_readiness: str | None,
) -> dict[str, Any]:
    if restore_plan is None and restore_readiness is None:
        return {"required": False, "ok": True, "status": "not_required"}
    if restore_plan is None:
        return {
            "required": True,
            "ok": False,
            "status": "missing_plan",
            "reasons": ["restore_plan_required"],
        }

    plan_path = Path(restore_plan)
    if not plan_path.exists():
        return {
            "required": True,
            "ok": False,
            "status": "missing_plan",
            "reasons": ["restore_plan_missing"],
            "plan": str(plan_path),
        }
    if restore_readiness is None:
        return {
            "required": True,
            "ok": False,
            "status": "missing_readiness",
            "reasons": ["restore_readiness_required"],
            "plan": str(plan_path),
        }

    readiness_path = Path(restore_readiness)
    if not readiness_path.exists():
        return {
            "required": True,
            "ok": False,
            "status": "missing_readiness",
            "reasons": ["restore_readiness_missing"],
            "plan": str(plan_path),
            "readiness": str(readiness_path),
        }

    try:
        plan = load_json(plan_path)
        readiness = load_json(readiness_path)
    except Exception as exc:
        return {
            "required": True,
            "ok": False,
            "status": "unreadable_evidence",
            "reasons": ["restore_readiness_unreadable"],
            "error": str(exc),
            "plan": str(plan_path),
            "readiness": str(readiness_path),
        }
    if not isinstance(plan, dict) or not isinstance(readiness, dict):
        return {
            "required": True,
            "ok": False,
            "status": "malformed_evidence",
            "reasons": ["restore_readiness_malformed"],
            "plan": str(plan_path),
            "readiness": str(readiness_path),
        }

    expected = _restore_readiness_expected(plan)
    actual = _restore_readiness_actual(readiness)
    reasons = _restore_readiness_mismatches(expected, actual)
    return {
        "required": True,
        "ok": not reasons,
        "status": "satisfied" if not reasons else "failed",
        "reasons": reasons,
        "plan": str(plan_path),
        "readiness": str(readiness_path),
        "expected": expected,
        "actual": actual,
    }


def _restore_readiness_expected(plan: dict[str, Any]) -> dict[str, Any]:
    target = _dict_value(plan.get("target"))
    backup = _dict_value(plan.get("backup"))
    contracts = _dict_value(plan.get("postflight_gate_contracts"))
    row_count_contract = _dict_value(contracts.get("row_count_above_watermark"))
    schema_contract = _dict_value(
        contracts.get("schema_version_matches_if_declared")
    )
    target_path = _string_or_none(target.get("path"))
    return {
        "task": _string_or_none(plan.get("task")),
        "mode": _string_or_none(plan.get("mode")),
        "mutationAllowed": _dict_value(plan.get("mutation")).get("allowed"),
        "planHash": _string_or_none(plan.get("planHash")),
        "targetPath": target_path,
        "targetClass": _string_or_none(
            target.get("target_class") or target.get("targetClass")
        )
        or _restore_target_class(target_path),
        "backupSha256": _string_or_none(backup.get("sha256")),
        "minRowCount": _int_or_none(row_count_contract.get("min_row_count")),
        "expectedSchemaVersion": _int_or_none(
            schema_contract.get("expected_schema_version")
        ),
    }


def _restore_readiness_actual(readiness: dict[str, Any]) -> dict[str, Any]:
    target = _dict_value(readiness.get("target"))
    backup = _dict_value(readiness.get("backup"))
    postflight = _dict_value(readiness.get("postflight"))
    return {
        "task": _string_or_none(readiness.get("task")),
        "mode": _string_or_none(readiness.get("mode")),
        "executeEligible": readiness.get("executeEligible"),
        "executePlanHash": _string_or_none(readiness.get("executePlanHash")),
        "targetPath": _string_or_none(target.get("path")),
        "targetClass": _string_or_none(target.get("class")),
        "backupSha256": _string_or_none(backup.get("sha256")),
        "minRowCount": _int_or_none(postflight.get("minRowCount")),
        "expectedSchemaVersion": _int_or_none(
            postflight.get("expectedSchemaVersion")
        ),
    }


def _restore_readiness_mismatches(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if expected.get("task") != "restore-db" or actual.get("task") != "restore-db":
        reasons.append("not_restore_plan")
    if expected.get("mode") != "execute":
        reasons.append("restore_plan_not_execute_mode")
    if expected.get("mutationAllowed") is not True:
        reasons.append("restore_plan_mutation_not_allowed")
    if actual.get("mode") != "execute" or actual.get("executeEligible") is not True:
        reasons.append("restore_readiness_not_execute_eligible")
    if not expected.get("planHash") or not actual.get("executePlanHash"):
        reasons.append("execute_plan_hash_missing")
    elif _normalize_optional_hash(
        actual.get("executePlanHash")
    ) != _normalize_optional_hash(expected.get("planHash")):
        reasons.append("execute_plan_hash_mismatch")
    if not expected.get("targetPath") or not actual.get("targetPath"):
        reasons.append("target_path_missing")
    elif not _paths_match(actual.get("targetPath"), expected.get("targetPath")):
        reasons.append("target_path_mismatch")
    if not expected.get("targetClass") or not actual.get("targetClass"):
        reasons.append("target_class_missing")
    elif actual.get("targetClass") != expected.get("targetClass"):
        reasons.append("target_class_mismatch")
    if not expected.get("backupSha256") or not actual.get("backupSha256"):
        reasons.append("backup_hash_missing")
    elif actual.get("backupSha256") != expected.get("backupSha256"):
        reasons.append("backup_hash_mismatch")
    if expected.get("minRowCount") is None or actual.get("minRowCount") is None:
        reasons.append("min_row_count_missing")
    elif actual.get("minRowCount") != expected.get("minRowCount"):
        reasons.append("min_row_count_mismatch")
    if actual.get("expectedSchemaVersion") != expected.get("expectedSchemaVersion"):
        reasons.append("expected_schema_version_mismatch")
    return reasons


def _restore_target_class(target_path: str | None) -> str | None:
    if not target_path:
        return None
    name = Path(target_path).name.lower()
    if name == "signals.db":
        return "live"
    if name == "signals.db.canary" or name.endswith(".canary"):
        return "canary"
    return "custom"


def _paths_match(left: Any, right: Any) -> bool:
    if not left or not right:
        return left == right
    return Path(str(left)) == Path(str(right))


def _normalize_optional_hash(value: Any) -> str | None:
    if value is None:
        return None
    return _normalize_hash(str(value))


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    sys.exit(main())

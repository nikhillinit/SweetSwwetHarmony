from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODE_DAILY_HEARTBEAT = "daily_heartbeat"
MODE_STRICT_WRITE_PROOF = "strict_write_proof"
VALID_MODES = (MODE_DAILY_HEARTBEAT, MODE_STRICT_WRITE_PROOF)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_WARN_DUPLICATE_ONLY = "WARN_DUPLICATE_ONLY"


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _normalize_exit_code(raw_exit_code: int) -> int:
    if 0 <= raw_exit_code <= 255:
        return raw_exit_code
    return 1


def _watchdog_exit_code(watchdog_payload: dict[str, Any]) -> int:
    raw_exit_code = watchdog_payload.get("exit_code")
    if isinstance(raw_exit_code, int) and 0 <= raw_exit_code <= 255:
        return raw_exit_code

    status = str(watchdog_payload.get("status") or "").upper()
    if status in {"OK", STATUS_PASS}:
        return 0
    if status == STATUS_FAIL:
        return 1
    return 2


def _collector_records(watchdog_payload: dict[str, Any]) -> list[dict[str, Any]]:
    collector_records = watchdog_payload.get("collectors")
    if not isinstance(collector_records, list):
        raise ValueError("watchdog JSON must contain a collectors list")
    for record in collector_records:
        if not isinstance(record, dict):
            raise ValueError("watchdog collectors entries must be objects")
    return collector_records


def _failing_operational_records(
    watchdog_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for record in _collector_records(watchdog_payload):
        if record.get("category") != "operational":
            continue
        if record.get("status") in {"STALE", "MISSING"}:
            failures.append(record)
    return failures


def _is_duplicate_only_failure(watchdog_payload: dict[str, Any]) -> bool:
    failures = _failing_operational_records(watchdog_payload)
    if not failures:
        return False
    return all(record.get("stale_reason") == "no_post_run_rows" for record in failures)


def _db_failure_reason(watchdog_payload: dict[str, Any]) -> str:
    watchdog_exit = _watchdog_exit_code(watchdog_payload)
    if watchdog_exit == 2:
        return "watchdog_error"

    failures = _failing_operational_records(watchdog_payload)
    if not failures:
        return "watchdog_failed"

    reasons = sorted(
        {
            str(record.get("stale_reason") or record.get("status") or "unknown")
            for record in failures
        }
    )
    return reasons[0] if len(reasons) == 1 else "mixed_failures"


def derive_db_progress(
    watchdog_payload: dict[str, Any],
    *,
    mode: str,
) -> tuple[str, str | None, int]:
    if mode not in VALID_MODES:
        raise ValueError(f"invalid keepalive mode: {mode}")

    watchdog_exit = _watchdog_exit_code(watchdog_payload)
    if watchdog_exit == 0:
        return STATUS_PASS, None, 0

    if _is_duplicate_only_failure(watchdog_payload):
        if mode == MODE_DAILY_HEARTBEAT:
            return STATUS_WARN_DUPLICATE_ONLY, "no_post_run_rows", 0
        return STATUS_FAIL, "no_post_run_rows", 1

    return STATUS_FAIL, _db_failure_reason(watchdog_payload), _normalize_exit_code(watchdog_exit)


def compose_payload(
    watchdog_payload: dict[str, Any],
    *,
    collector_exit_code: int,
    task_name: str,
    mode: str,
    artifact_path: Path,
    watchdog_artifact_path: Path,
    composed_at: str | None = None,
) -> dict[str, Any]:
    collector_exit = _normalize_exit_code(collector_exit_code)
    collector_status = STATUS_PASS if collector_exit == 0 else STATUS_FAIL
    watchdog_exit = _watchdog_exit_code(watchdog_payload)
    db_status, db_reason, db_exit = derive_db_progress(watchdog_payload, mode=mode)

    if collector_exit != 0:
        heartbeat_status = STATUS_FAIL
        pre_monitor_exit_code = collector_exit
    elif db_status == STATUS_FAIL:
        heartbeat_status = STATUS_FAIL
        pre_monitor_exit_code = db_exit
    elif db_status == STATUS_WARN_DUPLICATE_ONLY:
        heartbeat_status = STATUS_WARN_DUPLICATE_ONLY
        pre_monitor_exit_code = 0
    else:
        heartbeat_status = STATUS_PASS
        pre_monitor_exit_code = 0

    return {
        "kind": "harmonic_keepalive_composite",
        "schema_version": 1,
        "task_name": task_name,
        "mode": mode,
        "artifact": artifact_path.name,
        "watchdog_artifact": watchdog_artifact_path.name,
        "composed_at": composed_at or _utc_now_iso(),
        "collector_exit_code": collector_exit,
        "collector_exit_status": collector_status,
        "watchdog_exit_code": watchdog_exit,
        "db_progress_status": db_status,
        "db_progress_reason": db_reason,
        "heartbeat_status": heartbeat_status,
        "pre_monitor_exit_code": pre_monitor_exit_code,
        "watchdog": watchdog_payload,
    }


def finalize_payload(
    composite_payload: dict[str, Any],
    *,
    monitor_exit_code: int,
    completed_at: str | None = None,
) -> dict[str, Any]:
    pre_monitor_exit_code = composite_payload.get("pre_monitor_exit_code")
    if not isinstance(pre_monitor_exit_code, int):
        raise ValueError("composite artifact missing integer pre_monitor_exit_code")

    heartbeat_status = composite_payload.get("heartbeat_status")
    if heartbeat_status not in {STATUS_PASS, STATUS_WARN_DUPLICATE_ONLY, STATUS_FAIL}:
        raise ValueError("composite artifact missing heartbeat_status")

    monitor_exit = _normalize_exit_code(monitor_exit_code)
    monitor_status = STATUS_PASS if monitor_exit == 0 else STATUS_FAIL
    if monitor_exit != 0:
        overall_status = STATUS_FAIL
        exit_code = pre_monitor_exit_code if pre_monitor_exit_code != 0 else monitor_exit
    else:
        overall_status = heartbeat_status
        exit_code = pre_monitor_exit_code

    finalized = dict(composite_payload)
    finalized.update(
        {
            "monitor_delivery_status": monitor_status,
            "monitor_exit_code": monitor_exit,
            "overall_status": overall_status,
            "exit_code": exit_code,
            "completed_at": completed_at or _utc_now_iso(),
        }
    )
    return finalized


def _read_json(path: Path, label: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON root must be an object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _compose(args: argparse.Namespace) -> int:
    watchdog_path = Path(args.watchdog_json)
    artifact_path = Path(args.artifact)
    watchdog_payload = _read_json(watchdog_path, "watchdog")
    composite = compose_payload(
        watchdog_payload,
        collector_exit_code=args.collector_exit,
        task_name=args.task_name,
        mode=args.mode,
        artifact_path=artifact_path,
        watchdog_artifact_path=watchdog_path,
    )
    _write_json(artifact_path, composite)
    return int(composite["pre_monitor_exit_code"])


def _finalize(args: argparse.Namespace) -> int:
    artifact_path = Path(args.artifact)
    composite = _read_json(artifact_path, "composite")
    finalized = finalize_payload(composite, monitor_exit_code=args.monitor_exit)
    _write_json(artifact_path, finalized)
    return int(finalized["exit_code"])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compose and finalize Harmonic keepalive verdict artifacts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compose = subparsers.add_parser("compose")
    compose.add_argument("--watchdog-json", required=True)
    compose.add_argument("--artifact", required=True)
    compose.add_argument("--task-name", required=True)
    compose.add_argument("--collector-exit", required=True, type=int)
    compose.add_argument("--mode", choices=VALID_MODES, required=True)
    compose.set_defaults(func=_compose)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--artifact", required=True)
    finalize.add_argument("--monitor-exit", required=True, type=int)
    finalize.set_defaults(func=_finalize)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"keepalive verdict error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

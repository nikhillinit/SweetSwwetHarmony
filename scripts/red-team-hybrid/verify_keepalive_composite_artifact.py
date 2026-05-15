from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


KIND_COMPOSITE = "harmonic_keepalive_composite"
STATUS_PASS = "PASS"
STATUS_WARN_DUPLICATE_ONLY = "WARN_DUPLICATE_ONLY"


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _safe_task_name(task_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", task_name)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("artifact JSON root must be an object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _artifact_candidates(
    artifact_dir: Path,
    *,
    task_name: str,
    artifact_date: str | None,
) -> list[Path]:
    safe_task_name = _safe_task_name(task_name)
    if artifact_date:
        return [artifact_dir / f"{artifact_date}-{safe_task_name}.json"]

    candidates = [
        path
        for path in artifact_dir.glob(f"*-{safe_task_name}.json")
        if not path.name.endswith(".watchdog.json")
    ]
    return sorted(candidates, key=lambda path: (path.name, path.stat().st_mtime), reverse=True)


def _operational_failures(watchdog_payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = watchdog_payload.get("collectors")
    if not isinstance(records, list):
        return []

    failures: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("category") != "operational":
            continue
        if record.get("status") in {"STALE", "MISSING"}:
            failures.append(record)
    return failures


def _validate_warn_duplicate_only(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("db_progress_status") != STATUS_WARN_DUPLICATE_ONLY:
        errors.append("WARN_DUPLICATE_ONLY artifact must have db_progress_status=WARN_DUPLICATE_ONLY")
    if payload.get("db_progress_reason") != "no_post_run_rows":
        errors.append("WARN_DUPLICATE_ONLY artifact must have db_progress_reason=no_post_run_rows")

    watchdog_payload = payload.get("watchdog")
    if not isinstance(watchdog_payload, dict):
        errors.append("WARN_DUPLICATE_ONLY artifact must include nested watchdog payload")
        return errors

    failures = _operational_failures(watchdog_payload)
    if not failures:
        errors.append("WARN_DUPLICATE_ONLY artifact must include operational watchdog failures")
        return errors

    non_duplicate_failures = [
        str(record.get("source_api") or "unknown")
        for record in failures
        if record.get("stale_reason") != "no_post_run_rows"
    ]
    if non_duplicate_failures:
        joined = ", ".join(sorted(non_duplicate_failures))
        errors.append(f"WARN_DUPLICATE_ONLY has non-duplicate operational failures: {joined}")
    return errors


def validate_artifact(
    artifact_path: Path,
    payload: dict[str, Any],
    *,
    task_name: str,
    mode: str,
    allowed_statuses: set[str],
    require_finalized: bool,
) -> list[str]:
    errors: list[str] = []

    if payload.get("kind") != KIND_COMPOSITE:
        errors.append("artifact kind is not harmonic_keepalive_composite")
    if payload.get("task_name") != task_name:
        errors.append(f"artifact task_name is not {task_name}")
    if payload.get("mode") != mode:
        errors.append(f"artifact mode is not {mode}")

    if require_finalized:
        for field in ("monitor_delivery_status", "overall_status", "exit_code", "completed_at"):
            if field not in payload:
                errors.append(f"finalized artifact missing {field}")

    if payload.get("monitor_delivery_status") != STATUS_PASS:
        errors.append("monitor_delivery_status is not PASS")

    overall_status = str(payload.get("overall_status") or "")
    if overall_status not in allowed_statuses:
        allowed = ", ".join(sorted(allowed_statuses))
        errors.append(f"overall_status is not one of: {allowed}")

    if payload.get("exit_code") != 0:
        errors.append("exit_code is not 0")

    if overall_status == STATUS_PASS and payload.get("db_progress_status") != STATUS_PASS:
        errors.append("PASS artifact must have db_progress_status=PASS")
    if overall_status == STATUS_WARN_DUPLICATE_ONLY:
        errors.extend(_validate_warn_duplicate_only(payload))

    watchdog_artifact = payload.get("watchdog_artifact")
    if not isinstance(watchdog_artifact, str) or not watchdog_artifact:
        errors.append("artifact missing watchdog_artifact name")
    else:
        watchdog_path = artifact_path.parent / watchdog_artifact
        if not watchdog_path.exists():
            errors.append(f"companion watchdog artifact missing: {watchdog_path}")

    return errors


def verify(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    artifact_dir = Path(args.artifact_dir)
    candidates = _artifact_candidates(
        artifact_dir,
        task_name=args.task_name,
        artifact_date=args.date,
    )
    artifact_path = next((path for path in candidates if path.exists()), candidates[0] if candidates else None)
    allowed_statuses = {status.strip() for status in args.allow_status.split(",") if status.strip()}

    report: dict[str, Any] = {
        "checked_at": _utc_now_iso(),
        "status": "FAIL",
        "task_name": args.task_name,
        "mode": args.mode,
        "artifact_dir": str(artifact_dir),
        "artifact": str(artifact_path) if artifact_path else None,
        "errors": [],
    }

    if not artifact_path or not artifact_path.exists():
        report["errors"] = ["expected keepalive artifact not found"]
        return 1, report

    try:
        payload = _read_json(artifact_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        report["errors"] = [f"could not read artifact: {exc}"]
        return 1, report

    errors = validate_artifact(
        artifact_path,
        payload,
        task_name=args.task_name,
        mode=args.mode,
        allowed_statuses=allowed_statuses,
        require_finalized=not args.allow_unfinalized,
    )
    report["artifact"] = str(artifact_path)
    report["overall_status"] = payload.get("overall_status")
    report["db_progress_status"] = payload.get("db_progress_status")
    report["db_progress_reason"] = payload.get("db_progress_reason")
    report["monitor_delivery_status"] = payload.get("monitor_delivery_status")
    report["exit_code"] = payload.get("exit_code")
    report["errors"] = errors
    if errors:
        return 1, report

    report["status"] = "PASS"
    return 0, report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the first post-registration HarmonicKeepAlive composite artifact."
    )
    parser.add_argument("--artifact-dir", default="artifacts/keepalive")
    parser.add_argument("--task-name", default="HarmonicKeepAlive")
    parser.add_argument("--date", help="UTC artifact date prefix to verify, YYYY-MM-DD.")
    parser.add_argument("--mode", default="daily_heartbeat")
    parser.add_argument(
        "--allow-status",
        default="PASS,WARN_DUPLICATE_ONLY",
        help="Comma-separated final overall_status values accepted as verified.",
    )
    parser.add_argument("--allow-unfinalized", action="store_true")
    parser.add_argument("--report", help="Optional JSON report path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    exit_code, report = verify(args)
    if args.report:
        _write_json(Path(args.report), report)

    print(json.dumps(report, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_PING_URL_ENV = "HARMONIC_KEEPALIVE_PING_URL"
SOURCE_OF_RECORD = "signals.created_at"
RAW_WATCHDOG_COMPAT_MODE = "raw_watchdog_compat"


def _exit_status_from_payload(payload: dict[str, Any]) -> int:
    raw_exit_code = payload.get("pre_monitor_exit_code")
    if isinstance(raw_exit_code, int) and 0 <= raw_exit_code <= 255:
        return raw_exit_code

    status = str(payload.get("heartbeat_status") or "").upper()
    return 0 if status in {"PASS", "WARN_DUPLICATE_ONLY"} else 1


def _normalize_artifact_payload(artifact_payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(artifact_payload.get("watchdog"), dict):
        return artifact_payload

    if isinstance(artifact_payload.get("collectors"), list):
        raw_exit_code = artifact_payload.get("exit_code")
        pre_monitor_exit_code = raw_exit_code if isinstance(raw_exit_code, int) and 0 <= raw_exit_code <= 255 else 1
        raw_status = str(artifact_payload.get("status") or "").upper()
        heartbeat_status = "PASS" if pre_monitor_exit_code == 0 and raw_status in {"", "OK", "PASS"} else "FAIL"
        return {
            "kind": "harmonic_keepalive_raw_watchdog_compat",
            "mode": RAW_WATCHDOG_COMPAT_MODE,
            "collector_exit_code": None,
            "collector_exit_status": None,
            "db_progress_status": heartbeat_status,
            "db_progress_reason": "raw_watchdog_artifact",
            "heartbeat_status": heartbeat_status,
            "pre_monitor_exit_code": pre_monitor_exit_code,
            "watchdog": artifact_payload,
        }

    raise ValueError("artifact JSON must be a composite payload or raw watchdog payload")


def ping_url_for_exit_status(ping_url: str, exit_status: int) -> str:
    parts = urllib.parse.urlsplit(ping_url)
    if not parts.scheme or not parts.netloc:
        raise ValueError("ping URL must be absolute")

    path = parts.path.rstrip("/") + f"/{exit_status}"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def build_monitor_payload(
    artifact_payload: dict[str, Any],
    *,
    task_name: str,
    artifact_path: Path,
) -> dict[str, Any]:
    normalized_payload = _normalize_artifact_payload(artifact_payload)
    watchdog_payload = normalized_payload.get("watchdog")
    if not isinstance(watchdog_payload, dict):
        raise ValueError("artifact JSON must contain a watchdog object")

    collector_records = watchdog_payload.get("collectors")
    if not isinstance(collector_records, list):
        raise ValueError("composite watchdog JSON must contain a collectors list")

    sources: dict[str, dict[str, Any]] = {}
    for record in collector_records:
        if not isinstance(record, dict):
            raise ValueError("watchdog collectors entries must be objects")

        source_api = record.get("source_api")
        if not isinstance(source_api, str) or not source_api:
            raise ValueError("watchdog collector entry is missing source_api")

        source = {
            "category": record.get("category"),
            "last_created": record.get("last_created"),
            "age_hours": record.get("age_hours"),
            "status": record.get("status"),
        }
        for optional_field in ("required_after", "stale_reason"):
            if optional_field in record:
                source[optional_field] = record.get(optional_field)
        sources[source_api] = source

    if not sources:
        raise ValueError("composite watchdog JSON contains no source proof fields")

    return {
        "kind": "harmonic_keepalive_liveness",
        "task_name": task_name,
        "source_of_record": SOURCE_OF_RECORD,
        "artifact": artifact_path.name,
        "keepalive": {
            "mode": normalized_payload.get("mode"),
            "collector_exit_code": normalized_payload.get("collector_exit_code"),
            "collector_exit_status": normalized_payload.get("collector_exit_status"),
            "db_progress_status": normalized_payload.get("db_progress_status"),
            "db_progress_reason": normalized_payload.get("db_progress_reason"),
            "heartbeat_status": normalized_payload.get("heartbeat_status"),
            "pre_monitor_exit_code": normalized_payload.get("pre_monitor_exit_code"),
        },
        "watchdog": {
            "checked_at": watchdog_payload.get("checked_at"),
            "threshold_hours": watchdog_payload.get("threshold_hours"),
            "min_created_at": watchdog_payload.get("min_created_at"),
            "status": watchdog_payload.get("status"),
            "exit_code": watchdog_payload.get("exit_code"),
            "failures": watchdog_payload.get("failures", []),
            "sources": sources,
        },
        "post_run_db_proof_fields": [
            "watchdog.sources.<source_api>.last_created",
            "watchdog.sources.<source_api>.required_after",
            "watchdog.sources.<source_api>.stale_reason",
            "watchdog.sources.<source_api>.status",
            "watchdog.threshold_hours",
            "watchdog.min_created_at",
        ],
        "composite_verdict_fields": [
            "keepalive.mode",
            "keepalive.collector_exit_code",
            "keepalive.collector_exit_status",
            "keepalive.db_progress_status",
            "keepalive.db_progress_reason",
            "keepalive.heartbeat_status",
            "keepalive.pre_monitor_exit_code",
        ],
    }


def _read_artifact_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("artifact JSON root must be an object")
    return payload


def _post_payload(ping_url: str, exit_status: int, payload: dict[str, Any], timeout_seconds: float) -> int:
    target_url = ping_url_for_exit_status(ping_url, exit_status)
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        target_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "HarmonicKeepAlive/1.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return int(response.status)


def _resolve_ping_url(explicit_ping_url: str | None, ping_url_env: str) -> str | None:
    if explicit_ping_url:
        return explicit_ping_url
    return os.environ.get(ping_url_env)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Post Harmonic keepalive composite or raw watchdog JSON to a "
            "Healthchecks.io-compatible endpoint with runner and DB freshness proof fields."
        )
    )
    parser.add_argument(
        "--artifact-json",
        help="Path to the pre-monitor composite keepalive JSON artifact, or a legacy raw watchdog JSON artifact.",
    )
    parser.add_argument(
        "--watchdog-json",
        help="Deprecated alias for --artifact-json kept for legacy raw watchdog runners.",
    )
    parser.add_argument("--task-name", required=True, help="Scheduled task name producing the artifact.")
    parser.add_argument(
        "--ping-url-env",
        default=DEFAULT_PING_URL_ENV,
        help=f"Environment variable containing the ping URL. Default: {DEFAULT_PING_URL_ENV}.",
    )
    parser.add_argument("--ping-url", help="Explicit ping URL. Prefer --ping-url-env for production runners.")
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--dry-run", action="store_true", help="Print the payload without sending a ping.")
    args = parser.parse_args(argv)
    if args.artifact_json and args.watchdog_json and args.artifact_json != args.watchdog_json:
        parser.error("--artifact-json and --watchdog-json must refer to the same file when both are provided")
    args.artifact_json = args.artifact_json or args.watchdog_json
    if not args.artifact_json:
        parser.error("--artifact-json is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    artifact_path = Path(args.artifact_json)

    try:
        artifact_payload = _read_artifact_json(artifact_path)
        normalized_payload = _normalize_artifact_payload(artifact_payload)
        payload = build_monitor_payload(
            normalized_payload,
            task_name=args.task_name,
            artifact_path=artifact_path,
        )
        exit_status = _exit_status_from_payload(normalized_payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"keepalive monitor payload error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        json.dump(
            {
                "ping_exit_status": exit_status,
                "payload": payload,
            },
            sys.stdout,
            indent=2,
            sort_keys=True,
        )
        sys.stdout.write("\n")
        return 0

    ping_url = _resolve_ping_url(args.ping_url, args.ping_url_env)
    if not ping_url:
        print(f"keepalive monitor ping URL missing: set {args.ping_url_env}", file=sys.stderr)
        return 2

    try:
        http_status = _post_payload(ping_url, exit_status, payload, args.timeout_seconds)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        print(f"keepalive monitor ping failed: {exc}", file=sys.stderr)
        return 3

    print(f"keepalive monitor ping sent: exit_status={exit_status} http_status={http_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

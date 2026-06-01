from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..plan_contract import canonical_json_bytes

BYPASS_LIFECYCLE_SUBSYSTEM = "bypass_lifecycle"
BYPASS_LIFECYCLE_OWNER_TASK = "ledgered-bypass-artifacts"

_BYPASS_ARTIFACT_KEYS = {
    "bypass",
    "bypass_record",
    "bypass_records",
    "bypass_lifecycle",
}
_BYPASS_ARTIFACT_NAMES = {
    "bypass_record.json",
    "bypass_records.json",
    "bypass_lifecycle.json",
}
_REQUIRED_FIELDS = (
    "bypassId",
    "kind",
    "scope",
    "policyRef",
    "reason",
    "severity",
    "affectedResources",
    "operator",
    "authorizer",
    "createdAt",
    "expiresAt",
    "deadline",
    "expectedRemediation",
    "actualRemediationRunId",
    "status",
    "planHash",
    "evidence",
)
_ALLOWED_SCOPES = {"run", "resource", "task", "policy"}
_ALLOWED_STATUSES = {"active", "remediated", "expired", "revoked"}
_ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}


def empty_bypass_lifecycle_subsystem(*, enabled: bool) -> dict[str, Any]:
    return {
        "subsystem": BYPASS_LIFECYCLE_SUBSYSTEM,
        "ownerTask": BYPASS_LIFECYCLE_OWNER_TASK,
        "enabled": enabled,
        "runsChecked": 0,
        "resourcesChecked": 0,
        "activeRecords": 0,
        "expiredRecords": 0,
        "remediatedRecords": 0,
        "revokedRecords": 0,
        "findings": [],
    }


def audit_bypass_lifecycle_subsystem(rows: list[dict[str, Any]]) -> dict[str, Any]:
    state = empty_bypass_lifecycle_subsystem(enabled=True)
    findings: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for row, run_dir in _unique_run_dirs(rows):
        bypass_paths = _bypass_artifact_paths(run_dir)
        if not bypass_paths:
            continue

        state["runsChecked"] += 1
        run_id = str(row.get("runId") or run_dir.name or "unknown")
        task_plan = _read_json_object(run_dir / "task_plan.json") or {}
        expected_plan_hash = _string_or_none(task_plan.get("planHash"))

        for path in bypass_paths:
            payload, read_error = _read_json_payload(path)
            if read_error is not None:
                state["resourcesChecked"] += 1
                findings.append(
                    bypass_lifecycle_finding(
                        "malformed_json",
                        run_id=run_id,
                        resource_id=f"{BYPASS_LIFECYCLE_SUBSYSTEM}.artifact",
                        path=path,
                        detail=read_error,
                        observed="malformed",
                        expected="valid_bypass_record",
                    )
                )
                continue

            records, payload_error = _records_from_payload(payload)
            if payload_error is not None:
                state["resourcesChecked"] += 1
                findings.append(
                    bypass_lifecycle_finding(
                        "malformed_json",
                        run_id=run_id,
                        resource_id=f"{BYPASS_LIFECYCLE_SUBSYSTEM}.artifact",
                        path=path,
                        detail=payload_error,
                        observed=_type_name(payload),
                        expected="bypass_record_or_records_array",
                    )
                )
                continue

            for index, record in enumerate(records):
                state["resourcesChecked"] += 1
                if isinstance(record, dict):
                    _count_record_status(state, record.get("status"))
                findings.extend(
                    _record_findings(
                        record,
                        run_id=run_id,
                        path=path,
                        index=index,
                        expected_plan_hash=expected_plan_hash,
                        now=now,
                    )
                )

    state["findings"] = findings
    return state


def _unique_run_dirs(
    rows: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], Path]]:
    runs: list[tuple[dict[str, Any], Path]] = []
    seen: set[str] = set()
    for row in rows:
        run_dir_value = row.get("runDir")
        if not run_dir_value:
            continue
        run_dir = Path(str(run_dir_value))
        run_dir_key = str(run_dir)
        if run_dir_key in seen:
            continue
        seen.add(run_dir_key)
        runs.append((row, run_dir))
    return runs


def _bypass_artifact_paths(run_dir: Path) -> list[Path]:
    ledger = _read_json_object(run_dir / "ledger.json")
    if not isinstance(ledger, dict):
        return []
    artifacts = ledger.get("artifacts")
    if not isinstance(artifacts, dict):
        return []

    paths: list[Path] = []
    seen: set[str] = set()
    for key, relative_path in artifacts.items():
        if not isinstance(relative_path, str):
            continue
        artifact_path = run_dir / relative_path
        if not _is_bypass_artifact(str(key), artifact_path):
            continue
        artifact_key = str(artifact_path)
        if artifact_key in seen:
            continue
        seen.add(artifact_key)
        paths.append(artifact_path)
    return paths


def _is_bypass_artifact(key: str, path: Path) -> bool:
    normalized_key = key.strip().lower().replace("-", "_")
    return normalized_key in _BYPASS_ARTIFACT_KEYS or path.name in _BYPASS_ARTIFACT_NAMES


def _read_json_object(path: Path) -> dict[str, Any] | None:
    payload, error = _read_json_payload(path)
    if error is not None or not isinstance(payload, dict):
        return None
    return payload


def _read_json_payload(path: Path) -> tuple[Any, str | None]:
    if not path.exists() or not path.is_file():
        return None, f"missing bypass artifact: {path.name}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)


def _records_from_payload(payload: Any) -> tuple[list[Any], str | None]:
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        if _looks_like_bypass_record(payload):
            return [payload], None
        for key in ("records", "bypasses", "bypassRecords"):
            records = payload.get(key)
            if records is not None:
                if isinstance(records, list):
                    return records, None
                return [], f"{key} must be a list"
    return [], "bypass artifact must be a record object or records array"


def _looks_like_bypass_record(payload: dict[str, Any]) -> bool:
    return "bypassId" in payload or "planHash" in payload or "policyRef" in payload


def _count_record_status(state: dict[str, Any], status_value: Any) -> None:
    status = _string_or_none(status_value)
    if status == "active":
        state["activeRecords"] += 1
    elif status == "expired":
        state["expiredRecords"] += 1
    elif status == "remediated":
        state["remediatedRecords"] += 1
    elif status == "revoked":
        state["revokedRecords"] += 1


def _record_findings(
    record: Any,
    *,
    run_id: str,
    path: Path,
    index: int,
    expected_plan_hash: str | None,
    now: datetime,
) -> list[dict[str, Any]]:
    if not isinstance(record, dict):
        return [
            bypass_lifecycle_finding(
                "malformed_json",
                run_id=run_id,
                resource_id=f"{BYPASS_LIFECYCLE_SUBSYSTEM}.record_{index}",
                path=path,
                detail="bypass record must be a JSON object",
                observed=_type_name(record),
                expected="object",
            )
        ]

    bypass_id = str(record.get("bypassId") or f"record_{index}")
    findings: list[dict[str, Any]] = []
    missing = [field for field in _REQUIRED_FIELDS if field not in record]
    if missing:
        findings.append(
            bypass_lifecycle_finding(
                "malformed_json",
                run_id=run_id,
                resource_id=f"{BYPASS_LIFECYCLE_SUBSYSTEM}.{bypass_id}.required_fields",
                path=path,
                detail=f"bypass record missing required fields: {', '.join(missing)}",
                observed=missing,
                expected=list(_REQUIRED_FIELDS),
            )
        )

    scope = _string_or_none(record.get("scope"))
    if scope not in _ALLOWED_SCOPES:
        findings.append(
            bypass_lifecycle_finding(
                "malformed_json",
                run_id=run_id,
                resource_id=f"{BYPASS_LIFECYCLE_SUBSYSTEM}.{bypass_id}.scope",
                path=path,
                detail="bypass scope must be run, resource, task, or policy",
                observed=scope,
                expected=sorted(_ALLOWED_SCOPES),
            )
        )

    severity = _string_or_none(record.get("severity"))
    if severity not in _ALLOWED_SEVERITIES:
        findings.append(
            bypass_lifecycle_finding(
                "malformed_json",
                run_id=run_id,
                resource_id=f"{BYPASS_LIFECYCLE_SUBSYSTEM}.{bypass_id}.severity",
                path=path,
                detail="bypass severity must be low, medium, high, or critical",
                observed=severity,
                expected=sorted(_ALLOWED_SEVERITIES),
            )
        )

    status = _string_or_none(record.get("status"))
    if status not in _ALLOWED_STATUSES:
        findings.append(
            bypass_lifecycle_finding(
                "malformed_json",
                run_id=run_id,
                resource_id=f"{BYPASS_LIFECYCLE_SUBSYSTEM}.{bypass_id}.status",
                path=path,
                detail="bypass status must be active, remediated, expired, or revoked",
                observed=status,
                expected=sorted(_ALLOWED_STATUSES),
            )
        )

    affected_resources = record.get("affectedResources")
    if not _valid_affected_resources(affected_resources):
        findings.append(
            bypass_lifecycle_finding(
                "malformed_json",
                run_id=run_id,
                resource_id=f"{BYPASS_LIFECYCLE_SUBSYSTEM}.{bypass_id}.affectedResources",
                path=path,
                detail=(
                    "bypass affectedResources must be a non-empty list of "
                    "objects with type and id"
                ),
                observed=affected_resources,
                expected="non_empty_list_of_resource_objects",
            )
        )

    plan_hash = _string_or_none(record.get("planHash"))
    if expected_plan_hash is not None and plan_hash != expected_plan_hash:
        findings.append(
            bypass_lifecycle_finding(
                "plan_hash_mismatch",
                run_id=run_id,
                resource_id=f"{BYPASS_LIFECYCLE_SUBSYSTEM}.{bypass_id}.planHash",
                path=path,
                detail="bypass planHash does not match task_plan.json planHash",
                observed=plan_hash,
                expected=expected_plan_hash,
            )
        )

    created_at = _parse_datetime(record.get("createdAt"))
    expires_at = _parse_datetime(record.get("expiresAt"))
    deadline = _parse_datetime(record.get("deadline"))
    findings.extend(
        _datetime_findings(
            run_id=run_id,
            bypass_id=bypass_id,
            path=path,
            values={
                "createdAt": (record.get("createdAt"), created_at),
                "expiresAt": (record.get("expiresAt"), expires_at),
                "deadline": (record.get("deadline"), deadline),
            },
        )
    )

    remediation_run_id = _string_or_none(record.get("actualRemediationRunId"))
    if status == "remediated" and not remediation_run_id:
        findings.append(
            bypass_lifecycle_finding(
                "malformed_json",
                run_id=run_id,
                resource_id=(
                    f"{BYPASS_LIFECYCLE_SUBSYSTEM}.{bypass_id}."
                    "actualRemediationRunId"
                ),
                path=path,
                detail="remediated bypass must declare actualRemediationRunId",
                observed=remediation_run_id,
                expected="remediation_run_id",
            )
        )

    if _is_overdue_without_remediation(
        status=status,
        remediation_run_id=remediation_run_id,
        expires_at=expires_at,
        deadline=deadline,
        now=now,
    ):
        findings.append(
            bypass_lifecycle_finding(
                "bypass_overdue",
                run_id=run_id,
                resource_id=f"{BYPASS_LIFECYCLE_SUBSYSTEM}.{bypass_id}.deadline",
                path=path,
                detail=(
                    "bypass record is active or expired past deadline/expiresAt "
                    "without remediation"
                ),
                observed={
                    "status": status,
                    "expiresAt": record.get("expiresAt"),
                    "deadline": record.get("deadline"),
                    "actualRemediationRunId": remediation_run_id,
                },
                expected={"actualRemediationRunId": "present_before_deadline"},
            )
        )

    return findings


def _datetime_findings(
    *,
    run_id: str,
    bypass_id: str,
    path: Path,
    values: dict[str, tuple[Any, datetime | None]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for field, (raw_value, parsed_value) in values.items():
        if parsed_value is not None:
            continue
        findings.append(
            bypass_lifecycle_finding(
                "malformed_json",
                run_id=run_id,
                resource_id=f"{BYPASS_LIFECYCLE_SUBSYSTEM}.{bypass_id}.{field}",
                path=path,
                detail=(
                    f"bypass {field} must be an ISO 8601 date-time string "
                    "with timezone"
                ),
                observed=raw_value,
                expected="timezone_aware_date-time",
            )
        )
    return findings


def _is_overdue_without_remediation(
    *,
    status: str | None,
    remediation_run_id: str | None,
    expires_at: datetime | None,
    deadline: datetime | None,
    now: datetime,
) -> bool:
    if remediation_run_id:
        return False
    if status == "expired":
        return True
    if status != "active":
        return False
    return any(value is not None and value <= now for value in (expires_at, deadline))


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _valid_affected_resources(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if not _string_or_none(item.get("type")) or not _string_or_none(item.get("id")):
            return False
    return True


def bypass_lifecycle_finding(
    code: str,
    *,
    run_id: str,
    resource_id: str,
    path: Path,
    detail: str,
    observed: object | None = None,
    expected: object | None = None,
    severity: str = "critical",
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "subsystem": BYPASS_LIFECYCLE_SUBSYSTEM,
        "runId": run_id,
        "resourceId": resource_id,
        "path": str(path),
        "evidencePath": str(path),
        "detail": detail,
        "remediationHint": _bypass_lifecycle_remediation_hint(code),
    }
    if observed is not None:
        finding["observedDigest"] = _value_digest(observed)
    if expected is not None:
        finding["expectedDigest"] = _value_digest(expected)
    return finding


def _bypass_lifecycle_remediation_hint(code: str) -> str:
    hints = {
        "bypass_overdue": (
            "Close the bypass with a remediation Hermes run or revoke it before "
            "reusing the affected plan."
        ),
        "malformed_json": (
            "Regenerate the bypass artifact with the structured bypass record schema."
        ),
        "plan_hash_mismatch": (
            "Bind the bypass record to the same task_plan.json planHash as the "
            "ledgered run."
        ),
    }
    return hints.get(code, "Inspect the ledgered bypass lifecycle artifact.")


def _value_digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _type_name(value: object) -> str:
    return type(value).__name__

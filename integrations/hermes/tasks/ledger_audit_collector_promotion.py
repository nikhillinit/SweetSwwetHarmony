from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..plan_contract import CURRENT_CONTRACT_VERSION, canonical_json_bytes

COLLECTOR_PROMOTION_SUBSYSTEM = "collector_promotion"
COLLECTOR_PROMOTION_OWNER_TASK = "collector-promote"
COLLECTOR_PROMOTION_ARTIFACT_VERSION = 1

_BASE_REQUIRED_ARTIFACTS = {
    "task_plan": "task_plan.json",
    "run_record": "run_record.json",
}
_PROMOTION_REQUIRED_ARTIFACTS = {
    **_BASE_REQUIRED_ARTIFACTS,
    "collector_promotion": "collector_promotion.json",
}
_DRY_RUN_DRIFT_REQUIRED_ARTIFACTS = {
    **_BASE_REQUIRED_ARTIFACTS,
    "dry_run_drift": "dry_run_drift.json",
}


def empty_collector_promotion_subsystem(*, enabled: bool) -> dict[str, Any]:
    return {
        "subsystem": COLLECTOR_PROMOTION_SUBSYSTEM,
        "ownerTask": COLLECTOR_PROMOTION_OWNER_TASK,
        "enabled": enabled,
        "runsChecked": 0,
        "resourcesChecked": 0,
        "findings": [],
    }


def audit_collector_promotion_subsystem(rows: list[dict[str, Any]]) -> dict[str, Any]:
    state = empty_collector_promotion_subsystem(enabled=True)
    findings: list[dict[str, Any]] = []
    for row, run_dir in _unique_run_dirs(rows):
        if _task_name(row, run_dir) != COLLECTOR_PROMOTION_OWNER_TASK:
            continue

        run_id = str(row.get("runId") or run_dir.name or "unknown")
        mode = _run_mode(row, run_dir)
        required_artifacts = _required_artifacts_for(run_dir)
        state["runsChecked"] += 1
        state["resourcesChecked"] += len(required_artifacts)

        artifact_findings, artifacts = _artifact_findings(
            run_id=run_id,
            run_dir=run_dir,
            required_artifacts=required_artifacts,
        )
        findings.extend(artifact_findings)
        if artifact_findings:
            continue

        findings.extend(
            _contract_findings_for_run(
                run_id=run_id,
                run_dir=run_dir,
                mode=mode,
                artifacts=artifacts,
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


def _task_name(row: dict[str, Any], run_dir: Path) -> str | None:
    row_task = _string_or_none(row.get("task"))
    if row_task:
        return row_task
    plan = _read_plan_for_identity(run_dir)
    if not isinstance(plan, dict):
        return None
    return _string_or_none(plan.get("task"))


def _run_mode(row: dict[str, Any], run_dir: Path) -> str:
    row_mode = _string_or_none(row.get("mode"))
    if row_mode:
        return row_mode
    plan = _read_plan_for_identity(run_dir)
    if not isinstance(plan, dict):
        return "unknown"
    return _string_or_none(plan.get("mode")) or "unknown"


def _read_plan_for_identity(run_dir: Path) -> dict[str, Any] | None:
    plan_path = run_dir / "task_plan.json"
    if not plan_path.exists():
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return plan if isinstance(plan, dict) else None


def _required_artifacts_for(run_dir: Path) -> dict[str, str]:
    if (run_dir / "dry_run_drift.json").exists():
        return _DRY_RUN_DRIFT_REQUIRED_ARTIFACTS
    return _PROMOTION_REQUIRED_ARTIFACTS


def _artifact_findings(
    *,
    run_id: str,
    run_dir: Path,
    required_artifacts: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    artifacts: dict[str, dict[str, Any]] = {}
    for resource_name, relative_path in required_artifacts.items():
        path = run_dir / relative_path
        artifact_resource_id = _resource_id(resource_name)
        if not path.exists() or not path.is_file():
            findings.append(
                collector_promotion_finding(
                    "missing_required_artifact",
                    run_id=run_id,
                    resource_id=artifact_resource_id,
                    path=path,
                    detail=(
                        "required collector-promotion artifact is missing: "
                        f"{relative_path}"
                    ),
                    observed="missing",
                    expected="present",
                )
            )
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(
                collector_promotion_finding(
                    "malformed_json",
                    run_id=run_id,
                    resource_id=artifact_resource_id,
                    path=path,
                    detail=str(exc),
                    observed="malformed",
                    expected="valid_json_object",
                )
            )
            continue
        if not isinstance(payload, dict):
            findings.append(
                collector_promotion_finding(
                    "malformed_json",
                    run_id=run_id,
                    resource_id=artifact_resource_id,
                    path=path,
                    detail="collector-promotion artifact must be a JSON object",
                    observed=type(payload).__name__,
                    expected="object",
                )
            )
            continue
        artifacts[resource_name] = payload
    return findings, artifacts


def _contract_findings_for_run(
    *,
    run_id: str,
    run_dir: Path,
    mode: str,
    artifacts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    plan = artifacts["task_plan"]
    record = artifacts["run_record"]
    findings = _base_contract_findings(
        run_id=run_id,
        run_dir=run_dir,
        plan=plan,
        record=record,
    )
    if "dry_run_drift" in artifacts:
        findings.extend(
            _dry_run_drift_findings(
                run_id=run_id,
                run_dir=run_dir,
                mode=mode,
                plan=plan,
                drift=artifacts["dry_run_drift"],
            )
        )
        return findings

    findings.extend(
        _collector_promotion_contract_findings(
            run_id=run_id,
            run_dir=run_dir,
            mode=mode,
            plan=plan,
            promotion=artifacts["collector_promotion"],
        )
    )
    return findings


def _base_contract_findings(
    *,
    run_id: str,
    run_dir: Path,
    plan: dict[str, Any],
    record: dict[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    plan_contract = _int_or_none(plan.get("contractVersion"))
    record_contract = _int_or_none(record.get("contract_version"))
    if plan_contract != CURRENT_CONTRACT_VERSION:
        findings.append(
            collector_promotion_finding(
                "unsupported_contract_version",
                run_id=run_id,
                resource_id="collector_promotion.task_plan.contract_version",
                path=run_dir / "task_plan.json",
                detail="task_plan.json contractVersion is not supported",
                observed=plan_contract,
                expected=CURRENT_CONTRACT_VERSION,
            )
        )
    if record_contract != CURRENT_CONTRACT_VERSION:
        findings.append(
            collector_promotion_finding(
                "unsupported_contract_version",
                run_id=run_id,
                resource_id="collector_promotion.run_record.contract_version",
                path=run_dir / "run_record.json",
                detail="run_record.json contract_version is not supported",
                observed=record_contract,
                expected=CURRENT_CONTRACT_VERSION,
            )
        )
    findings.extend(
        _comparison_findings(
            run_id,
            (
                (
                    "collector_promotion.task_plan.task",
                    plan.get("task"),
                    COLLECTOR_PROMOTION_OWNER_TASK,
                    "task_plan.json task does not match ledger task",
                    run_dir / "task_plan.json",
                    "dry_run_binding_mismatch",
                ),
                (
                    "collector_promotion.run_record.task",
                    record.get("task"),
                    COLLECTOR_PROMOTION_OWNER_TASK,
                    "run_record.json task does not match ledger task",
                    run_dir / "run_record.json",
                    "dry_run_binding_mismatch",
                ),
            ),
        )
    )

    plan_hash = _string_or_none(plan.get("planHash"))
    record_plan_hash = _record_plan_hash(record)
    if record_plan_hash is not None and record_plan_hash != plan_hash:
        findings.append(
            collector_promotion_finding(
                "plan_hash_mismatch",
                run_id=run_id,
                resource_id="collector_promotion.run_record.plan_hash",
                path=run_dir / "run_record.json",
                detail=(
                    "run_record.json plan hash does not match task_plan.json "
                    "planHash"
                ),
                observed=record_plan_hash,
                expected=plan_hash,
            )
        )
    return findings


def _collector_promotion_contract_findings(
    *,
    run_id: str,
    run_dir: Path,
    mode: str,
    plan: dict[str, Any],
    promotion: dict[str, Any],
) -> list[dict[str, Any]]:
    path = run_dir / "collector_promotion.json"
    findings = _artifact_version_findings(
        run_id=run_id,
        resource_id="collector_promotion.collector_promotion.artifact_version",
        path=path,
        payload=promotion,
    )
    findings.extend(
        _collector_payload_binding_findings(
            run_id=run_id,
            path=path,
            mode=mode,
            plan=plan,
            payload=promotion,
        )
    )
    return findings


def _dry_run_drift_findings(
    *,
    run_id: str,
    run_dir: Path,
    mode: str,
    plan: dict[str, Any],
    drift: dict[str, Any],
) -> list[dict[str, Any]]:
    path = run_dir / "dry_run_drift.json"
    findings = _artifact_version_findings(
        run_id=run_id,
        resource_id="collector_promotion.dry_run_drift.artifact_version",
        path=path,
        payload=drift,
    )
    findings.extend(
        _comparison_findings(
            run_id,
            (
                (
                    "collector_promotion.dry_run_drift.task",
                    drift.get("task"),
                    COLLECTOR_PROMOTION_OWNER_TASK,
                    "dry_run_drift.json task does not identify collector-promote",
                    path,
                    "dry_run_binding_mismatch",
                ),
                (
                    "collector_promotion.dry_run_drift.mode",
                    drift.get("mode"),
                    "dry-run",
                    "dry_run_drift.json mode must be dry-run",
                    path,
                    "dry_run_binding_mismatch",
                ),
                (
                    "collector_promotion.dry_run_drift.drift_detected",
                    drift.get("driftDetected"),
                    True,
                    "dry_run_drift.json must declare detected drift",
                    path,
                    "dry_run_binding_mismatch",
                ),
            ),
        )
    )
    stale_preview = _dict_value(drift.get("stalePreview"))
    if not stale_preview:
        findings.append(
            collector_promotion_finding(
                "missing_required_artifact",
                run_id=run_id,
                resource_id="collector_promotion.dry_run_drift.stale_preview",
                path=path,
                detail="dry_run_drift.json must include stalePreview evidence",
                observed="missing",
                expected="present",
            )
        )
        return findings

    findings.extend(
        _collector_payload_binding_findings(
            run_id=run_id,
            path=path,
            mode=mode,
            plan=plan,
            payload=stale_preview,
        )
    )
    return findings


def _collector_payload_binding_findings(
    *,
    run_id: str,
    path: Path,
    mode: str,
    plan: dict[str, Any],
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    transition = _dict_value(plan.get("transition"))
    mutation = _dict_value(plan.get("mutation"))
    plan_database = _dict_value(plan.get("database"))
    audit_evidence = _dict_value(payload.get("auditEvidence"))
    expected_actual_status = (
        plan.get("result_target_state")
        if mode == "execute"
        else plan_database.get("status")
    )
    findings = _comparison_findings(
        run_id,
        (
            (
                "collector_promotion.task",
                payload.get("task"),
                COLLECTOR_PROMOTION_OWNER_TASK,
                "collector promotion output task does not match task plan",
                path,
                "dry_run_binding_mismatch",
            ),
            (
                "collector_promotion.mode",
                payload.get("mode"),
                mode,
                "collector promotion output mode does not match ledger mode",
                path,
                "dry_run_binding_mismatch",
            ),
            (
                "collector_promotion.collector",
                payload.get("collector"),
                plan.get("collector"),
                "collector promotion output collector does not match task plan",
                path,
                "dry_run_binding_mismatch",
            ),
            (
                "collector_promotion.result_id",
                payload.get("resultId"),
                plan.get("result_id"),
                "collector promotion output resultId does not match task plan",
                path,
                "dry_run_binding_mismatch",
            ),
            (
                "collector_promotion.target_state",
                payload.get("targetState"),
                plan.get("target_state"),
                "collector promotion output targetState does not match task plan",
                path,
                "dry_run_binding_mismatch",
            ),
            (
                "collector_promotion.result_target_state",
                payload.get("resultTargetState"),
                plan.get("result_target_state"),
                "collector promotion output resultTargetState does not match task plan",
                path,
                "dry_run_binding_mismatch",
            ),
            (
                "collector_promotion.requested_result_status",
                payload.get("requestedResultStatus"),
                plan.get("result_target_state"),
                "collector promotion requested result status does not match task plan",
                path,
                "dry_run_binding_mismatch",
            ),
            (
                "collector_promotion.actual_result_status",
                payload.get("actualResultStatus"),
                expected_actual_status,
                "collector promotion actual result status is not bound to plan state",
                path,
                "resource_digest_mismatch",
            ),
            (
                "collector_promotion.transition.ack_risk_token",
                _dict_value(payload.get("transition")).get("ack_risk_token"),
                transition.get("ack_risk_token"),
                "collector promotion transition ack token does not match task plan",
                path,
                "dry_run_binding_mismatch",
            ),
            (
                "collector_promotion.persistence.affected_db",
                _dict_value(payload.get("persistence")).get("affectedDb"),
                mutation.get("affected_db"),
                "collector promotion persistence DB binding does not match task plan",
                path,
                "dry_run_binding_mismatch",
            ),
            (
                "collector_promotion.persistence.affected_tables",
                _dict_value(payload.get("persistence")).get("affectedTables"),
                mutation.get("affected_tables"),
                "collector promotion affected tables do not match task plan",
                path,
                "dry_run_binding_mismatch",
            ),
            (
                "collector_promotion.audit_evidence.plan_hash",
                audit_evidence.get("planHash"),
                plan.get("planHash"),
                "collector promotion auditEvidence planHash does not match task plan",
                path,
                "plan_hash_mismatch",
            ),
            (
                "collector_promotion.audit_evidence.planned_result_updated_at",
                audit_evidence.get("plannedResultUpdatedAt"),
                plan.get("planned_result_updated_at"),
                "collector promotion auditEvidence updated_at does not match task plan",
                path,
                "resource_digest_mismatch",
            ),
            (
                "collector_promotion.audit_evidence.ack_risk_token",
                audit_evidence.get("ackRiskToken"),
                transition.get("ack_risk_token"),
                "collector promotion auditEvidence ack token does not match task plan",
                path,
                "dry_run_binding_mismatch",
            ),
        ),
    )
    findings.extend(_database_evidence_findings(run_id, path, plan, audit_evidence))
    findings.extend(_collector_state_findings(run_id, path, plan, audit_evidence))
    return findings


def _database_evidence_findings(
    run_id: str,
    path: Path,
    plan: dict[str, Any],
    audit_evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    plan_database = _dict_value(plan.get("database"))
    audit_database = _dict_value(audit_evidence.get("database"))
    comparisons = tuple(
        (
            f"collector_promotion.database.{field}",
            audit_database.get(field),
            plan_database.get(field),
            f"collector promotion database {field} does not match task plan",
            path,
            "resource_digest_mismatch",
        )
        for field in (
            "result_id",
            "status",
            "source_api",
            "query_collector",
            "updated_at",
        )
    )
    return _comparison_findings(run_id, comparisons)


def _collector_state_findings(
    run_id: str,
    path: Path,
    plan: dict[str, Any],
    audit_evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    plan_state = _dict_value(plan.get("collector_state"))
    audit_state = _dict_value(audit_evidence.get("collectorState"))
    comparisons = (
        (
            "collector_promotion.collector_state.collector_known",
            audit_state.get("collector_known"),
            plan_state.get("collector_known"),
            "collector promotion collector_known evidence does not match task plan",
            path,
            "resource_digest_mismatch",
        ),
        (
            "collector_promotion.collector_state.entry",
            audit_state.get("entry"),
            plan_state.get("entry"),
            "collector promotion collector state entry does not match task plan",
            path,
            "resource_digest_mismatch",
        ),
    )
    return _comparison_findings(run_id, comparisons)


def _artifact_version_findings(
    *,
    run_id: str,
    resource_id: str,
    path: Path,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    version = _int_or_none(payload.get("artifactVersion"))
    if version is None:
        return [
            collector_promotion_finding(
                "genesis_baseline_missing",
                run_id=run_id,
                resource_id=resource_id,
                path=path,
                detail="artifactVersion is absent; treating as legacy baseline",
                observed="missing",
                expected=COLLECTOR_PROMOTION_ARTIFACT_VERSION,
                severity="medium",
            )
        ]
    if version != COLLECTOR_PROMOTION_ARTIFACT_VERSION:
        return [
            collector_promotion_finding(
                "unsupported_contract_version",
                run_id=run_id,
                resource_id=resource_id,
                path=path,
                detail="artifactVersion is not supported",
                observed=version,
                expected=COLLECTOR_PROMOTION_ARTIFACT_VERSION,
            )
        ]
    return []


def _comparison_findings(
    run_id: str,
    comparisons: tuple[tuple[str, object, object, str, Path, str], ...],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for resource, observed, expected, detail, path, code in comparisons:
        if observed == expected:
            continue
        findings.append(
            collector_promotion_finding(
                code,
                run_id=run_id,
                resource_id=resource,
                path=path,
                detail=detail,
                observed=observed,
                expected=expected,
            )
        )
    return findings


def collector_promotion_finding(
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
        "subsystem": COLLECTOR_PROMOTION_SUBSYSTEM,
        "runId": run_id,
        "resourceId": resource_id,
        "path": str(path),
        "evidencePath": str(path),
        "detail": detail,
        "remediationHint": _collector_promotion_remediation_hint(code),
    }
    if observed is not None:
        finding["observedDigest"] = _value_digest(observed)
    if expected is not None:
        finding["expectedDigest"] = _value_digest(expected)
    return finding


def _collector_promotion_remediation_hint(code: str) -> str:
    hints = {
        "missing_required_artifact": (
            "Regenerate the collector-promote run so the ledger carries the "
            "required task-specific artifact for its outcome."
        ),
        "malformed_json": "Regenerate the malformed collector-promote ledger artifact.",
        "unsupported_contract_version": (
            "Use a collector-promote run emitted with the current Hermes "
            "contract and artifact versions."
        ),
        "plan_hash_mismatch": (
            "Bind collector-promote output evidence to the same task_plan.json "
            "planHash."
        ),
        "dry_run_binding_mismatch": (
            "Recreate the collector-promote run from the same task plan inputs."
        ),
        "resource_digest_mismatch": (
            "Refresh collector-promote evidence so database/result state and "
            "collector state match the task plan."
        ),
        "genesis_baseline_missing": (
            "Treat this historical collector-promote artifact as a legacy "
            "baseline and regenerate with artifactVersion for future audits."
        ),
    }
    return hints.get(code, "Inspect the collector-promote ledger artifacts.")


def _resource_id(resource_name: str) -> str:
    return f"{COLLECTOR_PROMOTION_SUBSYSTEM}.{resource_name}"


def _value_digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def _record_plan_hash(record: dict[str, Any]) -> str | None:
    inputs = _dict_value(record.get("inputs"))
    outputs = _dict_value(record.get("outputs"))
    for value in (
        record.get("planHash"),
        record.get("plan_hash"),
        record.get("input_plan_hash"),
        inputs.get("planHash"),
        inputs.get("plan_hash"),
        outputs.get("planHash"),
        outputs.get("plan_hash"),
    ):
        normalized = _string_or_none(value)
        if normalized is not None:
            return normalized
    return None


def _dict_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

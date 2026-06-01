from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..plan_contract import CURRENT_CONTRACT_VERSION, canonical_json_bytes

SUPPRESSION_OUTBOX_SUBSYSTEM = "suppression_outbox"
SUPPRESSION_OUTBOX_OWNER_TASK = "suppression-sync/outbox-purge"
SUPPRESSION_SYNC_TASK = "suppression-sync"
OUTBOX_PURGE_TASK = "outbox-purge"
SUPPRESSION_OUTBOX_ARTIFACT_VERSION = 1

_BASE_REQUIRED_ARTIFACTS = {
    "task_plan": "task_plan.json",
    "run_record": "run_record.json",
}
_SUPPRESSION_DRY_RUN_REQUIRED_ARTIFACTS = {
    **_BASE_REQUIRED_ARTIFACTS,
    "suppression_sync_command": "suppression_sync_command.json",
}
_SUPPRESSION_EXECUTE_REQUIRED_ARTIFACTS = {
    **_SUPPRESSION_DRY_RUN_REQUIRED_ARTIFACTS,
    "pre_suppression_sync_state": "pre_suppression_sync_state.json",
}
_OUTBOX_DRY_RUN_REQUIRED_ARTIFACTS = {
    **_BASE_REQUIRED_ARTIFACTS,
    "outbox_candidates": "outbox_candidates.json",
}
_OUTBOX_EXECUTE_REQUIRED_ARTIFACTS = {
    **_OUTBOX_DRY_RUN_REQUIRED_ARTIFACTS,
    "outbox_purge_result": "outbox_purge_result.json",
}


def empty_suppression_outbox_subsystem(*, enabled: bool) -> dict[str, Any]:
    return {
        "subsystem": SUPPRESSION_OUTBOX_SUBSYSTEM,
        "ownerTask": SUPPRESSION_OUTBOX_OWNER_TASK,
        "enabled": enabled,
        "runsChecked": 0,
        "resourcesChecked": 0,
        "findings": [],
    }


def audit_suppression_outbox_subsystem(rows: list[dict[str, Any]]) -> dict[str, Any]:
    state = empty_suppression_outbox_subsystem(enabled=True)
    findings: list[dict[str, Any]] = []
    for row, run_dir in _unique_run_dirs(rows):
        task_name = _task_name(row, run_dir)
        if task_name not in {SUPPRESSION_SYNC_TASK, OUTBOX_PURGE_TASK}:
            continue

        run_id = str(row.get("runId") or run_dir.name or "unknown")
        mode = _run_mode(row, run_dir)
        required_artifacts = _required_artifacts_for(task_name, mode)
        state["runsChecked"] += 1
        state["resourcesChecked"] += len(required_artifacts)

        artifact_findings, artifacts = _artifact_findings(
            task_name=task_name,
            run_id=run_id,
            run_dir=run_dir,
            required_artifacts=required_artifacts,
        )
        findings.extend(artifact_findings)
        if artifact_findings:
            continue

        findings.extend(
            _contract_findings_for_run(
                task_name=task_name,
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


def _required_artifacts_for(task_name: str, mode: str) -> dict[str, str]:
    if task_name == SUPPRESSION_SYNC_TASK:
        if mode == "execute":
            return _SUPPRESSION_EXECUTE_REQUIRED_ARTIFACTS
        return _SUPPRESSION_DRY_RUN_REQUIRED_ARTIFACTS
    if mode == "execute":
        return _OUTBOX_EXECUTE_REQUIRED_ARTIFACTS
    return _OUTBOX_DRY_RUN_REQUIRED_ARTIFACTS


def _artifact_findings(
    *,
    task_name: str,
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
                suppression_outbox_finding(
                    "missing_required_artifact",
                    run_id=run_id,
                    resource_id=artifact_resource_id,
                    path=path,
                    detail=(
                        f"required {task_name} artifact is missing: "
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
                suppression_outbox_finding(
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
                suppression_outbox_finding(
                    "malformed_json",
                    run_id=run_id,
                    resource_id=artifact_resource_id,
                    path=path,
                    detail="suppression/outbox artifact must be a JSON object",
                    observed=type(payload).__name__,
                    expected="object",
                )
            )
            continue
        artifacts[resource_name] = payload
    return findings, artifacts


def _contract_findings_for_run(
    *,
    task_name: str,
    run_id: str,
    run_dir: Path,
    mode: str,
    artifacts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    plan = artifacts["task_plan"]
    record = artifacts["run_record"]
    findings = _base_contract_findings(
        task_name=task_name,
        run_id=run_id,
        run_dir=run_dir,
        plan=plan,
        record=record,
    )
    if task_name == SUPPRESSION_SYNC_TASK:
        findings.extend(
            _suppression_sync_findings(
                run_id=run_id,
                run_dir=run_dir,
                mode=mode,
                plan=plan,
                artifacts=artifacts,
            )
        )
        return findings

    findings.extend(
        _outbox_purge_findings(
            run_id=run_id,
            run_dir=run_dir,
            mode=mode,
            plan=plan,
            artifacts=artifacts,
        )
    )
    return findings


def _base_contract_findings(
    *,
    task_name: str,
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
            suppression_outbox_finding(
                "unsupported_contract_version",
                run_id=run_id,
                resource_id=f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.task_plan.contract_version",
                path=run_dir / "task_plan.json",
                detail="task_plan.json contractVersion is not supported",
                observed=plan_contract,
                expected=CURRENT_CONTRACT_VERSION,
            )
        )
    if record_contract != CURRENT_CONTRACT_VERSION:
        findings.append(
            suppression_outbox_finding(
                "unsupported_contract_version",
                run_id=run_id,
                resource_id=f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.run_record.contract_version",
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
                    f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.task_plan.task",
                    plan.get("task"),
                    task_name,
                    "task_plan.json task does not match ledger task",
                    run_dir / "task_plan.json",
                    "dry_run_binding_mismatch",
                ),
                (
                    f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.run_record.task",
                    record.get("task"),
                    task_name,
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
            suppression_outbox_finding(
                "plan_hash_mismatch",
                run_id=run_id,
                resource_id=f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.run_record.plan_hash",
                path=run_dir / "run_record.json",
                detail="run_record.json plan hash does not match task_plan.json planHash",
                observed=record_plan_hash,
                expected=plan_hash,
            )
        )
    return findings


def _suppression_sync_findings(
    *,
    run_id: str,
    run_dir: Path,
    mode: str,
    plan: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    command_path = run_dir / "suppression_sync_command.json"
    command = artifacts["suppression_sync_command"]
    findings = _artifact_version_findings(
        run_id=run_id,
        resource_id=f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.suppression_sync_command.artifact_version",
        path=command_path,
        payload=command,
    )
    expected_command = _dict_value(plan.get("workflow")).get("command")
    findings.extend(
        _comparison_findings(
            run_id,
            (
                (
                    f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.suppression_sync_command.command",
                    command.get("command"),
                    expected_command,
                    "suppression_sync_command.json command does not match task plan",
                    command_path,
                    "dry_run_binding_mismatch",
                ),
                (
                    f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.suppression_sync_command.return_code",
                    command.get("returnCode"),
                    0,
                    "suppression_sync_command.json returnCode is not successful",
                    command_path,
                    "dry_run_binding_mismatch",
                ),
            ),
        )
    )
    if mode != "execute" or "pre_suppression_sync_state" not in artifacts:
        return findings

    state_path = run_dir / "pre_suppression_sync_state.json"
    pre_state = artifacts["pre_suppression_sync_state"]
    findings.extend(
        _artifact_version_findings(
            run_id=run_id,
            resource_id=f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.pre_suppression_sync_state.artifact_version",
            path=state_path,
            payload=pre_state,
        )
    )
    findings.extend(
        _suppression_database_findings(
            run_id=run_id,
            path=state_path,
            plan=plan,
            observed=pre_state,
        )
    )
    return findings


def _suppression_database_findings(
    *,
    run_id: str,
    path: Path,
    plan: dict[str, Any],
    observed: dict[str, Any],
) -> list[dict[str, Any]]:
    expected = _dict_value(plan.get("database"))
    comparisons = tuple(
        (
            f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.suppression_state.{field}",
            observed.get(field),
            expected.get(field),
            f"suppression pre-sync {field} does not match task plan",
            path,
            "resource_digest_mismatch",
        )
        for field in (
            "path",
            "exists",
            "openable",
            "integrity_check",
            "table_exists",
            "schema_valid",
            "row_count",
            "expired_count",
            "duplicates",
            "missing_columns",
        )
    )
    return _comparison_findings(run_id, comparisons)


def _outbox_purge_findings(
    *,
    run_id: str,
    run_dir: Path,
    mode: str,
    plan: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates_path = run_dir / "outbox_candidates.json"
    candidates = artifacts["outbox_candidates"]
    findings = _artifact_version_findings(
        run_id=run_id,
        resource_id=f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_candidates.artifact_version",
        path=candidates_path,
        payload=candidates,
    )
    findings.extend(
        _outbox_candidates_findings(
            run_id=run_id,
            path=candidates_path,
            plan=plan,
            candidates=candidates,
        )
    )
    if mode != "execute" or "outbox_purge_result" not in artifacts:
        return findings

    result_path = run_dir / "outbox_purge_result.json"
    result = artifacts["outbox_purge_result"]
    findings.extend(
        _artifact_version_findings(
            run_id=run_id,
            resource_id=f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_purge_result.artifact_version",
            path=result_path,
            payload=result,
        )
    )
    findings.extend(
        _outbox_result_findings(
            run_id=run_id,
            path=result_path,
            plan=plan,
            candidates=candidates,
            result=result,
        )
    )
    return findings


def _outbox_candidates_findings(
    *,
    run_id: str,
    path: Path,
    plan: dict[str, Any],
    candidates: dict[str, Any],
) -> list[dict[str, Any]]:
    planned = _dict_value(plan.get("candidates"))
    ids = _list_value(candidates.get("candidateIds"))
    rows = _list_value(candidates.get("rows"))
    findings = _comparison_findings(
        run_id,
        (
            (
                f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_candidates.count",
                candidates.get("candidateCount"),
                planned.get("count"),
                "outbox candidate count does not match task plan",
                path,
                "resource_digest_mismatch",
            ),
            (
                f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_candidates.ids",
                ids,
                planned.get("ids"),
                "outbox candidate ids do not match task plan",
                path,
                "resource_digest_mismatch",
            ),
            (
                f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_candidates.id_hash",
                candidates.get("candidateIdHash"),
                planned.get("id_hash"),
                "outbox candidateIdHash does not match task plan",
                path,
                "resource_digest_mismatch",
            ),
            (
                f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_candidates.candidate_hash",
                candidates.get("candidateHash"),
                planned.get("candidate_hash"),
                "outbox candidateHash does not match task plan",
                path,
                "resource_digest_mismatch",
            ),
            (
                f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_candidates.count_self_check",
                candidates.get("candidateCount"),
                len(rows),
                "outbox candidateCount does not match rows length",
                path,
                "resource_digest_mismatch",
            ),
            (
                f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_candidates.id_hash_self_check",
                candidates.get("candidateIdHash"),
                _hash_json(ids),
                "outbox candidateIdHash does not match candidateIds",
                path,
                "resource_digest_mismatch",
            ),
            (
                f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_candidates.hash_self_check",
                candidates.get("candidateHash"),
                _hash_json(rows),
                "outbox candidateHash does not match rows",
                path,
                "resource_digest_mismatch",
            ),
        ),
    )
    return findings


def _outbox_result_findings(
    *,
    run_id: str,
    path: Path,
    plan: dict[str, Any],
    candidates: dict[str, Any],
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    planned = _dict_value(plan.get("candidates"))
    before_count = _nested_int(result, "before", "matchingCount")
    after_count = _nested_int(result, "after", "matchingCount")
    deleted_count = _nested_int(result, "deleteResult", "deletedCount")
    expected_count = _int_or_none(planned.get("count"))
    findings = _comparison_findings(
        run_id,
        (
            (
                f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_purge_result.candidate_count",
                result.get("candidateCount"),
                expected_count,
                "outbox purge result candidateCount does not match task plan",
                path,
                "resource_digest_mismatch",
            ),
            (
                f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_purge_result.snapshot_count",
                result.get("snapshotCandidateCount"),
                candidates.get("candidateCount"),
                "outbox purge snapshot count does not match candidate artifact",
                path,
                "resource_digest_mismatch",
            ),
            (
                f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_purge_result.candidate_hash",
                result.get("candidateHash"),
                candidates.get("candidateHash"),
                "outbox purge candidateHash does not match candidate artifact",
                path,
                "resource_digest_mismatch",
            ),
            (
                f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_purge_result.id_hash",
                result.get("candidateIdHash"),
                candidates.get("candidateIdHash"),
                "outbox purge candidateIdHash does not match candidate artifact",
                path,
                "resource_digest_mismatch",
            ),
            (
                f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_purge_result.purge_criteria",
                result.get("purgeCriteria"),
                plan.get("purge_criteria"),
                "outbox purge criteria do not match task plan",
                path,
                "dry_run_binding_mismatch",
            ),
        ),
    )
    decrement_expected = (
        before_count is not None
        and after_count is not None
        and deleted_count is not None
        and before_count - after_count == deleted_count
        and deleted_count == expected_count
    )
    if not decrement_expected:
        findings.append(
            suppression_outbox_finding(
                "resource_digest_mismatch",
                run_id=run_id,
                resource_id=f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.outbox_purge_result.decrement",
                path=path,
                detail="outbox purge count decrement does not match planned candidates",
                observed={
                    "before": before_count,
                    "after": after_count,
                    "deleted": deleted_count,
                },
                expected={"deleted": expected_count},
            )
        )
    return findings


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
            suppression_outbox_finding(
                "genesis_baseline_missing",
                run_id=run_id,
                resource_id=resource_id,
                path=path,
                detail="artifactVersion is absent; treating as legacy baseline",
                observed="missing",
                expected=SUPPRESSION_OUTBOX_ARTIFACT_VERSION,
                severity="medium",
            )
        ]
    if version != SUPPRESSION_OUTBOX_ARTIFACT_VERSION:
        return [
            suppression_outbox_finding(
                "unsupported_contract_version",
                run_id=run_id,
                resource_id=resource_id,
                path=path,
                detail="artifactVersion is not supported",
                observed=version,
                expected=SUPPRESSION_OUTBOX_ARTIFACT_VERSION,
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
            suppression_outbox_finding(
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


def suppression_outbox_finding(
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
        "subsystem": SUPPRESSION_OUTBOX_SUBSYSTEM,
        "runId": run_id,
        "resourceId": resource_id,
        "path": str(path),
        "evidencePath": str(path),
        "detail": detail,
        "remediationHint": _suppression_outbox_remediation_hint(code),
    }
    if observed is not None:
        finding["observedDigest"] = _value_digest(observed)
    if expected is not None:
        finding["expectedDigest"] = _value_digest(expected)
    return finding


def _suppression_outbox_remediation_hint(code: str) -> str:
    hints = {
        "missing_required_artifact": (
            "Regenerate the suppression-sync or outbox-purge run so the ledger "
            "carries the required task-specific artifacts."
        ),
        "malformed_json": (
            "Regenerate the malformed suppression-sync or outbox-purge ledger "
            "artifact."
        ),
        "unsupported_contract_version": (
            "Use a suppression-sync or outbox-purge run emitted with the current "
            "Hermes contract and artifact versions."
        ),
        "plan_hash_mismatch": (
            "Bind the run record to the same task_plan.json planHash."
        ),
        "dry_run_binding_mismatch": (
            "Recreate the suppression-sync or outbox-purge run from the same "
            "task plan inputs."
        ),
        "resource_digest_mismatch": (
            "Refresh suppression/outbox evidence so database state, command "
            "binding, and candidate digests match the task plan."
        ),
        "genesis_baseline_missing": (
            "Treat this historical suppression/outbox artifact as a legacy "
            "baseline and regenerate with artifactVersion for future audits."
        ),
    }
    return hints.get(code, "Inspect the suppression/outbox ledger artifacts.")


def _resource_id(resource_name: str) -> str:
    return f"{SUPPRESSION_OUTBOX_SUBSYSTEM}.{resource_name}"


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _nested_int(payload: dict[str, Any], section: str, key: str) -> int | None:
    value = payload.get(section)
    if not isinstance(value, dict) or value.get(key) is None:
        return None
    try:
        return int(value[key])
    except (TypeError, ValueError):
        return None


def _dict_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


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

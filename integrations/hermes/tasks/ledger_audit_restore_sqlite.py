from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..plan_contract import CURRENT_CONTRACT_VERSION, canonical_json_bytes

RESTORE_SQLITE_SUBSYSTEM = "restore_sqlite"
RESTORE_SQLITE_OWNER_TASK = "restore-db"
RESTORE_REQUIRED_ARTIFACTS = {
    "task_plan": "task_plan.json",
    "run_record": "run_record.json",
    "restore_readiness": "restore_readiness.json",
}


def empty_restore_sqlite_subsystem(*, enabled: bool) -> dict[str, Any]:
    return {
        "subsystem": RESTORE_SQLITE_SUBSYSTEM,
        "ownerTask": RESTORE_SQLITE_OWNER_TASK,
        "enabled": enabled,
        "runsChecked": 0,
        "resourcesChecked": 0,
        "findings": [],
    }


def audit_restore_sqlite_subsystem(rows: list[dict[str, Any]]) -> dict[str, Any]:
    state = empty_restore_sqlite_subsystem(enabled=True)
    findings: list[dict[str, Any]] = []
    for row, run_dir in _unique_run_dirs(rows):
        run_id = str(row.get("runId") or run_dir.name or "unknown")
        if not _is_restore_sqlite_run(row, run_dir):
            continue

        state["runsChecked"] += 1
        state["resourcesChecked"] += len(RESTORE_REQUIRED_ARTIFACTS)
        run_findings, artifacts = _restore_sqlite_artifact_findings(run_id, run_dir)
        findings.extend(run_findings)
        if run_findings:
            continue

        findings.extend(
            _restore_sqlite_contract_findings(
                run_id=run_id,
                run_dir=run_dir,
                plan=artifacts["task_plan"],
                record=artifacts["run_record"],
                readiness=artifacts["restore_readiness"],
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


def _is_restore_sqlite_run(row: dict[str, Any], run_dir: Path) -> bool:
    if row.get("task") == RESTORE_SQLITE_OWNER_TASK:
        return True
    plan_path = run_dir / RESTORE_REQUIRED_ARTIFACTS["task_plan"]
    if not plan_path.exists():
        return False
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(plan, dict) and plan.get("task") == RESTORE_SQLITE_OWNER_TASK


def _restore_sqlite_artifact_findings(
    run_id: str,
    run_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    artifacts: dict[str, dict[str, Any]] = {}
    for resource_id, relative_path in RESTORE_REQUIRED_ARTIFACTS.items():
        path = run_dir / relative_path
        if not path.exists() or not path.is_file():
            findings.append(
                _restore_sqlite_finding(
                    "missing_required_artifact",
                    run_id=run_id,
                    resource_id=resource_id,
                    path=path,
                    detail=(
                        "required restore/SQLite artifact is missing: "
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
                _restore_sqlite_finding(
                    "malformed_json",
                    run_id=run_id,
                    resource_id=resource_id,
                    path=path,
                    detail=str(exc),
                    observed="malformed",
                    expected="valid_json_object",
                )
            )
            continue
        if not isinstance(payload, dict):
            findings.append(
                _restore_sqlite_finding(
                    "malformed_json",
                    run_id=run_id,
                    resource_id=resource_id,
                    path=path,
                    detail="restore/SQLite artifact must be a JSON object",
                    observed=type(payload).__name__,
                    expected="object",
                )
            )
            continue
        artifacts[resource_id] = payload
    return findings, artifacts


def _restore_sqlite_contract_findings(
    *,
    run_id: str,
    run_dir: Path,
    plan: dict[str, Any],
    record: dict[str, Any],
    readiness: dict[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    plan_path = run_dir / RESTORE_REQUIRED_ARTIFACTS["task_plan"]
    record_path = run_dir / RESTORE_REQUIRED_ARTIFACTS["run_record"]
    readiness_path = run_dir / RESTORE_REQUIRED_ARTIFACTS["restore_readiness"]

    plan_contract = _int_or_none(plan.get("contractVersion"))
    record_contract = _int_or_none(record.get("contract_version"))
    readiness_version = _int_or_none(readiness.get("artifactVersion"))
    if plan_contract != CURRENT_CONTRACT_VERSION:
        findings.append(
            _restore_sqlite_finding(
                "unsupported_contract_version",
                run_id=run_id,
                resource_id="restore_sqlite.task_plan.contract_version",
                path=plan_path,
                detail="task_plan.json contractVersion is not supported",
                observed=plan_contract,
                expected=CURRENT_CONTRACT_VERSION,
            )
        )
    if record_contract != CURRENT_CONTRACT_VERSION:
        findings.append(
            _restore_sqlite_finding(
                "unsupported_contract_version",
                run_id=run_id,
                resource_id="restore_sqlite.run_record.contract_version",
                path=record_path,
                detail="run_record.json contract_version is not supported",
                observed=record_contract,
                expected=CURRENT_CONTRACT_VERSION,
            )
        )
    if readiness_version != 1:
        findings.append(
            _restore_sqlite_finding(
                "unsupported_contract_version",
                run_id=run_id,
                resource_id="restore_sqlite.restore_readiness.artifact_version",
                path=readiness_path,
                detail="restore_readiness.json artifactVersion is not supported",
                observed=readiness_version,
                expected=1,
            )
        )
    findings.extend(
        _restore_sqlite_readiness_semantic_findings(
            run_id=run_id,
            readiness=readiness,
            readiness_path=readiness_path,
        )
    )

    plan_hash = _string_or_none(plan.get("planHash"))
    readiness_plan_hash = _string_or_none(readiness.get("executePlanHash"))
    if plan_hash != readiness_plan_hash:
        findings.append(
            _restore_sqlite_finding(
                "plan_hash_mismatch",
                run_id=run_id,
                resource_id="restore_sqlite.plan_hash",
                path=readiness_path,
                detail=(
                    "restore readiness executePlanHash does not match "
                    "task_plan.json planHash"
                ),
                observed=readiness_plan_hash,
                expected=plan_hash,
            )
        )

    record_plan_hash = _record_plan_hash(record)
    if record_plan_hash is not None and record_plan_hash != plan_hash:
        findings.append(
            _restore_sqlite_finding(
                "plan_hash_mismatch",
                run_id=run_id,
                resource_id="restore_sqlite.run_record.plan_hash",
                path=record_path,
                detail=(
                    "run_record.json plan hash does not match "
                    "task_plan.json planHash"
                ),
                observed=record_plan_hash,
                expected=plan_hash,
            )
        )

    findings.extend(
        _restore_sqlite_binding_findings(
            run_id=run_id,
            plan=plan,
            readiness=readiness,
            readiness_path=readiness_path,
        )
    )
    return findings


def _restore_sqlite_readiness_semantic_findings(
    *,
    run_id: str,
    readiness: dict[str, Any],
    readiness_path: Path,
) -> list[dict[str, Any]]:
    checks = (
        (
            "restore_sqlite.readiness.task",
            readiness.get("task"),
            RESTORE_SQLITE_OWNER_TASK,
            "restore readiness task does not identify restore-db",
        ),
        (
            "restore_sqlite.readiness.mode",
            readiness.get("mode"),
            "execute",
            "restore readiness mode is not execute",
        ),
        (
            "restore_sqlite.readiness.execute_eligible",
            readiness.get("executeEligible"),
            True,
            "restore readiness is not execute eligible",
        ),
    )
    findings: list[dict[str, Any]] = []
    for resource_id, observed, expected, detail in checks:
        if observed == expected:
            continue
        findings.append(
            _restore_sqlite_finding(
                "dry_run_binding_mismatch",
                run_id=run_id,
                resource_id=resource_id,
                path=readiness_path,
                detail=detail,
                observed=observed,
                expected=expected,
            )
        )
    return findings


def _restore_sqlite_binding_findings(
    *,
    run_id: str,
    plan: dict[str, Any],
    readiness: dict[str, Any],
    readiness_path: Path,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    plan_target = _dict_value(plan.get("target"))
    readiness_target = _dict_value(readiness.get("target"))
    expected_target = {
        "path": _string_or_none(plan_target.get("path")),
        "class": _string_or_none(
            plan_target.get("target_class") or plan_target.get("class")
        ),
    }
    actual_target = {
        "path": _string_or_none(readiness_target.get("path")),
        "class": _string_or_none(readiness_target.get("class")),
    }
    if actual_target != expected_target:
        findings.append(
            _restore_sqlite_finding(
                "dry_run_binding_mismatch",
                run_id=run_id,
                resource_id="restore_sqlite.target",
                path=readiness_path,
                detail="restore readiness target binding does not match task plan",
                observed=actual_target,
                expected=expected_target,
            )
        )

    expected_backup_sha = _string_or_none(
        _dict_value(plan.get("backup")).get("sha256")
    )
    actual_backup_sha = _string_or_none(
        _dict_value(readiness.get("backup")).get("sha256")
    )
    if actual_backup_sha != expected_backup_sha:
        findings.append(
            _restore_sqlite_finding(
                "resource_digest_mismatch",
                run_id=run_id,
                resource_id="restore_sqlite.backup.sha256",
                path=readiness_path,
                detail="restore readiness backup sha256 does not match task plan",
                observed=actual_backup_sha,
                expected=expected_backup_sha,
            )
        )

    contracts = _dict_value(plan.get("postflight_gate_contracts"))
    row_contract = _dict_value(contracts.get("row_count_above_watermark"))
    schema_contract = _dict_value(
        contracts.get("schema_version_matches_if_declared")
    )
    readiness_postflight = _dict_value(readiness.get("postflight"))
    expected_min_row_count = _int_or_none(row_contract.get("min_row_count"))
    actual_min_row_count = _int_or_none(readiness_postflight.get("minRowCount"))
    if actual_min_row_count != expected_min_row_count:
        findings.append(
            _restore_sqlite_finding(
                "resource_digest_mismatch",
                run_id=run_id,
                resource_id="restore_sqlite.postflight.min_row_count",
                path=readiness_path,
                detail="restore readiness minRowCount does not match task plan",
                observed=actual_min_row_count,
                expected=expected_min_row_count,
            )
        )

    expected_schema = _int_or_none(schema_contract.get("expected_schema_version"))
    actual_schema = _int_or_none(
        readiness_postflight.get("expectedSchemaVersion")
    )
    if actual_schema != expected_schema:
        findings.append(
            _restore_sqlite_finding(
                "schema_version_mismatch",
                run_id=run_id,
                resource_id="restore_sqlite.postflight.expected_schema_version",
                path=readiness_path,
                detail=(
                    "restore readiness expectedSchemaVersion does not match "
                    "task plan"
                ),
                observed=actual_schema,
                expected=expected_schema,
            )
        )
    return findings


def _restore_sqlite_finding(
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
        "subsystem": RESTORE_SQLITE_SUBSYSTEM,
        "runId": run_id,
        "resourceId": resource_id,
        "path": str(path),
        "evidencePath": str(path),
        "detail": detail,
        "remediationHint": _restore_sqlite_remediation_hint(code),
    }
    if observed is not None:
        finding["observedDigest"] = _value_digest(observed)
    if expected is not None:
        finding["expectedDigest"] = _value_digest(expected)
    return finding


def _restore_sqlite_remediation_hint(code: str) -> str:
    hints = {
        "missing_required_artifact": (
            "Regenerate the restore-db run after H2b readiness binding so the "
            "ledger carries task_plan.json, run_record.json, and "
            "restore_readiness.json."
        ),
        "malformed_json": "Regenerate the malformed restore-db ledger artifact.",
        "unsupported_contract_version": (
            "Use a restore-db run emitted with the current Hermes contract version."
        ),
        "plan_hash_mismatch": (
            "Bind restore readiness and any run-record plan hash to the same "
            "task_plan.json planHash."
        ),
        "dry_run_binding_mismatch": (
            "Recreate restore readiness from the same target binding used by the "
            "restore-db task plan."
        ),
        "resource_digest_mismatch": (
            "Recreate restore readiness from the same backup and postflight "
            "contract used by the restore-db task plan."
        ),
        "schema_version_mismatch": (
            "Align the restore readiness schema-version contract with task_plan.json."
        ),
    }
    return hints.get(code, "Inspect the restore-db ledger artifacts and rerun safely.")


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

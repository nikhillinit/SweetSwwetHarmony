from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ledger_audit_governance_config_contracts import (
    CONFIG_PROMOTE_TASK,
    GOVERNANCE_CONFIG_OWNER_TASK,
    GOVERNANCE_CONFIG_SUBSYSTEM,
    GOVERNANCE_TASK,
    contract_findings_for_run,
    governance_config_finding,
    required_artifacts_for,
    resource_id,
)


def empty_governance_config_subsystem(*, enabled: bool) -> dict[str, Any]:
    return {
        "subsystem": GOVERNANCE_CONFIG_SUBSYSTEM,
        "ownerTask": GOVERNANCE_CONFIG_OWNER_TASK,
        "enabled": enabled,
        "runsChecked": 0,
        "resourcesChecked": 0,
        "findings": [],
    }


def audit_governance_config_subsystem(rows: list[dict[str, Any]]) -> dict[str, Any]:
    state = empty_governance_config_subsystem(enabled=True)
    findings: list[dict[str, Any]] = []
    for row, run_dir in _unique_run_dirs(rows):
        task_name = _task_name(row, run_dir)
        if task_name not in {CONFIG_PROMOTE_TASK, GOVERNANCE_TASK}:
            continue

        run_id = str(row.get("runId") or run_dir.name or "unknown")
        mode = _run_mode(row, run_dir)
        required_artifacts = required_artifacts_for(task_name, mode)
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
            contract_findings_for_run(
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
        artifact_resource_id = resource_id(task_name, resource_name)
        if not path.exists() or not path.is_file():
            findings.append(
                governance_config_finding(
                    "missing_required_artifact",
                    run_id=run_id,
                    resource_id=artifact_resource_id,
                    path=path,
                    detail=(
                        "required governance/config artifact is missing: "
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
                governance_config_finding(
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
                governance_config_finding(
                    "malformed_json",
                    run_id=run_id,
                    resource_id=artifact_resource_id,
                    path=path,
                    detail="governance/config artifact must be a JSON object",
                    observed=type(payload).__name__,
                    expected="object",
                )
            )
            continue
        artifacts[resource_name] = payload
    return findings, artifacts


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)

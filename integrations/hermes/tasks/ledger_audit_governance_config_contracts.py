from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..plan_contract import CURRENT_CONTRACT_VERSION, canonical_json_bytes

GOVERNANCE_CONFIG_SUBSYSTEM = "governance_config"
GOVERNANCE_CONFIG_OWNER_TASK = "governance/config-promote"
GOVERNANCE_CONFIG_ARTIFACT_VERSION = 1

CONFIG_PROMOTE_TASK = "config-promote"
GOVERNANCE_TASK = "governance"

_BASE_REQUIRED_ARTIFACTS = {
    "task_plan": "task_plan.json",
    "run_record": "run_record.json",
}
_CONFIG_PROMOTE_REQUIRED_ARTIFACTS = {
    **_BASE_REQUIRED_ARTIFACTS,
    "config_report": "config_promote_report.json",
    "config_diff": "config_promote_diff.json",
    "previous_snapshot": "snapshots/model-routing.previous.json",
}
_GOVERNANCE_EXECUTE_REQUIRED_ARTIFACTS = {
    **_BASE_REQUIRED_ARTIFACTS,
    "pre_governance_state": "pre_governance_state.json",
    "governance_command": "governance_command.json",
    "state_verification": "state_verification.json",
}
_GOVERNANCE_DRY_RUN_REQUIRED_ARTIFACTS = {
    **_BASE_REQUIRED_ARTIFACTS,
    "governance_command": "governance_command.json",
}


def required_artifacts_for(task_name: str, mode: str) -> dict[str, str]:
    if task_name == CONFIG_PROMOTE_TASK:
        if mode == "execute":
            return _CONFIG_PROMOTE_REQUIRED_ARTIFACTS
        if mode == "dry-run":
            artifacts = dict(_CONFIG_PROMOTE_REQUIRED_ARTIFACTS)
            artifacts.pop("previous_snapshot")
            return artifacts
        return _BASE_REQUIRED_ARTIFACTS
    if mode == "execute":
        return _GOVERNANCE_EXECUTE_REQUIRED_ARTIFACTS
    if mode == "dry-run":
        return _GOVERNANCE_DRY_RUN_REQUIRED_ARTIFACTS
    return _BASE_REQUIRED_ARTIFACTS


def contract_findings_for_run(
    *,
    task_name: str,
    run_id: str,
    run_dir: Path,
    mode: str,
    artifacts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if task_name == CONFIG_PROMOTE_TASK:
        return _config_promote_contract_findings(
            run_id=run_id,
            run_dir=run_dir,
            mode=mode,
            plan=artifacts["task_plan"],
            record=artifacts["run_record"],
            report=artifacts.get("config_report"),
            diff=artifacts.get("config_diff"),
        )
    return _governance_contract_findings(
        run_id=run_id,
        run_dir=run_dir,
        mode=mode,
        plan=artifacts["task_plan"],
        record=artifacts["run_record"],
        command=artifacts.get("governance_command"),
        pre_state=artifacts.get("pre_governance_state"),
        state_verification=artifacts.get("state_verification"),
    )


def _config_promote_contract_findings(
    *,
    run_id: str,
    run_dir: Path,
    mode: str,
    plan: dict[str, Any],
    record: dict[str, Any],
    report: dict[str, Any] | None,
    diff: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    findings = _base_contract_findings(
        run_id=run_id,
        run_dir=run_dir,
        task_name=CONFIG_PROMOTE_TASK,
        plan=plan,
        record=record,
    )
    findings.extend(
        _artifact_version_findings(
            run_id=run_id,
            run_dir=run_dir,
            task_name=CONFIG_PROMOTE_TASK,
            resource_name="config_report",
            payload=report,
        )
    )
    findings.extend(
        _artifact_version_findings(
            run_id=run_id,
            run_dir=run_dir,
            task_name=CONFIG_PROMOTE_TASK,
            resource_name="config_diff",
            payload=diff,
        )
    )
    if report is None or diff is None:
        return findings

    plan_current = _dict_value(plan.get("current_config"))
    plan_proposed = _dict_value(plan.get("proposed_config"))
    report_current = _dict_value(report.get("currentConfig"))
    report_proposed = _dict_value(report.get("proposedConfig"))
    diff_current = _dict_value(diff.get("currentConfig"))
    diff_proposed = _dict_value(diff.get("proposedConfig"))
    expected_current_sha = _string_or_none(plan_current.get("sha256"))
    expected_proposed_sha = _string_or_none(plan_proposed.get("sha256"))

    comparisons = (
        (
            "config_promote.report.current_config.sha256_before",
            report_current.get("sha256Before"),
            expected_current_sha,
            "config report currentConfig.sha256Before does not match task plan",
            run_dir / "config_promote_report.json",
            "resource_digest_mismatch",
        ),
        (
            "config_promote.report.proposed_config.sha256",
            report_proposed.get("sha256"),
            expected_proposed_sha,
            "config report proposedConfig.sha256 does not match task plan",
            run_dir / "config_promote_report.json",
            "resource_digest_mismatch",
        ),
        (
            "config_promote.diff.current_config.sha256",
            diff_current.get("sha256"),
            expected_current_sha,
            "config diff currentConfig.sha256 does not match task plan",
            run_dir / "config_promote_diff.json",
            "resource_digest_mismatch",
        ),
        (
            "config_promote.diff.proposed_config.sha256",
            diff_proposed.get("sha256"),
            expected_proposed_sha,
            "config diff proposedConfig.sha256 does not match task plan",
            run_dir / "config_promote_diff.json",
            "resource_digest_mismatch",
        ),
        (
            "config_promote.report.diff_artifact",
            report.get("diffArtifact"),
            "config_promote_diff.json",
            "config report diffArtifact does not point at config diff",
            run_dir / "config_promote_report.json",
            "dry_run_binding_mismatch",
        ),
    )
    findings.extend(_comparison_findings(run_id, comparisons))
    if mode != "execute":
        return findings

    execute_comparisons = (
        (
            "config_promote.report.current_config.sha256_after",
            report_current.get("sha256After"),
            expected_proposed_sha,
            (
                "config report currentConfig.sha256After does not match "
                "planned proposed config sha256"
            ),
            run_dir / "config_promote_report.json",
            "resource_digest_mismatch",
        ),
        (
            "config_promote.report.previous_snapshot_ref",
            report.get("previousSnapshotRef"),
            _string_or_none(_dict_value(plan.get("artifacts")).get("previous_snapshot")),
            (
                "config report previousSnapshotRef does not match task plan "
                "snapshot artifact"
            ),
            run_dir / "config_promote_report.json",
            "resource_digest_mismatch",
        ),
    )
    findings.extend(_comparison_findings(run_id, execute_comparisons))
    return findings


def _governance_contract_findings(
    *,
    run_id: str,
    run_dir: Path,
    mode: str,
    plan: dict[str, Any],
    record: dict[str, Any],
    command: dict[str, Any] | None,
    pre_state: dict[str, Any] | None,
    state_verification: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    findings = _base_contract_findings(
        run_id=run_id,
        run_dir=run_dir,
        task_name=GOVERNANCE_TASK,
        plan=plan,
        record=record,
    )
    findings.extend(
        _artifact_version_findings(
            run_id=run_id,
            run_dir=run_dir,
            task_name=GOVERNANCE_TASK,
            resource_name="governance_command",
            payload=command,
        )
    )
    findings.extend(
        _artifact_version_findings(
            run_id=run_id,
            run_dir=run_dir,
            task_name=GOVERNANCE_TASK,
            resource_name="pre_governance_state",
            payload=pre_state,
        )
    )
    findings.extend(
        _artifact_version_findings(
            run_id=run_id,
            run_dir=run_dir,
            task_name=GOVERNANCE_TASK,
            resource_name="state_verification",
            payload=state_verification,
        )
    )
    if command is not None:
        findings.extend(
            _governance_command_findings(
                run_id=run_id,
                run_dir=run_dir,
                plan=plan,
                command=command,
            )
        )
    findings.extend(_governance_record_findings(run_id, run_dir, plan, record))
    if mode != "execute":
        return findings
    findings.extend(_governance_pre_state_findings(run_id, run_dir, plan, pre_state))
    findings.extend(
        _governance_state_verification_findings(
            run_id,
            run_dir,
            plan,
            state_verification,
        )
    )
    return findings


def _base_contract_findings(
    *,
    run_id: str,
    run_dir: Path,
    task_name: str,
    plan: dict[str, Any],
    record: dict[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    plan_contract = _int_or_none(plan.get("contractVersion"))
    record_contract = _int_or_none(record.get("contract_version"))
    if plan_contract != CURRENT_CONTRACT_VERSION:
        findings.append(
            governance_config_finding(
                "unsupported_contract_version",
                run_id=run_id,
                resource_id=f"{_task_prefix(task_name)}.task_plan.contract_version",
                path=run_dir / "task_plan.json",
                detail="task_plan.json contractVersion is not supported",
                observed=plan_contract,
                expected=CURRENT_CONTRACT_VERSION,
            )
        )
    if record_contract != CURRENT_CONTRACT_VERSION:
        findings.append(
            governance_config_finding(
                "unsupported_contract_version",
                run_id=run_id,
                resource_id=f"{_task_prefix(task_name)}.run_record.contract_version",
                path=run_dir / "run_record.json",
                detail="run_record.json contract_version is not supported",
                observed=record_contract,
                expected=CURRENT_CONTRACT_VERSION,
            )
        )

    comparisons = (
        (
            f"{_task_prefix(task_name)}.task_plan.task",
            plan.get("task"),
            task_name,
            "task_plan.json task does not match ledger task",
            run_dir / "task_plan.json",
            "dry_run_binding_mismatch",
        ),
        (
            f"{_task_prefix(task_name)}.run_record.task",
            record.get("task"),
            task_name,
            "run_record.json task does not match ledger task",
            run_dir / "run_record.json",
            "dry_run_binding_mismatch",
        ),
    )
    findings.extend(_comparison_findings(run_id, comparisons))

    plan_hash = _string_or_none(plan.get("planHash"))
    record_plan_hash = _record_plan_hash(record)
    if record_plan_hash is not None and record_plan_hash != plan_hash:
        findings.append(
            governance_config_finding(
                "plan_hash_mismatch",
                run_id=run_id,
                resource_id=f"{_task_prefix(task_name)}.run_record.plan_hash",
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


def _governance_record_findings(
    run_id: str,
    run_dir: Path,
    plan: dict[str, Any],
    record: dict[str, Any],
) -> list[dict[str, Any]]:
    inputs = _dict_value(record.get("inputs"))
    comparisons = (
        (
            "governance.run_record.inputs.feature",
            inputs.get("feature"),
            _string_or_none(plan.get("feature")),
            "governance run_record input does not match task plan",
            run_dir / "run_record.json",
            "dry_run_binding_mismatch",
        ),
        (
            "governance.run_record.inputs.from_state",
            inputs.get("from_state"),
            _string_or_none(plan.get("from_state")),
            "governance run_record input does not match task plan",
            run_dir / "run_record.json",
            "dry_run_binding_mismatch",
        ),
        (
            "governance.run_record.inputs.target_state",
            inputs.get("target_state"),
            _string_or_none(plan.get("target_state")),
            "governance run_record input does not match task plan",
            run_dir / "run_record.json",
            "dry_run_binding_mismatch",
        ),
    )
    return _comparison_findings(run_id, comparisons)


def _governance_command_findings(
    *,
    run_id: str,
    run_dir: Path,
    plan: dict[str, Any],
    command: dict[str, Any],
) -> list[dict[str, Any]]:
    command_args = command.get("command")
    if not isinstance(command_args, list):
        return [
            governance_config_finding(
                "dry_run_binding_mismatch",
                run_id=run_id,
                resource_id="governance.governance_command.command",
                path=run_dir / "governance_command.json",
                detail="governance command must be a list",
                observed=type(command_args).__name__,
                expected="list",
            )
        ]
    comparisons = (
        (
            "governance.governance_command.prefix",
            command_args[1:4],
            ["-m", "governance", "feature"],
            "governance command prefix does not match the governance CLI contract",
            run_dir / "governance_command.json",
            "dry_run_binding_mismatch",
        ),
        (
            "governance.governance_command.verb",
            _command_verb(command_args),
            _string_or_none(_dict_value(plan.get("transition")).get("cli_subcommand")),
            "governance command verb does not match planned transition",
            run_dir / "governance_command.json",
            "dry_run_binding_mismatch",
        ),
        (
            "governance.governance_command.feature",
            _command_feature(command_args),
            _string_or_none(plan.get("feature")),
            "governance command binding does not match task plan",
            run_dir / "governance_command.json",
            "dry_run_binding_mismatch",
        ),
        (
            "governance.governance_command.--from",
            _command_flag_value(command_args, "--from"),
            _string_or_none(plan.get("from_state")),
            "governance command binding does not match task plan",
            run_dir / "governance_command.json",
            "dry_run_binding_mismatch",
        ),
        (
            "governance.governance_command.--to",
            _command_flag_value(command_args, "--to"),
            _string_or_none(plan.get("target_state")),
            "governance command binding does not match task plan",
            run_dir / "governance_command.json",
            "dry_run_binding_mismatch",
        ),
    )
    return _comparison_findings(run_id, comparisons)


def _governance_pre_state_findings(
    run_id: str,
    run_dir: Path,
    plan: dict[str, Any],
    pre_state: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if pre_state is None:
        return []
    comparisons = (
        (
            "governance.pre_governance_state.actual_state",
            _state_value(pre_state),
            _string_or_none(plan.get("from_state")),
            "pre_governance_state actual_state does not match planned from_state",
            run_dir / "pre_governance_state.json",
            "resource_digest_mismatch",
        ),
    )
    return _comparison_findings(run_id, comparisons)


def _governance_state_verification_findings(
    run_id: str,
    run_dir: Path,
    plan: dict[str, Any],
    state_verification: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if state_verification is None:
        return []
    plan_target_state = _string_or_none(plan.get("target_state"))
    comparisons = (
        (
            "governance.state_verification.feature",
            state_verification.get("feature"),
            _string_or_none(plan.get("feature")),
            "state verification feature does not match task plan",
            run_dir / "state_verification.json",
            "dry_run_binding_mismatch",
        ),
        (
            "governance.state_verification.target_state",
            state_verification.get("target_state"),
            plan_target_state,
            "state verification target_state does not match task plan",
            run_dir / "state_verification.json",
            "dry_run_binding_mismatch",
        ),
        (
            "governance.state_verification.actual_state",
            _state_value(state_verification),
            plan_target_state,
            "state verification actual_state does not match target_state",
            run_dir / "state_verification.json",
            "resource_digest_mismatch",
        ),
        (
            "governance.state_verification.readable",
            state_verification.get("readable"),
            True,
            "state verification must be readable",
            run_dir / "state_verification.json",
            "dry_run_binding_mismatch",
        ),
    )
    return _comparison_findings(run_id, comparisons)


def _artifact_version_findings(
    *,
    run_id: str,
    run_dir: Path,
    task_name: str,
    resource_name: str,
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if payload is None:
        return []
    path = run_dir / artifact_path_for_resource(task_name, resource_name)
    version = _int_or_none(payload.get("artifactVersion"))
    version_resource_id = f"{resource_id(task_name, resource_name)}.artifact_version"
    if version is None:
        return [
            governance_config_finding(
                "genesis_baseline_missing",
                run_id=run_id,
                resource_id=version_resource_id,
                path=path,
                detail="artifactVersion is absent; treating as legacy baseline",
                observed="missing",
                expected=GOVERNANCE_CONFIG_ARTIFACT_VERSION,
                severity="medium",
            )
        ]
    if version != GOVERNANCE_CONFIG_ARTIFACT_VERSION:
        return [
            governance_config_finding(
                "unsupported_contract_version",
                run_id=run_id,
                resource_id=version_resource_id,
                path=path,
                detail="artifactVersion is not supported",
                observed=version,
                expected=GOVERNANCE_CONFIG_ARTIFACT_VERSION,
            )
        ]
    return []


def _comparison_findings(
    run_id: str,
    comparisons: tuple[tuple[str, object, object, str, Path, str], ...],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for comparison in comparisons:
        resource, observed, expected, detail, path, code = comparison
        if observed == expected:
            continue
        findings.append(
            governance_config_finding(
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


def governance_config_finding(
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
        "subsystem": GOVERNANCE_CONFIG_SUBSYSTEM,
        "runId": run_id,
        "resourceId": resource_id,
        "path": str(path),
        "evidencePath": str(path),
        "detail": detail,
        "remediationHint": _governance_config_remediation_hint(code),
    }
    if observed is not None:
        finding["observedDigest"] = _value_digest(observed)
    if expected is not None:
        finding["expectedDigest"] = _value_digest(expected)
    return finding


def _governance_config_remediation_hint(code: str) -> str:
    hints = {
        "missing_required_artifact": (
            "Regenerate the governance/config run so the ledger carries the "
            "required task-specific artifacts for its mode."
        ),
        "malformed_json": "Regenerate the malformed governance/config ledger artifact.",
        "unsupported_contract_version": (
            "Use a governance/config run emitted with the current Hermes "
            "contract and artifact versions."
        ),
        "plan_hash_mismatch": (
            "Bind the governance/config run record to the same task_plan.json "
            "planHash."
        ),
        "dry_run_binding_mismatch": (
            "Recreate the governance/config run from the same task plan inputs."
        ),
        "resource_digest_mismatch": (
            "Refresh the governance/config evidence so recorded digests and "
            "state verification match the task plan."
        ),
        "genesis_baseline_missing": (
            "Treat this historical governance/config artifact as a legacy "
            "baseline and regenerate with artifactVersion for future audits."
        ),
    }
    return hints.get(code, "Inspect the governance/config ledger artifacts.")


def resource_id(task_name: str, resource_name: str) -> str:
    return f"{_task_prefix(task_name)}.{resource_name}"


def artifact_path_for_resource(task_name: str, resource_name: str) -> str:
    return required_artifacts_for(task_name, "execute").get(
        resource_name,
        f"{resource_name}.json",
    )


def _task_prefix(task_name: str) -> str:
    return task_name.replace("-", "_")


def _command_feature(command_args: list[Any]) -> str | None:
    verb = _command_verb(command_args)
    if verb is None:
        return None
    feature_index = command_args.index(verb) + 1
    if feature_index < len(command_args):
        return _string_or_none(command_args[feature_index])
    return None


def _command_verb(command_args: list[Any]) -> str | None:
    for verb in ("promote", "demote"):
        if verb not in command_args:
            continue
        return verb
    return None


def _command_flag_value(command_args: list[Any], flag: str) -> str | None:
    if flag not in command_args:
        return None
    flag_index = command_args.index(flag) + 1
    if flag_index >= len(command_args):
        return None
    return _string_or_none(command_args[flag_index])


def _state_value(payload: dict[str, Any]) -> str | None:
    for key in ("actual_state", "state"):
        value = _string_or_none(payload.get(key))
        if value is not None:
            return value
    return None


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


def _value_digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"

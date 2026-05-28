from __future__ import annotations

import argparse
import difflib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from integrations.hermes.config import (
    DEFAULT_CONFIG_PATH,
    RoutingConfig,
)
from integrations.hermes.providers import doctor as provider_doctor

from .base import (
    CheckResult,
    HermesTask,
    TaskContext,
    TaskFailure,
    copy_snapshot,
    sha256_file,
)

CONFIG_PROMOTE_ACK = "CONFIG_PROMOTE"
CONFIG_DIFF_ARTIFACT = "config_promote_diff.json"
CONFIG_REPORT_ARTIFACT = "config_promote_report.json"
CONFIG_PREVIOUS_SNAPSHOT = "snapshots/model-routing.previous.json"

_ROUTING_POLICY_KEYS = ("phases", "specialists", "riskDefaults", "routing", "modes")


class ConfigPromoteTask(HermesTask):
    name = "config-promote"
    description = "Atomic, ledger-backed promotion for Hermes model-routing.json."
    risk_level = "high"
    ack_risk_token = CONFIG_PROMOTE_ACK
    supported_modes = ("plan-only", "preflight-only", "dry-run", "execute")
    required_locks = ("hermes-config",)
    ledger_backed = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--proposed",
            default=None,
            help="Path to the proposed Hermes routing config JSON",
        )
        parser.add_argument(
            "--policy-evidence",
            action="append",
            default=[],
            help=(
                "Evidence reference allowing routing or execute-support policy "
                "changes in the proposed config"
            ),
        )

    def plan(self, context: TaskContext) -> dict[str, Any]:
        current = _current_config_path(context)
        proposed = context.resolve(getattr(context.args, "proposed", None))
        current_state = _inspect_config_file(current)
        proposed_state = _inspect_config_file(proposed)
        diff = _config_diff(current_state.get("data"), proposed_state.get("data"))
        policy_evidence = _policy_evidence(context)
        policy_risks = _policy_risks(diff)

        plan = self._base_plan(context)
        plan.update(
            {
                "current_config": current_state,
                "proposed_config": proposed_state,
                "config_diff": diff,
                "policy_review": {
                    "risky_changes": policy_risks,
                    "requires_evidence": bool(policy_risks),
                    "evidence": policy_evidence,
                },
                "artifacts": {
                    "config_diff": CONFIG_DIFF_ARTIFACT,
                    "config_report": CONFIG_REPORT_ARTIFACT,
                    "previous_snapshot": CONFIG_PREVIOUS_SNAPSHOT,
                    "run_record": "run_record.json",
                    "task_plan": "task_plan.json",
                },
                "locks_required": list(self.required_locks),
                "ack_risk_required": True,
                "ack_risk_token": CONFIG_PROMOTE_ACK,
                "preflight_gates": [
                    "current_config_readable",
                    "current_config_hash_captured",
                    "proposed_argument_present",
                    "proposed_file_exists",
                    "proposed_json_valid",
                    "proposed_config_schema_valid",
                    "provider_doctor_passes_for_required_executors",
                    "policy_changes_have_evidence",
                ],
                "postflight_gates": [
                    "current_config_schema_valid",
                    "provider_doctor_postflight",
                    "config_hash_matches_expected",
                    "config_report_artifact_written",
                    "previous_config_snapshot_written",
                    "ledger_written",
                ],
                "rollback": {
                    "available": current.exists(),
                    "recipe": (
                        "Restore snapshots/model-routing.previous.json from the run "
                        "ledger to the current Hermes routing config path."
                    ),
                },
                "mutation": {
                    "allowed": context.mode == "execute",
                    "affected_files": [str(current)] if context.mode == "execute" else [],
                    "affected_tables": [],
                    "external_systems": [],
                    "ledger_artifacts": [
                        "task_plan.json",
                        "run_record.json",
                        CONFIG_DIFF_ARTIFACT,
                        CONFIG_REPORT_ARTIFACT,
                        CONFIG_PREVIOUS_SNAPSHOT,
                    ],
                },
                "external_reads": [],
                "database_reads": [],
            }
        )
        return plan

    def preflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> list[CheckResult]:
        current = plan["current_config"]
        proposed = plan["proposed_config"]
        proposed_config = _validated_config(proposed)
        report = provider_doctor(proposed_config) if proposed_config else None
        policy_review = plan["policy_review"]
        policy_ok = not policy_review["requires_evidence"] or bool(
            policy_review["evidence"]
        )

        checks = [
            CheckResult(
                "current_config_readable",
                bool(current["exists"] and current["readable"] and current["json_valid"]),
                current.get("detail") or current["path"],
                current,
            ),
            CheckResult(
                "current_config_hash_captured",
                bool(current.get("sha256")),
                str(current.get("sha256") or "missing"),
                {"path": current["path"], "sha256": current.get("sha256")},
            ),
            CheckResult(
                "proposed_argument_present",
                bool(proposed["path"]),
                proposed["path"] or "missing --proposed",
            ),
            CheckResult(
                "proposed_file_exists",
                bool(proposed["exists"]),
                proposed["path"] or "missing --proposed",
                proposed,
            ),
            CheckResult(
                "proposed_json_valid",
                bool(proposed["json_valid"]),
                proposed.get("detail") or proposed["path"] or "missing --proposed",
                proposed,
            ),
            CheckResult(
                "proposed_config_schema_valid",
                bool(proposed["schema_valid"]),
                proposed.get("schema_detail") or proposed.get("detail") or "",
                {"path": proposed["path"], "errors": proposed.get("schema_errors")},
            ),
            CheckResult(
                "provider_doctor_passes_for_required_executors",
                bool(report and report.success),
                "provider doctor passed" if report and report.success else "provider doctor failed",
                report.to_dict() if report else {"reason": "proposed config invalid"},
            ),
            CheckResult(
                "policy_changes_have_evidence",
                policy_ok,
                "evidence recorded" if policy_ok else "missing --policy-evidence",
                policy_review,
            ),
        ]
        return checks

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        diff_payload = _diff_artifact_payload(plan)
        context.write_json(CONFIG_DIFF_ARTIFACT, diff_payload)
        report = _report_payload(
            context,
            plan,
            dry_run=True,
            mutation_committed=False,
            previous_snapshot_ref=None,
            resulting_hash=plan["current_config"].get("sha256"),
        )
        context.write_json(CONFIG_REPORT_ARTIFACT, report)
        return report

    def execute(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        current = Path(plan["current_config"]["path"])
        proposed = Path(plan["proposed_config"]["path"])
        if not proposed.exists():
            raise TaskFailure(
                "proposed config missing",
                evidence={"proposedConfig": str(proposed)},
            )
        if not current.exists():
            raise TaskFailure(
                "current config missing",
                evidence={"currentConfig": str(current)},
            )

        planned_current_hash = plan["current_config"].get("sha256")
        current_hash = sha256_file(current)
        if planned_current_hash != current_hash:
            raise TaskFailure(
                "current config hash drift detected between plan and execute",
                evidence={
                    "currentConfig": str(current),
                    "plannedSha256": planned_current_hash,
                    "currentSha256": current_hash,
                },
            )

        planned_proposed_hash = plan["proposed_config"].get("sha256")
        proposed_hash = sha256_file(proposed)
        if planned_proposed_hash != proposed_hash:
            raise TaskFailure(
                "proposed config hash drift detected between plan and execute",
                evidence={
                    "proposedConfig": str(proposed),
                    "plannedSha256": planned_proposed_hash,
                    "currentSha256": proposed_hash,
                },
            )

        evidence: dict[str, Any] = {
            "currentConfig": str(current),
            "proposedConfig": str(proposed),
            "plannedCurrentSha256": planned_current_hash,
            "plannedProposedSha256": planned_proposed_hash,
            "previousSnapshotRef": None,
        }
        try:
            snapshot = copy_snapshot(
                current,
                context.artifact_path(CONFIG_PREVIOUS_SNAPSHOT),
            )
            evidence["previousSnapshotRef"] = str(snapshot.relative_to(context.run_dir))
            evidence["previousSnapshotSha256"] = sha256_file(snapshot)
        except Exception as exc:
            raise TaskFailure(
                "previous config snapshot failed",
                evidence={**evidence, "error": str(exc)},
            ) from exc

        tmp_path = current.with_name(f".{current.name}.{context.run.run_id}.tmp")
        try:
            tmp_path.write_bytes(proposed.read_bytes())
            os.replace(tmp_path, current)
            evidence["replacementCommitted"] = True
            evidence["resultingSha256"] = sha256_file(current)
            diff_payload = _diff_artifact_payload(plan)
            context.write_json(CONFIG_DIFF_ARTIFACT, diff_payload)
            report = _report_payload(
                context,
                plan,
                dry_run=False,
                mutation_committed=True,
                previous_snapshot_ref=str(snapshot.relative_to(context.run_dir)),
                resulting_hash=evidence["resultingSha256"],
            )
            context.write_json(CONFIG_REPORT_ARTIFACT, report)
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            raise TaskFailure(
                "config promotion execute failed",
                evidence={**evidence, "error": str(exc)},
            ) from exc
        return report

    def postflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        outputs: dict[str, Any],
    ) -> list[CheckResult]:
        current = Path(plan["current_config"]["path"])
        report_path = context.run_dir / CONFIG_REPORT_ARTIFACT
        snapshot_path = context.run_dir / CONFIG_PREVIOUS_SNAPSHOT
        execute_mode = context.mode == "execute"

        try:
            current_data = json.loads(current.read_text(encoding="utf-8"))
            current_config = RoutingConfig.model_validate(current_data)
            schema_check = CheckResult(
                "current_config_schema_valid",
                True,
                "RoutingConfig.model_validate passed",
                {"path": str(current)},
            )
            report = provider_doctor(current_config)
            doctor_check = CheckResult(
                "provider_doctor_postflight",
                report.success,
                "provider doctor passed" if report.success else "provider doctor failed",
                report.to_dict(),
            )
        except Exception as exc:
            schema_check = CheckResult(
                "current_config_schema_valid",
                False,
                str(exc),
                {"path": str(current)},
            )
            doctor_check = CheckResult(
                "provider_doctor_postflight",
                False,
                "schema validation failed",
                {"path": str(current)},
            )

        current_hash = sha256_file(current) if current.exists() else None
        proposed_hash = plan["proposed_config"].get("sha256")
        expected_hash = (
            proposed_hash if execute_mode else plan["current_config"].get("sha256")
        )
        return [
            schema_check,
            doctor_check,
            CheckResult(
                "config_hash_matches_expected",
                current_hash == expected_hash,
                f"current={current_hash} expected={expected_hash}",
                {"currentSha256": current_hash, "expectedSha256": expected_hash},
            ),
            CheckResult(
                "config_report_artifact_written",
                report_path.exists(),
                CONFIG_REPORT_ARTIFACT if report_path.exists() else "missing",
                {"path": str(report_path)},
            ),
            CheckResult(
                "previous_config_snapshot_written",
                (not execute_mode) or snapshot_path.exists(),
                CONFIG_PREVIOUS_SNAPSHOT if snapshot_path.exists() else "not required",
                {"path": str(snapshot_path)},
            ),
            CheckResult(
                "ledger_written",
                (context.run_dir / "run_record.json").exists(),
                "run_record.json",
            ),
        ]


def _current_config_path(context: TaskContext) -> Path:
    configured = getattr(context.args, "config", None)
    if configured:
        return context.resolve(configured) or Path(configured)
    return context.root / DEFAULT_CONFIG_PATH


def _inspect_config_file(path: Path | None) -> dict[str, Any]:
    state: dict[str, Any] = {
        "path": str(path) if path else None,
        "exists": bool(path and path.exists()),
        "readable": False,
        "json_valid": False,
        "schema_valid": False,
        "sha256": None,
        "size_bytes": None,
        "detail": "missing",
        "schema_detail": None,
        "schema_errors": [],
        "data": None,
    }
    if path is None:
        state["detail"] = "missing --proposed"
        return state
    if not path.exists():
        return state
    if not path.is_file():
        state["detail"] = "not a file"
        return state

    try:
        state["sha256"] = sha256_file(path)
        state["size_bytes"] = path.stat().st_size
        raw = path.read_text(encoding="utf-8")
        state["readable"] = True
    except (OSError, UnicodeError) as exc:
        state["detail"] = str(exc)
        return state

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        state["detail"] = str(exc)
        return state

    state["json_valid"] = True
    state["data"] = data
    try:
        RoutingConfig.model_validate(data)
    except Exception as exc:
        state["schema_detail"] = str(exc)
        state["schema_errors"] = _schema_errors(exc)
        return state

    state["schema_valid"] = True
    state["schema_detail"] = "RoutingConfig.model_validate passed"
    state["detail"] = "ok"
    return state


def _validated_config(state: dict[str, Any]) -> RoutingConfig | None:
    if not state.get("schema_valid") or state.get("data") is None:
        return None
    return RoutingConfig.model_validate(state["data"])


def _schema_errors(exc: Exception) -> list[dict[str, Any]]:
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return [{"message": str(exc)}]
    return [
        {
            "loc": [str(part) for part in error.get("loc", [])],
            "msg": str(error.get("msg", "")),
            "type": str(error.get("type", "")),
        }
        for error in errors()
    ]


def _config_diff(
    current: dict[str, Any] | None,
    proposed: dict[str, Any] | None,
) -> dict[str, Any]:
    current_payload = current or {}
    proposed_payload = proposed or {}
    current_sections = set(current_payload)
    proposed_sections = set(proposed_payload)
    section_changes = sorted(
        key
        for key in current_sections | proposed_sections
        if current_payload.get(key) != proposed_payload.get(key)
    )
    current_executors = current_payload.get("executors", {})
    proposed_executors = proposed_payload.get("executors", {})
    if not isinstance(current_executors, dict):
        current_executors = {}
    if not isinstance(proposed_executors, dict):
        proposed_executors = {}

    executor_changes = sorted(
        name
        for name in set(current_executors) & set(proposed_executors)
        if current_executors.get(name) != proposed_executors.get(name)
    )
    execute_support_changes: dict[str, dict[str, bool | None]] = {}
    for name in sorted(set(current_executors) | set(proposed_executors)):
        before = _supports_execute(current_executors.get(name), default=None)
        after = _supports_execute(proposed_executors.get(name), default=None)
        if before != after:
            execute_support_changes[name] = {"from": before, "to": after}

    return {
        "sections_changed": section_changes,
        "executors_added": sorted(set(proposed_executors) - set(current_executors)),
        "executors_removed": sorted(set(current_executors) - set(proposed_executors)),
        "executor_changes": executor_changes,
        "execute_support_changes": execute_support_changes,
        "routing_policy_changes": [
            key for key in _ROUTING_POLICY_KEYS if key in section_changes
        ],
        "unified_diff": _unified_diff(current_payload, proposed_payload),
    }


def _supports_execute(value: object, *, default: bool | None) -> bool | None:
    if not isinstance(value, dict):
        return default
    if "supportsExecute" not in value:
        return True
    return bool(value.get("supportsExecute"))


def _unified_diff(current: dict[str, Any], proposed: dict[str, Any]) -> list[str]:
    current_lines = json.dumps(current, indent=2, sort_keys=True).splitlines()
    proposed_lines = json.dumps(proposed, indent=2, sort_keys=True).splitlines()
    return list(
        difflib.unified_diff(
            current_lines,
            proposed_lines,
            fromfile="current/model-routing.json",
            tofile="proposed/model-routing.json",
            lineterm="",
        )
    )


def _policy_risks(diff: dict[str, Any]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    for name, change in diff.get("execute_support_changes", {}).items():
        if change.get("to") is True and change.get("from") is not True:
            risks.append(
                {
                    "type": "execute_support_enabled",
                    "executor": name,
                    "from": change.get("from"),
                    "to": change.get("to"),
                }
            )
    for key in diff.get("routing_policy_changes", []):
        risks.append({"type": "routing_policy_changed", "section": key})
    return risks


def _policy_evidence(context: TaskContext) -> list[str]:
    raw = getattr(context.args, "policy_evidence", []) or []
    if isinstance(raw, str):
        return [raw]
    return [str(item) for item in raw if str(item).strip()]


def _diff_artifact_payload(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "currentConfig": {
            "path": plan["current_config"]["path"],
            "sha256": plan["current_config"].get("sha256"),
        },
        "proposedConfig": {
            "path": plan["proposed_config"]["path"],
            "sha256": plan["proposed_config"].get("sha256"),
        },
        "diff": plan["config_diff"],
        "policyReview": plan["policy_review"],
    }


def _report_payload(
    context: TaskContext,
    plan: dict[str, Any],
    *,
    dry_run: bool,
    mutation_committed: bool,
    previous_snapshot_ref: str | None,
    resulting_hash: str | None,
) -> dict[str, Any]:
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "task": ConfigPromoteTask.name,
        "runId": context.run.run_id if context.run else None,
        "dryRun": dry_run,
        "mutationCommitted": mutation_committed,
        "currentConfig": {
            "path": plan["current_config"]["path"],
            "sha256Before": plan["current_config"].get("sha256"),
            "sha256After": resulting_hash,
        },
        "proposedConfig": {
            "path": plan["proposed_config"]["path"],
            "sha256": plan["proposed_config"].get("sha256"),
        },
        "previousSnapshotRef": previous_snapshot_ref,
        "diffArtifact": CONFIG_DIFF_ARTIFACT,
        "policyReview": plan["policy_review"],
    }

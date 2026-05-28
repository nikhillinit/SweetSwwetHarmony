from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from governance.state_policies import (
    GovernanceStatePolicyError,
    allowed_states_for_flag,
    ensure_registered_flag,
    validate_transition,
)

from .base import CheckResult, HermesTask, TaskContext, TaskFailure, run_command

GOVERNANCE_PROMOTE_ACK = "GOVERNANCE_PROMOTE"
GOVERNANCE_ROLLBACK_ACK = "GOVERNANCE_ROLLBACK"


class GovernanceTask(HermesTask):
    name = "governance"
    description = "Locked, ledger-backed wrapper for governance feature transitions."
    risk_level = "high"
    supported_modes = ("plan-only", "preflight-only", "dry-run", "execute")
    required_locks = ("signals.db", "governance")
    ledger_backed = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--feature")
        parser.add_argument("--from-state")
        parser.add_argument("--target-state")
        parser.add_argument("--reason")
        parser.add_argument("--regret-check-date")
        parser.add_argument("--effective-at")
        parser.add_argument("--repair-source")
        parser.add_argument("--rollback-ticket")
        parser.add_argument("--incident-id")
        parser.add_argument("--direct-db")
        parser.add_argument(
            "--state-source",
            help="Optional readable JSON state source for postflight verification.",
        )

    def plan(self, context: TaskContext) -> dict[str, Any]:
        feature = _arg(context, "feature")
        from_state = _arg(context, "from_state")
        target_state = _arg(context, "target_state")
        reason = _arg(context, "reason")
        transition = _transition(feature, from_state, target_state)
        command = _governance_command(context, transition, reason)

        plan = self._base_plan(context)
        plan.update(
            {
                "feature": feature,
                "from_state": from_state,
                "target_state": target_state,
                "reason": reason,
                "transition": transition,
                "command": command,
                "governance_cli": {
                    "module": "governance",
                    "contract": "python -m governance feature promote|demote FLAG --from OLD --to NEW --reason ...",
                },
                "state_source": _state_source_path(context),
                "locks_required": list(self.required_locks),
                "ack_risk_required": transition.get("ack_risk_token") is not None,
                "ack_risk_token": transition.get("ack_risk_token"),
                "preflight_gates": [
                    "feature_declared",
                    "from_state_declared",
                    "target_state_declared",
                    "reason_declared",
                    "state_policy_available",
                    "governance_cli_available",
                    "feature_registered",
                    "transition_allowed",
                ],
                "postflight_gates": [
                    "governance_cli_command_succeeded",
                    "final_state_matches_target",
                    "ledger_written",
                ],
                "rollback": {
                    "available": bool(feature and from_state),
                    "command": _rollback_command(
                        context,
                        feature,
                        target_state,
                        from_state,
                    ),
                },
                "mutation": {
                    "allowed": context.mode == "execute",
                    "affected_files": _affected_files(context),
                    "affected_tables": ["audit_events"],
                    "external_systems": [],
                },
            }
        )
        return plan

    def required_ack_token(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> str | None:
        token = plan.get("transition", {}).get("ack_risk_token")
        return str(token) if token else None

    def preflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> list[CheckResult]:
        feature = plan.get("feature")
        from_state = plan.get("from_state")
        target_state = plan.get("target_state")
        reason = plan.get("reason")
        transition = plan.get("transition", {})
        policy_error = str(transition.get("policy_error") or "")
        registered = _registered_detail(feature)

        return [
            CheckResult("feature_declared", bool(feature), str(feature or "missing")),
            CheckResult(
                "from_state_declared",
                bool(from_state),
                str(from_state or "missing"),
            ),
            CheckResult(
                "target_state_declared",
                bool(target_state),
                str(target_state or "missing"),
            ),
            CheckResult("reason_declared", bool(reason), str(reason or "missing")),
            CheckResult(
                "state_policy_available",
                (context.root / "governance" / "state_policies.py").exists(),
                "governance/state_policies.py",
            ),
            CheckResult(
                "governance_cli_available",
                (context.root / "governance" / "cli.py").exists(),
                "governance/cli.py",
            ),
            CheckResult(
                "feature_registered",
                registered["registered"],
                registered["detail"],
                registered,
            ),
            CheckResult(
                "transition_allowed",
                bool(transition.get("valid")),
                policy_error or str(transition.get("action_type") or "not evaluated"),
                transition,
            ),
        ]

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        command = plan.get("command", [])
        outputs = {
            "dryRun": True,
            "mutationCommitted": False,
            "command": command,
            "wouldTransition": {
                "feature": plan.get("feature"),
                "from": plan.get("from_state"),
                "to": plan.get("target_state"),
                "action": plan.get("transition", {}).get("action_type"),
            },
        }
        context.write_json("governance_command.json", outputs)
        return outputs

    def execute(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        state_before = _read_state_source(context, plan.get("feature"))
        context.write_json("pre_governance_state.json", state_before)

        command = plan.get("command", [])
        result = run_command(command, cwd=context.root, timeout_seconds=300)
        command_record = {"command": command, "result": result}
        context.write_json("governance_command.json", command_record)
        if int(result.get("returnCode", 1)) != 0:
            raise TaskFailure(
                "governance command failed",
                evidence=command_record,
            )
        return {
            "command": command,
            "result": result,
            "stateBefore": state_before,
        }

    def postflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        outputs: dict[str, Any],
    ) -> list[CheckResult]:
        result = outputs.get("result")
        command_succeeded = result is None or int(result.get("returnCode", 1)) == 0
        state_after = _read_state_source(context, plan.get("feature"))
        state_readable = bool(state_after.get("readable"))
        actual_state = state_after.get("state")
        target_state = plan.get("target_state")
        state_matches = (
            actual_state == target_state if state_readable else True
        )

        return [
            CheckResult(
                "governance_cli_command_succeeded",
                command_succeeded,
                str(result.get("returnCode"))
                if isinstance(result, dict)
                else "no command result",
                result if isinstance(result, dict) else {},
            ),
            CheckResult(
                "final_state_matches_target",
                state_matches,
                (
                    f"actual={actual_state} target={target_state}"
                    if state_readable
                    else "no readable state source"
                ),
                state_after,
            ),
            CheckResult(
                "ledger_written",
                (context.run_dir / "run_record.json").exists(),
                "run_record.json",
            ),
        ]


def _arg(context: TaskContext, name: str) -> str | None:
    value = getattr(context.args, name, None)
    return str(value) if value not in (None, "") else None


def _transition(
    feature: str | None,
    from_state: str | None,
    target_state: str | None,
) -> dict[str, Any]:
    transition: dict[str, Any] = {
        "valid": False,
        "action_type": None,
        "cli_subcommand": None,
        "ack_risk_token": None,
        "allowed_states": [],
        "policy_error": None,
    }
    if not feature or not from_state or not target_state:
        return transition

    try:
        states = allowed_states_for_flag(feature)
        transition["allowed_states"] = list(states)
        if from_state in states and target_state in states:
            from_idx = states.index(from_state)
            target_idx = states.index(target_state)
            if target_idx < from_idx:
                action_type = "feature_demote"
                cli_subcommand = "demote"
                ack_token = GOVERNANCE_ROLLBACK_ACK
            else:
                action_type = "feature_promote"
                cli_subcommand = "promote"
                ack_token = GOVERNANCE_PROMOTE_ACK
            transition.update(
                {
                    "action_type": action_type,
                    "cli_subcommand": cli_subcommand,
                    "ack_risk_token": ack_token,
                }
            )
        else:
            action_type = "feature_promote"
        validate_transition(action_type, feature, from_state, target_state)
    except GovernanceStatePolicyError as exc:
        transition["policy_error"] = str(exc)
        return transition

    transition["valid"] = True
    return transition


def _governance_command(
    context: TaskContext,
    transition: dict[str, Any],
    reason: str | None,
) -> list[str]:
    feature = _arg(context, "feature")
    from_state = _arg(context, "from_state")
    target_state = _arg(context, "target_state")
    cli_subcommand = transition.get("cli_subcommand")
    if not feature or not from_state or not target_state or not cli_subcommand:
        return []

    command = [
        sys.executable,
        "-m",
        "governance",
        "feature",
        str(cli_subcommand),
        feature,
        "--from",
        from_state,
        "--to",
        target_state,
        "--reason",
        reason or "",
    ]
    _append_optional(command, "--direct-db", _arg(context, "direct_db"))
    if cli_subcommand == "promote":
        _append_optional(
            command,
            "--regret-check-date",
            _arg(context, "regret_check_date"),
        )
        _append_optional(command, "--effective-at", _arg(context, "effective_at"))
        _append_optional(command, "--repair-source", _arg(context, "repair_source"))
    else:
        _append_optional(
            command,
            "--rollback-ticket",
            _arg(context, "rollback_ticket"),
        )
        _append_optional(command, "--incident-id", _arg(context, "incident_id"))
    return command


def _append_optional(command: list[str], flag: str, value: str | None) -> None:
    if value:
        command.extend([flag, value])


def _rollback_command(
    context: TaskContext,
    feature: str | None,
    from_state: str | None,
    target_state: str | None,
) -> list[str]:
    if not feature or not from_state or not target_state:
        return []
    rollback_transition = _transition(feature, from_state, target_state)
    return _governance_command(context, rollback_transition, _arg(context, "reason"))


def _registered_detail(feature: Any) -> dict[str, Any]:
    if not feature:
        return {"registered": False, "detail": "missing feature"}
    try:
        ensure_registered_flag(str(feature))
    except GovernanceStatePolicyError as exc:
        return {"registered": False, "detail": str(exc)}
    return {"registered": True, "detail": "registered"}


def _affected_files(context: TaskContext) -> list[str]:
    direct_db = context.resolve(getattr(context.args, "direct_db", None))
    return [str(direct_db)] if direct_db else []


def _state_source_path(context: TaskContext) -> str | None:
    path = context.resolve(getattr(context.args, "state_source", None))
    return str(path) if path else None


def _read_state_source(context: TaskContext, feature: Any) -> dict[str, Any]:
    path = context.resolve(getattr(context.args, "state_source", None))
    evidence: dict[str, Any] = {
        "path": str(path) if path else None,
        "readable": False,
        "feature": feature,
        "state": None,
        "detail": "no state source configured",
    }
    if path is None:
        return evidence
    if not path.exists() or not path.is_file():
        evidence["detail"] = "state source missing"
        return evidence

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        evidence["detail"] = str(exc)
        return evidence

    state = _extract_feature_state(data, str(feature) if feature else "")
    evidence.update(
        {
            "readable": True,
            "state": state,
            "actual_state": state,
            "detail": "state source readable",
        }
    )
    return evidence


def _extract_feature_state(data: Any, feature: str) -> str | None:
    if not isinstance(data, dict):
        return None
    value = data.get(feature)
    if value is None:
        value = data.get(feature.lower())
    if isinstance(value, dict):
        state = value.get("state")
        return str(state) if state is not None else None
    if value is not None:
        return str(value)
    return None

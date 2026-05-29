from __future__ import annotations

import argparse
import json
import sys
import time
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
        parser.add_argument("--state-verify-attempts", type=int, default=3)
        parser.add_argument("--state-verify-delay-seconds", type=float, default=1.0)

    def plan(self, context: TaskContext) -> dict[str, Any]:
        feature = _arg(context, "feature")
        from_state = _arg(context, "from_state")
        target_state = _arg(context, "target_state")
        reason = _arg(context, "reason")
        transition = _transition(feature, from_state, target_state)
        state_source = _state_source_path(context)
        command = _governance_command(
            context,
            transition,
            reason,
            feature=feature,
            from_state=from_state,
            target_state=target_state,
        )

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
                "state_source": state_source,
                "state_verification": {
                    "attempts": _state_verify_attempts(context),
                    "delay_seconds": _state_verify_delay_seconds(context),
                    "state_source": state_source,
                },
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
                    "state_source_readable",
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
                        from_state,
                        target_state,
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
        state_source = _read_state_source(
            context,
            feature,
            plan.get("state_source"),
        )
        state_source_required = context.mode == "execute"
        state_source_passed = (
            bool(state_source.get("readable")) if state_source_required else True
        )

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
            CheckResult(
                "state_source_readable",
                state_source_passed,
                str(state_source.get("detail") or "")
                if state_source_required
                else "not required outside execute mode",
                state_source,
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
        state_before = _read_state_source(
            context,
            plan.get("feature"),
            plan.get("state_source"),
        )
        context.write_json("pre_governance_state.json", state_before)
        if not state_before.get("readable"):
            raise TaskFailure(
                "governance state source is not readable",
                evidence=state_before,
            )

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
        state_check = (
            _verify_final_state(context, plan)
            if context.mode == "execute"
            else CheckResult(
                "final_state_matches_target",
                True,
                "not required outside execute mode",
                {
                    "skipped": True,
                    "mode": context.mode,
                    "path": plan.get("state_source"),
                },
            )
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
            state_check,
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
    *,
    feature: str | None,
    from_state: str | None,
    target_state: str | None,
) -> list[str]:
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
    rollback_from_state = target_state
    rollback_target_state = from_state
    rollback_transition = _transition(
        feature,
        rollback_from_state,
        rollback_target_state,
    )
    return _governance_command(
        context,
        rollback_transition,
        _arg(context, "reason"),
        feature=feature,
        from_state=rollback_from_state,
        target_state=rollback_target_state,
    )


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
    raw_path = getattr(context.args, "state_source", None)
    if raw_path in (None, ""):
        return None
    path = context.resolve(raw_path)
    return str(path) if path else None


def _read_state_source(
    context: TaskContext,
    feature: Any,
    state_source: Any = None,
) -> dict[str, Any]:
    raw_path = (
        state_source
        if state_source not in (None, "")
        else getattr(context.args, "state_source", None)
    )
    path = context.resolve(raw_path) if raw_path not in (None, "") else None
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


def _verify_final_state(
    context: TaskContext,
    plan: dict[str, Any],
) -> CheckResult:
    target_state = plan.get("target_state")
    attempts_count = _state_verify_attempts(context)
    delay_seconds = _state_verify_delay_seconds(context)
    attempts: list[dict[str, Any]] = []

    for attempt_number in range(1, attempts_count + 1):
        attempt = _read_state_source(
            context,
            plan.get("feature"),
            plan.get("state_source"),
        )
        attempt["attempt"] = attempt_number
        attempts.append(attempt)
        if attempt.get("readable") and attempt.get("state") == target_state:
            break
        if attempt_number < attempts_count and delay_seconds > 0:
            time.sleep(delay_seconds)

    final_attempt = attempts[-1]
    actual_state = final_attempt.get("state")
    passed = bool(final_attempt.get("readable")) and actual_state == target_state
    evidence = {
        "path": final_attempt.get("path"),
        "feature": plan.get("feature"),
        "target_state": target_state,
        "actual_state": actual_state,
        "readable": bool(final_attempt.get("readable")),
        "attempts_configured": attempts_count,
        "delay_seconds": delay_seconds,
        "attempts": attempts,
    }
    context.write_json("state_verification.json", evidence)
    detail = (
        f"actual={actual_state} target={target_state}"
        if final_attempt.get("readable")
        else f"state source unreadable: {final_attempt.get('detail')}"
    )
    return CheckResult(
        "final_state_matches_target",
        passed,
        detail,
        evidence,
    )


def _state_verify_attempts(context: TaskContext) -> int:
    value = getattr(context.args, "state_verify_attempts", 3)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 3


def _state_verify_delay_seconds(context: TaskContext) -> float:
    value = getattr(context.args, "state_verify_delay_seconds", 1.0)
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 1.0


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

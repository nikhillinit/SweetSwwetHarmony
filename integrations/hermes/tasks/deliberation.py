from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from integrations.hermes.adapters import (
    ExecutorResult,
    build_prompt_packet,
    build_reviewer_executor,
)
from integrations.hermes.deliberation_policy import (
    evaluate_reviewer_policy,
    quorum_record_evidence,
    reviewer_policy_from_plan,
)
from integrations.hermes.plan_contract import CURRENT_CONTRACT_VERSION

from .base import (
    CheckResult,
    HermesTask,
    TaskContext,
    run_async_blocking,
    sha256_file,
)

VALID_VERDICTS = {"approve", "block", "needs_changes", "skip"}
DEFAULT_PANEL = "codex,kimi,gemini"
TASK_TEXT_LIMIT = 12000
HIGH_RISK_APPROVAL_QUORUM = 2


class DeliberationTask(HermesTask):
    name = "deliberate"
    description = "Multi-reviewer plan deliberation artifact generator."
    risk_level = "high"
    supported_modes = ("plan-only", "preflight-only", "dry-run", "execute")
    required_locks = ()
    mutates_external_systems = False
    ledger_backed = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--plan")
        parser.add_argument("--task-text")
        parser.add_argument("--panel", default=DEFAULT_PANEL)
        parser.add_argument("--rounds", type=int, default=1)
        parser.add_argument("--synthesizer", default="gemini")
        parser.add_argument("--coding-pair", action="store_true")

    def plan(self, context: TaskContext) -> dict[str, Any]:
        plan_path = _plan_path(context)
        task_text, input_error = _task_text(context, plan_path)
        panel = _panel(context)
        available_reviewers = _available_reviewers(context, panel)

        plan = self._base_plan(context)
        plan.update(
            {
                "input": {
                    "plan_path": str(plan_path) if plan_path else None,
                    "plan_hash": _plan_hash(plan_path),
                    "task_text": task_text,
                    "task_text_chars": len(task_text),
                    "error": input_error,
                },
                "reviewers": {
                    "requested": panel,
                    "available": available_reviewers,
                    "skipped": _skipped_reviewers(context, panel),
                    "rounds": max(1, int(getattr(context.args, "rounds", 1) or 1)),
                    "synthesizer": getattr(context.args, "synthesizer", "gemini"),
                    "coding_pair": bool(getattr(context.args, "coding_pair", False)),
                },
                "artifacts": {
                    "record": "deliberation_record.json",
                    "markdown": "deliberation.md",
                },
                "preflight_gates": [
                    "input_plan_or_task_exists",
                    "selected_panel_has_available_reviewer",
                    "reviewers_are_non_mutating",
                    "prompt_packet_redacted",
                ],
                "postflight_gates": [
                    "reviewers_returned_valid_verdicts",
                    "quorum_completed",
                    "no_blocker_or_dissent_verdict",
                    "deliberation_artifacts_written",
                    "ledger_written",
                ],
                "mutation": {
                    "allowed": False,
                    "affected_files": [],
                    "affected_tables": [],
                    "external_systems": [],
                    "ledger_artifacts": [
                        "deliberation_record.json",
                        "deliberation.md",
                        "run_record.json",
                    ],
                },
            }
        )
        return plan

    def preflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> list[CheckResult]:
        task_text = str(plan.get("input", {}).get("task_text") or "")
        plan_hash = plan.get("input", {}).get("plan_hash")
        available = list(plan.get("reviewers", {}).get("available") or [])
        prompt = _prompt_packet(plan)

        return [
            CheckResult(
                "input_plan_or_task_exists",
                bool(task_text.strip() or plan_hash),
                str(plan.get("input", {}).get("plan_path") or "inline task"),
                dict(plan.get("input", {})),
            ),
            CheckResult(
                "selected_panel_has_available_reviewer",
                bool(available),
                ",".join(available) if available else "no enabled reviewers",
                dict(plan.get("reviewers", {})),
            ),
            CheckResult(
                "reviewers_are_non_mutating",
                plan.get("mutation", {}).get("allowed") is False
                and not plan.get("mutation", {}).get("external_systems"),
                "reviewers receive prompt packets only",
                dict(plan.get("mutation", {})),
            ),
            CheckResult(
                "prompt_packet_redacted",
                "Do not mutate files" in prompt,
                "review prompt forbids mutation",
                {"prompt_chars": len(prompt)},
            ),
        ]

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        record = run_async_blocking(_run_panel(context, plan))
        _write_deliberation_artifacts(context, record)
        return record

    def execute(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        return self.dry_run(context, plan)

    def postflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        outputs: dict[str, Any],
    ) -> list[CheckResult]:
        panel = list(outputs.get("panel") or [])
        consensus = dict(outputs.get("consensus") or {})
        quorum = dict(consensus.get("quorum") or {})
        blockers = list(consensus.get("blockers") or [])
        dissent = bool(consensus.get("dissent", {}).get("present"))
        active_count = len(list(quorum.get("countedApprovals") or []))
        quorum_target = int(quorum.get("required") or HIGH_RISK_APPROVAL_QUORUM)
        record_path = context.run_dir / "deliberation_record.json"
        markdown_path = context.run_dir / "deliberation.md"

        return [
            CheckResult(
                "reviewers_returned_valid_verdicts",
                all(item.get("verdict") in VALID_VERDICTS for item in panel),
                "panel verdicts parsed",
                {"panel": panel},
            ),
            CheckResult(
                "quorum_completed",
                quorum.get("status") == "satisfied",
                f"active={active_count} required={quorum_target}",
                quorum,
            ),
            CheckResult(
                "no_blocker_or_dissent_verdict",
                not blockers and not dissent,
                f"blockers={blockers} dissent={dissent}",
                consensus,
            ),
            CheckResult(
                "deliberation_artifacts_written",
                record_path.exists() and markdown_path.exists(),
                f"{record_path.name}; {markdown_path.name}",
                {"record": str(record_path), "markdown": str(markdown_path)},
            ),
            CheckResult(
                "ledger_written",
                (context.run_dir / "run_record.json").exists(),
                "run_record.json",
            ),
        ]


async def _run_panel(context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
    prompt = _prompt_packet(plan)
    reviewers = plan.get("reviewers", {}).get("requested") or []
    panel = await asyncio.gather(
        *[_run_reviewer(context, str(name), prompt) for name in reviewers]
    )
    reviewer_policy = reviewer_policy_from_plan(plan)
    consensus = _synthesize(panel, reviewer_policy)
    created_at = datetime.now(timezone.utc)
    return {
        "contractVersion": CURRENT_CONTRACT_VERSION,
        "deliberationId": _deliberation_id(created_at),
        "createdAt": created_at.isoformat(),
        "task": DeliberationTask.name,
        "mode": context.mode,
        "dryRun": context.mode == "dry-run",
        "mutationCommitted": False,
        "artifactCommit": {
            "ledgerOnly": True,
            "runtimeState": False,
            "externalSystems": False,
        },
        "input": {
            "planPath": plan.get("input", {}).get("plan_path"),
            "planHash": plan.get("input", {}).get("plan_hash"),
            "taskTextChars": plan.get("input", {}).get("task_text_chars", 0),
        },
        "panel": panel,
        "reviewerPolicy": reviewer_policy,
        "synthesizer": {
            "executor": plan.get("reviewers", {}).get("synthesizer"),
            "strategy": "deterministic-majority-with-dissent",
        },
        "consensus": consensus,
        "freshnessTtlSeconds": 86400,
    }


async def _run_reviewer(
    context: TaskContext,
    name: str,
    prompt: str,
) -> dict[str, Any]:
    config = context.config
    if config is None:
        return _skipped_result(name, "config_missing")
    if name in config.deferred_executors:
        return _skipped_result(name, "provider_deferred")
    if name not in config.executors:
        return _skipped_result(name, "unknown_executor")
    if not config.executors[name].enabled:
        return _skipped_result(name, "provider_disabled")

    try:
        executor = build_reviewer_executor(name, config)
        result = await executor.execute(prompt, context_files=None)
    except Exception as exc:
        return _skipped_result(name, str(exc))

    payload = _parse_reviewer_payload(result)
    if not result.success:
        payload["verdict"] = "skip"
        payload["parsed"] = False
    payload.update(
        {
            "executor": name,
            "success": result.success,
            "exitCode": result.exit_code,
            "durationMs": result.duration_ms,
            "tokenUsage": result.token_usage,
        }
    )
    if result.error:
        payload["error"] = result.error
    return payload


def _write_deliberation_artifacts(context: TaskContext, record: dict[str, Any]) -> None:
    context.write_json("deliberation_record.json", record)
    context.write_text("deliberation.md", _deliberation_markdown(record))


def _prompt_packet(plan: dict[str, Any]) -> str:
    return build_prompt_packet(
        title="Hermes deliberation review",
        body=str(plan.get("input", {}).get("task_text") or ""),
        required_json_keys=[
            "verdict",
            "confidence",
            "concerns",
            "required_changes",
        ],
    )


def _parse_reviewer_payload(result: ExecutorResult) -> dict[str, Any]:
    content = result.content.strip()
    parsed, parsed_ok = _parse_json_object(content)
    if not parsed_ok:
        parsed = _classify_text_response(content)

    verdict = str(parsed.get("verdict", "skip")).lower()
    if verdict not in VALID_VERDICTS:
        verdict = "needs_changes"

    return {
        "verdict": verdict,
        "parsed": parsed_ok,
        "confidence": _float_value(parsed.get("confidence")),
        "concerns": _string_list(parsed.get("concerns")),
        "requiredChanges": _string_list(parsed.get("required_changes")),
        "contentExcerpt": content[:1000],
    }


def _parse_json_object(content: str) -> tuple[dict[str, Any], bool]:
    if not content:
        return {}, False
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}, False
    return (parsed, True) if isinstance(parsed, dict) else ({}, False)


def _classify_text_response(content: str) -> dict[str, Any]:
    lower = content.lower()
    if not content:
        return {"verdict": "skip", "confidence": 0.0}
    if "block" in lower or "reject" in lower:
        return {
            "verdict": "block",
            "confidence": 0.5,
            "concerns": [content[:500]],
        }
    if "needs" in lower and "change" in lower:
        return {
            "verdict": "needs_changes",
            "confidence": 0.5,
            "concerns": [content[:500]],
        }
    return {
        "verdict": "needs_changes",
        "confidence": 0.5,
        "concerns": [content[:500]],
    }


def _synthesize(
    panel: list[dict[str, Any]],
    reviewer_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active = [item for item in panel if item.get("verdict") != "skip"]
    blockers = [
        item
        for item in active
        if item.get("verdict") in {"block", "needs_changes"}
    ]
    quorum = evaluate_reviewer_policy(
        panel,
        reviewer_policy or reviewer_policy_from_plan({"task": DeliberationTask.name}),
    )
    persisted_quorum = quorum_record_evidence(quorum)
    verdicts = {item.get("verdict") for item in active}
    dissent_present = len(verdicts) > 1

    if blockers:
        status = "blocked"
    elif dissent_present:
        status = "conflicted"
    elif persisted_quorum.get("status") == "satisfied":
        status = "approved"
    else:
        status = "no_quorum"

    return {
        "status": status,
        "blockers": [str(item.get("executor")) for item in blockers],
        "dissent": {
            "present": dissent_present,
            "summary": _dissent_summary(active) if dissent_present else "",
        },
        "quorum": persisted_quorum,
        "overrideAllowed": status != "approved",
        "overrideAckToken": "DELIBERATION_OVERRIDE" if status != "approved" else None,
    }


def _dissent_summary(active: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{item.get('executor')}={item.get('verdict')}" for item in active
    )


def _deliberation_markdown(record: dict[str, Any]) -> str:
    lines = [
        "# Hermes Deliberation",
        "",
        f"Consensus: {record.get('consensus', {}).get('status')}",
        "",
    ]
    for item in record.get("panel", []):
        lines.extend(
            [
                f"## {item.get('executor')}",
                f"- Verdict: {item.get('verdict')}",
                f"- Success: {item.get('success')}",
                "",
            ]
        )
        concerns = item.get("concerns") or []
        if concerns:
            lines.append("Concerns:")
            lines.extend(f"- {concern}" for concern in concerns)
            lines.append("")
    return "\n".join(lines) + "\n"


def _plan_path(context: TaskContext) -> Path | None:
    return context.resolve(getattr(context.args, "plan", None))


def _task_text(context: TaskContext, plan_path: Path | None) -> tuple[str, str | None]:
    inline_text = str(getattr(context.args, "task_text", None) or "")
    if plan_path is None:
        return inline_text[:TASK_TEXT_LIMIT], None
    if not plan_path.exists() or not plan_path.is_file():
        return inline_text[:TASK_TEXT_LIMIT], "plan file missing"
    try:
        return plan_path.read_text(encoding="utf-8")[:TASK_TEXT_LIMIT], None
    except OSError as exc:
        return inline_text[:TASK_TEXT_LIMIT], str(exc)


def _plan_hash(plan_path: Path | None) -> str | None:
    if plan_path is None or not plan_path.exists() or not plan_path.is_file():
        return None
    return sha256_file(plan_path)


def _panel(context: TaskContext) -> list[str]:
    raw = str(getattr(context.args, "panel", DEFAULT_PANEL) or "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _available_reviewers(context: TaskContext, panel: list[str]) -> list[str]:
    if context.config is None:
        return []
    return [
        name
        for name in panel
        if name in context.config.executors and context.config.executors[name].enabled
    ]


def _skipped_reviewers(context: TaskContext, panel: list[str]) -> list[dict[str, str]]:
    if context.config is None:
        return [{"executor": name, "reason": "config_missing"} for name in panel]
    skipped: list[dict[str, str]] = []
    for name in panel:
        if name in context.config.deferred_executors:
            skipped.append({"executor": name, "reason": "provider_deferred"})
        elif name not in context.config.executors:
            skipped.append({"executor": name, "reason": "unknown_executor"})
        elif not context.config.executors[name].enabled:
            skipped.append({"executor": name, "reason": "provider_disabled"})
    return skipped


def _skipped_result(name: str, reason: str) -> dict[str, Any]:
    return {
        "executor": name,
        "verdict": "skip",
        "parsed": False,
        "success": False,
        "error": reason,
        "confidence": 0.0,
        "concerns": [],
        "requiredChanges": [],
        "contentExcerpt": "",
    }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _deliberation_id(created_at: datetime | None = None) -> str:
    stamp = (created_at or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"deliberate-{stamp}"

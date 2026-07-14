from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from integrations.hermes.adapters import ExecutorResult
from integrations.hermes.plan_contract import CURRENT_CONTRACT_VERSION
from integrations.hermes.tasks.base import EXIT_GATE_FAILURE
from integrations.hermes.tasks.deliberation import (
    _classify_text_response,
    _parse_reviewer_payload,
    _synthesize,
)
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


def _config_path(
    tmp_path: Path,
    *,
    include_disabled: bool = False,
    include_gemini_reviewer: bool = False,
) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = []
    if include_disabled:
        data["executors"]["antigravity"] = {
            "provider": "antigravity",
            "displayName": "Google Antigravity",
            "enabled": False,
            "required": False,
            "binary": "antigravity",
            "env": [],
            "supportsExecute": False,
        }
    if include_gemini_reviewer:
        data["deferredExecutors"] = {}
        data["executors"]["gemini"] = {
            "provider": "gemini",
            "displayName": "Gemini CLI",
            "enabled": True,
            "required": False,
            "binary": "gemini",
            "env": [],
            "supportsExecute": False,
        }
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _args(
    tmp_path: Path,
    *,
    mode: str = "preflight-only",
    task_text: str | None = "Review this Hermes plan.",
    plan: Path | None = None,
    panel: str = "codex,kimi",
    include_disabled: bool = False,
    include_gemini_reviewer: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="deliberate",
        config=str(
            _config_path(
                tmp_path,
                include_disabled=include_disabled,
                include_gemini_reviewer=include_gemini_reviewer,
            )
        ),
        plan_only=mode == "plan-only",
        preflight_only=mode == "preflight-only",
        dry_run=mode == "dry-run",
        execute=mode == "execute",
        ack_risk=None,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="test",
        json_output=False,
        plan=str(plan) if plan else None,
        task_text=task_text,
        panel=panel,
        rounds=1,
        synthesizer="codex",
        coding_pair=False,
    )


class _FakeReviewer:
    def __init__(
        self,
        name: str,
        payload: dict[str, Any],
        calls: list[str],
        *,
        mode: str = "ok",
    ) -> None:
        self.name = name
        self.payload = payload
        self.calls = calls
        self.mode = mode

    async def execute(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> ExecutorResult:
        self.calls.append(self.name)
        assert "Do not mutate files" in prompt
        assert context_files is None
        if self.mode == "raise":
            raise RuntimeError("reviewer spawn blew up")
        if self.mode == "fail":
            return ExecutorResult(
                executor=self.name,
                success=False,
                exit_code=1,
                content="",
                duration_ms=54704,
                error="ERROR: model requires a newer version of Codex",
            )
        if self.mode == "empty":
            return ExecutorResult(
                executor=self.name,
                success=True,
                exit_code=0,
                content="",
                duration_ms=12,
                token_usage={},
            )
        return ExecutorResult(
            executor=self.name,
            success=True,
            exit_code=0,
            content=json.dumps(self.payload),
            duration_ms=12,
            token_usage={"total_tokens": 9},
        )


def _patch_reviewers(
    monkeypatch: pytest.MonkeyPatch,
    payloads: dict[str, dict[str, Any]],
    modes: dict[str, str] | None = None,
) -> list[str]:
    calls: list[str] = []

    def fake_build_reviewer_executor(name: str, *_: Any, **__: Any) -> _FakeReviewer:
        return _FakeReviewer(
            name,
            payloads.get(name, {}),
            calls,
            mode=(modes or {}).get(name, "ok"),
        )

    monkeypatch.setattr(
        "integrations.hermes.tasks.deliberation.build_reviewer_executor",
        fake_build_reviewer_executor,
    )
    return calls


def _executor_result(content: str, *, success: bool = True) -> ExecutorResult:
    return ExecutorResult(
        executor="codex",
        success=success,
        exit_code=0 if success else 1,
        content=content,
        duration_ms=12,
        token_usage={"total_tokens": 9},
    )


def _approval(executor: str) -> dict[str, Any]:
    return {
        "executor": executor,
        "success": True,
        "parsed": True,
        "verdict": "approve",
        "confidence": 0.9,
        "concerns": [],
        "requiredChanges": [],
        "contentExcerpt": "",
    }


def test_empty_reviewer_content_yields_error_and_cannot_approve_alone() -> None:
    payload = _parse_reviewer_payload(_executor_result(""))

    assert payload["verdict"] == "error"
    assert payload["parsed"] is False
    consensus = _synthesize([{"executor": "codex", "success": True, **payload}])
    assert consensus["status"] == "no_quorum"
    assert consensus["quorum"]["status"] == "malformed_reviewer_output"
    assert consensus["quorum"]["malformedReviewers"] == ["codex"]


def test_malformed_non_empty_reviewer_content_blocks_approval() -> None:
    payload = _parse_reviewer_payload(_executor_result("{not json"))

    assert payload["verdict"] == "needs_changes"
    assert payload["parsed"] is False
    consensus = _synthesize([_approval("codex"), {"executor": "kimi", "success": True, **payload}])
    assert consensus["status"] == "blocked"
    assert consensus["blockers"] == ["kimi"]
    assert consensus["quorum"]["malformedReviewers"] == ["kimi"]


def test_fallback_text_classifier_never_approves() -> None:
    assert _classify_text_response("approve this plan")["verdict"] == "needs_changes"
    assert _classify_text_response("block this plan")["verdict"] == "block"
    assert _classify_text_response("")["verdict"] == "error"


def test_invalid_verdict_does_not_count_as_approval() -> None:
    payload = _parse_reviewer_payload(
        _executor_result(
            json.dumps(
                {
                    "verdict": "ship_it",
                    "confidence": 0.9,
                    "concerns": [],
                    "required_changes": [],
                }
            )
        )
    )

    assert payload["parsed"] is True
    assert payload["verdict"] == "needs_changes"
    consensus = _synthesize([_approval("codex"), {"executor": "kimi", "success": True, **payload}])
    assert consensus["status"] == "blocked"


def test_fenced_json_reviewer_payload_counts_as_parsed_approval() -> None:
    payload = _parse_reviewer_payload(
        _executor_result(
            """```json
{"verdict":"approve","confidence":0.91,"concerns":[],"required_changes":[]}
```"""
        )
    )

    assert payload["parsed"] is True
    assert payload["verdict"] == "approve"


def test_approved_with_concerns_without_required_changes_normalizes_to_approval() -> None:
    payload = _parse_reviewer_payload(
        _executor_result(
            json.dumps(
                {
                    "verdict": "approved_with_concerns",
                    "confidence": 0.86,
                    "concerns": ["watch operator copy"],
                    "required_changes": [],
                }
            )
        )
    )

    assert payload["parsed"] is True
    assert payload["verdict"] == "approve"
    assert payload["concerns"] == ["watch operator copy"]
    consensus = _synthesize([_approval("codex"), {"executor": "kimi", "success": True, **payload}])
    assert consensus["status"] == "approved"


def test_approved_with_none_required_changes_normalizes_to_approval() -> None:
    payload = _parse_reviewer_payload(
        _executor_result(
            json.dumps(
                {
                    "verdict": "approved",
                    "confidence": "high",
                    "concerns": "sidecar gate should stay visible",
                    "required_changes": "none",
                }
            )
        )
    )

    assert payload["parsed"] is True
    assert payload["verdict"] == "approve"
    assert payload["requiredChanges"] == []
    consensus = _synthesize([_approval("codex"), {"executor": "kimi", "success": True, **payload}])
    assert consensus["status"] == "approved"


def test_approved_with_concerns_with_required_changes_blocks_approval() -> None:
    payload = _parse_reviewer_payload(
        _executor_result(
            json.dumps(
                {
                    "verdict": "approved_with_concerns",
                    "confidence": 0.86,
                    "concerns": ["missing rollback note"],
                    "requiredChanges": ["add rollback note"],
                }
            )
        )
    )

    assert payload["parsed"] is True
    assert payload["verdict"] == "needs_changes"
    assert payload["requiredChanges"] == ["add rollback note"]
    consensus = _synthesize([_approval("codex"), {"executor": "kimi", "success": True, **payload}])
    assert consensus["status"] == "blocked"


def test_high_risk_deliberation_requires_two_valid_approvals() -> None:
    assert _synthesize([_approval("codex")])["status"] == "no_quorum"
    assert _synthesize([_approval("codex"), _approval("kimi")])["status"] == "approved"


def test_untrusted_reviewer_identity_cannot_satisfy_high_risk_quorum() -> None:
    consensus = _synthesize([_approval("codex"), _approval("claude")])

    assert consensus["status"] == "no_quorum"
    assert consensus["quorum"]["status"] == "insufficient_quorum"
    assert consensus["quorum"]["required"] == 2
    assert consensus["quorum"]["countedApprovals"] == ["codex"]
    assert consensus["quorum"]["untrustedApprovals"] == ["claude"]


def test_skip_is_neutral_for_approval_quorum() -> None:
    consensus = _synthesize(
        [
            _approval("codex"),
            {
                "executor": "gemini",
                "success": False,
                "parsed": False,
                "verdict": "skip",
            },
            _approval("kimi"),
        ]
    )

    assert consensus["status"] == "approved"
    assert consensus["dissent"]["present"] is False


def test_executed_but_failed_reviewer_lane_is_error_and_fails_run_despite_quorum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for 2026-07-10: a hard-failed codex lane silently became
    'skip', shrinking the panel without any alarm. Even when the remaining
    trusted lanes satisfy quorum, an executed-but-failed lane must fail the
    run loudly."""
    _patch_reviewers(
        monkeypatch,
        {
            "kimi": {"verdict": "approve", "confidence": 0.9, "concerns": []},
            "gemini": {"verdict": "approve", "confidence": 0.9, "concerns": []},
        },
        modes={"codex": "fail"},
    )

    result = run_registered_task(
        _args(
            tmp_path,
            mode="dry-run",
            panel="codex,kimi,gemini",
            include_gemini_reviewer=True,
        )
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "dry_run_failed"
    verdicts = {item["executor"]: item["verdict"] for item in result.outputs["panel"]}
    assert verdicts == {"codex": "error", "kimi": "approve", "gemini": "approve"}
    check = next(c for c in result.checks if c.name == "no_reviewer_lane_errors")
    assert check.passed is False
    assert [e["executor"] for e in check.evidence["laneErrors"]] == ["codex"]
    quorum = result.outputs["consensus"]["quorum"]
    assert quorum["status"] == "malformed_reviewer_output"
    assert "codex" in quorum["malformedReviewers"]


def test_reviewer_exception_is_error_lane_not_silent_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_reviewers(
        monkeypatch,
        {"codex": {"verdict": "approve", "confidence": 0.9, "concerns": []}},
        modes={"kimi": "raise"},
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run", panel="codex,kimi"))

    assert result.exit_code == EXIT_GATE_FAILURE
    verdicts = {item["executor"]: item["verdict"] for item in result.outputs["panel"]}
    assert verdicts["kimi"] == "error"
    check = next(c for c in result.checks if c.name == "no_reviewer_lane_errors")
    assert check.passed is False
    kimi_item = next(i for i in result.outputs["panel"] if i["executor"] == "kimi")
    assert "reviewer spawn blew up" in (kimi_item.get("error") or "")


def test_empty_content_success_lane_is_error_with_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_reviewers(
        monkeypatch,
        {"codex": {"verdict": "approve", "confidence": 0.9, "concerns": []}},
        modes={"kimi": "empty"},
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run", panel="codex,kimi"))

    assert result.exit_code == EXIT_GATE_FAILURE
    kimi_item = next(i for i in result.outputs["panel"] if i["executor"] == "kimi")
    assert kimi_item["verdict"] == "error"
    assert kimi_item["success"] is True
    assert kimi_item["error"] == "empty_reviewer_content"
    check = next(c for c in result.checks if c.name == "no_reviewer_lane_errors")
    assert check.passed is False


def test_error_lanes_do_not_create_phantom_dissent() -> None:
    error_lane = {
        "executor": "codex",
        "success": False,
        "parsed": False,
        "verdict": "error",
    }
    consensus = _synthesize([_approval("kimi"), _approval("gemini"), error_lane])

    assert consensus["dissent"]["present"] is False
    assert consensus["blockers"] == []
    assert consensus["status"] == "no_quorum"
    assert consensus["quorum"]["status"] == "malformed_reviewer_output"


def test_preexecution_skips_stay_neutral_and_do_not_fail_lane_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_reviewers(
        monkeypatch,
        {
            "codex": {"verdict": "approve", "confidence": 0.9, "concerns": []},
            "kimi": {"verdict": "approve", "confidence": 0.9, "concerns": []},
        },
    )

    result = run_registered_task(
        _args(
            tmp_path,
            mode="dry-run",
            panel="missing,antigravity,codex,kimi",
            include_disabled=True,
        )
    )

    assert result.exit_code == 0
    check = next(c for c in result.checks if c.name == "no_reviewer_lane_errors")
    assert check.passed is True
    verdicts = {item["executor"]: item["verdict"] for item in result.outputs["panel"]}
    assert verdicts["missing"] == "skip"
    assert verdicts["antigravity"] == "skip"


def test_one_approval_plus_needs_changes_does_not_approve() -> None:
    consensus = _synthesize(
        [
            _approval("codex"),
            {
                "executor": "kimi",
                "success": True,
                "parsed": True,
                "verdict": "needs_changes",
            },
        ]
    )

    assert consensus["status"] == "blocked"


def test_one_valid_approval_high_risk_dry_run_fails_no_quorum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_reviewers(
        monkeypatch,
        {"codex": {"verdict": "approve", "confidence": 0.95, "concerns": []}},
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run", panel="codex"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "dry_run_failed"
    assert result.outputs["consensus"]["status"] == "no_quorum"
    check = next(check for check in result.checks if check.name == "quorum_completed")
    assert check.passed is False
    assert check.evidence["countedApprovals"] == ["codex"]
    assert check.evidence["required"] == 2
    assert check.evidence["status"] == "insufficient_quorum"


def test_plan_only_writes_ledger_artifacts_and_stays_non_mutating(
    tmp_path: Path,
) -> None:
    result = run_registered_task(_args(tmp_path, mode="plan-only"))

    assert result.exit_code == 0
    assert result.status == "planned"
    assert result.plan["mutation"]["allowed"] is False
    assert result.plan["mutation"]["external_systems"] == []
    assert result.plan["artifacts"]["record"] == "deliberation_record.json"
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "task_plan.json").exists()
    assert (run_dir / "run_record.json").exists()
    assert (run_dir / "plan.md").exists()


def test_missing_task_text_or_plan_fails_preflight_safely(
    tmp_path: Path,
) -> None:
    result = run_registered_task(
        _args(tmp_path, task_text=None, panel="codex"),
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(check for check in result.checks if check.name == "input_plan_or_task_exists")
    assert check.passed is False
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_unknown_and_disabled_panel_providers_are_skipped_without_external_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_reviewers(
        monkeypatch,
        {
            "codex": {"verdict": "approve", "confidence": 0.9, "concerns": []},
            "kimi": {"verdict": "approve", "confidence": 0.9, "concerns": []},
        },
    )

    result = run_registered_task(
        _args(
            tmp_path,
            mode="dry-run",
            panel="missing,antigravity,codex,kimi",
            include_disabled=True,
        )
    )

    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    assert calls == ["codex", "kimi"]
    assert result.outputs["mutationCommitted"] is False
    verdicts = {item["executor"]: item["verdict"] for item in result.outputs["panel"]}
    assert verdicts == {
        "missing": "skip",
        "antigravity": "skip",
        "codex": "approve",
        "kimi": "approve",
    }


def test_unknown_executor_skip_does_not_count_toward_quorum_in_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_reviewers(
        monkeypatch,
        {"codex": {"verdict": "approve", "confidence": 0.9, "concerns": []}},
    )

    result = run_registered_task(
        _args(tmp_path, mode="dry-run", panel="missing,codex")
    )

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "dry_run_failed"
    assert calls == ["codex"]
    assert result.outputs["consensus"]["status"] == "no_quorum"
    assert result.outputs["consensus"]["quorum"]["countedApprovals"] == ["codex"]
    assert "missing" not in result.outputs["consensus"]["quorum"]["countedApprovals"]


def test_dry_run_writes_deliberation_artifacts_under_temp_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_reviewers(
        monkeypatch,
        {
            "codex": {"verdict": "approve", "confidence": 0.95, "concerns": []},
            "kimi": {"verdict": "approve", "confidence": 0.95, "concerns": []},
        },
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run", panel="codex,kimi"))

    run_dir = Path(result.run_dir or "")
    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    assert result.outputs["consensus"]["status"] == "approved"
    assert result.outputs["contractVersion"] == CURRENT_CONTRACT_VERSION
    assert result.outputs["reviewerPolicy"]["requiredQuorum"] == 2
    assert result.outputs["reviewerPolicy"]["trustedReviewers"] == [
        "codex",
        "gemini",
        "kimi",
    ]
    assert result.outputs["consensus"]["quorum"]["status"] == "satisfied"
    assert result.outputs["consensus"]["quorum"]["countedApprovals"] == [
        "codex",
        "kimi",
    ]
    assert "policyPresent" not in result.outputs["consensus"]["quorum"]
    assert "quorumEvidencePresent" not in result.outputs["consensus"]["quorum"]
    assert result.outputs["mutationCommitted"] is False
    assert (run_dir / "deliberation_record.json").exists()
    assert (run_dir / "deliberation.md").exists()
    assert str(run_dir).startswith(str(tmp_path))
    assert not (tmp_path / "signals.db").exists()


async def test_dry_run_can_block_from_existing_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_reviewers(
        monkeypatch,
        {
            "codex": {"verdict": "approve", "confidence": 0.95, "concerns": []},
            "kimi": {"verdict": "approve", "confidence": 0.95, "concerns": []},
        },
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run", panel="codex,kimi"))

    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    assert result.outputs["consensus"]["status"] == "approved"


def test_execute_only_commits_deliberation_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_reviewers(
        monkeypatch,
        {
            "codex": {"verdict": "approve", "confidence": 0.95, "concerns": []},
            "kimi": {"verdict": "approve", "confidence": 0.95, "concerns": []},
        },
    )

    result = run_registered_task(_args(tmp_path, mode="execute", panel="codex,kimi"))

    run_dir = Path(result.run_dir or "")
    assert result.exit_code == 0
    assert result.status == "executed"
    assert result.outputs["mutationCommitted"] is False
    assert result.outputs["artifactCommit"]["ledgerOnly"] is True
    assert (run_dir / "execute.json").exists()
    assert (run_dir / "deliberation_record.json").exists()
    assert not (tmp_path / "signals.db").exists()


def test_postflight_catches_blocker_or_dissent_verdicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_reviewers(
        monkeypatch,
        {
            "codex": {"verdict": "approve", "confidence": 0.9, "concerns": []},
            "kimi": {
                "verdict": "needs_changes",
                "confidence": 0.8,
                "concerns": ["missing rollback note"],
                "required_changes": ["add rollback note"],
            },
        },
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run", panel="codex,kimi"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "dry_run_failed"
    check = next(
        check for check in result.checks if check.name == "no_blocker_or_dissent_verdict"
    )
    assert check.passed is False
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_postflight_catches_missing_deliberation_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_reviewers(
        monkeypatch,
        {"codex": {"verdict": "approve", "confidence": 0.95, "concerns": []}},
    )
    monkeypatch.setattr(
        "integrations.hermes.tasks.deliberation._write_deliberation_artifacts",
        lambda *_: None,
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run", panel="codex"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "dry_run_failed"
    check = next(
        check for check in result.checks if check.name == "deliberation_artifacts_written"
    )
    assert check.passed is False
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()

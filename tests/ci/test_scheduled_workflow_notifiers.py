"""Cross-workflow contract for scheduled-workflow failure notifiers.

All three scheduled workflows (daily pipeline, nightly litestream restore
verify, weekly thesis eval) must route failure alerts through the shared
``./.github/actions/failure-issue`` composite action, and each notifier job
must carry ``issues: write`` (the 403 regression guard).

Raw-text assertions (YAML-1.1 loaders mis-parse the ``on`` key).
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

SCHEDULED_WORKFLOWS = (
    "discovery-pipeline.yml",
    "litestream-restore-verify-nightly.yml",
    "thesis-eval.yml",
)


def _notify_job_block(workflow: str) -> str:
    # every scheduled workflow names its notifier job notify-on-failure and
    # declares it last, so the block runs to end-of-file
    return workflow.split("\n  notify-on-failure:", maxsplit=1)[1]


@pytest.mark.parametrize("workflow_file", SCHEDULED_WORKFLOWS)
def test_scheduled_workflow_notifier_uses_composite_action(workflow_file: str) -> None:
    workflow = (WORKFLOWS / workflow_file).read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "uses: ./.github/actions/failure-issue" in workflow

    notify = _notify_job_block(workflow)
    assert "uses: ./.github/actions/failure-issue" in notify
    assert "issues: write" in notify


def _close_job_block(workflow: str) -> str:
    # the recovery job is declared immediately before notify-on-failure
    block = workflow.split("\n  close-failure-tracker:", maxsplit=1)[1]
    return block.split("\n  notify-on-failure:", maxsplit=1)[0]


def _labels(block: str) -> set[str]:
    return {
        line.split("label:", maxsplit=1)[1].strip()
        for line in block.splitlines()
        if line.strip().startswith("label:")
    }


def test_failure_issue_action_supports_close_mode() -> None:
    action = (
        ROOT / ".github" / "actions" / "failure-issue" / "action.yml"
    ).read_text(encoding="utf-8")

    assert "mode:" in action
    assert "default: 'open'" in action
    assert "state: 'closed'" in action
    assert "state_reason: 'completed'" in action


@pytest.mark.parametrize("workflow_file", SCHEDULED_WORKFLOWS)
def test_scheduled_workflow_closes_tracker_on_recovery(workflow_file: str) -> None:
    """The 2026-07-12 provisioning failures left issues #300/#301 open across
    four consecutive green runs; every scheduled workflow must close its
    labeled tracker on the first green run."""
    workflow = (WORKFLOWS / workflow_file).read_text(encoding="utf-8")

    close = _close_job_block(workflow)
    assert "uses: ./.github/actions/failure-issue" in close
    assert "mode: close" in close
    assert "issues: write" in close
    assert "if: success()" in close


@pytest.mark.parametrize("workflow_file", SCHEDULED_WORKFLOWS)
def test_recovery_job_covers_every_failure_label(workflow_file: str) -> None:
    workflow = (WORKFLOWS / workflow_file).read_text(encoding="utf-8")

    notify_labels = _labels(_notify_job_block(workflow))
    close_labels = _labels(_close_job_block(workflow))
    assert notify_labels, "notifier must declare at least one label"
    assert notify_labels <= close_labels

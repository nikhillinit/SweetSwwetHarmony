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

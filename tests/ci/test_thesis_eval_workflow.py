"""Contract test for the Thesis Classification Evaluation workflow.

Locks the fixes for the weekly eval that failed 25/25 runs:
- task discovery uses the ``file.py@task`` selector (inspect_ai splits specs
  on ``@``; the old ``file.py:task`` form resolves to a nonexistent path),
- least-privilege job-level permissions (the notifier previously failed with
  HTTP 403 because the workflow declared no ``permissions:`` at all),
- the evaluate job declares and propagates a numeric accuracy output,
- infrastructure failures are reported distinctly from a genuine
  below-threshold accuracy result, and
- the notifier goes through the reusable failure-issue composite action,
  which opens or updates ONE tracking issue per label.

Raw-text assertions (YAML-1.1 loaders mis-parse the ``on`` key).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "thesis-eval.yml"
ACTION = ROOT / ".github" / "actions" / "failure-issue" / "action.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _action() -> str:
    return ACTION.read_text(encoding="utf-8")


def _job_block(workflow: str, job: str) -> str:
    block = workflow.split(f"\n  {job}:", maxsplit=1)[1]
    for other in (
        "\n  evaluate:",
        "\n  close-failure-tracker:",
        "\n  notify-on-failure:",
    ):
        cut = block.find(other)
        if cut != -1:
            block = block[:cut]
    return block


def test_workflow_exists() -> None:
    assert WORKFLOW.exists()
    assert "name: Thesis Classification Evaluation" in _workflow()


# ---------------------------------------------------------------------------
# Slice 1/6: task discovery selector shape + consolidated eval step
# ---------------------------------------------------------------------------

def test_task_selector_uses_at_separator_not_colon() -> None:
    wf = _workflow()
    # inspect_ai split_spec() rsplits on '@'; ':' is treated as part of the path
    assert "tests/evaluation/thesis_eval.py@" in wf
    assert "thesis_eval.py:" not in wf


def test_single_consolidated_eval_step_with_computed_task() -> None:
    wf = _workflow()
    assert wf.count("inspect eval") == 1
    assert 'TASK="thesis_sample_data"' in wf
    assert "dataset_exists" in wf


def test_eval_writes_json_logs_for_parsing() -> None:
    # the parse step globs logs/thesis_eval/*.json; inspect_ai defaults to
    # the binary .eval log format, so the json format must be forced
    assert "--log-format json" in _workflow()


# ---------------------------------------------------------------------------
# Slice 2: least-privilege, job-level permissions
# ---------------------------------------------------------------------------

def test_no_workflow_level_permissions_block() -> None:
    wf = _workflow()
    prologue = wf.split("\njobs:", maxsplit=1)[0]
    assert "permissions:" not in prologue


def test_evaluate_job_is_read_only() -> None:
    evaluate = _job_block(_workflow(), "evaluate")
    assert "permissions:" in evaluate
    assert "contents: read" in evaluate
    assert "issues: write" not in evaluate


def test_notifier_has_issues_write_403_regression() -> None:
    # MANDATORY 403 regression: the notifier previously ran with no
    # permissions block at all and failed HTTP 403 on issue creation
    notify = _job_block(_workflow(), "notify-on-failure")
    assert "permissions:" in notify
    assert "issues: write" in notify
    # checkout of the in-repo composite action is the only other need
    assert "contents: read" in notify
    assert "actions/checkout@v4" in notify


# ---------------------------------------------------------------------------
# Slice 3: accuracy output plumbing + infra vs low-accuracy distinction
# ---------------------------------------------------------------------------

def test_evaluate_job_declares_accuracy_output() -> None:
    evaluate = _job_block(_workflow(), "evaluate")
    assert "outputs:" in evaluate
    assert "accuracy: ${{ steps.parse-results.outputs.accuracy }}" in evaluate
    assert "low_accuracy: ${{ steps.check-threshold.outputs.low_accuracy }}" in evaluate


def test_notifier_consumes_propagated_accuracy() -> None:
    notify = _job_block(_workflow(), "notify-on-failure")
    assert "needs.evaluate.outputs.accuracy" in notify


def test_parse_step_fails_closed_on_missing_or_bad_log() -> None:
    evaluate = _job_block(_workflow(), "evaluate")
    parse = evaluate.split("id: parse-results", maxsplit=1)[1]
    parse = parse[: parse.find("\n      - name:")]
    # no log or no accuracy metric is an infrastructure failure, not accuracy=0
    assert "exit 1" in parse
    assert "infrastructure failure" in parse
    assert "accuracy=0" not in parse


def test_low_accuracy_fails_the_evaluate_job() -> None:
    evaluate = _job_block(_workflow(), "evaluate")
    threshold = evaluate.split("id: check-threshold", maxsplit=1)[1]
    threshold = threshold[: threshold.find("\n      - name:")]
    assert "low_accuracy=true" in threshold
    assert "exit 1" in threshold
    # output must be written before the failing exit so the notifier sees it
    assert threshold.index("low_accuracy=true") < threshold.index("exit 1")


def test_notifier_runs_on_evaluate_failure_and_branches_by_cause() -> None:
    notify = _job_block(_workflow(), "notify-on-failure")
    assert "if: always() && needs.evaluate.result == 'failure'" in notify
    # genuine low score -> low-accuracy alert path
    assert "if: needs.evaluate.outputs.low_accuracy == 'true'" in notify
    # anything else -> infrastructure failure report
    assert "if: needs.evaluate.outputs.low_accuracy != 'true'" in notify
    assert "infrastructure failure" in notify


def test_infra_and_low_accuracy_paths_use_distinct_issues() -> None:
    notify = _job_block(_workflow(), "notify-on-failure")
    assert "label: thesis-eval-alert" in notify
    assert "label: thesis-eval-infra" in notify
    assert "Accuracy below threshold" in notify


# ---------------------------------------------------------------------------
# Slice 4: failure-issue composite action (decision 2A)
# ---------------------------------------------------------------------------

def test_workflow_uses_failure_issue_composite_action() -> None:
    notify = _job_block(_workflow(), "notify-on-failure")
    assert notify.count("uses: ./.github/actions/failure-issue") == 2


def test_failure_issue_action_exists_with_generic_inputs() -> None:
    assert ACTION.exists()
    action = _action()
    assert "using: composite" in action
    for input_name in ("title:", "label:", "body:", "workflow-name:"):
        assert input_name in action, f"missing input {input_name}"


def test_failure_issue_action_opens_or_updates_one_issue() -> None:
    action = _action()
    # dedupe: look up open issues by label ...
    assert "issues.listForRepo" in action
    # ... update the existing tracking issue ...
    assert "issues.createComment" in action
    # ... or create exactly one new one
    assert "issues.create(" in action

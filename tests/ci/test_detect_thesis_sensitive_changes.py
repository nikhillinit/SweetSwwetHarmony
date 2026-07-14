from scripts.ci.detect_thesis_sensitive_changes import (
    THESIS_SENSITIVE_PATTERNS,
    is_sensitive,
)


def test_thesis_filter_path_is_sensitive():
    assert is_sensitive(["consumer/thesis_filter/matcher.py"]) is True


def test_golden_set_fixture_is_sensitive():
    assert is_sensitive(["tests/fixtures/thesis_llm_golden_set.jsonl"]) is True


def test_hermes_thesis_task_path_is_sensitive():
    assert is_sensitive(["integrations/hermes/tasks/thesis_eval.py"]) is True


def test_canonical_thesis_eval_workflow_is_sensitive():
    assert is_sensitive([".github/workflows/thesis-eval.yml"]) is True


def test_scheduled_recovery_observer_is_not_thesis_sensitive():
    assert is_sensitive([".github/workflows/thesis-eval-recovery.yml"]) is False


def test_non_thesis_hermes_task_path_is_not_sensitive():
    assert is_sensitive(["integrations/hermes/tasks/deliberation.py"]) is False


def test_unrelated_path_is_not_sensitive():
    assert is_sensitive(["dashboard/app.py", "README.md"]) is False


def test_empty_changeset_is_not_sensitive():
    assert is_sensitive([]) is False


def test_patterns_are_nonempty():
    assert len(THESIS_SENSITIVE_PATTERNS) >= 5

from scripts.ci.detect_thesis_sensitive_changes import (
    THESIS_SENSITIVE_PATTERNS,
    is_sensitive,
)


def test_thesis_filter_path_is_sensitive():
    assert is_sensitive(["consumer/thesis_filter/matcher.py"]) is True


def test_golden_set_fixture_is_sensitive():
    assert is_sensitive(["tests/fixtures/thesis_llm_golden_set.jsonl"]) is True


def test_unrelated_path_is_not_sensitive():
    assert is_sensitive(["dashboard/app.py", "README.md"]) is False


def test_empty_changeset_is_not_sensitive():
    assert is_sensitive([]) is False


def test_patterns_are_nonempty():
    assert len(THESIS_SENSITIVE_PATTERNS) >= 5

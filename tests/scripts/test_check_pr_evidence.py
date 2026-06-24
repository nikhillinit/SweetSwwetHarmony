import pytest

from scripts.check_pr_evidence import EvidenceChecker, EvidenceError

GOOD_BODY = """
## Summary
This PR fixes the thesis gate.

## Evidence
- test results: https://github.com/nikhillinit/SweetSwwetHarmony/actions/runs/123456789
- artifact links: https://github.com/nikhillinit/SweetSwwetHarmony/actions/runs/123456789/artifacts/1
"""

FORGEABLE_BODY = """
## Evidence
- test results: see CI
- artifact links: passing
"""

EMPTY_BODY = ""


def test_good_evidence_passes():
    EvidenceChecker().check(GOOD_BODY)


def test_placeholder_evidence_fails():
    with pytest.raises(EvidenceError, match="placeholder"):
        EvidenceChecker().check(FORGEABLE_BODY)


def test_empty_body_fails():
    with pytest.raises(EvidenceError, match="evidence|empty"):
        EvidenceChecker().check(EMPTY_BODY)


def test_body_with_phrase_but_no_links_fails():
    body = "## Evidence\n- test results: see above\n- artifact links: N/A"
    with pytest.raises(EvidenceError, match="link|URL"):
        EvidenceChecker().check(body)


def test_known_ci_url_pattern_accepted():
    body = "Evidence: https://github.com/nikhillinit/SweetSwwetHarmony/actions/runs/99"
    EvidenceChecker().check(body)


def test_wrong_repo_actions_url_fails():
    body = "Evidence: https://github.com/other-org/other-repo/actions/runs/123"
    with pytest.raises(EvidenceError, match="repository"):
        EvidenceChecker().check(body)


def test_zero_actions_run_id_fails():
    body = "Evidence: https://github.com/nikhillinit/SweetSwwetHarmony/actions/runs/0"
    with pytest.raises(EvidenceError, match="run id 0"):
        EvidenceChecker().check(body)

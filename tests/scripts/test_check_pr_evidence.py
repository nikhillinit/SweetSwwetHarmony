import pytest

from scripts.check_pr_evidence import (
    EvidenceChecker,
    EvidenceError,
    EvidenceState,
    RunVerification,
)


class _StubVerifier:
    """Duck-typed RunVerifier for tests — no network."""

    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls = []

    def verify_run(self, owner, repo, run_id):
        self.calls.append((owner, repo, run_id))
        if self._exc is not None:
            raise self._exc
        return self._result


RUN_URL_BODY = (
    "Evidence: https://github.com/nikhillinit/SweetSwwetHarmony/actions/runs/123"
)

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


# --- T1b: explicit evidence state ---------------------------------------------


def test_valid_url_without_verifier_is_syntax_only():
    result = EvidenceChecker().check(RUN_URL_BODY)
    assert result.state is EvidenceState.SYNTAX_ONLY


# --- T1b: live GitHub verification --------------------------------------------


def test_verifier_confirms_run_is_live_verified():
    verifier = _StubVerifier(RunVerification(exists=True, conclusion="success"))
    result = EvidenceChecker(verifier=verifier).check(RUN_URL_BODY)
    assert result.state is EvidenceState.LIVE_VERIFIED
    assert verifier.calls == [("nikhillinit", "SweetSwwetHarmony", "123")]


def test_live_verify_rejects_non_success_conclusion():
    verifier = _StubVerifier(RunVerification(exists=True, conclusion="failure"))
    with pytest.raises(EvidenceError, match="success|conclud"):
        EvidenceChecker(verifier=verifier).check(RUN_URL_BODY)


def test_live_verify_rejects_missing_run():
    verifier = _StubVerifier(RunVerification(exists=False))
    with pytest.raises(EvidenceError, match="not found|exist"):
        EvidenceChecker(verifier=verifier).check(RUN_URL_BODY)


def test_live_verify_rejects_head_sha_mismatch():
    verifier = _StubVerifier(
        RunVerification(exists=True, conclusion="success", head_sha_matches=False)
    )
    with pytest.raises(EvidenceError, match="head"):
        EvidenceChecker(verifier=verifier, expected_head_sha="deadbeef").check(
            RUN_URL_BODY
        )


def test_verifier_outage_does_not_silently_pass():
    verifier = _StubVerifier(exc=RuntimeError("gh api 503"))
    with pytest.raises(EvidenceError, match="unavailable|verification"):
        EvidenceChecker(verifier=verifier).check(RUN_URL_BODY)


# --- T1b: manual override (logged human attestation) --------------------------


def test_manual_override_marker_passes_with_reason():
    body = (
        "EVIDENCE-OVERRIDE: gh api outage; attested by @nikhillinit\n"
        "## Evidence\n- test results: see CI"
    )
    result = EvidenceChecker().check(body)
    assert result.state is EvidenceState.MANUAL_OVERRIDE
    assert "attested" in result.detail


def test_manual_override_without_reason_fails():
    body = "EVIDENCE-OVERRIDE:\n## Evidence"
    with pytest.raises(EvidenceError, match="reason"):
        EvidenceChecker().check(body)


def test_manual_override_passes_without_any_ci_url():
    body = "EVIDENCE-OVERRIDE: bootstrap PR; checker cannot validate itself"
    result = EvidenceChecker().check(body)
    assert result.state is EvidenceState.MANUAL_OVERRIDE


# --- T1b: concrete gh-cli verifier (subprocess mocked) ------------------------


def test_gh_cli_verifier_parses_success(monkeypatch):
    import scripts.check_pr_evidence as mod

    class _FakeProc:
        returncode = 0
        stdout = '{"conclusion": "success", "head_sha": "abc123"}'
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _FakeProc())
    verifier = mod.GitHubCliRunVerifier(expected_head_sha="abc123")
    result = verifier.verify_run("o", "r", "1")
    assert result.exists is True
    assert result.conclusion == "success"
    assert result.head_sha_matches is True


def test_gh_cli_verifier_reports_missing_on_404(monkeypatch):
    import scripts.check_pr_evidence as mod

    class _FakeProc:
        returncode = 1
        stdout = ""
        stderr = "gh: Not Found (HTTP 404)"

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _FakeProc())
    verifier = mod.GitHubCliRunVerifier()
    result = verifier.verify_run("o", "r", "1")
    assert result.exists is False


def test_gh_cli_verifier_detects_head_sha_mismatch(monkeypatch):
    import scripts.check_pr_evidence as mod

    class _FakeProc:
        returncode = 0
        stdout = '{"conclusion": "success", "head_sha": "abc123"}'
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _FakeProc())
    verifier = mod.GitHubCliRunVerifier(expected_head_sha="deadbeef")
    result = verifier.verify_run("o", "r", "1")
    assert result.head_sha_matches is False

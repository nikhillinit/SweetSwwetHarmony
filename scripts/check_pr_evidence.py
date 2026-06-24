from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from urllib.parse import urlparse

ALLOWED_GITHUB_OWNER = "nikhillinit"
ALLOWED_GITHUB_REPO = "SweetSwwetHarmony"
ALLOWED_GITHUB_REPOSITORY = f"{ALLOWED_GITHUB_OWNER}/{ALLOWED_GITHUB_REPO}"
URL_PATTERN = re.compile(r"https?://[^\s<>\"]+")
OVERRIDE_PATTERN = re.compile(r"(?im)^[ \t]*EVIDENCE-OVERRIDE:[ \t]*(?P<reason>.*)$")

EVIDENCE_TRIGGER_PHRASES = ["test results:", "artifact links:"]

PLACEHOLDER_INDICATORS = [
    "see ci", "see above", "passing", "n/a", "none", "tbd", "todo",
    "see pr", "check ci", "all passing",
]


class EvidenceError(RuntimeError):
    pass


class EvidenceState(str, Enum):
    """How an evidence bundle was accepted.

    - SYNTAX_ONLY: a well-formed allow-listed CI URL is present, but it was not
      confirmed against GitHub.
    - LIVE_VERIFIED: the referenced run was confirmed (exists, optional head-sha
      match, concluded success).
    - MANUAL_OVERRIDE: a human attested out of band via an EVIDENCE-OVERRIDE line.
    """

    SYNTAX_ONLY = "syntax_only"
    LIVE_VERIFIED = "live_verified"
    MANUAL_OVERRIDE = "manual_override"


@dataclass(frozen=True)
class EvidenceResult:
    state: EvidenceState
    detail: str = ""
    url: str | None = None


@dataclass(frozen=True)
class RunVerification:
    exists: bool
    conclusion: str | None = None
    head_sha_matches: bool = True


class RunVerifier(Protocol):
    def verify_run(self, owner: str, repo: str, run_id: str) -> RunVerification: ...


@dataclass(frozen=True)
class CiRef:
    owner: str
    repo: str
    kind: str  # "run" | "suite"
    resource_id: str
    url: str


def _extract_urls(body: str) -> list[str]:
    return [url.rstrip(").,;]") for url in URL_PATTERN.findall(body)]


def _repository_matches(owner: str, repo: str) -> bool:
    return (
        owner.lower() == ALLOWED_GITHUB_OWNER.lower()
        and repo.lower() == ALLOWED_GITHUB_REPO.lower()
    )


def _validate_github_actions_id(resource_id: str, resource_name: str) -> None:
    if not resource_id.isdigit():
        raise EvidenceError(
            f"invalid GitHub Actions {resource_name} id: {resource_id!r}"
        )
    if int(resource_id) == 0:
        raise EvidenceError(
            f"GitHub Actions {resource_name} id 0 is not valid evidence"
        )


def _parse_ci_url(url: str) -> CiRef | None:
    """Parse an allow-listed GitHub Actions URL into a CiRef.

    Returns None for non-CI URLs; raises EvidenceError for CI-shaped URLs that
    point at the wrong repository or carry an invalid resource id.
    """
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        len(segments) >= 5
        and segments[2].lower() == "actions"
        and segments[3].lower() == "runs"
    ):
        owner, repo, run_id = segments[0], segments[1], segments[4]
        if not _repository_matches(owner, repo):
            raise EvidenceError(
                "wrong repository for GitHub Actions evidence: "
                f"{owner}/{repo}; expected {ALLOWED_GITHUB_REPOSITORY}"
            )
        _validate_github_actions_id(run_id, "run")
        return CiRef(owner, repo, "run", run_id, url)

    if len(segments) >= 4 and segments[2].lower() == "suites":
        owner, repo, suite_id = segments[0], segments[1], segments[3]
        if not _repository_matches(owner, repo):
            raise EvidenceError(
                "wrong repository for GitHub Actions evidence: "
                f"{owner}/{repo}; expected {ALLOWED_GITHUB_REPOSITORY}"
            )
        _validate_github_actions_id(suite_id, "suite")
        return CiRef(owner, repo, "suite", suite_id, url)

    return None


def _override_reason(body: str) -> str | None:
    match = OVERRIDE_PATTERN.search(body)
    if not match:
        return None
    reason = match.group("reason").strip()
    if not reason:
        raise EvidenceError(
            "manual override requires a reason after 'EVIDENCE-OVERRIDE:'"
        )
    return reason


class GitHubCliRunVerifier:
    """Confirm a GitHub Actions run via the `gh` CLI (`gh api`)."""

    def __init__(self, expected_head_sha: str | None = None) -> None:
        self.expected_head_sha = expected_head_sha

    def verify_run(self, owner: str, repo: str, run_id: str) -> RunVerification:
        proc = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/actions/runs/{run_id}"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            if "404" in stderr or "Not Found" in stderr:
                return RunVerification(exists=False)
            raise RuntimeError(f"gh api failed for run {run_id}: {stderr}")

        data = json.loads(proc.stdout)
        head_sha = data.get("head_sha", "")
        head_sha_matches = (
            True
            if not self.expected_head_sha
            else head_sha == self.expected_head_sha
        )
        return RunVerification(
            exists=True,
            conclusion=data.get("conclusion"),
            head_sha_matches=head_sha_matches,
        )


class EvidenceChecker:
    def __init__(
        self,
        verifier: RunVerifier | None = None,
        expected_head_sha: str | None = None,
    ) -> None:
        self.verifier = verifier
        self.expected_head_sha = expected_head_sha

    def check(self, body: str) -> EvidenceResult:
        if not body or not body.strip():
            raise EvidenceError("PR body is empty - evidence bundle required")

        reason = _override_reason(body)
        if reason is not None:
            return EvidenceResult(state=EvidenceState.MANUAL_OVERRIDE, detail=reason)

        urls = _extract_urls(body)
        refs = [ref for ref in (_parse_ci_url(url) for url in urls) if ref is not None]
        if refs:
            if self.verifier is None:
                return EvidenceResult(
                    state=EvidenceState.SYNTAX_ONLY,
                    detail="syntactically valid CI URL; not live-verified",
                    url=refs[0].url,
                )
            return self._live_verify(refs)

        # No real CI URL. Check whether trigger phrases appear with only placeholders.
        lower = body.lower()
        has_trigger = any(phrase in lower for phrase in EVIDENCE_TRIGGER_PHRASES)
        if has_trigger:
            for line in body.splitlines():
                line_lower = line.lower()
                if any(p in line_lower for p in PLACEHOLDER_INDICATORS):
                    raise EvidenceError(
                        f"placeholder evidence detected: {line.strip()!r} - "
                        "replace with a real GitHub Actions URL"
                    )

        raise EvidenceError(
            "evidence section has no CI artifact links - "
            "provide a GitHub Actions run URL "
            f"(https://github.com/{ALLOWED_GITHUB_REPOSITORY}/actions/runs/...)"
        )

    def _live_verify(self, refs: list[CiRef]) -> EvidenceResult:
        run_refs = [ref for ref in refs if ref.kind == "run"]
        if not run_refs:
            return EvidenceResult(
                state=EvidenceState.SYNTAX_ONLY,
                detail="only check-suite URLs present; not live-verifiable",
                url=refs[0].url,
            )

        ref = run_refs[0]
        try:
            verification = self.verifier.verify_run(ref.owner, ref.repo, ref.resource_id)
        except Exception as exc:  # noqa: BLE001 - any failure must not silently pass
            raise EvidenceError(
                "live verification unavailable and no manual override present: "
                f"{exc}. Add 'EVIDENCE-OVERRIDE: <reason>' to attest out of band."
            ) from exc

        if not verification.exists:
            raise EvidenceError(f"GitHub Actions run not found: {ref.url}")
        if self.expected_head_sha and not verification.head_sha_matches:
            raise EvidenceError(
                f"run {ref.resource_id} is not tied to the PR head sha "
                f"{self.expected_head_sha}"
            )
        if verification.conclusion != "success":
            raise EvidenceError(
                f"run {ref.resource_id} did not conclude success: "
                f"{verification.conclusion!r}"
            )
        return EvidenceResult(
            state=EvidenceState.LIVE_VERIFIED,
            detail=f"run {ref.resource_id} verified success",
            url=ref.url,
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--body", required=True)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Verify referenced CI runs via the gh CLI",
    )
    parser.add_argument(
        "--head-sha",
        default=None,
        help="Require the verified run to match this PR head SHA",
    )
    args = parser.parse_args()

    verifier = GitHubCliRunVerifier(expected_head_sha=args.head_sha) if args.live else None
    checker = EvidenceChecker(verifier=verifier, expected_head_sha=args.head_sha)
    try:
        result = checker.check(args.body)
    except EvidenceError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Evidence check passed: {result.state.value}")
    if result.state is EvidenceState.MANUAL_OVERRIDE:
        print(f"MANUAL OVERRIDE (logged): {result.detail}", file=sys.stderr)

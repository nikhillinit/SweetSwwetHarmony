from __future__ import annotations

import re
import sys
from urllib.parse import urlparse

ALLOWED_GITHUB_OWNER = "nikhillinit"
ALLOWED_GITHUB_REPO = "SweetSwwetHarmony"
ALLOWED_GITHUB_REPOSITORY = f"{ALLOWED_GITHUB_OWNER}/{ALLOWED_GITHUB_REPO}"
URL_PATTERN = re.compile(r"https?://[^\s<>\"]+")

EVIDENCE_TRIGGER_PHRASES = ["test results:", "artifact links:"]

PLACEHOLDER_INDICATORS = [
    "see ci", "see above", "passing", "n/a", "none", "tbd", "todo",
    "see pr", "check ci", "all passing",
]


class EvidenceError(RuntimeError):
    pass


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


def _is_allowed_ci_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return False

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
        return True

    if len(segments) >= 4 and segments[2].lower() == "suites":
        owner, repo, suite_id = segments[0], segments[1], segments[3]
        if not _repository_matches(owner, repo):
            raise EvidenceError(
                "wrong repository for GitHub Actions evidence: "
                f"{owner}/{repo}; expected {ALLOWED_GITHUB_REPOSITORY}"
            )
        _validate_github_actions_id(suite_id, "suite")
        return True

    return False


class EvidenceChecker:
    def check(self, body: str) -> None:
        if not body or not body.strip():
            raise EvidenceError("PR body is empty - evidence bundle required")

        urls = _extract_urls(body)
        ci_urls = [url for url in urls if _is_allowed_ci_url(url)]
        if ci_urls:
            return

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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--body", required=True)
    args = parser.parse_args()
    try:
        EvidenceChecker().check(args.body)
        print("Evidence check passed.")
    except EvidenceError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)

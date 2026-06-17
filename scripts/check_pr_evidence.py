from __future__ import annotations

import re
import sys

KNOWN_CI_URL_PATTERNS = [
    r"https://github\.com/\S+/actions/runs/\d+",
    r"https://github\.com/\S+/suites/\d+",
]

EVIDENCE_TRIGGER_PHRASES = ["test results:", "artifact links:"]

PLACEHOLDER_INDICATORS = [
    "see ci", "see above", "passing", "n/a", "none", "tbd", "todo",
    "see pr", "check ci", "all passing",
]


class EvidenceError(RuntimeError):
    pass


class EvidenceChecker:
    def check(self, body: str) -> None:
        if not body or not body.strip():
            raise EvidenceError("PR body is empty — evidence bundle required")

        # A real CI URL anywhere in the body is sufficient evidence.
        urls = re.findall(r"https?://\S+", body)
        ci_urls = [
            url for url in urls
            if any(re.search(pat, url) for pat in KNOWN_CI_URL_PATTERNS)
        ]
        if ci_urls:
            return  # real artifact link present — pass

        # No real CI URL. Check whether trigger phrases appear with only placeholders.
        lower = body.lower()
        has_trigger = any(phrase in lower for phrase in EVIDENCE_TRIGGER_PHRASES)
        if has_trigger:
            for line in body.splitlines():
                line_lower = line.lower()
                if any(p in line_lower for p in PLACEHOLDER_INDICATORS):
                    raise EvidenceError(
                        f"placeholder evidence detected: {line.strip()!r} — "
                        "replace with a real GitHub Actions URL"
                    )

        raise EvidenceError(
            "evidence section has no CI artifact links — "
            "provide a GitHub Actions run URL "
            "(https://github.com/org/repo/actions/runs/...)"
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

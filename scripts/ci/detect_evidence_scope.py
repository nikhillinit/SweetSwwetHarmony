"""Decide whether a PR's changed files require a live evidence bundle.

Used by the PR Evidence Gate workflow (``.github/workflows/pr-evidence.yml``).
The gate is always-on for PRs to ``main``; this helper scopes evidence *inside*
the job so a required check never parks on ``Pending`` (which a trigger-level
``paths:`` filter would cause).

Generalizes the ``detect_thesis_sensitive_changes`` pattern. The sensitive set
is the documented evidence paths PLUS the gate's own surface, so a PR that
weakens the gate is itself gated.

CLI contract (the workflow depends on it): prints ``true``/``false`` to stdout
and ALWAYS exits 0. The workflow captures stdout; it must not branch on exit
status.
"""
from __future__ import annotations

import argparse
import fnmatch

# fnmatch's ``*`` matches ``/`` too, so ``storage/**`` already covers nested
# paths; the explicit ``/**`` variants are belt-and-suspenders and mirror the
# existing thesis detector's style.
EVIDENCE_REQUIRED_PATTERNS = [
    # Documented evidence paths (pipeline + storage + thesis golden set).
    "workflows/pipeline.py",
    "workflows/run_manager.py",
    "storage/*",
    "storage/**",
    "tests/fixtures/thesis_llm_golden_set*",
    "tests/fixtures/thesis_llm_golden_set.**",
    # The gate's own surface — self-protecting.
    "scripts/check_pr_evidence.py",
    "scripts/ci/detect_evidence_scope.py",
    ".github/workflows/pr-evidence.yml",
    "tests/ci/test_pr_evidence_workflow.py",
    "tests/ci/test_detect_evidence_scope.py",
    "tests/scripts/test_check_pr_evidence.py",
]


def is_evidence_required(changed_files: list[str]) -> bool:
    for path in changed_files:
        norm = path.replace("\\", "/")
        for pattern in EVIDENCE_REQUIRED_PATTERNS:
            if fnmatch.fnmatch(norm, pattern):
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print 'true' if changed files require a PR evidence bundle."
    )
    parser.add_argument(
        "--changed-files",
        required=True,
        help="Newline- or comma-separated changed file paths.",
    )
    args = parser.parse_args(argv)
    raw = args.changed_files.replace(",", "\n")
    files = [line.strip() for line in raw.splitlines() if line.strip()]
    required = is_evidence_required(files)
    print("true" if required else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

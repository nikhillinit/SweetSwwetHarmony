"""Detect whether a PR's changed files touch thesis-sensitive paths."""
from __future__ import annotations

import argparse
import fnmatch

THESIS_SENSITIVE_PATTERNS = [
    "consumer/thesis_filter/*",
    "consumer/thesis_filter/**",
    "utils/thesis_*.py",
    "integrations/hermes/tasks/*thesis*.py",
    "scripts/*thesis*.py",
    "scripts/ci/*thesis*.py",
    "tests/fixtures/thesis_llm_golden_set*",
    "artifacts/thesis_diagnostics/candidate_v3*",
    ".github/workflows/thesis-golden-gate.yml",
    ".github/workflows/thesis-eval.yml",
]


def is_sensitive(changed_files: list[str]) -> bool:
    for path in changed_files:
        norm = path.replace("\\", "/")
        for pattern in THESIS_SENSITIVE_PATTERNS:
            if fnmatch.fnmatch(norm, pattern):
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-files", required=True,
                        help="Newline- or comma-separated changed file paths.")
    args = parser.parse_args(argv)
    raw = args.changed_files.replace(",", "\n")
    files = [line.strip() for line in raw.splitlines() if line.strip()]
    sensitive = is_sensitive(files)
    print("true" if sensitive else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

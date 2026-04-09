#!/usr/bin/env bash
# check_protected_paths.sh
#
# Step 4B regret window guard for the Direction-A-Derived Hybrid strategy.
# Window: 2026-04-06 -> 2026-04-19. During this window, no commits on the
# prep/red-team-hybrid-prep branch may touch the protected paths below.
#
# Usage:
#   bash scripts/red-team-hybrid/check_protected_paths.sh [base_ref]
#
# Default base_ref is "main". The script greps `git diff --name-only <base>...HEAD`
# against the forbidden list and exits non-zero on any match.
#
# Run before every commit on the prep branch:
#   bash scripts/red-team-hybrid/check_protected_paths.sh && git commit -m "..."
#
# This guard exists to enforce R1 (Step 4B regret window) per
# docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md.

set -euo pipefail

BASE_REF="${1:-main}"

# Forbidden top-level path prefixes during the regret window.
# These are the paths whose evidence the Step 4B regret check on 2026-04-18
# depends on. Any modification corrupts the evidence.
FORBIDDEN_PATTERNS=(
  '^collectors/'
  '^workflows/'
  '^governance/'
  '^monitoring/'
  '^connectors/'
  '^storage/migrations/'
)

# Build a single ERE pattern for grep -E.
PATTERN="$(IFS='|'; echo "${FORBIDDEN_PATTERNS[*]}")"

# What's actually changed vs the base ref. Capture FOUR categories:
#   1. Committed delta vs base ref (commits on this branch but not on main)
#   2. Staged but uncommitted
#   3. Unstaged (modified tracked files)
#   4. Untracked new files (excluding gitignore)
# Use --diff-filter=ACMR to catch added, copied, modified, renamed (ignore deletes).
COMMITTED="$(git diff --name-only --diff-filter=ACMR "${BASE_REF}"...HEAD 2>/dev/null || true)"
STAGED="$(git diff --name-only --cached --diff-filter=ACMR 2>/dev/null || true)"
UNSTAGED="$(git diff --name-only --diff-filter=ACMR 2>/dev/null || true)"
UNTRACKED="$(git ls-files --others --exclude-standard 2>/dev/null || true)"

ALL_CHANGED="$(printf '%s\n%s\n%s\n%s\n' "${COMMITTED}" "${STAGED}" "${UNSTAGED}" "${UNTRACKED}" | sort -u | grep -v '^$' || true)"

if [ -z "${ALL_CHANGED}" ]; then
  echo "OK: no file changes detected vs ${BASE_REF}."
  exit 0
fi

# Match against the forbidden patterns.
VIOLATIONS="$(printf '%s\n' "${ALL_CHANGED}" | grep -E "${PATTERN}" || true)"

if [ -n "${VIOLATIONS}" ]; then
  echo "FAIL: protected-path violations detected." >&2
  echo "" >&2
  echo "The following files match forbidden patterns during the Step 4B" >&2
  echo "regret window (2026-04-06 -> 2026-04-19):" >&2
  echo "" >&2
  printf '  %s\n' ${VIOLATIONS} >&2
  echo "" >&2
  echo "Forbidden patterns:" >&2
  printf '  %s\n' "${FORBIDDEN_PATTERNS[@]}" >&2
  echo "" >&2
  echo "If this is a false positive, see" >&2
  echo "  docs/plans/2026-04-06-red-team-hybrid/01-move-0-charter.md" >&2
  echo "for the allowed-paths list and the rationale." >&2
  exit 1
fi

# Report the safe changes for visibility.
echo "OK: ${BASE_REF}...HEAD changes do not touch protected paths."
echo ""
echo "Changed files (vs ${BASE_REF}):"
printf '  %s\n' ${ALL_CHANGED}
exit 0

"""Ruleset-parity drift check: live ruleset vs runbook.

Reads the active default-branch ruleset via ``gh api`` and diffs its
required status checks (plus enforcement and the strict up-to-date policy)
against the check-list table in
``docs/runbooks/branch-protection-setup.md``. Exits nonzero on drift.

Q1's acceptance criterion "live ruleset output and the runbook list the
same checks" is otherwise verified once, manually, then drifts forever --
the drift class that left the ruleset requiring only Core Regression Suite
out of 7 green merge gates.

Usage:
    python -m scripts.ci.check_ruleset_parity [--json]
        [--repo OWNER/NAME] [--ruleset-id ID] [--runbook PATH]

Exit codes: 0 parity, 1 drift, 2 fetch/parse error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_REPO = "nikhillinit/SweetSwwetHarmony"
DEFAULT_RULESET_ID = 12778551
DEFAULT_RUNBOOK = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "runbooks"
    / "branch-protection-setup.md"
)
CHECKS_HEADING = "### Required status checks"


def parse_runbook_checks(text: str) -> list[str]:
    """Return the required-check contexts from the runbook's check table.

    The table lives under the "### Required status checks" heading; the
    first column holds the check context exactly as CI reports it.
    """
    lines = text.splitlines()
    try:
        start = next(
            i for i, line in enumerate(lines) if line.startswith(CHECKS_HEADING)
        )
    except StopIteration:
        raise ValueError(
            f"runbook has no {CHECKS_HEADING!r} heading; cannot derive the "
            "required-check list"
        ) from None

    checks: list[str] = []
    in_table = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        in_table = True
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        first = cells[0]
        if not first or set(first) <= {"-", " "}:
            continue
        if first == "Required check context":
            continue
        checks.append(first)

    if not checks:
        raise ValueError(
            "runbook required-checks table is empty or unparseable under "
            f"{CHECKS_HEADING!r}"
        )
    return checks


def extract_ruleset_checks(ruleset: dict[str, Any]) -> list[str]:
    """Return required status-check contexts from a ruleset API payload."""
    contexts: list[str] = []
    for rule in ruleset.get("rules", []):
        if rule.get("type") != "required_status_checks":
            continue
        for check in rule.get("parameters", {}).get("required_status_checks", []):
            context = check.get("context")
            if context:
                contexts.append(str(context))
    return contexts


def _strict_policy(ruleset: dict[str, Any]) -> bool | None:
    for rule in ruleset.get("rules", []):
        if rule.get("type") == "required_status_checks":
            return rule.get("parameters", {}).get(
                "strict_required_status_checks_policy"
            )
    return None


def diff_ruleset(
    runbook_checks: list[str],
    ruleset: dict[str, Any],
) -> dict[str, Any]:
    """Diff runbook checks against the live ruleset; parity is fail-closed.

    Parity additionally requires enforcement "active" and the strict
    up-to-date policy, both documented as expected state in the runbook.
    """
    live = extract_ruleset_checks(ruleset)
    missing = sorted(set(runbook_checks) - set(live))
    extra = sorted(set(live) - set(runbook_checks))
    enforcement = ruleset.get("enforcement")
    strict = _strict_policy(ruleset)
    return {
        "parity": not missing
        and not extra
        and enforcement == "active"
        and strict is True,
        "runbook_checks": sorted(set(runbook_checks)),
        "live_checks": sorted(set(live)),
        "missing_from_live": missing,
        "extra_in_live": extra,
        "enforcement": enforcement,
        "strict": strict,
    }


def fetch_live_ruleset(repo: str, ruleset_id: int) -> dict[str, Any]:
    """Fetch the ruleset via the gh CLI (read-only)."""
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/rulesets/{ruleset_id}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh api failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diff the live default-branch ruleset against the runbook"
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--ruleset-id", type=int, default=DEFAULT_RULESET_ID)
    parser.add_argument("--runbook", type=Path, default=DEFAULT_RUNBOOK)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)

    try:
        runbook_checks = parse_runbook_checks(
            args.runbook.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        ruleset = fetch_live_ruleset(args.repo, args.ruleset_id)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    result = diff_ruleset(runbook_checks, ruleset)

    if args.json_output:
        print(json.dumps(result, indent=2))
    elif result["parity"]:
        print(
            f"PARITY OK: ruleset {args.ruleset_id} requires exactly the "
            f"{len(result['runbook_checks'])} runbook checks "
            f"(enforcement={result['enforcement']}, strict={result['strict']})"
        )
    else:
        print(f"RULESET DRIFT for ruleset {args.ruleset_id}:")
        for name in result["missing_from_live"]:
            print(f"  missing from live ruleset: {name}")
        for name in result["extra_in_live"]:
            print(f"  extra in live ruleset:     {name}")
        if result["enforcement"] != "active":
            print(f"  enforcement is {result['enforcement']!r}, expected 'active'")
        if result["strict"] is not True:
            print(f"  strict up-to-date policy is {result['strict']!r}, expected True")

    return 0 if result["parity"] else 1


if __name__ == "__main__":
    sys.exit(main())

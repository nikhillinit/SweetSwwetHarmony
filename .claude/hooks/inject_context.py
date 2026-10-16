#!/usr/bin/env python3
"""UserPromptSubmit hook -- injects Harmonic session context before Claude sees the prompt.

Cross-platform port of inject_context.ps1. Stdout becomes part of Claude's context.
Non-blocking (exit 0 always). Purpose: force branch -> canonical plan resolution at
every turn, not just session start. See feedback_ownership_vs_laziness.md for why.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def resolve_repo() -> Path | None:
    repo = os.environ.get("CLAUDE_PROJECT_DIR") or ""
    if not repo or not Path(repo).is_dir():
        repo = os.getcwd()
    return Path(repo) if Path(repo).is_dir() else None


def current_branch(repo: Path) -> str:
    try:
        res = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def plan_line(repo: Path, branch: str) -> str:
    m = re.match(r"^(prep|release)/(.+?)(-prep)?$", branch)
    if not m:
        return ""
    topic = m.group(2)
    plans_dir = repo / "docs" / "plans"
    if not plans_dir.is_dir():
        return ""
    candidates = sorted(
        p.name for p in plans_dir.iterdir() if p.is_dir() and topic in p.name
    )
    if len(candidates) == 1:
        return (
            f"Canonical plan: docs/plans/{candidates[0]}/00-strategy.md (or README.md)"
        )
    if candidates:
        return (
            f"Canonical plan: AMBIGUOUS ({len(candidates)} candidates match '{topic}'); "
            "check docs/plans/README.md for the flagged canonical"
        )
    return f"Canonical plan: no docs/plans/*{topic}* dir found"


def main() -> int:
    sys.stdin.read()  # drain stdin; we don't parse it
    repo = resolve_repo()
    if repo is None:
        return 0
    branch = current_branch(repo)
    if not branch:
        return 0

    lines = ["", "[harmonic session context]", f"Branch: {branch}"]
    plan = plan_line(repo, branch)
    if plan:
        lines.append(plan)
    # Protected paths reminder (only on prep branches during Move 0)
    if branch == "prep/red-team-hybrid-prep":
        lines.append(
            "Protected paths (Move 0, ends 2026-04-19): collectors/ workflows/ governance/ "
            "monitoring/ connectors/ storage/migrations/"
        )
    lines += [
        "Ownership rules (feedback memory: feedback_ownership_vs_laziness.md):",
        "  1. Before proposing infrastructure: Glob/Read existing config first.",
        "  2. Before claiming done: run the verification command + paste output.",
        "  3. Before modifying a function: Read it in THIS conversation first.",
        "[end context]",
        "",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

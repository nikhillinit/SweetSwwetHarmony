"""Lightweight git info utilities for branch-safety guardrails."""

import subprocess
from pathlib import Path
from typing import Optional, Tuple

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)

# Three-state branch result:
#   "main"           -> on main
#   "feature/foo"    -> on named branch
#   "DETACHED"       -> detached HEAD (git works, but not on a branch)
#   None             -> git unavailable or not a repo

DETACHED = "DETACHED"


def get_git_info(repo_root: str = _REPO_ROOT) -> Tuple[Optional[str], Optional[str]]:
    """Return (branch_or_state, short_sha). branch is None only when git is unavailable."""
    sha = _git_cmd(["git", "rev-parse", "--short", "HEAD"], repo_root)
    if sha is None:
        # git not available or not a repo
        return None, None
    branch = _git_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    if branch is None or branch == "HEAD":
        # branch=None here means git is available (SHA worked) but branch
        # lookup failed unexpectedly — treat same as detached HEAD
        return DETACHED, sha
    return branch, sha


def _git_cmd(cmd: list, cwd: str) -> Optional[str]:
    """Run a git command, return stripped stdout or None."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, cwd=cwd,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None

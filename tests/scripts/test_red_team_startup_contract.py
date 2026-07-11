"""Startup contract for the red-team-hybrid utilities (PR #285 review, Q8).

Two invariants, one per test:

1. Import-light startup: ``python -S <script> --help`` must exit 0.
   ``-S`` disables site-packages, so any module-scope import of the
   application stack (storage/__init__.py pulls in aiosqlite) fails the
   test. This proves argparse runs before any application dependency is
   imported.

2. Fail-closed DB guard unchanged at runtime: running each script with an
   explicit in-tree ``--db`` path must still exit 2 with the
   InTreeDatabaseError message. This exercises the real code path
   (main -> lazy import -> resolve_db_path_env -> guard_db_path) and
   proves the lazy import did not weaken canonical DB-path enforcement.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

RED_TEAM_SCRIPTS = (
    "scripts/red-team-hybrid/build_holdout_split.py",
    "scripts/red-team-hybrid/extract_founder_candidates.py",
    "scripts/red-team-hybrid/freshness_watchdog.py",
    "scripts/red-team-hybrid/mine_track_b_candidates.py",
)


def _clean_env(**overrides: str) -> dict[str, str]:
    """Subprocess env with the knobs that could mask a regression removed."""
    env = os.environ.copy()
    # PYTHONPATH could re-expose site-packages under -S; the allow flag
    # could disable the in-tree guard. Strip both for determinism.
    env.pop("PYTHONPATH", None)
    env.pop("HARMONIC_ALLOW_IN_TREE_DB", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(overrides)
    return env


@pytest.mark.parametrize("script", RED_TEAM_SCRIPTS)
def test_help_is_import_light_under_no_site(script: str) -> None:
    """--help must succeed with site-packages disabled (-S)."""
    result = subprocess.run(
        [sys.executable, "-S", str(ROOT / script), "--help"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=_clean_env(),
        timeout=60,
    )

    assert result.returncode == 0, (
        f"{script} --help failed under python -S "
        f"(application import before argparse?)\nstderr:\n{result.stderr}"
    )
    assert "usage:" in result.stdout.lower()


@pytest.mark.parametrize("script", RED_TEAM_SCRIPTS)
def test_in_tree_db_guard_still_fails_closed(script: str) -> None:
    """Explicit in-tree --db must still exit 2 via InTreeDatabaseError."""
    in_tree_db = ROOT / "signals-in-tree-guard-check.db"

    result = subprocess.run(
        [sys.executable, str(ROOT / script), "--db", str(in_tree_db)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=_clean_env(),
        timeout=60,
    )

    assert result.returncode == 2, (
        f"{script} accepted an in-tree DB path (guard weakened?)\n"
        f"rc={result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "working tree" in result.stderr

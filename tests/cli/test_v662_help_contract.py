"""Help contract tests for v6.6.2 CLI commands.

Verifies:
- All 5 commands appear in --help output
- Each command's --help exits zero
- --db deprecated alias emits warning
"""

import subprocess
import sys

import pytest

PYTHON = sys.executable
CLI = "run_pipeline.py"

V662_COMMANDS = [
    "canary-preflight",
    "backfill-evidence-family",
    "rehydrate-canonical-keys-v2",
    "convergence-kpi",
    "health-json-pure",
]


def _run(args, **kwargs):
    """Run CLI and return CompletedProcess."""
    return subprocess.run(
        [PYTHON, CLI] + args,
        capture_output=True, text=True, timeout=30,
        **kwargs,
    )


class TestHelpListsAllV662Commands:
    """Top-level --help lists all 5 v6.6.2 commands."""

    def test_help_lists_all_v662_commands(self):
        result = _run(["--help"])
        assert result.returncode == 0
        for cmd in V662_COMMANDS:
            assert cmd in result.stdout, f"Command '{cmd}' not found in --help output"


class TestCommandHelpExitsZero:
    """Each v6.6.2 command's --help exits 0."""

    @pytest.mark.parametrize("cmd", V662_COMMANDS)
    def test_help_exits_zero(self, cmd):
        result = _run([cmd, "--help"])
        assert result.returncode == 0, f"{cmd} --help exited {result.returncode}: {result.stderr}"


class TestDbAliasDeprecationWarning:
    """Using --db emits a deprecation warning on stderr."""

    def test_db_alias_emits_deprecation_warning(self):
        # Use canary-preflight with --db (non-existent DB is fine, we just
        # want to see the deprecation warning)
        result = _run(["canary-preflight", "--db", "nonexistent.db"])
        assert "DEPRECATED" in result.stderr, (
            f"Expected DEPRECATED warning on stderr, got: {result.stderr}"
        )

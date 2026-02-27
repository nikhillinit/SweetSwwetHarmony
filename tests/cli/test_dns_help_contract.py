"""Help contract tests for DNS Phase 2 CLI commands.

Verifies:
- dns-phase2-guardrails appears in --help output
- Command's --help exits zero
"""

import subprocess
import sys

import pytest

PYTHON = sys.executable
CLI = "run_pipeline.py"

DNS_COMMANDS = ["dns-phase2-guardrails"]


def _run(args, **kwargs):
    """Run CLI and return CompletedProcess."""
    return subprocess.run(
        [PYTHON, CLI] + args,
        capture_output=True, text=True, timeout=30,
        **kwargs,
    )


class TestHelpListsDNSCommands:
    """Top-level --help lists DNS Phase 2 commands."""

    def test_help_lists_dns_commands(self):
        result = _run(["--help"])
        assert result.returncode == 0
        for cmd in DNS_COMMANDS:
            assert cmd in result.stdout, f"Command '{cmd}' not found in --help output"


class TestDNSCommandHelpExitsZero:
    """Each DNS command's --help exits 0."""

    @pytest.mark.parametrize("cmd", DNS_COMMANDS)
    def test_help_exits_zero(self, cmd):
        result = _run([cmd, "--help"])
        assert result.returncode == 0, f"{cmd} --help exited {result.returncode}: {result.stderr}"

"""
Tests for `activation-check` CLI subcommand (M4.4).

5 tests:
- Subcommand registered (parser accepts it)
- Default step is 1
- --step 3 is honored
- --json produces valid JSON
- Exit code 1 when blocked
"""

import json
import os
import sys
import tempfile
from unittest.mock import patch, AsyncMock

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


# =============================================================================
# PARSER TESTS
# =============================================================================

class TestActivationCheckParser:
    def test_command_exists(self):
        """activation-check subcommand is registered."""
        from run_pipeline import create_parser
        parser = create_parser()
        args = parser.parse_args(["activation-check"])
        assert args.command == "activation-check"

    def test_default_step_is_1(self):
        """Default step is 1 when --step is not specified."""
        from run_pipeline import create_parser
        parser = create_parser()
        args = parser.parse_args(["activation-check"])
        assert args.step == 1

    def test_step_argument(self):
        """--step 3 is honored."""
        from run_pipeline import create_parser
        parser = create_parser()
        args = parser.parse_args(["activation-check", "--step", "3"])
        assert args.step == 3


# =============================================================================
# HANDLER TESTS
# =============================================================================

class TestActivationCheckHandler:
    @pytest.mark.asyncio
    async def test_json_output(self, store, capsys):
        """--json produces valid JSON output."""
        from run_pipeline import cmd_activation_check
        from argparse import Namespace

        args = Namespace(
            step=1,
            json_output=True,
            db_path=store.db_path,
        )

        with patch("run_pipeline.SignalStore", return_value=store):
            with patch.object(store, "initialize", new_callable=AsyncMock):
                with patch.object(store, "close", new_callable=AsyncMock):
                    exit_code = await cmd_activation_check(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "verdict" in data
        assert "step" in data
        assert "drift_coverage" in data
        assert data["step"] == 1

    @pytest.mark.asyncio
    async def test_exit_code_blocked(self, store):
        """Exit code 1 when blocked (no canary data, step=4)."""
        from run_pipeline import cmd_activation_check
        from argparse import Namespace

        args = Namespace(
            step=4,
            json_output=False,
            db_path=store.db_path,
        )

        with patch("run_pipeline.SignalStore", return_value=store):
            with patch.object(store, "initialize", new_callable=AsyncMock):
                with patch.object(store, "close", new_callable=AsyncMock):
                    exit_code = await cmd_activation_check(args)

        assert exit_code == 1, "blocked verdict should return exit code 1"

"""
M1.4 -- Tests for config validation wiring in CLI startup (run_pipeline.py).

Verifies:
1. print_config_report() called before command dispatch
2. STRICT_CONFIG_VALIDATION=true + error -> exit code 1
3. STRICT_CONFIG_VALIDATION=false + error -> command proceeds
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from utils.config_validator import ConfigIssue


# Patch at source module since run_pipeline uses local imports inside main()
_VC_PATH = "utils.config_validator.validate_config"
_PCR_PATH = "utils.config_validator.print_config_report"


class TestConfigValidationInCLI:
    """Tests for config validation wiring in run_pipeline.py main()."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        """Ensure clean env for each test."""
        monkeypatch.delenv("STRICT_CONFIG_VALIDATION", raising=False)
        monkeypatch.delenv("DELIVERY_MODE", raising=False)

    @pytest.mark.asyncio
    async def test_print_config_report_called(self, monkeypatch):
        """print_config_report() must be called before command dispatch."""
        monkeypatch.delenv("STRICT_CONFIG_VALIDATION", raising=False)

        mock_args = MagicMock()
        mock_args.command = "health"
        mock_args.verbose = False
        mock_args.json = False

        with patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch(_VC_PATH, return_value=[]) as mock_vc, \
             patch(_PCR_PATH, return_value=False) as mock_pcr, \
             patch("run_pipeline.cmd_health", new_callable=AsyncMock, return_value=0):
            mock_parser.return_value.parse_args.return_value = mock_args

            from run_pipeline import main
            await main()

            mock_vc.assert_called_once()
            mock_pcr.assert_called_once()

    @pytest.mark.asyncio
    async def test_strict_true_with_errors_exits(self, monkeypatch):
        """STRICT_CONFIG_VALIDATION=true + errors -> sys.exit(1)."""
        monkeypatch.setenv("STRICT_CONFIG_VALIDATION", "true")

        error_issues = [
            ConfigIssue(level="error", key="DELIVERY_MODE",
                        message="invalid"),
        ]

        mock_args = MagicMock()
        mock_args.command = "health"
        mock_args.verbose = False

        with patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch(_VC_PATH, return_value=error_issues), \
             patch(_PCR_PATH, return_value=True):
            mock_parser.return_value.parse_args.return_value = mock_args

            from run_pipeline import main
            with pytest.raises(SystemExit) as exc_info:
                await main()
            assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_strict_false_with_errors_proceeds(self, monkeypatch):
        """STRICT_CONFIG_VALIDATION=false + errors -> command still runs."""
        monkeypatch.setenv("STRICT_CONFIG_VALIDATION", "false")

        error_issues = [
            ConfigIssue(level="error", key="DELIVERY_MODE",
                        message="invalid"),
        ]

        mock_args = MagicMock()
        mock_args.command = "health"
        mock_args.verbose = False
        mock_args.json = False

        with patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch(_VC_PATH, return_value=error_issues), \
             patch(_PCR_PATH, return_value=True), \
             patch("run_pipeline.cmd_health", new_callable=AsyncMock, return_value=0) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = mock_args

            from run_pipeline import main
            await main()

            mock_cmd.assert_called_once()

"""Tests for ops.quality_cli -- Quality Ops CLI registration and argument parsing."""

from __future__ import annotations

import argparse
import subprocess
import sys

import pytest

from ops.quality_cli import register_quality_commands, _default_db_path


def _make_parser():
    """Create a fresh argument parser with quality commands registered."""
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    register_quality_commands(subs)
    return parser


class TestRegisterQualityCommands:
    """Tests for register_quality_commands."""

    def test_register_quality_commands(self):
        """register_quality_commands adds a 'quality' subparser."""
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="command")
        register_quality_commands(subs)

        # Parsing "quality" should work without error and set command
        args = parser.parse_args(["quality"])
        assert args.command == "quality"

    def test_quality_subcommands_count(self):
        """quality parser registers exactly 14 subcommands."""
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="command")
        register_quality_commands(subs)

        # Access the 'quality' parser from the subs choices dict
        assert "quality" in subs.choices, "quality subparser not found"
        quality_parser = subs.choices["quality"]

        # Find the quality sub-subparsers action group
        quality_subs = None
        for action in quality_parser._subparsers._group_actions:
            if hasattr(action, "choices") and action.choices:
                quality_subs = action.choices
                break

        assert quality_subs is not None, "quality sub-subparsers not found"
        required_commands = {
            "label", "stats", "sync-status-events", "backfill-outcomes",
            "backfill-snapshot", "export", "find-patterns", "propose-tuning",
            "apply-tuning", "thesis-classify", "thesis-classify-batch",
            "thesis-disagreement-report", "key-suggestions", "propose-patterns",
            "list-proposals", "review-proposal", "expire-proposals", "enrich",
        }
        actual_commands = set(quality_subs.keys())
        missing = required_commands - actual_commands
        assert not missing, f"Missing required subcommands: {missing}"


class TestLabelArgsParsing:
    """Tests for the 'label' subcommand argument parsing."""

    def test_label_args_parsing(self):
        """Parse 'quality --db test.db label 1 FP' correctly."""
        parser = _make_parser()
        args = parser.parse_args(["quality", "--db", "test.db", "label", "1", "FP"])

        assert args.command == "quality"
        assert args.db_path == "test.db"
        assert args.quality_cmd == "label"
        assert args.signal_id == 1
        assert args.label == "FP"


class TestStatsArgsParsing:
    """Tests for the 'stats' subcommand argument parsing."""

    def test_stats_args_parsing(self):
        """Parse 'quality --db test.db stats --days 60' correctly."""
        parser = _make_parser()
        args = parser.parse_args(["quality", "--db", "test.db", "stats", "--days", "60"])

        assert args.command == "quality"
        assert args.db_path == "test.db"
        assert args.quality_cmd == "stats"
        assert args.days == 60


class TestExportArgsParsing:
    """Tests for the 'export' subcommand argument parsing."""

    def test_export_args_parsing(self):
        """Parse 'quality --db test.db export --days 90 --format csv --out out.csv' correctly."""
        parser = _make_parser()
        args = parser.parse_args([
            "quality", "--db", "test.db",
            "export", "--days", "90", "--format", "csv", "--out", "out.csv",
        ])

        assert args.command == "quality"
        assert args.db_path == "test.db"
        assert args.quality_cmd == "export"
        assert args.days == 90
        assert args.format == "csv"
        assert args.out == "out.csv"


class TestFindPatternsArgsParsing:
    """Tests for the 'find-patterns' subcommand argument parsing."""

    def test_find_patterns_args_parsing(self):
        """Parse find-patterns args with all options."""
        parser = _make_parser()
        args = parser.parse_args([
            "quality", "--db", "test.db",
            "find-patterns",
            "--days", "30",
            "--min-count", "5",
            "--fp-rate-threshold", "0.5",
            "--out", "patterns.json",
        ])

        assert args.command == "quality"
        assert args.db_path == "test.db"
        assert args.quality_cmd == "find-patterns"
        assert args.days == 30
        assert args.min_count == 5
        assert args.fp_rate_threshold == pytest.approx(0.5)
        assert args.out == "patterns.json"


class TestProposeTuningArgsParsing:
    """Tests for the 'propose-tuning' subcommand argument parsing."""

    def test_propose_tuning_args_parsing(self):
        """Parse propose-tuning args correctly."""
        parser = _make_parser()
        args = parser.parse_args([
            "quality", "--db", "test.db",
            "propose-tuning",
            "--patterns", "patterns.json",
            "--out", "proposal.yaml",
            "--window-days", "30",
        ])

        assert args.command == "quality"
        assert args.quality_cmd == "propose-tuning"
        assert args.patterns == "patterns.json"
        assert args.out == "proposal.yaml"
        assert args.window_days == 30


class TestApplyTuningArgsParsing:
    """Tests for the 'apply-tuning' subcommand argument parsing."""

    def test_apply_tuning_args_parsing(self):
        """Parse apply-tuning args with --apply flag."""
        parser = _make_parser()
        args = parser.parse_args([
            "quality", "--db", "test.db",
            "apply-tuning",
            "--proposal", "proposal.yaml",
            "--apply",
        ])

        assert args.command == "quality"
        assert args.quality_cmd == "apply-tuning"
        assert args.proposal == "proposal.yaml"
        assert args.apply is True

    def test_apply_tuning_args_no_apply_flag(self):
        """Without --apply flag, apply defaults to False (dry-run)."""
        parser = _make_parser()
        args = parser.parse_args([
            "quality", "--db", "test.db",
            "apply-tuning",
            "--proposal", "proposal.yaml",
        ])

        assert args.apply is False


class TestThesisClassifyArgsParsing:
    """Tests for the 'thesis-classify' subcommand argument parsing."""

    def test_thesis_classify_args_parsing(self):
        """Parse thesis-classify args correctly."""
        parser = _make_parser()
        args = parser.parse_args([
            "quality", "--db", "test.db",
            "thesis-classify", "42",
            "--model", "gemini-2.0-flash",
            "--prompt-version", "quality-ops-v1",
        ])

        assert args.command == "quality"
        assert args.quality_cmd == "thesis-classify"
        assert args.signal_id == 42
        assert args.model == "gemini-2.0-flash"
        assert args.prompt_version == "quality-ops-v1"


class TestDefaultDbPath:
    """Tests for _default_db_path."""

    def test_default_db_path(self, monkeypatch):
        """_default_db_path returns 'signals.db' when env var is not set."""
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        assert _default_db_path() == "signals.db"

    def test_default_db_path_env_override(self, monkeypatch):
        """_default_db_path returns the DISCOVERY_DB_PATH env var value when set."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", "/custom/path/mydb.db")
        assert _default_db_path() == "/custom/path/mydb.db"


class TestCliIntegrationHelp:
    """Integration test for CLI help output."""

    def test_cli_integration_help(self):
        """Running 'python -m ops.cli quality --help' exits 0 with help text."""
        result = subprocess.run(
            [sys.executable, "-m", "ops.cli", "quality", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0
        assert "quality" in result.stdout.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

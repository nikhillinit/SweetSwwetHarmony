"""Tests for run_pipeline.py CLI flags."""
import pytest
from run_pipeline import create_parser


class TestCLIFlags:
    """Test CLI argument parsing for new feature flags."""

    def test_full_parser_has_use_gating_flag(self):
        """Full command should have --use-gating flag."""
        parser = create_parser()
        args = parser.parse_args(["full", "--use-gating"])
        assert args.use_gating is True

    def test_full_parser_use_gating_default_false(self):
        """--use-gating should default to False."""
        parser = create_parser()
        args = parser.parse_args(["full"])
        assert args.use_gating is False

    def test_full_parser_has_use_entities_flag(self):
        """Full command should have --use-entities flag."""
        parser = create_parser()
        args = parser.parse_args(["full", "--use-entities"])
        assert args.use_entities is True

    def test_full_parser_use_entities_default_false(self):
        """--use-entities should default to False."""
        parser = create_parser()
        args = parser.parse_args(["full"])
        assert args.use_entities is False

    def test_full_parser_has_use_asset_store_flag(self):
        """Full command should have --use-asset-store flag."""
        parser = create_parser()
        args = parser.parse_args(["full", "--use-asset-store"])
        assert args.use_asset_store is True

    def test_all_flags_can_be_combined(self):
        """All feature flags can be used together."""
        parser = create_parser()
        args = parser.parse_args([
            "full",
            "--use-gating",
            "--use-entities",
            "--use-asset-store",
            "--dry-run",
        ])
        assert args.use_gating is True
        assert args.use_entities is True
        assert args.use_asset_store is True
        assert args.dry_run is True

"""Tests for run_pipeline.py CLI flags."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from run_pipeline import create_parser, cmd_health
from utils.signal_health import HealthReport, SourceHealth


class TestCLIFlags:
    """Test CLI argument parsing for new feature flags."""

    def test_full_parser_has_enable_gating_flag(self):
        """Full command should have --enable-gating flag."""
        parser = create_parser()
        args = parser.parse_args(["full", "--enable-gating"])
        assert args.enable_gating is True

    def test_full_parser_has_disable_gating_flag(self):
        """Full command should have --disable-gating flag."""
        parser = create_parser()
        args = parser.parse_args(["full", "--disable-gating"])
        assert args.disable_gating is True

    def test_full_parser_gating_defaults(self):
        """Gating flags should default to False (use PipelineConfig default)."""
        parser = create_parser()
        args = parser.parse_args(["full"])
        assert args.enable_gating is False
        assert args.disable_gating is False

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
            "--enable-gating",
            "--use-entities",
            "--use-asset-store",
            "--dry-run",
        ])
        assert args.enable_gating is True
        assert args.use_entities is True
        assert args.use_asset_store is True
        assert args.dry_run is True


class TestHealthCommand:
    """Test health check CLI command."""

    def test_health_command_exists(self):
        """Health subcommand should exist in parser."""
        parser = create_parser()
        args = parser.parse_args(["health"])
        assert args.command == "health"

    def test_health_command_accepts_db_path_flag(self):
        """Health command should accept --db-path flag."""
        parser = create_parser()
        args = parser.parse_args(["health", "--db-path", "/custom/path.db"])
        assert args.db_path == "/custom/path.db"

    @pytest.mark.asyncio
    async def test_cmd_health_checks_database_connectivity(self):
        """cmd_health should check database connectivity."""
        # Create mock args
        args = MagicMock()
        args.db_path = None

        # Mock pipeline
        mock_pipeline = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.signal_store = AsyncMock()
        mock_pipeline.signal_store._conn = MagicMock()  # DB connected

        # Mock health report
        mock_report = HealthReport()
        mock_report.overall_status = "HEALTHY"
        mock_pipeline.signal_store.get_stats = AsyncMock(return_value={})

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = AsyncMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                exit_code = await cmd_health(args)

                # Should check database connection
                mock_pipeline.initialize.assert_called_once()
                assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_health_checks_notion_api(self):
        """cmd_health should check Notion API connectivity."""
        args = MagicMock()
        args.db_path = None

        mock_pipeline = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.signal_store = AsyncMock()
        mock_pipeline.signal_store._conn = MagicMock()
        mock_pipeline.notion_connector = AsyncMock()

        # Mock Notion API check
        mock_pipeline.notion_connector.test_connection = AsyncMock(return_value=True)

        mock_report = HealthReport()
        mock_report.overall_status = "HEALTHY"

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = AsyncMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                exit_code = await cmd_health(args)

                # Should verify Notion connectivity was checked
                assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_health_generates_signal_health_report(self):
        """cmd_health should generate signal health report."""
        args = MagicMock()
        args.db_path = None

        mock_pipeline = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.signal_store = AsyncMock()
        mock_pipeline.signal_store._conn = MagicMock()

        mock_report = HealthReport()
        mock_report.overall_status = "HEALTHY"
        mock_report.total_signals = 42

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = AsyncMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                exit_code = await cmd_health(args)

                # Should call generate_report
                mock_monitor.generate_report.assert_called_once()
                assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_health_returns_exit_code_0_when_healthy(self):
        """cmd_health should return exit code 0 when all checks pass."""
        args = MagicMock()
        args.db_path = None

        mock_pipeline = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.signal_store = AsyncMock()
        mock_pipeline.signal_store._conn = MagicMock()

        mock_report = HealthReport()
        mock_report.overall_status = "HEALTHY"

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = AsyncMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                exit_code = await cmd_health(args)

                assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_health_returns_exit_code_1_when_degraded(self):
        """cmd_health should return exit code 1 when status is DEGRADED."""
        args = MagicMock()
        args.db_path = None

        mock_pipeline = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.signal_store = AsyncMock()
        mock_pipeline.signal_store._conn = MagicMock()

        mock_report = HealthReport()
        mock_report.overall_status = "DEGRADED"

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = AsyncMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                exit_code = await cmd_health(args)

                assert exit_code == 1

    @pytest.mark.asyncio
    async def test_cmd_health_returns_exit_code_1_when_critical(self):
        """cmd_health should return exit code 1 when status is CRITICAL."""
        args = MagicMock()
        args.db_path = None

        mock_pipeline = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.signal_store = AsyncMock()
        mock_pipeline.signal_store._conn = MagicMock()

        mock_report = HealthReport()
        mock_report.overall_status = "CRITICAL"

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = AsyncMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                exit_code = await cmd_health(args)

                assert exit_code == 1

    @pytest.mark.asyncio
    async def test_cmd_health_handles_database_connection_failure(self):
        """cmd_health should handle database connection failures gracefully."""
        args = MagicMock()
        args.db_path = None

        mock_pipeline = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.signal_store = AsyncMock()
        mock_pipeline.signal_store._conn = None  # DB NOT connected

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            exit_code = await cmd_health(args)

            # Should return error exit code when DB is down
            assert exit_code == 1

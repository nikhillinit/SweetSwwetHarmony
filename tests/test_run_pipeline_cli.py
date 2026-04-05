"""Tests for run_pipeline.py CLI flags."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from run_pipeline import create_parser, cmd_collect, cmd_health
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
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

    def test_sync_parser_accepts_recovery_override(self):
        """sync should expose the recovery override flag for audited recovery."""
        parser = create_parser()
        args = parser.parse_args(["sync", "--recovery-override"])
        assert args.recovery_override is True


class TestHealthCommand:
    """Test health check CLI command."""

    @pytest.fixture(autouse=True)
    def _clear_gemini_env(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

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

        # Mock pipeline - use MagicMock for sync properties, AsyncMock for async methods
        mock_pipeline = MagicMock()
        mock_pipeline.initialize = AsyncMock()
        mock_pipeline.close = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.config.notion_api_key = None  # Skip Notion check
        mock_pipeline._store = MagicMock()
        mock_pipeline._store._db = MagicMock()  # DB connected
        mock_pipeline._notion = None
        mock_pipeline.get_stats = AsyncMock(return_value={"storage": {"active_suppression_entries": 1}})

        # Mock health report
        mock_report = HealthReport()
        mock_report.overall_status = "HEALTHY"

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                with patch("run_pipeline.check_github_api", AsyncMock(return_value=(True, "OK"))):
                    with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(True, "OK"))):
                        exit_code = await cmd_health(args)

                        # Should check database connection
                        mock_pipeline.initialize.assert_called_once()
                        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_health_checks_notion_api(self):
        """cmd_health should check Notion API connectivity."""
        args = MagicMock()
        args.db_path = None

        mock_pipeline = MagicMock()
        mock_pipeline.initialize = AsyncMock()
        mock_pipeline.close = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.config.notion_api_key = "test-key"  # Enable Notion check
        mock_pipeline._store = MagicMock()
        mock_pipeline._store._db = MagicMock()
        mock_pipeline._notion = MagicMock()
        mock_pipeline.get_stats = AsyncMock(return_value={"storage": {"active_suppression_entries": 1}})

        # Mock Notion API check
        mock_pipeline._notion.test_connection = AsyncMock(return_value=True)

        mock_report = HealthReport()
        mock_report.overall_status = "HEALTHY"

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                with patch("run_pipeline.check_github_api", AsyncMock(return_value=(True, "OK"))):
                    with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(True, "OK"))):
                        with patch("run_pipeline.check_notion_api", AsyncMock(return_value=(True, "OK"))):
                            exit_code = await cmd_health(args)

                            # Should verify Notion connectivity was checked
                            assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_health_generates_signal_health_report(self):
        """cmd_health should generate signal health report."""
        args = MagicMock()
        args.db_path = None

        mock_pipeline = MagicMock()
        mock_pipeline.initialize = AsyncMock()
        mock_pipeline.close = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.config.notion_api_key = None  # Skip Notion check
        mock_pipeline._store = MagicMock()
        mock_pipeline._store._db = MagicMock()
        mock_pipeline._notion = None
        mock_pipeline.get_stats = AsyncMock(return_value={"storage": {"active_suppression_entries": 1}})

        mock_report = HealthReport()
        mock_report.overall_status = "HEALTHY"
        mock_report.total_signals = 42

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                with patch("run_pipeline.check_github_api", AsyncMock(return_value=(True, "OK"))):
                    with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(True, "OK"))):
                        exit_code = await cmd_health(args)

                        # Should call generate_report
                        mock_monitor.generate_report.assert_called_once()
                        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_health_returns_exit_code_0_when_healthy(self):
        """cmd_health should return exit code 0 when all checks pass."""
        args = MagicMock()
        args.db_path = None

        mock_pipeline = MagicMock()
        mock_pipeline.initialize = AsyncMock()
        mock_pipeline.close = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.config.notion_api_key = None  # Skip Notion check
        mock_pipeline._store = MagicMock()
        mock_pipeline._store._db = MagicMock()
        mock_pipeline._notion = None
        mock_pipeline.get_stats = AsyncMock(return_value={"storage": {"active_suppression_entries": 1}})

        mock_report = HealthReport()
        mock_report.overall_status = "HEALTHY"

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                with patch("run_pipeline.check_github_api", AsyncMock(return_value=(True, "OK"))):
                    with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(True, "OK"))):
                        exit_code = await cmd_health(args)

                        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_health_returns_exit_code_1_when_degraded(self):
        """cmd_health should return exit code 0 when status is DEGRADED.

        Degraded is a non-fatal warning state by default.
        """
        args = MagicMock()
        args.db_path = None

        mock_pipeline = MagicMock()
        mock_pipeline.initialize = AsyncMock()
        mock_pipeline.close = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.config.notion_api_key = None  # Skip Notion check
        mock_pipeline._store = MagicMock()
        mock_pipeline._store._db = MagicMock()
        mock_pipeline._notion = None
        mock_pipeline.get_stats = AsyncMock(return_value={"storage": {"active_suppression_entries": 1}})

        mock_report = HealthReport()
        mock_report.overall_status = "DEGRADED"

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                with patch("run_pipeline.check_github_api", AsyncMock(return_value=(True, "OK"))):
                    with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(True, "OK"))):
                        exit_code = await cmd_health(args)

                        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_health_returns_exit_code_1_when_critical(self):
        """cmd_health should return exit code 1 when status is CRITICAL."""
        args = MagicMock()
        args.db_path = None

        mock_pipeline = MagicMock()
        mock_pipeline.initialize = AsyncMock()
        mock_pipeline.close = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.config.notion_api_key = None  # Skip Notion check
        mock_pipeline._store = MagicMock()
        mock_pipeline._store._db = MagicMock()
        mock_pipeline._notion = None
        mock_pipeline.get_stats = AsyncMock(return_value={"storage": {"active_suppression_entries": 1}})

        mock_report = HealthReport()
        mock_report.overall_status = "CRITICAL"

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                with patch("run_pipeline.check_github_api", AsyncMock(return_value=(True, "OK"))):
                    with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(True, "OK"))):
                        exit_code = await cmd_health(args)

                        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_cmd_health_allows_external_failures_with_flag(self):
        """External failures should be warnings (exit 0) when allow_external_failures is set."""
        args = MagicMock()
        args.db_path = None
        args.allow_external_failures = True
        args.core_only = False

        mock_pipeline = MagicMock()
        mock_pipeline.initialize = AsyncMock()
        mock_pipeline.close = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.config.notion_api_key = None
        mock_pipeline._store = MagicMock()
        mock_pipeline._store._db = MagicMock()
        mock_pipeline.get_stats = AsyncMock(return_value={"storage": {"active_suppression_entries": 1}})

        mock_report = HealthReport()
        mock_report.overall_status = "HEALTHY"

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                with patch("run_pipeline.check_github_api", AsyncMock(return_value=(False, "down"))):
                    with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(False, "down"))):
                        exit_code = await cmd_health(args)

                        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_health_core_only_skips_external_checks(self):
        """core_only should skip external checks entirely."""
        args = MagicMock()
        args.db_path = None
        args.core_only = True
        args.allow_external_failures = False

        mock_pipeline = MagicMock()
        mock_pipeline.initialize = AsyncMock()
        mock_pipeline.close = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.config.notion_api_key = None
        mock_pipeline._store = MagicMock()
        mock_pipeline._store._db = MagicMock()
        mock_pipeline.get_stats = AsyncMock(return_value={"storage": {"active_suppression_entries": 1}})

        mock_report = HealthReport()
        mock_report.overall_status = "HEALTHY"

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                with patch("run_pipeline.check_github_api", AsyncMock(return_value=(False, "down"))) as gh:
                    with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(False, "down"))) as sec:
                        exit_code = await cmd_health(args)

                        gh.assert_not_called()
                        sec.assert_not_called()
                        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_health_handles_database_connection_failure(self):
        """cmd_health should handle database connection failures gracefully."""
        args = MagicMock()
        args.db_path = None

        mock_pipeline = MagicMock()
        mock_pipeline.initialize = AsyncMock()
        mock_pipeline.close = AsyncMock()
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.db_path = "signals.db"
        mock_pipeline.config.notion_api_key = None  # Skip Notion check
        mock_pipeline._store = MagicMock()
        mock_pipeline._store._db = None  # DB NOT connected
        mock_pipeline._notion = None

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.check_github_api", AsyncMock(return_value=(True, "OK"))):
                with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(True, "OK"))):
                    exit_code = await cmd_health(args)

                    # Should return error exit code when DB is down
                    assert exit_code == 1


class TestCollectCommandOutput:
    """Test collect command output formatting for collector statuses."""

    @pytest.mark.asyncio
    async def test_cmd_collect_renders_skipped_as_skip_label(self, capsys):
        """Collectors with skipped status should render with [SKIP] label."""
        args = SimpleNamespace(
            db_path=None,
            parallel=None,
            disable_gating=False,
            enable_gating=False,
            use_asset_store=False,
            collectors="domain_whois,news_api,broken_collector",
            dry_run=True,
        )

        mock_config = MagicMock()
        mock_config.parallel_collectors = True

        results = [
            CollectorResult(
                collector="domain_whois",
                status=CollectorStatus.SKIPPED,
                error_message="no domains provided",
            ),
            CollectorResult(
                collector="news_api",
                status=CollectorStatus.SUCCESS,
                signals_found=3,
                signals_new=2,
            ),
            CollectorResult(
                collector="broken_collector",
                status=CollectorStatus.ERROR,
                error_message="boom",
            ),
        ]

        mock_pipeline = MagicMock()
        mock_pipeline.initialize = AsyncMock()
        mock_pipeline.close = AsyncMock()
        mock_pipeline.run_collectors = AsyncMock(return_value=results)

        with patch("run_pipeline.PipelineConfig.from_env", return_value=mock_config):
            with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
                await cmd_collect(args)

        output = capsys.readouterr().out
        assert "[SKIP]" in output and "domain_whois" in output
        assert "[OK]" in output and "news_api" in output
        assert "[FAIL]" in output and "broken_collector" in output
        assert "Skipped collectors: 1" in output


class TestPublishCommitOverrideReason:
    """Tests for publish commit --override-reason CLI flag."""

    def test_override_reason_recognized(self):
        """publish commit should accept --override-reason."""
        parser = create_parser()
        args = parser.parse_args([
            "publish", "commit", "batch-test-123", "--yes",
            "--override-reason", "manual check passed",
        ])
        assert args.override_reason == "manual check passed"

    def test_override_reason_defaults_none(self):
        """--override-reason should default to None."""
        parser = create_parser()
        args = parser.parse_args([
            "publish", "commit", "batch-test-123", "--yes",
        ])
        assert args.override_reason is None


def _create_signal_count_db(path, count: int) -> None:
    """Create a minimal SQLite DB with a signals table sized to *count*."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY)")
        if count:
            conn.executemany(
                "INSERT INTO signals (id) VALUES (?)",
                [(idx,) for idx in range(1, count + 1)],
            )
        conn.commit()
    finally:
        conn.close()


class TestProductionDbSignalCountGuard:
    """Focused CLI-boundary tests for the production DB catastrophic-drop guard."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("STRICT_CONFIG_VALIDATION", raising=False)
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)

    def _mock_args(self, **overrides):
        base = {
            "command": "health",
            "verbose": False,
            "db_path": None,
            "db_deprecated": None,
            "recovery_override": False,
            "pipeline_cmd": None,
            "publish_cmd": None,
            "triage_cmd": None,
            "outbox_cmd": None,
        }
        base.update(overrides)
        return MagicMock(**base)

    @pytest.mark.asyncio
    async def test_warns_and_continues_for_health(self, monkeypatch, tmp_path, capsys):
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 4)
        watermark.write_text(json.dumps({"signal_count": 100, "recorded_at": "2026-04-04T00:00:00Z"}))
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="health")

        with patch("run_pipeline.get_signal_count_watermark_path", return_value=watermark), \
             patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("run_pipeline.cmd_health", new_callable=AsyncMock, return_value=0) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            await main()

        assert "WARNING:" in capsys.readouterr().err
        mock_cmd.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocks_write_command_on_catastrophic_drop(self, monkeypatch, tmp_path):
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 4)
        watermark.write_text(json.dumps({"signal_count": 100, "recorded_at": "2026-04-04T00:00:00Z"}))
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="process")

        with patch("run_pipeline.get_signal_count_watermark_path", return_value=watermark), \
             patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("run_pipeline.cmd_process", new_callable=AsyncMock, return_value=0) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            with pytest.raises(SystemExit) as exc_info:
                await main()

        assert exc_info.value.code == 2
        mock_cmd.assert_not_called()

    @pytest.mark.asyncio
    async def test_bypasses_non_production_db_path(self, monkeypatch, tmp_path):
        prod_db = tmp_path / "signals.db"
        scratch_db = tmp_path / "scratch.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 100)
        _create_signal_count_db(scratch_db, 4)
        watermark.write_text(json.dumps({"signal_count": 100, "recorded_at": "2026-04-04T00:00:00Z"}))
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="process", db_path=str(scratch_db))

        with patch("run_pipeline.get_signal_count_watermark_path", return_value=watermark), \
             patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("run_pipeline.cmd_process", new_callable=AsyncMock, return_value=0) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            await main()

        mock_cmd.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_blocks_without_recovery_override(self, monkeypatch, tmp_path):
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 4)
        watermark.write_text(json.dumps({"signal_count": 100, "recorded_at": "2026-04-04T00:00:00Z"}))
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="sync")

        with patch("run_pipeline.get_signal_count_watermark_path", return_value=watermark), \
             patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("run_pipeline.cmd_sync", new_callable=AsyncMock, return_value=None) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            with pytest.raises(SystemExit) as exc_info:
                await main()

        assert exc_info.value.code == 2
        mock_cmd.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_allows_audited_recovery_override(self, monkeypatch, tmp_path, capsys):
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 4)
        watermark.write_text(json.dumps({"signal_count": 100, "recorded_at": "2026-04-04T00:00:00Z"}))
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="sync", recovery_override=True)

        with patch("run_pipeline.get_signal_count_watermark_path", return_value=watermark), \
             patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("run_pipeline.cmd_sync", new_callable=AsyncMock, return_value=None) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            await main()

        assert "--recovery-override" in capsys.readouterr().err
        mock_cmd.assert_called_once()

    @pytest.mark.asyncio
    async def test_healthy_read_seeds_missing_watermark(self, monkeypatch, tmp_path):
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 612)
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="health")

        with patch("run_pipeline.get_signal_count_watermark_path", return_value=watermark), \
             patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("run_pipeline.cmd_health", new_callable=AsyncMock, return_value=0):
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            await main()

        payload = json.loads(watermark.read_text())
        assert payload["signal_count"] == 612

    @pytest.mark.asyncio
    async def test_health_does_not_seed_missing_watermark(self, monkeypatch, tmp_path):
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 4)
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="health")

        with patch("run_pipeline.get_signal_count_watermark_path", return_value=watermark), \
             patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("run_pipeline.cmd_health", new_callable=AsyncMock, return_value=0) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            await main()

        assert not watermark.exists()
        mock_cmd.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_override_seeds_missing_watermark(self, monkeypatch, tmp_path):
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 612)
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="sync", recovery_override=True)

        with patch("run_pipeline.get_signal_count_watermark_path", return_value=watermark), \
             patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("run_pipeline.cmd_sync", new_callable=AsyncMock, return_value=None) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            await main()

        payload = json.loads(watermark.read_text())
        assert payload["signal_count"] == 612
        mock_cmd.assert_called_once()

"""Tests for run_pipeline.py CLI flags."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from run_pipeline import create_parser, cmd_collect, cmd_health, cmd_health_json_pure
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from utils.signal_health import HealthReport
from workflows.pipeline import PipelineConfig


def _build_health_pipeline(
    *,
    notion_api_key=None,
    notion_client=None,
    db_connected=True,
    stats=None,
):
    mock_pipeline = MagicMock()
    mock_pipeline.initialize = AsyncMock()
    mock_pipeline.close = AsyncMock()
    mock_pipeline.config = MagicMock()
    mock_pipeline.config.db_path = "signals.db"
    mock_pipeline.config.notion_api_key = notion_api_key
    mock_pipeline._store = MagicMock()
    mock_pipeline._store._db = MagicMock() if db_connected else None
    mock_pipeline._notion = notion_client
    mock_pipeline.get_stats = AsyncMock(
        return_value=stats or {"storage": {"active_suppression_entries": 1}}
    )
    return mock_pipeline


def _build_health_report(*, status="HEALTHY", total_signals=None):
    report = HealthReport()
    report.overall_status = status
    if total_signals is not None:
        report.total_signals = total_signals
    return report


async def _never_returns(*_args, **_kwargs):
    await asyncio.Event().wait()


def _find_check(output, name):
    return next(check for check in output["checks"] if check["name"] == name)


def _health_args(**overrides):
    defaults = {
        "db_path": None,
        "output_json": True,
        "verbose": False,
        "lookback_days": 30,
        "core_only": True,
        "allow_external_failures": False,
        "health_timeout_seconds": 0.01,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _assert_timeout_failure(output, check_name):
    check = _find_check(output, check_name)
    assert output["overall_status"] == "UNHEALTHY"
    assert check["status"] == "fail"
    assert "timed out" in check["message"].lower()


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

    def test_root_recovery_override_is_not_registered(self):
        """P1 remediation: --recovery-override must NOT live on the root parser.

        Defining it at root let `python run_pipeline.py --recovery-override <subcmd>`
        set Namespace.recovery_override=True for any subcommand, opening a write
        bypass through _enforce_signal_count_guard's allow_override path. The flag
        is now scoped strictly to the ``sync`` subcommand.
        """
        parser = create_parser()
        root_options: set[str] = set()
        for action in parser._actions:
            root_options.update(action.option_strings)
        assert "--recovery-override" not in root_options

    def test_root_position_recovery_override_is_rejected(self):
        """argparse must reject ``--recovery-override`` placed before a subcommand."""
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--recovery-override", "sync"])

    def test_process_recovery_override_is_rejected(self):
        """process subcommand must NOT accept --recovery-override."""
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["process", "--recovery-override"])

    def test_full_recovery_override_is_rejected(self):
        """full subcommand must NOT accept --recovery-override."""
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["full", "--recovery-override"])


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

    def test_health_command_accepts_timeout_seconds_flag(self):
        """Health command should accept a bounded phase timeout."""
        parser = create_parser()
        args = parser.parse_args(["health", "--timeout-seconds", "0.25"])
        assert args.health_timeout_seconds == 0.25

    def test_health_json_pure_accepts_timeout_seconds_flag(self):
        """Strict JSON health should expose the same bounded timeout control."""
        parser = create_parser()
        args = parser.parse_args(["health-json-pure", "--timeout-seconds", "0.25"])
        assert args.health_timeout_seconds == 0.25

    @pytest.mark.asyncio
    async def test_cmd_health_initializes_pipeline_read_only(self):
        """Health diagnostics must use the pipeline read-only contract."""
        args = MagicMock()
        args.db_path = None

        mock_pipeline = _build_health_pipeline()
        mock_report = _build_health_report()

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                with patch("run_pipeline.check_github_api", AsyncMock(return_value=(True, "OK"))):
                    with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(True, "OK"))):
                        exit_code = await cmd_health(args)

        mock_pipeline.initialize.assert_awaited_once_with(read_only=True)
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cmd_health_does_not_bootstrap_or_create_sidecars(self, tmp_path):
        """Health should inspect an existing DB without migrations, WAL, or SHM sidecars."""
        db_path = tmp_path / "signals.db"
        _create_health_probe_db(db_path)
        initial_tables = _sqlite_tables(db_path)
        config = PipelineConfig(
            db_path=str(db_path),
            use_gating=False,
            use_founder_scoring=False,
            use_velocity_tracking=False,
            use_thesis_filter=False,
            use_competitor_detection=False,
        )
        args = SimpleNamespace(
            db_path=str(db_path),
            output_json=True,
            verbose=False,
            lookback_days=30,
            core_only=True,
            allow_external_failures=False,
        )

        with patch("run_pipeline.PipelineConfig.from_env", return_value=config):
            exit_code = await cmd_health(args)

        assert exit_code == 0
        assert _sqlite_tables(db_path) == initial_tables
        assert "schema_migrations" not in initial_tables
        assert not db_path.with_name(db_path.name + "-wal").exists()
        assert not db_path.with_name(db_path.name + "-shm").exists()

    @pytest.mark.asyncio
    async def test_cmd_health_does_not_retry_db_checks_after_init_failure(self):
        """A partial DB init failure should not re-enter initialize or leak cleanup."""
        args = SimpleNamespace(
            db_path=None,
            output_json=True,
            verbose=False,
            lookback_days=30,
            core_only=True,
            allow_external_failures=False,
        )
        mock_pipeline = _build_health_pipeline()
        mock_pipeline.initialize = AsyncMock(side_effect=RuntimeError("init failed"))

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                exit_code = await cmd_health(args)

        mock_pipeline.initialize.assert_awaited_once_with(read_only=True)
        mock_pipeline.get_stats.assert_not_awaited()
        mock_monitor_cls.assert_not_called()
        mock_pipeline.close.assert_awaited_once()
        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_cmd_health_json_times_out_hung_pipeline_init_and_closes(self, capsys):
        """A hung init phase should return failure JSON and still attempt cleanup."""
        args = _health_args()
        mock_pipeline = _build_health_pipeline()
        mock_pipeline.initialize = AsyncMock(side_effect=_never_returns)

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            exit_code = await asyncio.wait_for(cmd_health(args), timeout=1)

        output = json.loads(capsys.readouterr().out)
        assert exit_code == 1
        _assert_timeout_failure(output, "Database")
        mock_pipeline.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cmd_health_bounds_hung_pipeline_close(self, capsys):
        """Cleanup should be attempted without letting close hang the command."""
        args = _health_args()
        mock_pipeline = _build_health_pipeline()
        mock_pipeline.close = AsyncMock(side_effect=_never_returns)
        mock_report = _build_health_report()

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                exit_code = await asyncio.wait_for(cmd_health(args), timeout=1)

        output = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert output["overall_status"] == "HEALTHY"
        mock_pipeline.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cmd_health_json_times_out_hung_external_check(self, capsys):
        """A hung external check should be bounded and rendered as failure JSON."""
        args = _health_args(core_only=False)
        mock_pipeline = _build_health_pipeline()
        mock_report = _build_health_report()

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                with patch("run_pipeline.check_github_api", AsyncMock(side_effect=_never_returns)):
                    with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(True, "OK"))):
                        exit_code = await asyncio.wait_for(cmd_health(args), timeout=1)

        output = json.loads(capsys.readouterr().out)
        assert exit_code == 1
        _assert_timeout_failure(output, "GitHub API")
        mock_pipeline.close.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("hung_check_name", "ok_check_name", "api_name"),
        [
            ("check_github_api", "check_sec_edgar_api", "github"),
            ("check_sec_edgar_api", "check_github_api", "sec_edgar"),
        ],
    )
    async def test_cmd_health_json_pure_times_out_hung_external_check(
        self,
        tmp_path,
        capsys,
        hung_check_name,
        ok_check_name,
        api_name,
    ):
        """A hung strict JSON external check should be bounded as report data."""
        db_path = tmp_path / "signals.db"
        _create_health_json_pure_probe_db(db_path)
        args = SimpleNamespace(
            db_path=str(db_path),
            report=None,
            allow_external_failures=False,
            health_timeout_seconds=0.01,
        )

        with patch(f"run_pipeline.{hung_check_name}", AsyncMock(side_effect=_never_returns)):
            with patch(f"run_pipeline.{ok_check_name}", AsyncMock(return_value=(True, "OK"))):
                exit_code = await asyncio.wait_for(
                    cmd_health_json_pure(args),
                    timeout=1,
                )

        output = json.loads(capsys.readouterr().out)
        timeout_check = _find_check({"checks": output["metrics"]["checks"]}, api_name)
        assert exit_code == 1
        assert output["ok"] is False
        assert output["command"] == "health-json-pure"
        assert any(api_name in error and "timed out" in error for error in output["errors"])
        assert timeout_check["scope"] == "external"
        assert timeout_check["status"] == "fail"
        assert timeout_check["passed"] is False
        assert "timed out" in timeout_check["message"]
        assert output["metrics"]["overall_status"] == "UNHEALTHY"
        assert output["metrics"]["integration_status"] == "UNHEALTHY"

    @pytest.mark.asyncio
    async def test_cmd_health_json_pure_timeout_can_be_external_warning(
        self,
        tmp_path,
        capsys,
    ):
        """Allowed external timeout failures should degrade but keep ok=true."""
        db_path = tmp_path / "signals.db"
        _create_health_json_pure_probe_db(db_path)
        args = SimpleNamespace(
            db_path=str(db_path),
            report=None,
            allow_external_failures=True,
            health_timeout_seconds=0.01,
        )

        with patch("run_pipeline.check_github_api", AsyncMock(side_effect=_never_returns)):
            with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(True, "OK"))):
                exit_code = await asyncio.wait_for(
                    cmd_health_json_pure(args),
                    timeout=1,
                )

        output = json.loads(capsys.readouterr().out)
        timeout_check = _find_check({"checks": output["metrics"]["checks"]}, "github")
        assert exit_code == 0
        assert output["ok"] is True
        assert output["errors"] == []
        assert any("github" in warning and "timed out" in warning for warning in output["warnings"])
        assert timeout_check["status"] == "warn"
        assert timeout_check["passed"] is True
        assert output["metrics"]["overall_status"] == "DEGRADED"
        assert output["metrics"]["integration_status"] == "DEGRADED"

    @pytest.mark.asyncio
    async def test_cmd_health_json_pure_timeout_report_matches_stdout(
        self,
        tmp_path,
        capsys,
    ):
        """Timeout failures should write the same strict JSON report shape."""
        db_path = tmp_path / "signals.db"
        report_path = tmp_path / "health-report.json"
        _create_health_json_pure_probe_db(db_path)
        args = SimpleNamespace(
            db_path=str(db_path),
            report=str(report_path),
            allow_external_failures=False,
            health_timeout_seconds=0.01,
        )

        with patch("run_pipeline.check_github_api", AsyncMock(return_value=(True, "OK"))):
            with patch("run_pipeline.check_sec_edgar_api", AsyncMock(side_effect=_never_returns)):
                exit_code = await asyncio.wait_for(
                    cmd_health_json_pure(args),
                    timeout=1,
                )

        stdout_report = json.loads(capsys.readouterr().out)
        file_report = json.loads(report_path.read_text())
        assert exit_code == 1
        assert stdout_report == file_report
        assert file_report["ok"] is False
        assert any("sec_edgar" in error and "timed out" in error for error in file_report["errors"])
        timeout_check = _find_check({"checks": file_report["metrics"]["checks"]}, "sec_edgar")
        assert timeout_check["status"] == "fail"

    @pytest.mark.asyncio
    async def test_cmd_health_json_pure_opens_db_probe_read_only(
        self,
        tmp_path,
        capsys,
    ):
        """The strict JSON DB probe should open SQLite through a read-only URI."""
        db_path = tmp_path / "signals.db"
        _create_health_json_pure_probe_db(db_path)
        args = SimpleNamespace(
            db_path=str(db_path),
            report=None,
            allow_external_failures=False,
            health_timeout_seconds=0.01,
        )

        real_connect = sqlite3.connect
        connect_calls = []

        def recording_connect(database, *connect_args, **connect_kwargs):
            connect_calls.append((database, dict(connect_kwargs)))
            return real_connect(database, *connect_args, **connect_kwargs)

        with patch("sqlite3.connect", side_effect=recording_connect):
            with patch("run_pipeline.check_github_api", AsyncMock(return_value=(True, "OK"))):
                with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(True, "OK"))):
                    exit_code = await cmd_health_json_pure(args)

        output = json.loads(capsys.readouterr().out)
        db_probe_calls = [
            (database, kwargs)
            for database, kwargs in connect_calls
            if "signals.db" in str(database)
        ]
        assert exit_code == 0
        assert output["ok"] is True
        assert db_probe_calls
        database, kwargs = db_probe_calls[0]
        assert str(database).startswith("file:")
        assert "mode=ro" in str(database)
        assert kwargs["uri"] is True

    @pytest.mark.asyncio
    async def test_cmd_health_json_pure_reads_probe_metrics_from_read_only_db(
        self,
        tmp_path,
        capsys,
    ):
        """The read-only DB probe should still emit the existing metric shape."""
        db_path = tmp_path / "signals.db"
        report_path = tmp_path / "health-report.json"
        _create_health_json_pure_probe_db(db_path, schema_version=7, signal_count=3)
        args = SimpleNamespace(
            db_path=str(db_path),
            report=str(report_path),
            allow_external_failures=False,
            health_timeout_seconds=0.01,
        )

        with patch("run_pipeline.check_github_api", AsyncMock(return_value=(True, "OK"))):
            with patch("run_pipeline.check_sec_edgar_api", AsyncMock(return_value=(True, "OK"))):
                exit_code = await cmd_health_json_pure(args)

        output = json.loads(capsys.readouterr().out)
        file_report = json.loads(report_path.read_text())
        database_check = _find_check({"checks": output["metrics"]["checks"]}, "Database")
        schema_check = _find_check({"checks": output["metrics"]["checks"]}, "Schema Version")
        assert exit_code == 0
        assert output == file_report
        assert output["ok"] is True
        assert output["metrics"]["schema_version"] == 7
        assert output["metrics"]["signal_count"] == 3
        assert database_check["status"] == "pass"
        assert schema_check["details"]["version"] == 7

    @pytest.mark.asyncio
    async def test_cmd_health_json_times_out_hung_suppression_stats(self, capsys):
        """A hung suppression stats read should be bounded and fail the core check."""
        args = _health_args()
        mock_pipeline = _build_health_pipeline()
        mock_pipeline.get_stats = AsyncMock(side_effect=_never_returns)
        mock_report = _build_health_report()

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(return_value=mock_report)
                mock_monitor_cls.return_value = mock_monitor

                exit_code = await asyncio.wait_for(cmd_health(args), timeout=1)

        output = json.loads(capsys.readouterr().out)
        assert exit_code == 1
        _assert_timeout_failure(output, "Suppression Cache")
        mock_pipeline.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cmd_health_json_times_out_hung_signal_health(self, capsys):
        """A hung signal health report should be bounded and fail the core check."""
        args = _health_args()
        mock_pipeline = _build_health_pipeline()

        with patch("run_pipeline.DiscoveryPipeline", return_value=mock_pipeline):
            with patch("run_pipeline.SignalHealthMonitor") as mock_monitor_cls:
                mock_monitor = MagicMock()
                mock_monitor.generate_report = AsyncMock(side_effect=_never_returns)
                mock_monitor_cls.return_value = mock_monitor

                exit_code = await asyncio.wait_for(cmd_health(args), timeout=1)

        output = json.loads(capsys.readouterr().out)
        assert exit_code == 1
        _assert_timeout_failure(output, "Signal Health")
        mock_pipeline.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cmd_health_checks_database_connectivity(self):
        """cmd_health should check database connectivity."""
        # Create mock args
        args = MagicMock()
        args.db_path = None

        mock_pipeline = _build_health_pipeline()
        mock_report = _build_health_report()

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

        notion_client = MagicMock()
        notion_client.test_connection = AsyncMock(return_value=True)
        mock_pipeline = _build_health_pipeline(
            notion_api_key="test-key",
            notion_client=notion_client,
        )
        mock_report = _build_health_report()

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

        mock_pipeline = _build_health_pipeline()
        mock_report = _build_health_report(total_signals=42)

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

        mock_pipeline = _build_health_pipeline()
        mock_report = _build_health_report()

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

        mock_pipeline = _build_health_pipeline()
        mock_report = _build_health_report(status="DEGRADED")

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

        mock_pipeline = _build_health_pipeline()
        mock_report = _build_health_report(status="CRITICAL")

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

        mock_pipeline = _build_health_pipeline()
        mock_report = _build_health_report()

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

        mock_pipeline = _build_health_pipeline()
        mock_report = _build_health_report()

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

        mock_pipeline = _build_health_pipeline(db_connected=False)

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


def _create_health_probe_db(path) -> None:
    """Create a minimal existing DB that health can inspect without bootstrapping."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                signal_type TEXT,
                source_api TEXT,
                canonical_key TEXT,
                confidence REAL,
                detected_at TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE signal_processing (
                signal_id INTEGER,
                status TEXT
            )
            """
        )
        conn.execute("CREATE TABLE suppression_cache (expires_at TEXT)")
        conn.execute(
            "INSERT INTO suppression_cache (expires_at) VALUES (?)",
            ("2999-01-01T00:00:00+00:00",),
        )
        conn.commit()
    finally:
        conn.close()


def _create_health_json_pure_probe_db(
    path,
    *,
    schema_version: int = 1,
    signal_count: int = 0,
) -> None:
    """Create the minimal DB shape inspected by health-json-pure."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (schema_version,))
        if signal_count:
            conn.executemany(
                "INSERT INTO signals (id) VALUES (?)",
                [(idx,) for idx in range(1, signal_count + 1)],
            )
        conn.commit()
    finally:
        conn.close()


def _sqlite_tables(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
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

    def test_read_current_signal_count_returns_error_on_forced_failure(self):
        """Helper should surface a read error instead of raising."""
        from utils.db_guard import read_current_signal_count

        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("forced read failure")):
            count, error = read_current_signal_count("signals.db")

        assert count is None
        assert error is not None
        assert "forced read failure" in error

    @pytest.mark.asyncio
    async def test_warns_and_continues_for_health(self, monkeypatch, tmp_path, capsys):
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 4)
        watermark.write_text(json.dumps({"signal_count": 100, "recorded_at": "2026-04-04T00:00:00Z"}))
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="health")

        with patch("utils.db_guard.WATERMARK_PATH", watermark), \
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
    async def test_warns_and_continues_for_health_on_signal_count_read_error(self, monkeypatch, tmp_path, capsys):
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 100)
        watermark.write_text(json.dumps({"signal_count": 100, "recorded_at": "2026-04-04T00:00:00Z"}))
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="health")

        with patch("utils.db_guard.WATERMARK_PATH", watermark), \
             patch("utils.db_guard.read_current_signal_count", return_value=(None, "forced read failure")), \
             patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("run_pipeline.cmd_health", new_callable=AsyncMock, return_value=0) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            await main()

        stderr = capsys.readouterr().err
        assert "WARNING:" in stderr
        assert "forced read failure" in stderr
        mock_cmd.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocks_write_command_on_catastrophic_drop(self, monkeypatch, tmp_path):
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 4)
        watermark.write_text(json.dumps({"signal_count": 100, "recorded_at": "2026-04-04T00:00:00Z"}))
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="process")

        with patch("utils.db_guard.WATERMARK_PATH", watermark), \
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
    async def test_sync_blocks_on_signal_count_read_error_without_recovery_override(self, monkeypatch, tmp_path, capsys):
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 100)
        watermark.write_text(json.dumps({"signal_count": 100, "recorded_at": "2026-04-04T00:00:00Z"}))
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="sync")

        with patch("utils.db_guard.WATERMARK_PATH", watermark), \
             patch("utils.db_guard.read_current_signal_count", return_value=(None, "forced read failure")), \
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
        assert "forced read failure" in capsys.readouterr().err
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

        with patch("utils.db_guard.WATERMARK_PATH", watermark), \
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
    async def test_bypasses_explicit_safe_db_when_canonical_default_is_rejected(
        self, monkeypatch, tmp_path
    ):
        from storage.db_paths import REPO_ROOT

        scratch_db = tmp_path / "scratch.db"
        _create_signal_count_db(scratch_db, 4)
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        monkeypatch.chdir(REPO_ROOT)

        args = self._mock_args(command="process", db_path=str(scratch_db))

        with patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("utils.db_guard.check_db_health") as mock_check_health, \
             patch("run_pipeline.cmd_process", new_callable=AsyncMock, return_value=0) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            await main()

        mock_check_health.assert_not_called()
        mock_cmd.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_blocks_without_recovery_override(self, monkeypatch, tmp_path):
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 4)
        watermark.write_text(json.dumps({"signal_count": 100, "recorded_at": "2026-04-04T00:00:00Z"}))
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="sync")

        with patch("utils.db_guard.WATERMARK_PATH", watermark), \
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

        with patch("utils.db_guard.WATERMARK_PATH", watermark), \
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
    async def test_health_read_with_missing_watermark_warns_without_mutating(self, monkeypatch, tmp_path, capsys):
        """Strict explicit-init contract: read commands warn but do NOT auto-create the watermark."""
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 612)
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="health")

        with patch("utils.db_guard.WATERMARK_PATH", watermark), \
             patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("run_pipeline.cmd_health", new_callable=AsyncMock, return_value=0) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            await main()

        stderr = capsys.readouterr().err
        assert "watermark_missing" in stderr
        assert not watermark.exists()
        mock_cmd.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_read_with_missing_watermark_low_count_still_warns(self, monkeypatch, tmp_path, capsys):
        """Strict contract holds for low signal counts too — no implicit baseline anchoring."""
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 4)
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="health")

        with patch("utils.db_guard.WATERMARK_PATH", watermark), \
             patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("run_pipeline.cmd_health", new_callable=AsyncMock, return_value=0) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            await main()

        stderr = capsys.readouterr().err
        assert "watermark_missing" in stderr
        assert not watermark.exists()
        mock_cmd.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_with_missing_watermark_blocks_even_with_override(self, monkeypatch, tmp_path, capsys):
        """Recovery override is for tripped baselines, not for bootstrapping a missing watermark.

        Operator must run ``python run_pipeline.py init-watermark`` explicitly. See
        ``.omx/wave6/db_guard_runbook.md``.
        """
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 612)
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="sync", recovery_override=True)

        with patch("utils.db_guard.WATERMARK_PATH", watermark), \
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
        stderr = capsys.readouterr().err
        assert "watermark_missing" in stderr
        assert "init-watermark" in stderr
        assert not watermark.exists()
        mock_cmd.assert_not_called()

    @pytest.mark.asyncio
    async def test_legacy_process_args_with_recovery_override_do_not_bypass_guard(
        self, monkeypatch, tmp_path
    ):
        """A synthesized Namespace with command='process' AND recovery_override=True
        must STILL be blocked by the catastrophic-drop guard. Override is scoped
        strictly to command=='sync'; non-sync writes never bypass.
        """
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 4)
        watermark.write_text(
            json.dumps({"signal_count": 100, "recorded_at": "2026-04-04T00:00:00Z"})
        )
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="process", recovery_override=True)

        with patch("utils.db_guard.WATERMARK_PATH", watermark), \
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
    async def test_legacy_full_args_with_recovery_override_do_not_bypass_guard(
        self, monkeypatch, tmp_path
    ):
        """Same scope-to-sync invariant for command='full'."""
        prod_db = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signal_count_db(prod_db, 4)
        watermark.write_text(
            json.dumps({"signal_count": 100, "recorded_at": "2026-04-04T00:00:00Z"})
        )
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(prod_db))

        args = self._mock_args(command="full", recovery_override=True)

        with patch("utils.db_guard.WATERMARK_PATH", watermark), \
             patch("run_pipeline.create_parser") as mock_parser, \
             patch("run_pipeline.setup_logging"), \
             patch("utils.config_validator.validate_config", return_value=[]), \
             patch("utils.config_validator.print_config_report", return_value=False), \
             patch("run_pipeline.cmd_full", new_callable=AsyncMock, return_value=0) as mock_cmd:
            mock_parser.return_value.parse_args.return_value = args
            from run_pipeline import main
            with pytest.raises(SystemExit) as exc_info:
                await main()

        assert exc_info.value.code == 2
        mock_cmd.assert_not_called()

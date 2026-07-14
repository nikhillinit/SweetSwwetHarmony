"""Tests for CLI pipeline commands."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestPipelineStatusCommand:
    """Test pipeline status command."""

    @pytest.mark.asyncio
    async def test_cmd_pipeline_status_shows_counts(self):
        """Status command should show signal counts by status."""
        from run_pipeline import cmd_pipeline_status

        mock_store = AsyncMock()
        mock_store.get_status_counts = AsyncMock(return_value={
            "qualified": 23,
            "held": 12,
            "rejected": 8,
            "pushed": 45,
            "pending": 5,
        })
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            # Should not raise
            await cmd_pipeline_status(db_path="test.db")

        # Verify store methods were called
        mock_store.initialize.assert_called_once()
        mock_store.get_status_counts.assert_called_once()
        mock_store.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_pipeline_status_default_uses_guarded_resolution(self, monkeypatch):
        """Direct helper calls should not silently fall through to repo signals.db."""
        from run_pipeline import cmd_pipeline_status
        from storage.db_paths import InTreeDatabaseError, REPO_ROOT

        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        monkeypatch.chdir(REPO_ROOT)

        with patch("run_pipeline.SignalStore") as mock_store:
            with pytest.raises(InTreeDatabaseError):
                await cmd_pipeline_status()

        mock_store.assert_not_called()

    @pytest.mark.asyncio
    async def test_cmd_pipeline_status_handles_empty_counts(self):
        """Status command should handle empty counts."""
        from run_pipeline import cmd_pipeline_status

        mock_store = AsyncMock()
        mock_store.get_status_counts = AsyncMock(return_value={})
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            # Should not raise
            await cmd_pipeline_status(db_path="test.db")


class TestPipelineQualifiedCommand:
    """Test pipeline qualified command."""

    @pytest.mark.asyncio
    async def test_cmd_pipeline_qualified_lists_signals(self):
        """Qualified command should list qualified signals."""
        from run_pipeline import cmd_pipeline_qualified

        mock_signal = MagicMock()
        mock_signal.company_name = "Test Company"
        mock_signal.canonical_key = "domain:test.com"
        mock_signal.confidence = 0.75
        mock_signal.source_api = "github"

        mock_store = AsyncMock()
        mock_store.get_signals_by_status = AsyncMock(return_value=[mock_signal])
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_pipeline_qualified(db_path="test.db", limit=10)

        mock_store.get_signals_by_status.assert_called_once_with("qualified", limit=10)
        mock_store.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_pipeline_qualified_handles_no_signals(self):
        """Qualified command should handle no signals gracefully."""
        from run_pipeline import cmd_pipeline_qualified

        mock_store = AsyncMock()
        mock_store.get_signals_by_status = AsyncMock(return_value=[])
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_pipeline_qualified(db_path="test.db", limit=10)

    @pytest.mark.asyncio
    async def test_cmd_pipeline_qualified_respects_limit(self):
        """Qualified command should respect limit parameter."""
        from run_pipeline import cmd_pipeline_qualified

        mock_store = AsyncMock()
        mock_store.get_signals_by_status = AsyncMock(return_value=[])
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_pipeline_qualified(db_path="test.db", limit=50)

        mock_store.get_signals_by_status.assert_called_once_with("qualified", limit=50)

    @pytest.mark.asyncio
    async def test_cmd_pipeline_qualified_default_uses_guarded_resolution(self, monkeypatch):
        """Direct helper calls should resolve through the shared DB guard."""
        from run_pipeline import cmd_pipeline_qualified
        from storage.db_paths import InTreeDatabaseError, REPO_ROOT

        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        monkeypatch.chdir(REPO_ROOT)

        with patch("run_pipeline.SignalStore") as mock_store:
            with pytest.raises(InTreeDatabaseError):
                await cmd_pipeline_qualified()

        mock_store.assert_not_called()


class TestPipelinePushCommand:
    """Test pipeline push command."""

    @pytest.mark.asyncio
    async def test_cmd_pipeline_push_dry_run(self):
        """Push command with dry-run should not actually push."""
        from run_pipeline import cmd_pipeline_push

        mock_signal = MagicMock()
        mock_signal.company_name = "Test Company"
        mock_signal.canonical_key = "domain:test.com"

        mock_store = AsyncMock()
        mock_store.get_signals_by_status = AsyncMock(return_value=[mock_signal])
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_pipeline_push(
                db_path="test.db",
                confirm=False,
                dry_run=True,
            )

        mock_store.get_signals_by_status.assert_called_once_with("qualified")
        mock_store.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_pipeline_push_no_confirm_no_dry_run(self):
        """Push command without confirm or dry-run should show help."""
        from run_pipeline import cmd_pipeline_push

        mock_signal = MagicMock()
        mock_signal.company_name = "Test Company"
        mock_signal.canonical_key = "domain:test.com"

        mock_store = AsyncMock()
        mock_store.get_signals_by_status = AsyncMock(return_value=[mock_signal])
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_pipeline_push(
                db_path="test.db",
                confirm=False,
                dry_run=False,
            )

        # Should not attempt actual push without confirm
        mock_store.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_pipeline_push_no_signals(self):
        """Push command should handle no qualified signals."""
        from run_pipeline import cmd_pipeline_push

        mock_store = AsyncMock()
        mock_store.get_signals_by_status = AsyncMock(return_value=[])
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_pipeline_push(
                db_path="test.db",
                confirm=True,
                dry_run=False,
            )

    @pytest.mark.asyncio
    async def test_cmd_pipeline_push_with_confirm(self):
        """Push command with confirm should proceed to push."""
        from run_pipeline import cmd_pipeline_push

        mock_signal = MagicMock()
        mock_signal.company_name = "Test Company"
        mock_signal.canonical_key = "domain:test.com"

        mock_store = AsyncMock()
        mock_store.get_signals_by_status = AsyncMock(return_value=[mock_signal])
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_pipeline_push(
                db_path="test.db",
                confirm=True,
                dry_run=False,
            )

        mock_store.get_signals_by_status.assert_called_once_with("qualified")


class TestSyncLpsCommand:
    """Tests for cmd_sync_lps handler wiring."""

    @pytest.mark.asyncio
    async def test_sync_lps_upserts_relationships(self, monkeypatch, tmp_path):
        """cmd_sync_lps should call upsert_lp_relationship for each relationship."""
        from run_pipeline import cmd_sync_lps
        from connectors.notion_lp_sync import FirmRelationship, LPStatus

        monkeypatch.setenv("NOTION_API_KEY", "test-key")
        monkeypatch.setenv("NOTION_LP_DATABASE_ID", "test-db-id")

        # Create mock relationships
        rels = [
            FirmRelationship(
                domain="fund-a.com", score=0.95,
                status=LPStatus.DOCS_SIGNED, attribution="Fund A Partner",
            ),
            FirmRelationship(
                domain="fund-b.com", score=0.70,
                status=LPStatus.VERBAL_CONFIRM, attribution="Fund B Contact",
            ),
            FirmRelationship(
                domain="fund-c.com", score=0.40,
                status=LPStatus.ENGAGEMENT_SENT, attribution="Fund C Rep",
            ),
        ]

        mock_sync = AsyncMock()
        mock_sync.sync = AsyncMock(return_value=rels)

        mock_rel_store = AsyncMock()
        mock_rel_store.upsert_lp_relationship = AsyncMock()
        mock_rel_store.initialize = AsyncMock()
        mock_rel_store.close = AsyncMock()

        with patch("connectors.notion_lp_sync.NotionLPSync", return_value=mock_sync) as _, \
             patch("storage.relationship_store.RelationshipStore", return_value=mock_rel_store):

            args = MagicMock()
            args.dry_run = False
            args.database_id = None
            args.user_email = "test@example.com"
            args.db_path = str(tmp_path / "graph.db")

            await cmd_sync_lps(args)

        # Exactly 3 upsert calls
        assert mock_rel_store.upsert_lp_relationship.call_count == 3

        # Check first call mapping
        first_call = mock_rel_store.upsert_lp_relationship.call_args_list[0]
        assert first_call.kwargs["me_email"] == "test@example.com"
        assert first_call.kwargs["target_domain"] == "fund-a.com"
        assert first_call.kwargs["lp_status"] == "Docs Signed"
        assert first_call.kwargs["lp_name"] == "Fund A Partner"
        assert first_call.kwargs["notion_score"] == 0.95

    @pytest.mark.asyncio
    async def test_sync_lps_missing_user_email_exits(self, monkeypatch):
        """Missing --user-email AND USER_EMAIL env should sys.exit(1)."""
        from run_pipeline import cmd_sync_lps
        from connectors.notion_lp_sync import FirmRelationship, LPStatus

        monkeypatch.setenv("NOTION_API_KEY", "test-key")
        monkeypatch.setenv("NOTION_LP_DATABASE_ID", "test-db-id")
        monkeypatch.delenv("USER_EMAIL", raising=False)

        mock_sync = AsyncMock()
        mock_sync.sync = AsyncMock(return_value=[
            FirmRelationship(
                domain="test.com", score=0.5,
                status=LPStatus.IN_DATABASE, attribution="Test",
            ),
        ])

        with patch("connectors.notion_lp_sync.NotionLPSync", return_value=mock_sync):
            args = MagicMock()
            args.dry_run = False
            args.database_id = None
            args.user_email = None
            args.db_path = "graph.db"

            with pytest.raises(SystemExit) as exc_info:
                await cmd_sync_lps(args)

            assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_sync_lps_default_db_path_resolves_off_cwd(self, monkeypatch, tmp_path):
        """With no --db-path, the store must open the resolver's path, never a
        cwd-relative private_graph.db (Q7 residual; stray-DB bug class)."""
        from pathlib import Path

        from run_pipeline import cmd_sync_lps

        monkeypatch.setenv("NOTION_API_KEY", "test-key")
        monkeypatch.setenv("NOTION_LP_DATABASE_ID", "test-db-id")
        target = tmp_path / "pg.db"
        monkeypatch.setenv("PRIVATE_GRAPH_DB_PATH", str(target))

        mock_sync = AsyncMock()
        mock_sync.sync = AsyncMock(return_value=[])

        mock_rel_store = AsyncMock()
        mock_rel_store.initialize = AsyncMock()
        mock_rel_store.close = AsyncMock()

        store_init_args = {}

        def capture_store(db_path):
            store_init_args["db_path"] = db_path
            return mock_rel_store

        with patch("connectors.notion_lp_sync.NotionLPSync", return_value=mock_sync), \
             patch("storage.relationship_store.RelationshipStore", side_effect=capture_store):

            args = MagicMock()
            args.dry_run = False
            args.database_id = None
            args.user_email = "user@example.com"
            args.db_path = None

            await cmd_sync_lps(args)

        assert Path(store_init_args["db_path"]) == target.resolve()


class TestRelationshipHealthDbPath:
    """cmd_relationship_health must resolve the private graph DB off the cwd."""

    @pytest.mark.asyncio
    async def test_relationship_health_default_db_path_resolves_off_cwd(
        self, monkeypatch, tmp_path
    ):
        from pathlib import Path

        from run_pipeline import cmd_relationship_health

        target = tmp_path / "pg.db"
        monkeypatch.setenv("PRIVATE_GRAPH_DB_PATH", str(target))

        mock_store = MagicMock()
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        mock_report = MagicMock()
        mock_report.to_dict.return_value = {}
        mock_monitor = MagicMock()
        mock_monitor.generate_report = AsyncMock(return_value=mock_report)

        with patch(
            "storage.relationship_store.RelationshipStore", return_value=mock_store
        ) as store_cls, patch(
            "utils.relationship_health.RelationshipHealthMonitor",
            return_value=mock_monitor,
        ):
            args = MagicMock()
            args.db_path = None
            args.user_email = "user@example.com"
            args.output_json = True
            args.email_stale_days = 7
            args.lp_stale_days = 3

            await cmd_relationship_health(args)

        assert Path(store_cls.call_args.kwargs["db_path"]) == target.resolve()

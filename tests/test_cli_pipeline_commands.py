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

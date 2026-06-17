"""Tests for cmd_pipeline_push DB path guarding (Track 3) and NotionPusher wiring (Track 4)."""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class TestCmdPipelinePushDbPath:
    """cmd_pipeline_push must apply the in-tree guard to its DB path."""

    def test_rejects_in_tree_db_when_no_env(self, monkeypatch):
        """Default 'signals.db' resolves inside the repo → InTreeDatabaseError."""
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)

        from storage.db_paths import InTreeDatabaseError
        from run_pipeline import cmd_pipeline_push

        with pytest.raises(InTreeDatabaseError):
            asyncio.run(cmd_pipeline_push())

    def test_rejects_explicit_in_tree_path(self, monkeypatch):
        """Even an explicit --db-path pointing in-tree must be rejected."""
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)

        from storage.db_paths import InTreeDatabaseError
        from run_pipeline import cmd_pipeline_push

        with pytest.raises(InTreeDatabaseError):
            asyncio.run(cmd_pipeline_push(db_path="signals.db"))

    def test_accepts_out_of_tree_path(self, monkeypatch, tmp_path):
        """An explicit out-of-tree path is accepted (passes the guard, then the store
        can fail for other reasons like table not found — that's ok for this test)."""
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)

        from run_pipeline import cmd_pipeline_push

        # The guard should pass; after that SignalStore may fail but that's fine
        try:
            asyncio.run(cmd_pipeline_push(db_path=str(tmp_path / "test.db")))
        except Exception as e:
            # Should NOT be InTreeDatabaseError
            from storage.db_paths import InTreeDatabaseError
            assert not isinstance(e, InTreeDatabaseError), (
                f"Should not raise InTreeDatabaseError for out-of-tree path, got: {e}"
            )


class TestCmdPipelinePushNotionWiring:
    """cmd_pipeline_push must call NotionPusher.process_single_prospect, not print a stub."""

    @pytest.mark.asyncio
    async def test_confirms_calls_process_prospect_with_qualified_signals(self, monkeypatch, tmp_path):
        """pipeline push --confirm must call _process_prospect with only qualified signals.

        Uses _process_prospect (not process_single_prospect) so that the push is
        scoped to the pre-loaded qualified signals rather than reloading all signals
        for the canonical key (which could include pending/held/rejected rows).
        """
        db = tmp_path / "test.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(db))
        monkeypatch.setenv("HARMONIC_ALLOW_IN_TREE_DB", "true")
        monkeypatch.setenv("NOTION_API_KEY", "secret_test")
        monkeypatch.setenv("NOTION_DATABASE_ID", "db_test")

        # Seed a qualified signal
        from storage.signal_store import SignalStore
        store = SignalStore(str(db))
        await store.initialize()
        await store.save_signal(
            signal_type="trending_repo",
            source_api="github",
            canonical_key="domain:test.ai",
            company_name="Test AI",
            confidence=0.8,
            raw_data={"repo": "test/repo"},
        )
        # Mark it qualified (save_signal creates a 'pending' record)
        await store._db.execute(
            "UPDATE signal_processing SET status = 'qualified' WHERE signal_id IN "
            "(SELECT id FROM signals WHERE canonical_key = 'domain:test.ai')"
        )
        await store._db.commit()
        await store.close()

        mock_result = MagicMock()
        mock_result.error = None
        mock_result.decision = MagicMock()
        mock_result.decision.value = "source"
        mock_result.confidence = 0.8

        with patch("workflows.notion_pusher.NotionPusher._process_prospect",
                   new_callable=AsyncMock, return_value=mock_result) as mock_push:
            from run_pipeline import cmd_pipeline_push
            await cmd_pipeline_push(confirm=True)

        mock_push.assert_called_once()

    @pytest.mark.asyncio
    async def test_stub_text_not_printed(self, monkeypatch, tmp_path, capsys):
        """The old stub text '(Push integration with NotionPusher pending)' must not appear."""
        db = tmp_path / "test.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(db))
        monkeypatch.setenv("HARMONIC_ALLOW_IN_TREE_DB", "true")
        monkeypatch.setenv("NOTION_API_KEY", "secret_test")
        monkeypatch.setenv("NOTION_DATABASE_ID", "db_test")

        # Seed a qualified signal
        from storage.signal_store import SignalStore
        store = SignalStore(str(db))
        await store.initialize()
        await store.save_signal(
            signal_type="trending_repo",
            source_api="github",
            canonical_key="domain:stub-check.ai",
            company_name="Stub Check",
            confidence=0.8,
            raw_data={"repo": "test/repo"},
        )
        await store._db.execute(
            "UPDATE signal_processing SET status = 'qualified' WHERE signal_id IN "
            "(SELECT id FROM signals WHERE canonical_key = 'domain:stub-check.ai')"
        )
        await store._db.commit()
        await store.close()

        mock_result = MagicMock()
        mock_result.error = None
        mock_result.decision = MagicMock()
        mock_result.decision.value = "source"
        mock_result.confidence = 0.8

        with patch("workflows.notion_pusher.NotionPusher._process_prospect",
                   new_callable=AsyncMock, return_value=mock_result):
            from run_pipeline import cmd_pipeline_push
            await cmd_pipeline_push(confirm=True)

        captured = capsys.readouterr()
        assert "NotionPusher pending" not in captured.out, (
            "Stub text should not appear after Track 4 implementation"
        )

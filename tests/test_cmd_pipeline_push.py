"""Tests for cmd_pipeline_push DB path guarding (Track 3 of milestone roadmap)."""

import asyncio
import pytest
from pathlib import Path


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

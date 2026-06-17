"""
Tests for DB path resolution precedence.

Validates the unified resolution chain:
  explicit arg > DISCOVERY_DB_PATH > SIGNAL_DB_PATH > "signals.db"

Covers:
- resolve_db_path_env() standalone
- resolve_db_path() argparse wrapper
- SignalStore() env-awareness
- signal_store() context manager env-awareness
"""

import os
import pytest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


# =============================================================================
# resolve_db_path_env() tests
# =============================================================================

class TestResolveDbPathEnv:
    """Tests for the non-argparse resolution function."""

    def test_explicit_wins_over_all_env(self, monkeypatch):
        """Explicit argument takes highest priority."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", "/env/discovery.db")
        monkeypatch.setenv("SIGNAL_DB_PATH", "/env/signal.db")

        from utils.db_path_helper import resolve_db_path_env
        assert resolve_db_path_env("/explicit/my.db") == "/explicit/my.db"

    def test_explicit_path_object(self):
        """Path objects are accepted and returned as strings."""
        from utils.db_path_helper import resolve_db_path_env
        result = resolve_db_path_env(Path("/some/path.db"))
        assert result == str(Path("/some/path.db"))

    def test_discovery_db_path_wins_over_signal_db_path(self, monkeypatch):
        """DISCOVERY_DB_PATH takes priority over SIGNAL_DB_PATH."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", "/env/discovery.db")
        monkeypatch.setenv("SIGNAL_DB_PATH", "/env/signal.db")

        from utils.db_path_helper import resolve_db_path_env
        assert resolve_db_path_env() == "/env/discovery.db"

    def test_signal_db_path_used_when_discovery_absent(self, monkeypatch):
        """SIGNAL_DB_PATH is used when DISCOVERY_DB_PATH is not set."""
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.setenv("SIGNAL_DB_PATH", "/env/signal.db")

        from utils.db_path_helper import resolve_db_path_env
        assert resolve_db_path_env() == "/env/signal.db"

    def test_default_when_no_env(self, monkeypatch):
        """Falls back to signals.db when no env vars are set."""
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)

        from utils.db_path_helper import resolve_db_path_env
        assert resolve_db_path_env() == "signals.db"

    def test_explicit_none_falls_through_to_env(self, monkeypatch):
        """Passing None explicitly triggers env resolution."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", "/env/discovery.db")

        from utils.db_path_helper import resolve_db_path_env
        assert resolve_db_path_env(None) == "/env/discovery.db"

    def test_empty_discovery_db_path_falls_through(self, monkeypatch):
        """Empty string DISCOVERY_DB_PATH is treated as unset (falsy)."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", "")
        monkeypatch.setenv("SIGNAL_DB_PATH", "/env/signal.db")

        from utils.db_path_helper import resolve_db_path_env
        assert resolve_db_path_env() == "/env/signal.db"

    def test_empty_both_env_falls_to_default(self, monkeypatch):
        """Empty strings for both env vars fall through to default."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", "")
        monkeypatch.setenv("SIGNAL_DB_PATH", "")

        from utils.db_path_helper import resolve_db_path_env
        assert resolve_db_path_env() == "signals.db"


# =============================================================================
# resolve_db_path() argparse wrapper tests
# =============================================================================

class TestResolveDbPath:
    """Tests for the argparse-aware resolution function."""

    def test_db_path_arg_wins(self, monkeypatch):
        """--db-path argument takes top priority."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", "/env/discovery.db")
        args = Namespace(db_path="/cli/explicit.db", db_deprecated=None)

        from utils.db_path_helper import resolve_db_path
        assert resolve_db_path(args) == "/cli/explicit.db"

    def test_deprecated_db_arg_used_with_warning(self, monkeypatch, capsys):
        """--db (deprecated) is used with stderr warning."""
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        args = Namespace(db_path=None, db_deprecated="/cli/old.db")

        from utils.db_path_helper import resolve_db_path
        result = resolve_db_path(args)
        assert result == "/cli/old.db"
        captured = capsys.readouterr()
        assert "DEPRECATED" in captured.err

    def test_falls_through_to_env(self, monkeypatch):
        """Falls through to env when no CLI args provided."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", "/env/discovery.db")
        args = Namespace(db_path=None, db_deprecated=None)

        from utils.db_path_helper import resolve_db_path
        assert resolve_db_path(args) == "/env/discovery.db"

    def test_falls_through_to_default(self, monkeypatch):
        """Falls through to default when no args or env."""
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        args = Namespace(db_path=None, db_deprecated=None)

        from utils.db_path_helper import resolve_db_path
        assert resolve_db_path(args) == "signals.db"


# =============================================================================
# SignalStore env-awareness tests
# =============================================================================

class TestSignalStorePathResolution:
    """Tests that SignalStore respects env vars when no explicit path given."""

    def test_no_arg_uses_discovery_db_path(self, monkeypatch):
        """SignalStore() with no args reads DISCOVERY_DB_PATH."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", "/tmp/test_discovery.db")
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)

        from storage.signal_store import SignalStore
        store = SignalStore()
        assert store.db_path == Path("/tmp/test_discovery.db")

    def test_no_arg_uses_signal_db_path(self, monkeypatch):
        """SignalStore() with no args falls to SIGNAL_DB_PATH."""
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.setenv("SIGNAL_DB_PATH", "/tmp/test_signal.db")

        from storage.signal_store import SignalStore
        store = SignalStore()
        assert store.db_path == Path("/tmp/test_signal.db")

    def test_no_arg_no_env_defaults(self, monkeypatch):
        """SignalStore() with no args and no env defaults to signals.db."""
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)

        from storage.signal_store import SignalStore
        store = SignalStore()
        assert store.db_path == Path("signals.db")

    def test_explicit_arg_wins(self, monkeypatch):
        """SignalStore(db_path=...) overrides env vars."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", "/env/discovery.db")

        from storage.signal_store import SignalStore
        store = SignalStore(db_path="/explicit/my.db")
        assert store.db_path == Path("/explicit/my.db")

    def test_explicit_none_triggers_env(self, monkeypatch):
        """SignalStore(db_path=None) explicitly triggers env resolution."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", "/env/via_none.db")

        from storage.signal_store import SignalStore
        store = SignalStore(db_path=None)
        assert store.db_path == Path("/env/via_none.db")

    def test_path_object_accepted(self, monkeypatch):
        """SignalStore accepts Path objects."""
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)

        from storage.signal_store import SignalStore
        store = SignalStore(db_path=Path("/some/path.db"))
        assert store.db_path == Path("/some/path.db")

    def test_backward_compat_string_arg(self, monkeypatch):
        """Existing callers passing a string path still work."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", "/should/be/ignored.db")

        from storage.signal_store import SignalStore
        store = SignalStore("my_signals.db")
        assert store.db_path == Path("my_signals.db")


# =============================================================================
# Precedence integration test
# =============================================================================

class TestPrecedenceIntegration:
    """Full-chain precedence test matching baseline #9 from the plan."""

    def test_baseline_9_env_probe(self, monkeypatch, tmp_path):
        """Reproduce baseline #9: SignalStore() must respect DISCOVERY_DB_PATH."""
        test_db = str(tmp_path / "test_signals.db")
        monkeypatch.setenv("DISCOVERY_DB_PATH", test_db)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)

        from storage.signal_store import SignalStore
        store = SignalStore()
        assert str(store.db_path) == test_db


# =============================================================================
# Track 3: guard_db_path() helper
# =============================================================================

class TestGuardDbPath:
    """guard_db_path() applies the in-tree safety check to any resolved path."""

    def test_out_of_tree_path_passes(self, tmp_path, monkeypatch):
        """Path outside the repo root is returned unchanged."""
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        out_of_tree = tmp_path / "signals.db"

        from storage.db_paths import guard_db_path
        result = guard_db_path(out_of_tree)
        assert result == out_of_tree

    def test_in_tree_path_raises(self, monkeypatch):
        """Path inside the repo root raises InTreeDatabaseError."""
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        from pathlib import Path
        from storage.db_paths import guard_db_path, InTreeDatabaseError, REPO_ROOT
        in_tree = REPO_ROOT / "signals.db"

        with pytest.raises(InTreeDatabaseError):
            guard_db_path(in_tree)

    def test_in_tree_allowed_when_env_set(self, monkeypatch):
        """HARMONIC_ALLOW_IN_TREE_DB=true bypasses the guard."""
        monkeypatch.setenv("HARMONIC_ALLOW_IN_TREE_DB", "true")
        from pathlib import Path
        from storage.db_paths import guard_db_path, REPO_ROOT
        in_tree = REPO_ROOT / "signals.db"

        result = guard_db_path(in_tree)
        assert result == in_tree

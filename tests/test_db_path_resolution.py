"""Tests for guarded DB path resolution precedence."""

import ast
from argparse import Namespace
from pathlib import Path

import pytest


def _resolved(path: Path) -> str:
    return str(path.resolve())


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


class TestResolveDbPathEnv:
    """Tests for the non-argparse resolution function."""

    def test_explicit_wins_over_all_env(self, monkeypatch, tmp_path):
        """Explicit arguments take priority and are returned as guarded absolutes."""
        explicit = tmp_path / "explicit.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(tmp_path / "env-discovery.db"))
        monkeypatch.setenv("SIGNAL_DB_PATH", str(tmp_path / "env-signal.db"))

        from utils.db_path_helper import resolve_db_path_env

        assert resolve_db_path_env(explicit) == _resolved(explicit)

    def test_memory_db_is_preserved(self):
        """SQLite's in-memory sentinel must not be resolved as a filesystem path."""
        from utils.db_path_helper import resolve_db_path_env

        assert resolve_db_path_env(":memory:") == ":memory:"

    def test_discovery_db_path_wins_over_signal_db_path(self, monkeypatch, tmp_path):
        """DISCOVERY_DB_PATH takes priority over SIGNAL_DB_PATH."""
        primary = tmp_path / "discovery.db"
        secondary = tmp_path / "signal.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(primary))
        monkeypatch.setenv("SIGNAL_DB_PATH", str(secondary))

        from utils.db_path_helper import resolve_db_path_env

        assert resolve_db_path_env() == _resolved(primary)

    def test_signal_db_path_used_when_discovery_absent(self, monkeypatch, tmp_path):
        """SIGNAL_DB_PATH is used when DISCOVERY_DB_PATH is not set."""
        secondary = tmp_path / "signal.db"
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.setenv("SIGNAL_DB_PATH", str(secondary))

        from utils.db_path_helper import resolve_db_path_env

        assert resolve_db_path_env() == _resolved(secondary)

    def test_default_when_no_env_uses_canonical_path(self, monkeypatch):
        """No env falls through to the canonical guarded signals.db path."""
        from storage.db_paths import REPO_ROOT
        from utils.db_path_helper import resolve_db_path_env

        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.chdir(REPO_ROOT)

        assert resolve_db_path_env() == _resolved(REPO_ROOT / "signals.db")

    def test_default_in_tree_fails_closed_without_allow_flag(self, monkeypatch):
        """The old repo-root signals.db default is rejected in production mode."""
        from storage.db_paths import InTreeDatabaseError, REPO_ROOT
        from utils.db_path_helper import resolve_db_path_env

        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        monkeypatch.chdir(REPO_ROOT)

        with pytest.raises(InTreeDatabaseError):
            resolve_db_path_env()

    def test_empty_discovery_db_path_falls_through(self, monkeypatch, tmp_path):
        """Empty string DISCOVERY_DB_PATH is treated as unset."""
        secondary = tmp_path / "signal.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", "")
        monkeypatch.setenv("SIGNAL_DB_PATH", str(secondary))

        from utils.db_path_helper import resolve_db_path_env

        assert resolve_db_path_env() == _resolved(secondary)


class TestProductionDbPathDetection:
    """Tests for production DB path comparisons used by write guards."""

    def test_explicit_out_of_tree_candidate_is_not_production_when_default_rejected(
        self, monkeypatch, tmp_path
    ):
        """A rejected canonical default must not abort explicit safe DB commands."""
        from storage.db_paths import REPO_ROOT
        from utils.db_path_helper import is_production_db_path

        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        monkeypatch.chdir(REPO_ROOT)

        assert is_production_db_path(tmp_path / "explicit.db") is False


class TestArgparseDbDefaults:
    """Tests that argparse entrypoints defer guarded DB resolution."""

    def test_script_argparse_db_defaults_are_not_eagerly_resolved(self):
        """Scripts must parse explicit --db values before applying the DB guard."""
        from storage.db_paths import REPO_ROOT

        offenders = []
        for script in sorted((REPO_ROOT / "scripts").rglob("*.py")):
            tree = ast.parse(script.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _call_name(node.func) != "add_argument":
                    continue
                if not any(
                    isinstance(arg, ast.Constant) and arg.value in {"--db", "--db-path"}
                    for arg in node.args
                ):
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "default" and _call_name(keyword.value) == "resolve_db_path_env":
                        offenders.append(f"{script.relative_to(REPO_ROOT)}:{node.lineno}")

        assert offenders == []


class TestResolveDbPath:
    """Tests for the argparse-aware resolution function."""

    def test_db_path_arg_wins(self, monkeypatch, tmp_path):
        """--db-path takes top priority and is guarded."""
        explicit = tmp_path / "explicit.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(tmp_path / "env.db"))
        args = Namespace(db_path=str(explicit), db_deprecated=None)

        from utils.db_path_helper import resolve_db_path

        assert resolve_db_path(args) == _resolved(explicit)

    def test_deprecated_db_arg_used_with_warning(self, monkeypatch, capsys, tmp_path):
        """--db is still accepted, warned, and guarded."""
        legacy = tmp_path / "old.db"
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        args = Namespace(db_path=None, db_deprecated=str(legacy))

        from utils.db_path_helper import resolve_db_path

        result = resolve_db_path(args)
        assert result == _resolved(legacy)
        captured = capsys.readouterr()
        assert "DEPRECATED" in captured.err

    def test_falls_through_to_env(self, monkeypatch, tmp_path):
        """Falls through to env when no CLI args are provided."""
        target = tmp_path / "discovery.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(target))
        args = Namespace(db_path=None, db_deprecated=None)

        from utils.db_path_helper import resolve_db_path

        assert resolve_db_path(args) == _resolved(target)

    def test_explicit_in_tree_arg_requires_allow_flag(self, monkeypatch):
        """Explicit repo-root DB paths are still rejected without the dev flag."""
        from storage.db_paths import InTreeDatabaseError, REPO_ROOT
        from utils.db_path_helper import resolve_db_path

        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        args = Namespace(db_path=str(REPO_ROOT / "signals.db"), db_deprecated=None)

        with pytest.raises(InTreeDatabaseError):
            resolve_db_path(args)


class TestSignalStorePathResolution:
    """Tests that SignalStore respects the guarded resolver."""

    def test_no_arg_uses_discovery_db_path(self, monkeypatch, tmp_path):
        """SignalStore() with no args reads DISCOVERY_DB_PATH."""
        target = tmp_path / "test_discovery.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(target))
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)

        from storage.signal_store import SignalStore

        store = SignalStore()
        assert store.db_path == target.resolve()

    def test_no_arg_uses_signal_db_path(self, monkeypatch, tmp_path):
        """SignalStore() with no args falls to SIGNAL_DB_PATH."""
        target = tmp_path / "test_signal.db"
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.setenv("SIGNAL_DB_PATH", str(target))

        from storage.signal_store import SignalStore

        store = SignalStore()
        assert store.db_path == target.resolve()

    def test_no_arg_no_env_defaults_to_canonical_path(self, monkeypatch):
        """SignalStore() default is the guarded canonical DB path."""
        from storage.db_paths import REPO_ROOT
        from storage.signal_store import SignalStore

        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.chdir(REPO_ROOT)

        store = SignalStore()
        assert store.db_path == (REPO_ROOT / "signals.db").resolve()

    def test_no_arg_default_fails_closed_without_allow_flag(self, monkeypatch):
        """SignalStore() no longer silently opens repo-root signals.db in production."""
        from storage.db_paths import InTreeDatabaseError, REPO_ROOT
        from storage.signal_store import SignalStore

        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        monkeypatch.chdir(REPO_ROOT)

        with pytest.raises(InTreeDatabaseError):
            SignalStore()

    def test_explicit_arg_wins(self, monkeypatch, tmp_path):
        """SignalStore(db_path=...) overrides env vars and is guarded."""
        explicit = tmp_path / "explicit.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(tmp_path / "env.db"))

        from storage.signal_store import SignalStore

        store = SignalStore(db_path=explicit)
        assert store.db_path == explicit.resolve()

    def test_memory_db_is_preserved(self):
        """SignalStore still supports SQLite in-memory fixtures."""
        from storage.signal_store import SignalStore

        store = SignalStore(":memory:")
        assert store.db_path == Path(":memory:")

    def test_relative_fixture_path_allowed_with_dev_flag(self, monkeypatch):
        """Existing relative fixture paths still work when the test/dev flag is set."""
        from storage.signal_store import SignalStore

        monkeypatch.setenv("HARMONIC_ALLOW_IN_TREE_DB", "true")
        store = SignalStore("my_signals.db")
        assert store.db_path == Path("my_signals.db").resolve()


class TestPrecedenceIntegration:
    """Full-chain precedence test matching baseline #9 from the plan."""

    def test_baseline_9_env_probe(self, monkeypatch, tmp_path):
        """SignalStore() must respect DISCOVERY_DB_PATH."""
        test_db = tmp_path / "test_signals.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(test_db))
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)

        from storage.signal_store import SignalStore

        store = SignalStore()
        assert store.db_path == test_db.resolve()


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
        from storage.db_paths import InTreeDatabaseError, REPO_ROOT, guard_db_path

        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        in_tree = REPO_ROOT / "signals.db"

        with pytest.raises(InTreeDatabaseError):
            guard_db_path(in_tree)

    def test_in_tree_allowed_when_env_set(self, monkeypatch):
        """HARMONIC_ALLOW_IN_TREE_DB=true bypasses the guard."""
        from storage.db_paths import REPO_ROOT, guard_db_path

        monkeypatch.setenv("HARMONIC_ALLOW_IN_TREE_DB", "true")
        in_tree = REPO_ROOT / "signals.db"

        result = guard_db_path(in_tree)
        assert result == in_tree

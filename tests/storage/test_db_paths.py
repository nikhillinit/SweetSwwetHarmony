"""Tests for canonical signals DB path resolution + in-tree fail-fast guard."""
from __future__ import annotations

import pytest

from storage.db_paths import (
    REPO_ROOT,
    InTreeDatabaseError,
    resolve_canonical_db_path,
)


def test_out_of_tree_path_resolves(monkeypatch, tmp_path):
    target = tmp_path / "signals.db"
    monkeypatch.setenv("DISCOVERY_DB_PATH", str(target))
    monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
    monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
    assert resolve_canonical_db_path() == target.resolve()


def test_in_tree_path_fails_closed(monkeypatch):
    monkeypatch.setenv("DISCOVERY_DB_PATH", str(REPO_ROOT / "signals.db"))
    monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
    monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
    with pytest.raises(InTreeDatabaseError):
        resolve_canonical_db_path()


def test_default_signals_db_fails_closed(monkeypatch):
    monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
    monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
    monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
    # The bare "signals.db" default resolves against cwd; inside the repo this
    # is exactly the incident path, so it must fail closed.
    monkeypatch.chdir(REPO_ROOT)
    with pytest.raises(InTreeDatabaseError):
        resolve_canonical_db_path()


def test_in_tree_allowed_with_flag(monkeypatch):
    monkeypatch.setenv("DISCOVERY_DB_PATH", str(REPO_ROOT / "signals.db"))
    monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
    monkeypatch.setenv("HARMONIC_ALLOW_IN_TREE_DB", "true")
    assert resolve_canonical_db_path() == (REPO_ROOT / "signals.db").resolve()


def test_resolution_order(monkeypatch, tmp_path):
    primary = tmp_path / "primary.db"
    secondary = tmp_path / "secondary.db"
    monkeypatch.setenv("DISCOVERY_DB_PATH", str(primary))
    monkeypatch.setenv("SIGNAL_DB_PATH", str(secondary))
    monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
    assert resolve_canonical_db_path() == primary.resolve()


def test_signal_db_path_fallback(monkeypatch, tmp_path):
    secondary = tmp_path / "secondary.db"
    monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
    monkeypatch.setenv("SIGNAL_DB_PATH", str(secondary))
    monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
    assert resolve_canonical_db_path() == secondary.resolve()


class TestResolvePrivateGraphDbPath:
    """Q7: private graph DB path resolution mirrors the canonical DB guard."""

    @staticmethod
    def _resolve():
        from storage.db_paths import resolve_private_graph_db_path

        return resolve_private_graph_db_path()

    def test_defaults_beside_canonical_db(self, monkeypatch, tmp_path):
        """AC1: with only DISCOVERY_DB_PATH set, private graph lands beside it."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(tmp_path / "signals.db"))
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("PRIVATE_GRAPH_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        assert self._resolve() == (tmp_path / "private_graph.db").resolve()

    def test_explicit_env_always_wins(self, monkeypatch, tmp_path):
        """AC2: explicit PRIVATE_GRAPH_DB_PATH beats the derived default."""
        other = tmp_path / "elsewhere" / "graph.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(tmp_path / "signals.db"))
        monkeypatch.setenv("PRIVATE_GRAPH_DB_PATH", str(other))
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        assert self._resolve() == other.resolve()

    def test_empty_env_var_treated_as_unset(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(tmp_path / "signals.db"))
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.setenv("PRIVATE_GRAPH_DB_PATH", "")
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        assert self._resolve() == (tmp_path / "private_graph.db").resolve()

    def test_in_tree_explicit_path_fails_closed(self, monkeypatch):
        """AC3: an in-tree PRIVATE_GRAPH_DB_PATH fails closed."""
        monkeypatch.setenv(
            "PRIVATE_GRAPH_DB_PATH", str(REPO_ROOT / "private_graph.db")
        )
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        with pytest.raises(InTreeDatabaseError):
            self._resolve()

    def test_default_from_in_tree_canonical_fails_closed(self, monkeypatch):
        """AC3: the derived default inherits the canonical in-tree guard."""
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("PRIVATE_GRAPH_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        monkeypatch.chdir(REPO_ROOT)
        with pytest.raises(InTreeDatabaseError):
            self._resolve()

    def test_in_tree_explicit_allowed_with_escape_hatch(self, monkeypatch):
        """AC4: HARMONIC_ALLOW_IN_TREE_DB bypasses the guard for fixtures."""
        target = REPO_ROOT / "private_graph.db"
        monkeypatch.setenv("PRIVATE_GRAPH_DB_PATH", str(target))
        monkeypatch.setenv("HARMONIC_ALLOW_IN_TREE_DB", "true")
        assert self._resolve() == target.resolve()

    def test_in_tree_default_allowed_with_escape_hatch(self, monkeypatch):
        """AC4: the escape hatch also covers the derived in-tree default."""
        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("PRIVATE_GRAPH_DB_PATH", raising=False)
        monkeypatch.setenv("HARMONIC_ALLOW_IN_TREE_DB", "true")
        monkeypatch.chdir(REPO_ROOT)
        assert self._resolve() == (REPO_ROOT / "private_graph.db").resolve()

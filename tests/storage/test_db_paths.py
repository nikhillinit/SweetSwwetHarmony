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

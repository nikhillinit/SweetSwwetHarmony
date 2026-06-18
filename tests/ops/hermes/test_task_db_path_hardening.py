from __future__ import annotations

from argparse import Namespace

import pytest

from integrations.hermes.tasks.base import TaskContext, resolve_task_db_path
from integrations.hermes.tasks.suppression_sync import SuppressionSyncTask
from storage.db_paths import InTreeDatabaseError, REPO_ROOT


def _context(args: Namespace) -> TaskContext:
    return TaskContext(
        task=SuppressionSyncTask(),
        mode="plan-only",
        args=args,
        root=REPO_ROOT,
    )


def test_hermes_task_omitted_db_path_uses_canonical_env(monkeypatch, tmp_path):
    target = tmp_path / "signals.db"
    monkeypatch.setenv("DISCOVERY_DB_PATH", str(target))
    monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
    monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)

    resolved = resolve_task_db_path(_context(Namespace(db_path=None)), None)

    assert resolved == target.resolve()


def test_hermes_task_omitted_db_path_fails_closed_in_tree(monkeypatch):
    monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
    monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
    monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
    monkeypatch.chdir(REPO_ROOT)

    with pytest.raises(InTreeDatabaseError):
        resolve_task_db_path(_context(Namespace(db_path=None)), None)


def test_hermes_task_explicit_in_tree_db_path_fails_closed(monkeypatch):
    monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)

    with pytest.raises(InTreeDatabaseError):
        resolve_task_db_path(
            _context(Namespace(db_path="signals.db")),
            "signals.db",
        )


def test_hermes_task_explicit_in_tree_db_path_allowed_for_dev(monkeypatch):
    monkeypatch.setenv("HARMONIC_ALLOW_IN_TREE_DB", "true")

    resolved = resolve_task_db_path(
        _context(Namespace(db_path="signals.db")),
        "signals.db",
    )

    assert resolved == (REPO_ROOT / "signals.db").resolve()

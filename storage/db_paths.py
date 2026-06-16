"""Canonical signals DB path resolution with an in-tree fail-fast guard.

The 2026-05 incident (#149) was caused by the live ``signals.db`` living inside
the git working tree: a ``git checkout``/``reset``/clone (or a Daily-Pipeline
artifact restore) could silently overwrite it with a stale/truncated committed
blob. This resolver centralises path resolution and fails closed when the
canonical DB would resolve *inside* the repo working tree, so the canonical DB
is forced out of tree.

Resolution order: ``DISCOVERY_DB_PATH`` > ``SIGNAL_DB_PATH`` > ``"signals.db"``.

Set ``HARMONIC_ALLOW_IN_TREE_DB=true`` to permit an in-tree path (CI/dev
fixtures and tests that legitimately use a repo-relative scratch DB).
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_TRUTHY = {"1", "true", "yes", "on"}


class InTreeDatabaseError(RuntimeError):
    """Raised when the canonical signals DB resolves inside the git working tree."""


def _is_in_tree(path: Path) -> bool:
    try:
        path.relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def _allow_in_tree() -> bool:
    return os.getenv("HARMONIC_ALLOW_IN_TREE_DB", "").strip().lower() in _TRUTHY


def resolve_canonical_db_path() -> Path:
    """Resolve the canonical signals DB path, failing closed on in-tree paths.

    Returns:
        The resolved absolute :class:`~pathlib.Path` to the canonical DB.

    Raises:
        InTreeDatabaseError: if the resolved path is inside the repo working
            tree and ``HARMONIC_ALLOW_IN_TREE_DB`` is not truthy.
    """
    raw = (
        os.getenv("DISCOVERY_DB_PATH")
        or os.getenv("SIGNAL_DB_PATH")
        or "signals.db"
    )
    path = Path(raw).expanduser().resolve()
    if not _allow_in_tree() and _is_in_tree(path):
        raise InTreeDatabaseError(
            f"canonical signals DB resolves inside the repo working tree: {path}. "
            f"Set DISCOVERY_DB_PATH to a location outside {REPO_ROOT}, or set "
            f"HARMONIC_ALLOW_IN_TREE_DB=true for fixtures/scratch DBs."
        )
    return path

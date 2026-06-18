"""Shared database path resolution for CLI commands and library code.

Registers --db-path (normative) and --db (deprecated alias) on an
argparse parser.  resolve_db_path() returns the final path with
priority: --db-path > --db (+ stderr warning) > DISCOVERY_DB_PATH env
> SIGNAL_DB_PATH env > guarded canonical "signals.db".

For non-CLI code (SignalStore, MCP server, API), use resolve_db_path_env()
which skips the argparse layer but still applies the canonical in-tree guard.
"""

from __future__ import annotations

import hashlib
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Union

from storage.db_paths import InTreeDatabaseError, guard_db_path, resolve_canonical_db_path


_SQLITE_MEMORY_DB = ":memory:"


def _resolve_explicit_db_path(explicit: Union[str, Path]) -> str:
    """Resolve and guard an explicitly supplied SQLite DB path."""
    raw = str(explicit)
    if raw == _SQLITE_MEMORY_DB:
        return raw
    return str(guard_db_path(Path(raw).expanduser().resolve()))


def resolve_db_path_env(explicit: Union[str, Path, None] = None) -> str:
    """Resolve database path from explicit value, env vars, or guarded default.

    Priority:
      1. explicit argument (if not None), guarded unless ":memory:"
      2. DISCOVERY_DB_PATH env var
      3. SIGNAL_DB_PATH env var (legacy, for MCP server compat)
      4. guarded canonical "signals.db"

    Returns:
        Resolved path as a string.
    """
    if explicit is not None:
        return _resolve_explicit_db_path(explicit)
    return str(resolve_canonical_db_path())


def get_production_db_path() -> Path:
    """Return the resolved production DB path as an absolute path."""
    return resolve_canonical_db_path()


def is_production_db_path(candidate: Union[str, Path, None]) -> bool:
    """Return True when *candidate* resolves to the configured production DB path."""
    if candidate is None:
        return False
    try:
        production_db = get_production_db_path()
    except InTreeDatabaseError:
        return False
    return Path(candidate).resolve() == production_db


def get_signal_count_watermark_path() -> Path:
    """Return the external watermark path for the production DB signal count."""
    production_db = get_production_db_path()
    digest = hashlib.sha256(str(production_db).encode("utf-8")).hexdigest()[:12]
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / ".omx" / "state" / f"signal-count-watermark-{digest}.json"


def add_db_path_args(parser: ArgumentParser) -> None:
    """Register --db-path and --db on *parser*."""
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to SQLite database (overrides DISCOVERY_DB_PATH env var)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        dest="db_deprecated",
        help="[DEPRECATED] Use --db-path instead",
    )


def resolve_db_path(args: Namespace) -> str:
    """Resolve the database path from args / env / default.

    Priority: --db-path > --db (deprecated, emits warning) >
    DISCOVERY_DB_PATH env > SIGNAL_DB_PATH env > guarded canonical "signals.db".
    """
    db_path = getattr(args, "db_path", None)
    if db_path:
        return _resolve_explicit_db_path(db_path)

    db_deprecated = getattr(args, "db_deprecated", None)
    if db_deprecated:
        print(
            "WARNING: --db is DEPRECATED and will be removed in a future "
            "release. Use --db-path instead.",
            file=sys.stderr,
        )
        return _resolve_explicit_db_path(db_deprecated)

    return resolve_db_path_env()

"""Shared database path resolution for CLI commands and library code.

Registers --db-path (normative) and --db (deprecated alias) on an
argparse parser.  resolve_db_path() returns the final path with
priority: --db-path > --db (+ stderr warning) > DISCOVERY_DB_PATH env
> SIGNAL_DB_PATH env > "signals.db".

For non-CLI code (SignalStore, MCP server, API), use resolve_db_path_env()
which skips the argparse layer.
"""

from __future__ import annotations

import os
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Union


_DEFAULT_DB = "signals.db"


def resolve_db_path_env(explicit: Union[str, Path, None] = None) -> str:
    """Resolve database path from explicit value, env vars, or default.

    Priority:
      1. explicit argument (if not None)
      2. DISCOVERY_DB_PATH env var
      3. SIGNAL_DB_PATH env var (legacy, for MCP server compat)
      4. "signals.db"

    Returns:
        Resolved path as a string.
    """
    if explicit is not None:
        return str(explicit)
    return (
        os.environ.get("DISCOVERY_DB_PATH")
        or os.environ.get("SIGNAL_DB_PATH")
        or _DEFAULT_DB
    )


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
    DISCOVERY_DB_PATH env > SIGNAL_DB_PATH env > "signals.db".
    """
    db_path = getattr(args, "db_path", None)
    if db_path:
        return db_path

    db_deprecated = getattr(args, "db_deprecated", None)
    if db_deprecated:
        print(
            "WARNING: --db is DEPRECATED and will be removed in a future "
            "release. Use --db-path instead.",
            file=sys.stderr,
        )
        return db_deprecated

    return resolve_db_path_env()

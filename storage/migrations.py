"""Compatibility shim — forwards to storage.migrations package CLI.

The canonical entry point is now:
    python -m storage.migrations <command> [args]

This file is kept for backward compatibility with scripts and the
governance allowlist. It will be removed in a future PR.
"""

import asyncio
import sys
import warnings

warnings.warn(
    "storage/migrations.py is deprecated. Use: python -m storage.migrations",
    DeprecationWarning,
    stacklevel=1,
)

from storage.migrations.cli import main  # noqa: E402

if __name__ == "__main__":
    asyncio.run(main())

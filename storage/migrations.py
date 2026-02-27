"""Compatibility shim — forwards to storage.migrations package CLI.

The canonical entry point is now:
    python -m storage.migrations <command> [args]

This file is kept for backward compatibility with scripts and the
governance allowlist. It will be removed in a future PR.
"""

import os
import sys
import warnings

warnings.warn(
    "storage/migrations.py is deprecated. Use: python -m storage.migrations",
    DeprecationWarning,
    stacklevel=1,
)

if __name__ == "__main__" and __package__ is None:
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

from storage.migrations.cli import main  # noqa: E402

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

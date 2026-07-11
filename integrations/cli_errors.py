"""Shared spawn-failure error contract for CLI wrapper adapters.

``MISSING_BINARY_ERROR`` is the single source of truth for how the Codex,
Kimi, and Gemini/Antigravity wrappers report a binary that could not be
found on PATH. ``integrations.hermes.failures`` derives its spawn-error
hint from ``MISSING_BINARY_HINT``, so wrapper wording and failure
classification cannot drift apart.

This module is intentionally stdlib-free and import-cycle-safe: it must be
importable by every wrapper and by the Hermes failure classifier.
"""

from __future__ import annotations

MISSING_BINARY_HINT = "not found on PATH"
MISSING_BINARY_ERROR = "{binary!r} " + MISSING_BINARY_HINT


def missing_binary_error(binary: str) -> str:
    """Format the canonical missing-binary error message for ``binary``."""
    return MISSING_BINARY_ERROR.format(binary=binary)

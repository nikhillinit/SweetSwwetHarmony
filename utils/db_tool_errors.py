"""Structured exceptions for DB-tool failures.

Subclasses populate ``partial_evidence`` so error-path rows in
``db_ops_ledger.jsonl`` carry forensic context — e.g. which sidecars were
present, which operation failed, the integrity-check result — instead of
just ``error: <message>``. The base class is intentionally thin: a message
plus a ``partial_evidence`` dict. Per-script subclasses live next to their
scripts (see ``scripts/backup_db.BackupError`` for the precedent) and pack
typed kwargs into the dict so the ledger writer can serialize uniformly:

    except DBToolError as exc:
        append_db_ops_ledger(
            ...,
            status="error",
            details={**exc.partial_evidence, "error": str(exc)},
        )
"""

from __future__ import annotations

from typing import Any


class DBToolError(RuntimeError):
    """Base for DB-tool failures with structured partial-evidence forensics."""

    def __init__(
        self,
        message: str,
        *,
        partial_evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.partial_evidence: dict[str, Any] = dict(partial_evidence or {})

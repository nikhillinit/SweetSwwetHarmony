"""Identity migration gate (Task 3).

Pipeline refuses to run if any signal has NULL company_id.
Uses the unified validator from backfill_v28_identity.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from storage.migrations.backfill_v28_identity import validate_company_ids

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


class IdentityMigrationRequired(RuntimeError):
    """Raised when signals have NULL company_id and backfill is needed."""
    pass


async def check_identity_integrity(store: SignalStore) -> None:
    """Check that all signals have non-NULL company_id.

    Raises IdentityMigrationRequired with actionable message if NULLs found.
    No-op if all signals are populated or DB is empty.
    """
    result = await validate_company_ids(store)

    if not result["valid"]:
        raise IdentityMigrationRequired(
            f"{result['null_count']} signals have NULL company_id. "
            f"Run backfill first:\n"
            f"  python -m storage.migrations.backfill_v28_identity "
            f"--db signals.db --apply"
        )

    logger.debug(
        f"Identity gate passed: {result['total_signals']} signals, "
        f"0 NULLs"
    )

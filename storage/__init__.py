"""
Storage layer for Discovery Engine.

Provides persistent SQLite storage for signals with deduplication,
processing state tracking, and Notion suppression cache.

Main components:
- SignalStore: Async SQLite storage with connection pooling
- StoredSignal: Signal data loaded from database
- SuppressionEntry: Suppression cache entry

Quick start:
    from storage import signal_store

    async with signal_store("signals.db") as store:
        # Save a signal
        signal_id = await store.save_signal(
            signal_type="github_spike",
            source_api="github",
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            confidence=0.85,
            raw_data={...}
        )

        # Check for duplicates
        if await store.is_duplicate("domain:acme.ai"):
            print("Already seen this company")

        # Get pending signals
        pending = await store.get_pending_signals()

        # Mark as pushed to Notion
        await store.mark_pushed(signal_id, "notion-page-123")
"""

from storage.signal_store import (
    SignalStore,
    StoredSignal,
    SuppressionEntry,
    signal_store,
    CURRENT_SCHEMA_VERSION,
)

__all__ = [
    "SignalStore",
    "StoredSignal",
    "SuppressionEntry",
    "signal_store",
    "CURRENT_SCHEMA_VERSION",
]

__version__ = "1.0.0"

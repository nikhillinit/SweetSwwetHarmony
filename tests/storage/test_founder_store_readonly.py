"""Regression tests for FounderStore read-only dry-run support."""

from __future__ import annotations

import pytest

from storage.founder_store import FounderProfile, FounderStore
from storage.signal_store import ReadOnlyStoreError


@pytest.mark.asyncio
async def test_read_only_founder_store_initializes_against_existing_db(tmp_path):
    db_path = tmp_path / "signals.db"

    writable = FounderStore(db_path=db_path)
    await writable.initialize()
    await writable.close()

    readonly = FounderStore(db_path=db_path, read_only=True)
    await readonly.initialize()

    assert await readonly.get_founder("linkedin:none") is None
    await readonly.close()


@pytest.mark.asyncio
async def test_read_only_founder_store_blocks_save_founder(tmp_path):
    db_path = tmp_path / "signals.db"

    writable = FounderStore(db_path=db_path)
    await writable.initialize()
    await writable.close()

    readonly = FounderStore(db_path=db_path, read_only=True)
    await readonly.initialize()

    with pytest.raises(ReadOnlyStoreError):
        await readonly.save_founder(
            FounderProfile(
                name="Readonly Founder",
                founder_key="linkedin:readonly-founder",
                canonical_key="domain:readonly.example",
                source_api="linkedin",
            )
        )

    await readonly.close()

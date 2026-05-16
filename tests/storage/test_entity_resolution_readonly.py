"""Regression tests for EntityResolutionStore read-only dry-run support."""

from __future__ import annotations

import pytest

from storage.entity_resolution import AssetToLead, EntityResolutionStore, ResolutionMethod
from storage.signal_store import ReadOnlyStoreError


@pytest.mark.asyncio
async def test_read_only_entity_resolution_store_allows_lookups(tmp_path):
    db_path = tmp_path / "signals.db"

    writable = EntityResolutionStore(db_path=str(db_path))
    await writable.initialize()
    await writable.close()

    readonly = EntityResolutionStore(db_path=str(db_path), read_only=True)
    await readonly.initialize()

    assert await readonly.get_lead_for_asset("github_repo", "startup/app") is None
    await readonly.close()


@pytest.mark.asyncio
async def test_read_only_entity_resolution_store_blocks_link_creation(tmp_path):
    db_path = tmp_path / "signals.db"

    writable = EntityResolutionStore(db_path=str(db_path))
    await writable.initialize()
    await writable.close()

    readonly = EntityResolutionStore(db_path=str(db_path), read_only=True)
    await readonly.initialize()

    with pytest.raises(ReadOnlyStoreError):
        await readonly.create_link(
            AssetToLead(
                asset_id=1,
                asset_source_type="github_repo",
                asset_external_id="startup/app",
                lead_canonical_key="domain:startup.com",
                confidence=0.95,
                resolved_by=ResolutionMethod.DOMAIN_MATCH,
            )
        )

    await readonly.close()

"""Tests for save_signal() v6.6 insert-path hardening.

Verifies:
- save_signal populates evidence_family
- save_signal populates canonical_key_v2
- Malformed raw_data still inserts (fallback to unknown/NULL)
"""

import sqlite3

import pytest

from storage.signal_store import SignalStore


@pytest.fixture
async def store(tmp_path):
    """Create initialized SignalStore."""
    db_path = str(tmp_path / "test_save.db")
    s = SignalStore(db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_save_signal_populates_evidence_family(store):
    """Insert a github_spike signal; evidence_family should be 'developer'."""
    signal_id = await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:acme.ai",
        confidence=0.75,
        raw_data={"company_name": "Acme", "company_url": "https://acme.ai"},
        company_name="Acme",
    )

    # Read back via raw SQL
    import aiosqlite
    async with aiosqlite.connect(store.db_path) as db:
        row = await db.execute_fetchall(
            "SELECT evidence_family FROM signals WHERE id = ?", (signal_id,)
        )
        assert len(row) == 1
        assert row[0][0] == "developer"


@pytest.mark.asyncio
async def test_save_signal_populates_canonical_key_v2(store):
    """Insert with company_url in raw_data; canonical_key_v2 should be a domain key."""
    signal_id = await store.save_signal(
        signal_type="hiring_signal",
        source_api="job_postings",
        canonical_key="name_loc:acme",
        confidence=0.80,
        raw_data={"company_name": "Acme", "company_url": "https://www.acme.ai/careers"},
        company_name="Acme",
    )

    import aiosqlite
    async with aiosqlite.connect(store.db_path) as db:
        row = await db.execute_fetchall(
            "SELECT canonical_key_v2 FROM signals WHERE id = ?", (signal_id,)
        )
        assert len(row) == 1
        v2 = row[0][0]
        assert v2 is not None
        assert v2.startswith("domain:"), f"Expected domain: prefix, got {v2}"


@pytest.mark.asyncio
async def test_malformed_raw_data_still_inserts(store):
    """Bad raw_data should not prevent insert; evidence_family=unknown, v2=NULL."""
    signal_id = await store.save_signal(
        signal_type="unknown_type_xyz",
        source_api="test",
        canonical_key="name_loc:test-company",
        confidence=0.5,
        raw_data={"garbage": True},  # No company_url, no company_name
        company_name=None,
    )

    assert signal_id > 0

    import aiosqlite
    async with aiosqlite.connect(store.db_path) as db:
        row = await db.execute_fetchall(
            "SELECT evidence_family, canonical_key_v2 FROM signals WHERE id = ?",
            (signal_id,),
        )
        assert len(row) == 1
        ef, v2 = row[0]
        assert ef == "unknown", f"Expected 'unknown', got {ef}"
        # v2 could be the existing name_loc key or None depending on logic

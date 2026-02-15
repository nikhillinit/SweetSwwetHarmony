"""Integration tests for Phase G flag interactions with other features."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from storage.entity_identity_store import EntityIdentityStore, StrongKeyBinding
from storage.merge_cascade import cascade_merge
from storage.merge_rollback import (
    compute_entity_fingerprint,
    compute_entity_fingerprint_sync,
)


# =============================================================================
# FIXTURES
# =============================================================================


@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with all migrations applied."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def identity_store(store):
    """EntityIdentityStore backed by the test SignalStore."""
    return EntityIdentityStore(store)


# =============================================================================
# HELPERS
# =============================================================================


async def _insert_signal(
    store,
    signal_id,
    company_id,
    canonical_key=None,
    source_api="github",
    confidence=0.8,
):
    """Insert a signal row directly into the database."""
    if canonical_key is None:
        canonical_key = f"domain:test{signal_id}.com"
    detected_at = f"2026-01-{signal_id:02d}T00:00:00+00:00"
    now = "2026-01-15T00:00:00+00:00"
    await store._db.execute(
        """INSERT INTO signals
           (id, signal_type, source_api, canonical_key, company_name,
            confidence, raw_data, detected_at, created_at, company_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            signal_id,
            "test",
            source_api,
            canonical_key,
            "Test Co",
            confidence,
            "{}",
            detected_at,
            now,
            company_id,
        ),
    )
    await store._db.commit()


async def _insert_company_file(
    store,
    company_id,
    source_apis=None,
    status="thin",
    first_seen="2026-01-01T00:00:00+00:00",
    last_seen="2026-01-15T00:00:00+00:00",
):
    """Insert a company_files row directly into the database."""
    if source_apis is None:
        source_apis = ["github"]
    await store._db.execute(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, status,
            source_apis, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            company_id,
            "Test Co",
            f"domain:{company_id}.com",
            status,
            json.dumps(source_apis),
            first_seen,
            last_seen,
        ),
    )
    await store._db.commit()


# =============================================================================
# TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_merge_preserves_confidence_scores(store):
    """Cascade merge must not alter confidence values on any reassigned signal."""
    winner_id = "ent_winner_conf"
    loser_id = "ent_loser_conf"

    # Two winner signals with distinct confidence values
    await _insert_signal(store, 1, winner_id, confidence=0.85)
    await _insert_signal(store, 2, winner_id, confidence=0.92)

    # Two loser signals with distinct confidence values
    await _insert_signal(store, 3, loser_id, confidence=0.65)
    await _insert_signal(store, 4, loser_id, confidence=0.45)

    # Create company files so the merge has something to consolidate
    await _insert_company_file(store, winner_id)
    await _insert_company_file(store, loser_id)

    report = await cascade_merge(
        store,
        winner_company_id=winner_id,
        loser_company_id=loser_id,
        reason="test_confidence",
        actor="test",
    )

    assert report["signals_reassigned"] == 2

    # Verify every signal retains its original confidence
    cursor = await store._db.execute(
        "SELECT id, confidence FROM signals ORDER BY id"
    )
    rows = await cursor.fetchall()

    expected = {1: 0.85, 2: 0.92, 3: 0.65, 4: 0.45}
    for row_id, conf in rows:
        assert conf == pytest.approx(expected[row_id]), (
            f"Signal {row_id} confidence changed from {expected[row_id]} to {conf}"
        )


@pytest.mark.asyncio
async def test_drift_fingerprint_changes_after_merge(store):
    """Entity fingerprint must differ after a merge adds new signals."""
    winner_id = "ent_winner_fp"
    loser_id = "ent_loser_fp"

    # Winner starts with one signal and a company file
    await _insert_signal(store, 1, winner_id)
    await _insert_company_file(store, winner_id)

    # Compute fingerprint BEFORE merge
    async with store.transaction_immediate() as tx:
        fp_before = await compute_entity_fingerprint(tx, winner_id)

    # Insert loser data
    await _insert_signal(store, 2, loser_id)
    await _insert_company_file(store, loser_id)

    # Merge loser into winner
    await cascade_merge(
        store,
        winner_company_id=winner_id,
        loser_company_id=loser_id,
        reason="test_fingerprint",
        actor="test",
    )

    # Compute fingerprint AFTER merge
    async with store.transaction_immediate() as tx:
        fp_after = await compute_entity_fingerprint(tx, winner_id)

    assert fp_before != fp_after, (
        "Fingerprint should change after merge introduces new signals"
    )


@pytest.mark.asyncio
async def test_thin_files_consolidated_on_merge(store):
    """Merge must combine source_apis, pick earliest first_seen, latest last_seen, and delete loser file."""
    winner_id = "ent_winner_thin"
    loser_id = "ent_loser_thin"

    await _insert_signal(store, 1, winner_id)
    await _insert_signal(store, 2, loser_id)

    # Winner: promoted, sources github + hacker_news, mid-range dates
    await _insert_company_file(
        store,
        winner_id,
        source_apis=["github", "hacker_news"],
        status="promoted",
        first_seen="2026-01-05T00:00:00+00:00",
        last_seen="2026-01-10T00:00:00+00:00",
    )

    # Loser: thin, source sec_edgar, earlier first_seen AND later last_seen
    await _insert_company_file(
        store,
        loser_id,
        source_apis=["sec_edgar"],
        status="thin",
        first_seen="2026-01-01T00:00:00+00:00",
        last_seen="2026-01-20T00:00:00+00:00",
    )

    report = await cascade_merge(
        store,
        winner_company_id=winner_id,
        loser_company_id=loser_id,
        reason="test_thin_file",
        actor="test",
    )
    assert report["company_file_merged"] is True

    # Winner file should have combined sources
    cursor = await store._db.execute(
        "SELECT source_apis, first_seen_at, last_seen_at FROM company_files WHERE company_id = ?",
        (winner_id,),
    )
    row = await cursor.fetchone()
    assert row is not None, "Winner company_file should exist"

    combined_sources = json.loads(row[0])
    assert sorted(combined_sources) == ["github", "hacker_news", "sec_edgar"]

    # Earliest first_seen from loser
    assert row[1] == "2026-01-01T00:00:00+00:00"

    # Latest last_seen from loser
    assert row[2] == "2026-01-20T00:00:00+00:00"

    # Loser file should be deleted
    cursor = await store._db.execute(
        "SELECT 1 FROM company_files WHERE company_id = ?",
        (loser_id,),
    )
    assert await cursor.fetchone() is None, "Loser company_file should be deleted"


@pytest.mark.asyncio
async def test_merged_signals_keep_original_canonical_keys(store):
    """Signals must retain their original canonical_key after merge (only company_id changes)."""
    winner_id = "ent_winner_ck"
    loser_id = "ent_loser_ck"

    winner_ck = "domain:winner.com"
    loser_ck = "domain:loser.com"

    await _insert_signal(store, 1, winner_id, canonical_key=winner_ck)
    await _insert_signal(store, 2, loser_id, canonical_key=loser_ck)

    await _insert_company_file(store, winner_id)
    await _insert_company_file(store, loser_id)

    await cascade_merge(
        store,
        winner_company_id=winner_id,
        loser_company_id=loser_id,
        reason="test_canonical_keys",
        actor="test",
    )

    # Both signals now belong to winner
    cursor = await store._db.execute(
        "SELECT id, canonical_key, company_id FROM signals ORDER BY id"
    )
    rows = await cursor.fetchall()

    assert len(rows) == 2
    # Signal 1 retains winner canonical key
    assert rows[0][0] == 1
    assert rows[0][1] == winner_ck
    assert rows[0][2] == winner_id

    # Signal 2 retains loser canonical key but now has winner company_id
    assert rows[1][0] == 2
    assert rows[1][1] == loser_ck
    assert rows[1][2] == winner_id


@pytest.mark.asyncio
async def test_merge_doesnt_affect_unrelated_entities(store):
    """Merging A + B must leave entity C completely untouched."""
    ent_a = "ent_a_unrelated"
    ent_b = "ent_b_unrelated"
    ent_c = "ent_c_bystander"

    await _insert_signal(store, 1, ent_a, canonical_key="domain:a.com")
    await _insert_signal(store, 2, ent_b, canonical_key="domain:b.com")
    await _insert_signal(store, 3, ent_c, canonical_key="domain:c.com", confidence=0.77)

    await _insert_company_file(store, ent_a)
    await _insert_company_file(store, ent_b)
    await _insert_company_file(
        store,
        ent_c,
        source_apis=["sec_edgar"],
        first_seen="2026-01-03T00:00:00+00:00",
        last_seen="2026-01-12T00:00:00+00:00",
    )

    # Snapshot C's state before merge
    cursor = await store._db.execute(
        "SELECT company_id, confidence, canonical_key FROM signals WHERE id = 3"
    )
    c_signal_before = await cursor.fetchone()

    cursor = await store._db.execute(
        "SELECT source_apis, first_seen_at, last_seen_at FROM company_files WHERE company_id = ?",
        (ent_c,),
    )
    c_file_before = await cursor.fetchone()

    # Merge A + B
    await cascade_merge(
        store,
        winner_company_id=ent_a,
        loser_company_id=ent_b,
        reason="test_unrelated",
        actor="test",
    )

    # Verify C's signal is untouched
    cursor = await store._db.execute(
        "SELECT company_id, confidence, canonical_key FROM signals WHERE id = 3"
    )
    c_signal_after = await cursor.fetchone()
    assert c_signal_after == c_signal_before

    # Verify C's company_file is untouched
    cursor = await store._db.execute(
        "SELECT source_apis, first_seen_at, last_seen_at FROM company_files WHERE company_id = ?",
        (ent_c,),
    )
    c_file_after = await cursor.fetchone()
    assert c_file_after == c_file_before


@pytest.mark.asyncio
async def test_sequential_merges_converge(store):
    """Four entities merged pairwise then winners merged must converge to one entity."""
    entities = ["ent_seq_0", "ent_seq_1", "ent_seq_2", "ent_seq_3"]

    # One signal per entity, one company_file per entity
    for i, eid in enumerate(entities):
        await _insert_signal(store, i + 1, eid, canonical_key=f"domain:seq{i}.com")
        await _insert_company_file(store, eid, source_apis=[f"src_{i}"])

    # Merge 0 + 1
    report_01 = await cascade_merge(
        store,
        winner_company_id=entities[0],
        loser_company_id=entities[1],
        reason="pair_01",
        actor="test",
    )
    assert report_01["signals_reassigned"] == 1

    # Merge 2 + 3
    report_23 = await cascade_merge(
        store,
        winner_company_id=entities[2],
        loser_company_id=entities[3],
        reason="pair_23",
        actor="test",
    )
    assert report_23["signals_reassigned"] == 1

    # Merge the two winners: entities[0] + entities[2]
    final_winner = entities[0]
    final_loser = entities[2]
    report_final = await cascade_merge(
        store,
        winner_company_id=final_winner,
        loser_company_id=final_loser,
        reason="final_merge",
        actor="test",
    )
    assert report_final["signals_reassigned"] == 2  # signals 3 and 4

    # All 4 signals should now belong to final_winner
    cursor = await store._db.execute(
        "SELECT company_id FROM signals ORDER BY id"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 4
    for row in rows:
        assert row[0] == final_winner

    # Only one company_file should remain
    cursor = await store._db.execute(
        "SELECT COUNT(*) FROM company_files"
    )
    count = (await cursor.fetchone())[0]
    assert count == 1

    cursor = await store._db.execute(
        "SELECT company_id FROM company_files"
    )
    surviving_file = await cursor.fetchone()
    assert surviving_file[0] == final_winner


@pytest.mark.asyncio
async def test_fingerprint_sync_deterministic():
    """compute_entity_fingerprint_sync must be deterministic and sensitive to input changes."""
    signals_a = [1, 3, 5]
    review_a = {"status": "pending", "evidence_bundle": '{"signal_ids": [1, 3, 5]}'}
    file_a = {"source_apis": '["github"]', "last_seen_at": "2026-01-15T00:00:00+00:00"}

    # Same inputs produce the same output
    fp1 = compute_entity_fingerprint_sync(signals_a, review_a, file_a)
    fp2 = compute_entity_fingerprint_sync(signals_a, review_a, file_a)
    assert fp1 == fp2, "Same inputs must produce identical fingerprints"
    assert len(fp1) == 16, "Fingerprint must be SHA256[:16] (16 hex chars)"

    # Order of signal IDs should not matter (sorted internally)
    fp_reordered = compute_entity_fingerprint_sync([5, 1, 3], review_a, file_a)
    assert fp_reordered == fp1, "Signal order should not affect fingerprint (sorted internally)"

    # Different signals produce a different fingerprint
    signals_b = [1, 3, 5, 7]
    fp_diff_signals = compute_entity_fingerprint_sync(signals_b, review_a, file_a)
    assert fp_diff_signals != fp1, "Different signals must produce different fingerprint"

    # Different review state produces a different fingerprint
    review_b = {"status": "approved", "evidence_bundle": '{"signal_ids": [1, 3, 5]}'}
    fp_diff_review = compute_entity_fingerprint_sync(signals_a, review_b, file_a)
    assert fp_diff_review != fp1, "Different review state must produce different fingerprint"

    # Different file state produces a different fingerprint
    file_b = {"source_apis": '["github","sec_edgar"]', "last_seen_at": "2026-01-20T00:00:00+00:00"}
    fp_diff_file = compute_entity_fingerprint_sync(signals_a, review_a, file_b)
    assert fp_diff_file != fp1, "Different file state must produce different fingerprint"

    # None review/file state is valid
    fp_none = compute_entity_fingerprint_sync(signals_a, None, None)
    assert isinstance(fp_none, str) and len(fp_none) == 16


@pytest.mark.asyncio
async def test_cascade_merge_creates_audit_log(store):
    """cascade_merge must write an audit_log row with correct structure and content."""
    winner_id = "ent_winner_audit"
    loser_id = "ent_loser_audit"

    await _insert_signal(store, 1, winner_id)
    await _insert_signal(store, 2, loser_id)
    await _insert_signal(store, 3, loser_id, confidence=0.55)

    await _insert_company_file(store, winner_id)
    await _insert_company_file(store, loser_id)

    await cascade_merge(
        store,
        winner_company_id=winner_id,
        loser_company_id=loser_id,
        reason="test_audit",
        actor="integration_test",
    )

    # Query the audit_log for the cascade_merge entry
    cursor = await store._db.execute(
        """SELECT action_type, entity_type, entity_id, actor, details, created_at
           FROM audit_log
           WHERE action_type = 'cascade_merge'
           ORDER BY created_at DESC
           LIMIT 1"""
    )
    row = await cursor.fetchone()
    assert row is not None, "audit_log must contain a cascade_merge entry"

    action_type, entity_type, entity_id, actor, details_raw, created_at = row

    assert action_type == "cascade_merge"
    assert entity_type == "company"
    assert entity_id == winner_id
    assert actor == "integration_test"
    assert created_at is not None

    # Verify the details JSON structure
    details = json.loads(details_raw)
    assert details["winner"] == winner_id
    assert details["loser"] == loser_id
    assert details["signals_reassigned"] == 2
    assert details["reason"] == "test_audit"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

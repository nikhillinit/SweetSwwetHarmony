"""Full lifecycle integration tests for Phase G entity resolution.

Covers the complete entity identity lifecycle:
- Deterministic entity ID generation (SHA256[:16])
- Strong key binding registration and lookup
- Alias key binding registration, lookup, and collision-triggered merges
- Entity merge with lexmin winner selection
- Transitive resolution chains (A->B->C)
- Cascade merge: signals, reviews, company_files reassignment
- Drift fingerprint computation post-merge
- LIFO chain ordering across multiple merges
- Edge cases: self-merge noop, review collision resolution
"""

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
from storage.entity_identity_store import (
    EntityIdentityStore, StrongKeyBinding, AliasKeyBinding, BlockingToken,
)
from storage.review_store import create_review_item, update_review_status, get_review_queue
from storage.merge_cascade import cascade_merge
from storage.merge_rollback import compute_entity_fingerprint


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

async def _insert_signal(store, signal_id, company_id, canonical_key=None, source_api="github"):
    """Insert a signal row directly, bypassing identity wiring."""
    if canonical_key is None:
        canonical_key = f"domain:test{signal_id}.com"
    detected_at = f"2026-01-{signal_id:02d}T00:00:00+00:00"
    now = "2026-01-15T00:00:00+00:00"
    await store._db.execute(
        """INSERT INTO signals
           (id, signal_type, source_api, canonical_key, company_name,
            confidence, raw_data, detected_at, created_at, company_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (signal_id, "test", source_api, canonical_key, "Test Co",
         0.8, "{}", detected_at, now, company_id),
    )
    await store._db.commit()


async def _insert_company_file(store, company_id, source_apis=None, status="thin",
                                first_seen="2026-01-01T00:00:00+00:00",
                                last_seen="2026-01-15T00:00:00+00:00"):
    """Insert a company_files row directly."""
    if source_apis is None:
        source_apis = ["github"]
    await store._db.execute(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, status,
            source_apis, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (company_id, "Test Co", f"domain:{company_id}.com", status,
         json.dumps(source_apis), first_seen, last_seen),
    )
    await store._db.commit()


async def _insert_review(store, review_id, company_id, status="pending", signal_ids=None):
    """Insert a review_items row directly with explicit ID."""
    if signal_ids is None:
        signal_ids = [1]
    evidence = json.dumps({"signal_ids": sorted(signal_ids), "schema_version": 1})
    now = "2026-01-15T00:00:00+00:00"
    await store._db.execute(
        """INSERT INTO review_items
           (id, company_id, status, evidence_bundle, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (review_id, company_id, status, evidence, now, now),
    )
    await store._db.commit()


# =============================================================================
# TEST 1: Create entity from domain key
# =============================================================================

@pytest.mark.asyncio
async def test_create_entity_from_domain_key(store, identity_store):
    """entity_id_for_seed is deterministic SHA256[:16]. Register strong key
    binding with upsert_strong_key_bindings, verify lookup_strong_keys
    returns it."""
    seed_key = "domain:acme.ai"
    entity_id = EntityIdentityStore.entity_id_for_seed(seed_key)

    # Deterministic: same input always yields same output
    assert entity_id == EntityIdentityStore.entity_id_for_seed(seed_key)
    assert len(entity_id) == 16
    # All hex characters
    assert all(c in "0123456789abcdef" for c in entity_id)

    # Register strong key binding
    binding = StrongKeyBinding(
        strong_key=seed_key,
        entity_id=entity_id,
        source_key="test",
    )
    async with store.transaction_immediate() as tx:
        merges = await identity_store.upsert_strong_key_bindings([binding], tx)

    assert merges == []

    # Lookup should return the entity
    result = await identity_store.lookup_strong_keys([seed_key])
    assert result[seed_key] == entity_id


# =============================================================================
# TEST 2: Add aliases to entity
# =============================================================================

@pytest.mark.asyncio
async def test_add_aliases_to_entity(store, identity_store):
    """Register entity, add alias bindings with upsert_alias_bindings.
    Verify lookup_alias_keys returns them."""
    entity_id = EntityIdentityStore.entity_id_for_seed("domain:fizzbuzz.com")

    alias_a = AliasKeyBinding(
        alias_key="name_norm:fizzbuzz",
        entity_id=entity_id,
        alias_type="name_norm",
        confidence=0.9,
        source="test",
    )
    alias_b = AliasKeyBinding(
        alias_key="name_loc:fizzbuzz_sf",
        entity_id=entity_id,
        alias_type="name_loc",
        confidence=0.85,
        source="test",
    )

    async with store.transaction_immediate() as tx:
        merges = await identity_store.upsert_alias_bindings([alias_a, alias_b], tx)

    assert merges == []

    # Both aliases should resolve to the same entity
    result = await identity_store.lookup_alias_keys(
        ["name_norm:fizzbuzz", "name_loc:fizzbuzz_sf"]
    )
    assert result["name_norm:fizzbuzz"] == entity_id
    assert result["name_loc:fizzbuzz_sf"] == entity_id


# =============================================================================
# TEST 3: Overlapping alias triggers merge
# =============================================================================

@pytest.mark.asyncio
async def test_overlapping_alias_triggers_merge(store, identity_store):
    """Two entities claim same alias_key. Second upsert_alias_bindings
    should return merge pairs."""
    entity_a = EntityIdentityStore.entity_id_for_seed("domain:alpha.com")
    entity_b = EntityIdentityStore.entity_id_for_seed("domain:beta.com")
    shared_alias = "name_norm:shared_name"

    # First entity claims the alias
    alias_a = AliasKeyBinding(
        alias_key=shared_alias,
        entity_id=entity_a,
        alias_type="name_norm",
        confidence=0.8,
        source="test",
    )
    async with store.transaction_immediate() as tx:
        merges_first = await identity_store.upsert_alias_bindings([alias_a], tx)
    assert merges_first == []

    # Second entity claims the same alias -- triggers merge
    alias_b = AliasKeyBinding(
        alias_key=shared_alias,
        entity_id=entity_b,
        alias_type="name_norm",
        confidence=0.8,
        source="test",
    )
    async with store.transaction_immediate() as tx:
        merges_second = await identity_store.upsert_alias_bindings([alias_b], tx)

    # Should have exactly one merge pair (loser, winner)
    assert len(merges_second) == 1
    loser, winner = merges_second[0]
    assert winner == min(entity_a, entity_b)
    assert loser == max(entity_a, entity_b)


# =============================================================================
# TEST 4: merge_entities lexmin winner
# =============================================================================

@pytest.mark.asyncio
async def test_merge_entities_lexmin_winner(store, identity_store):
    """merge_entities(eid_a, eid_b, reason, tx) returns lexmin winner."""
    eid_a = "aaaa000000000001"
    eid_b = "bbbb000000000002"

    async with store.transaction_immediate() as tx:
        winner = await identity_store.merge_entities(
            eid_a, eid_b, reason="test_merge", tx=tx
        )

    # Lexicographically smallest wins
    assert winner == "aaaa000000000001"

    # Also works when arguments are reversed
    eid_c = "cccc000000000003"
    eid_d = "dddd000000000004"
    async with store.transaction_immediate() as tx:
        winner2 = await identity_store.merge_entities(
            eid_d, eid_c, reason="test_merge_rev", tx=tx
        )
    assert winner2 == "cccc000000000003"


# =============================================================================
# TEST 5: entity_migrations row created
# =============================================================================

@pytest.mark.asyncio
async def test_entity_migrations_row_created(store, identity_store):
    """After merge_entities, entity_migrations table should have a row
    with from_entity_id=loser, to_entity_id=winner, merge_reason."""
    eid_a = "1111aaaaaaaaaaaa"
    eid_b = "2222bbbbbbbbbbbb"
    winner = min(eid_a, eid_b)
    loser = max(eid_a, eid_b)

    async with store.transaction_immediate() as tx:
        result = await identity_store.merge_entities(
            eid_a, eid_b, reason="duplicate_domain", tx=tx
        )

    assert result == winner

    # Verify migration row exists
    cursor = await store._db.execute(
        """SELECT from_entity_id, to_entity_id, merge_reason
           FROM entity_migrations
           WHERE from_entity_id = ? AND to_entity_id = ?""",
        (loser, winner),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == loser
    assert row[1] == winner
    assert row[2] == "duplicate_domain"


# =============================================================================
# TEST 6: Transitive resolution chain
# =============================================================================

@pytest.mark.asyncio
async def test_transitive_resolution_chain(store, identity_store):
    """Create A->B->C chain via two merges. resolve_entity_root(A) returns C
    (the lexmin). Use entity IDs like 'f'*16, 'b'*16, 'a'*16."""
    eid_f = "f" * 16  # ffffffffffffffff
    eid_b = "b" * 16  # bbbbbbbbbbbbbbbb
    eid_a = "a" * 16  # aaaaaaaaaaaaaaaa

    # Merge F into B: winner = B (lexmin of B, F)
    async with store.transaction_immediate() as tx:
        winner1 = await identity_store.merge_entities(
            eid_f, eid_b, reason="merge_1", tx=tx
        )
    assert winner1 == eid_b

    # Merge B into A: winner = A (lexmin of A, B)
    async with store.transaction_immediate() as tx:
        winner2 = await identity_store.merge_entities(
            eid_b, eid_a, reason="merge_2", tx=tx
        )
    assert winner2 == eid_a

    # Transitive resolution: F -> B -> A
    root_f = await identity_store.resolve_entity_root(eid_f)
    assert root_f == eid_a

    # B -> A
    root_b = await identity_store.resolve_entity_root(eid_b)
    assert root_b == eid_a

    # A is already the root
    root_a = await identity_store.resolve_entity_root(eid_a)
    assert root_a == eid_a


# =============================================================================
# TEST 7: Signals reparented after cascade merge
# =============================================================================

@pytest.mark.asyncio
async def test_signals_reparented_after_cascade_merge(store, identity_store):
    """Insert signals for loser, cascade_merge reassigns them to winner."""
    winner_id = "aaaa111100001111"
    loser_id = "bbbb222200002222"

    # Insert signals owned by the loser
    await _insert_signal(store, 1, loser_id, canonical_key="domain:loser.com")
    await _insert_signal(store, 2, loser_id, canonical_key="domain:loser.com",
                         source_api="sec_edgar")

    # Insert a company file for winner (cascade_merge expects to audit)
    await _insert_company_file(store, winner_id, source_apis=["github"])

    report = await cascade_merge(
        store, winner_id, loser_id,
        reason="identity_merge", actor="test_runner"
    )

    assert report["signals_reassigned"] == 2

    # Verify signals now belong to winner
    cursor = await store._db.execute(
        "SELECT company_id FROM signals WHERE id IN (1, 2)"
    )
    rows = await cursor.fetchall()
    for row in rows:
        assert row[0] == winner_id


# =============================================================================
# TEST 8: Review items reparented
# =============================================================================

@pytest.mark.asyncio
async def test_review_items_reparented(store, identity_store):
    """Insert review for loser, cascade_merge reassigns to winner."""
    winner_id = "aaaa333300003333"
    loser_id = "bbbb444400004444"

    # Insert a review for the loser
    await _insert_review(store, 100, loser_id, status="pending", signal_ids=[3])
    # Insert a signal so the review has backing data
    await _insert_signal(store, 3, loser_id, canonical_key="domain:loser2.com")

    report = await cascade_merge(
        store, winner_id, loser_id,
        reason="identity_merge", actor="test_runner"
    )

    # Verify review is now assigned to winner
    cursor = await store._db.execute(
        "SELECT company_id FROM review_items WHERE id = 100"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == winner_id


# =============================================================================
# TEST 9: Company files consolidated
# =============================================================================

@pytest.mark.asyncio
async def test_company_files_consolidated(store, identity_store):
    """Both winner and loser have company_files. After merge, winner has
    combined source_apis, earliest first_seen, latest last_seen, loser
    file deleted."""
    winner_id = "aaaa555500005555"
    loser_id = "bbbb666600006666"

    await _insert_company_file(
        store, winner_id,
        source_apis=["github", "sec_edgar"],
        first_seen="2026-01-10T00:00:00+00:00",
        last_seen="2026-01-15T00:00:00+00:00",
    )
    await _insert_company_file(
        store, loser_id,
        source_apis=["hacker_news", "news_api"],
        first_seen="2026-01-05T00:00:00+00:00",
        last_seen="2026-01-20T00:00:00+00:00",
    )

    report = await cascade_merge(
        store, winner_id, loser_id,
        reason="identity_merge", actor="test_runner"
    )

    assert report["company_file_merged"] is True

    # Winner file should have combined sources
    cursor = await store._db.execute(
        "SELECT source_apis, first_seen_at, last_seen_at FROM company_files WHERE company_id = ?",
        (winner_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    sources = json.loads(row[0])
    assert set(sources) == {"github", "hacker_news", "news_api", "sec_edgar"}
    # Earliest first_seen
    assert row[1] == "2026-01-05T00:00:00+00:00"
    # Latest last_seen
    assert row[2] == "2026-01-20T00:00:00+00:00"

    # Loser file should be deleted
    cursor = await store._db.execute(
        "SELECT 1 FROM company_files WHERE company_id = ?",
        (loser_id,),
    )
    assert await cursor.fetchone() is None


# =============================================================================
# TEST 10: Drift fingerprint computed post-merge
# =============================================================================

@pytest.mark.asyncio
async def test_drift_fingerprint_computed_post_merge(store, identity_store):
    """After cascade_merge, compute_entity_fingerprint(tx, winner) returns
    a 16-char hex string."""
    winner_id = "aaaa777700007777"
    loser_id = "bbbb888800008888"

    # Set up data for both sides
    await _insert_signal(store, 5, winner_id, canonical_key="domain:winner3.com")
    await _insert_signal(store, 6, loser_id, canonical_key="domain:loser3.com")
    await _insert_company_file(store, winner_id, source_apis=["github"])
    await _insert_company_file(store, loser_id, source_apis=["sec_edgar"])

    await cascade_merge(
        store, winner_id, loser_id,
        reason="identity_merge", actor="test_runner"
    )

    # Compute fingerprint AFTER merge (post-merge state)
    async with store.transaction_immediate() as tx:
        fp = await compute_entity_fingerprint(tx, winner_id)

    assert isinstance(fp, str)
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)

    # Fingerprint should be deterministic (same state = same hash)
    async with store.transaction_immediate() as tx:
        fp2 = await compute_entity_fingerprint(tx, winner_id)
    assert fp == fp2


# =============================================================================
# TEST 11: LIFO chain ordering
# =============================================================================

@pytest.mark.asyncio
async def test_lifo_chain_ordering(store, identity_store):
    """Merge B into A, then C into winner. entity_migrations should have
    2 rows. All resolve to same root."""
    eid_a = "aaaa999900009999"
    eid_b = "bbbbaaaaaaaaaaaa"
    eid_c = "ccccbbbbbbbbbbbb"

    # Merge B into A: winner = A (lexmin)
    async with store.transaction_immediate() as tx:
        winner1 = await identity_store.merge_entities(
            eid_b, eid_a, reason="merge_b_into_a", tx=tx
        )
    assert winner1 == eid_a

    # Merge C into winner (A): winner = A (lexmin of A, C)
    async with store.transaction_immediate() as tx:
        winner2 = await identity_store.merge_entities(
            eid_c, eid_a, reason="merge_c_into_a", tx=tx
        )
    assert winner2 == eid_a

    # Should have 2 migration rows
    cursor = await store._db.execute(
        """SELECT from_entity_id, to_entity_id, merge_reason
           FROM entity_migrations
           ORDER BY merged_at ASC"""
    )
    rows = await cursor.fetchall()
    migration_rows = [r for r in rows if r[1] == eid_a]
    assert len(migration_rows) == 2

    # First merge: B -> A
    assert migration_rows[0][0] == eid_b
    assert migration_rows[0][1] == eid_a
    assert migration_rows[0][2] == "merge_b_into_a"

    # Second merge: C -> A
    assert migration_rows[1][0] == eid_c
    assert migration_rows[1][1] == eid_a
    assert migration_rows[1][2] == "merge_c_into_a"

    # All resolve to A
    assert await identity_store.resolve_entity_root(eid_a) == eid_a
    assert await identity_store.resolve_entity_root(eid_b) == eid_a
    assert await identity_store.resolve_entity_root(eid_c) == eid_a


# =============================================================================
# TEST 12: Merge same entity is noop
# =============================================================================

@pytest.mark.asyncio
async def test_merge_same_entity_is_noop(store, identity_store):
    """merge_entities(eid, eid, reason, tx) returns eid, no migrations row."""
    eid = "dddddddddddddddd"

    async with store.transaction_immediate() as tx:
        result = await identity_store.merge_entities(
            eid, eid, reason="self_merge_test", tx=tx
        )

    assert result == eid

    # No migration row should exist
    cursor = await store._db.execute(
        """SELECT COUNT(*) FROM entity_migrations
           WHERE from_entity_id = ? OR to_entity_id = ?""",
        (eid, eid),
    )
    count = (await cursor.fetchone())[0]
    assert count == 0


# =============================================================================
# TEST 13: get_entity_aliases after merge
# =============================================================================

@pytest.mark.asyncio
async def test_get_entity_aliases_after_merge(store, identity_store):
    """After merge, get_entity_aliases(winner) returns strong keys bound
    to winner."""
    eid_winner = "aaaa000011110000"
    eid_loser = "bbbb000022220000"
    key_winner = "domain:winner-aliases.com"
    key_loser = "domain:loser-aliases.com"

    # Register strong keys for both entities
    async with store.transaction_immediate() as tx:
        await identity_store.upsert_strong_key_bindings([
            StrongKeyBinding(strong_key=key_winner, entity_id=eid_winner,
                             source_key="test"),
            StrongKeyBinding(strong_key=key_loser, entity_id=eid_loser,
                             source_key="test"),
        ], tx)

    # Merge loser into winner
    async with store.transaction_immediate() as tx:
        winner = await identity_store.merge_entities(
            eid_winner, eid_loser, reason="test_alias_merge", tx=tx
        )
    assert winner == eid_winner

    # get_entity_aliases for winner should return winner's strong keys
    aliases = await identity_store.get_entity_aliases(eid_winner)
    strong_aliases = [a for a in aliases if a["type"] == "strong"]
    strong_keys = {a["key"] for a in strong_aliases}
    assert key_winner in strong_keys

    # Querying the loser should resolve to winner and return winner's aliases
    aliases_via_loser = await identity_store.get_entity_aliases(eid_loser)
    strong_via_loser = [a for a in aliases_via_loser if a["type"] == "strong"]
    strong_keys_via_loser = {a["key"] for a in strong_via_loser}
    assert key_winner in strong_keys_via_loser


# =============================================================================
# TEST 14: Review collision resolved on merge
# =============================================================================

@pytest.mark.asyncio
async def test_review_collision_resolved_on_merge(store, identity_store):
    """Both winner and loser have active reviews (winner=approved,
    loser=pending). After cascade_merge, loser review rejected with
    merged_into reason, evidence merged."""
    winner_id = "aaaa000033330000"
    loser_id = "bbbb000044440000"

    # Winner: signals + company file + approved review
    await _insert_signal(store, 10, winner_id, canonical_key="domain:w-review.com")
    await _insert_signal(store, 11, winner_id, canonical_key="domain:w-review.com",
                         source_api="sec_edgar")
    await _insert_company_file(store, winner_id, source_apis=["github", "sec_edgar"])
    await _insert_review(store, 200, winner_id, status="approved", signal_ids=[10, 11])

    # Loser: signals + company file + pending review
    await _insert_signal(store, 12, loser_id, canonical_key="domain:l-review.com",
                         source_api="hacker_news")
    await _insert_signal(store, 13, loser_id, canonical_key="domain:l-review.com",
                         source_api="news_api")
    await _insert_company_file(store, loser_id, source_apis=["hacker_news", "news_api"])
    await _insert_review(store, 201, loser_id, status="pending", signal_ids=[12, 13])

    report = await cascade_merge(
        store, winner_id, loser_id,
        reason="confirmed_duplicate", actor="operator"
    )

    assert report["reviews_merged"] is True

    # Winner's review (approved) should be the primary (higher precedence)
    cursor = await store._db.execute(
        "SELECT status, evidence_bundle FROM review_items WHERE id = 200"
    )
    winner_review = await cursor.fetchone()
    assert winner_review is not None
    assert winner_review[0] == "approved"
    # Evidence should contain signals from both reviews
    evidence = json.loads(winner_review[1])
    assert 10 in evidence["signal_ids"]
    assert 11 in evidence["signal_ids"]
    assert 12 in evidence["signal_ids"]
    assert 13 in evidence["signal_ids"]

    # Loser's review should be rejected with merged_into reason
    cursor = await store._db.execute(
        "SELECT status, reason, company_id FROM review_items WHERE id = 201"
    )
    loser_review = await cursor.fetchone()
    assert loser_review is not None
    assert loser_review[0] == "rejected"
    assert loser_review[1] is not None
    assert "merged_into:" in loser_review[1]
    # After cascade, loser's review company_id is reassigned to winner
    assert loser_review[2] == winner_id


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

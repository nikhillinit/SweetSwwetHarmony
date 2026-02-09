"""End-to-end integration tests for Phase 1a canonical identity pipeline.

Tests the complete flow across all Phase 1a components:
- SignalStore (with identity store + thin files wired)
- EntityIdentityStore (strong key bindings, merge, root resolution)
- ReviewStore (state machine lifecycle)
- ThinFileManager (company files, promotion, sweep)
- MergeCascade (cross-table cascade)
- IdentityGate (NULL company_id enforcement)
- Backfill (dry-run + apply + validation)

Each scenario exercises multiple modules together, validating the integration
contract rather than isolated unit behavior.
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
from storage.entity_identity_store import EntityIdentityStore, StrongKeyBinding
from storage.review_store import (
    create_review_item,
    update_review_status,
    get_review_queue,
    InvalidStateTransition,
)
from storage.identity_gate import check_identity_integrity, IdentityMigrationRequired
from storage.merge_cascade import cascade_merge
from storage.migrations.backfill_v28_identity import backfill_company_ids, validate_company_ids
from workflows.thin_file_manager import (
    upsert_company_file,
    check_and_promote_atomic,
    run_promotion_sweep,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with all migrations applied (no identity wiring)."""
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
async def wired_store():
    """SignalStore with identity store + thin files fully wired.

    This is the production-like configuration where save_signal() automatically:
    - Resolves company_id via EntityIdentityStore
    - Registers strong key bindings
    - Upserts company_files rows
    - Triggers cascade_merge on identity collisions
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    s = SignalStore(db_path=path, use_thin_files=True)
    await s.initialize()

    id_store = EntityIdentityStore(s)
    s._identity_store = id_store

    yield s

    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


# =============================================================================
# HELPERS
# =============================================================================

async def _insert_signal_raw(store, signal_id, canonical_key, source_api="github",
                             company_id=None):
    """Insert a signal row directly, bypassing identity wiring.

    Useful for setting up test state that would be awkward to create
    through the normal save_signal() path (e.g., NULL company_id).
    """
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


async def _insert_company_file(store, company_id, source_apis=None,
                               status="thin"):
    """Insert a company_files row directly."""
    if source_apis is None:
        source_apis = ["github"]
    now = "2026-01-15T00:00:00+00:00"
    await store._db.execute(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, status,
            source_apis, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (company_id, "Test Co", f"domain:{company_id}.com", status,
         json.dumps(source_apis), now, now),
    )
    await store._db.commit()


async def _insert_review(store, company_id, status="pending",
                         evidence_ids=None):
    """Insert a review_items row directly."""
    if evidence_ids is None:
        evidence_ids = [1]
    now = "2026-01-15T00:00:00+00:00"
    bundle = json.dumps({"signal_ids": sorted(evidence_ids), "schema_version": 1})
    cursor = await store._db.execute(
        """INSERT INTO review_items
           (company_id, status, evidence_bundle, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (company_id, status, bundle, now, now),
    )
    await store._db.commit()
    return cursor.lastrowid


# =============================================================================
# SCENARIO 1: End-to-end signals -> company_files -> promotion -> review_items
# =============================================================================

class TestEndToEndSignalToReview:
    """Scenario 1: Save signals via wired store and verify the full chain.

    Two signals for the same company, from different source APIs, should:
    1. Resolve to the same company_id (deterministic SHA256[:16])
    2. Create/update a single company_files row accumulating sources
    3. Trigger promotion once multi-source criteria are met
    4. Create a pending review_item with evidence bundle
    """

    @pytest.mark.asyncio
    async def test_two_signals_same_company_triggers_promotion(self, wired_store):
        """Two signals from different sources for the same canonical key
        should create a company file, accumulate sources, and be ready
        for promotion."""
        store = wired_store
        canonical_key = "domain:acme-health.com"

        # Save first signal (github source)
        sig1 = await store.save_signal(
            signal_type="github_spike",
            source_api="github",
            canonical_key=canonical_key,
            confidence=0.7,
            raw_data={"repo": "acme-health/app", "stars": 150},
            company_name="Acme Health",
        )

        # Verify company_files row was created
        cursor = await store._db.execute(
            "SELECT company_id, status, source_apis FROM company_files"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        company_id = rows[0][0]
        assert rows[0][1] == "thin"
        sources_after_1 = json.loads(rows[0][2])
        assert sources_after_1 == ["github"]

        # Verify company_id matches deterministic generation
        expected_id = EntityIdentityStore.entity_id_for_seed(canonical_key)
        assert company_id == expected_id

        # Save second signal (sec_edgar source) -- same canonical key
        sig2 = await store.save_signal(
            signal_type="sec_filing",
            source_api="sec_edgar",
            canonical_key=canonical_key,
            confidence=0.8,
            raw_data={"form": "D", "cik": "0001234567"},
            company_name="Acme Health Inc",
        )

        # Verify sources accumulated in company_files
        cursor = await store._db.execute(
            "SELECT source_apis FROM company_files WHERE company_id = ?",
            (company_id,),
        )
        row = await cursor.fetchone()
        sources_after_2 = json.loads(row[0])
        assert "github" in sources_after_2
        assert "sec_edgar" in sources_after_2
        assert len(sources_after_2) == 2

        # Both signals should have the same company_id
        cursor = await store._db.execute(
            "SELECT company_id FROM signals WHERE id IN (?, ?)",
            (sig1, sig2),
        )
        signal_rows = await cursor.fetchall()
        assert all(r[0] == company_id for r in signal_rows)

        # Trigger promotion (2 sources = multi-source criteria met)
        review_id = await check_and_promote_atomic(store, company_id)
        assert review_id is not None

        # Verify company_files status changed to promoted
        cursor = await store._db.execute(
            "SELECT status, promoted_at FROM company_files WHERE company_id = ?",
            (company_id,),
        )
        cf_row = await cursor.fetchone()
        assert cf_row[0] == "promoted"
        assert cf_row[1] is not None  # promoted_at set

        # Verify review_item was created with pending status
        queue = await get_review_queue(store, status="pending")
        assert len(queue) == 1
        review = queue[0]
        assert review["id"] == review_id
        assert review["company_id"] == company_id
        assert review["status"] == "pending"

        # Evidence bundle should contain both signal IDs
        evidence = review["evidence_bundle"]
        assert sig1 in evidence["signal_ids"]
        assert sig2 in evidence["signal_ids"]


# =============================================================================
# SCENARIO 2: Merge with review collision
# =============================================================================

class TestMergeWithReviewCollision:
    """Scenario 2: Merge two companies that both have active reviews.

    When cascade_merge is triggered:
    - Loser's review is rejected with reason 'merged_into:X'
    - Winner's review evidence_bundle absorbs loser's signal_ids
    - Loser's signals are reassigned to winner
    - Loser's company_file is deleted, winner's sources are merged
    - An audit_log entry records the merge
    """

    @pytest.mark.asyncio
    async def test_cascade_merge_resolves_review_collision(self, store):
        """Full cascade merge with both companies having active reviews,
        signals, and company files."""
        winner_id = "aaaa111111111111"
        loser_id = "bbbb222222222222"

        # Set up winner: 2 signals, company file, pending review
        await _insert_signal_raw(store, 1, "domain:winner.com",
                                 source_api="github", company_id=winner_id)
        await _insert_signal_raw(store, 2, "domain:winner.com",
                                 source_api="sec_edgar", company_id=winner_id)
        await _insert_company_file(
            store, winner_id, source_apis=["github", "sec_edgar"]
        )
        winner_review_id = await _insert_review(
            store, winner_id, status="pending", evidence_ids=[1, 2]
        )

        # Set up loser: 2 signals, company file, pending review
        await _insert_signal_raw(store, 3, "domain:loser.com",
                                 source_api="hacker_news", company_id=loser_id)
        await _insert_signal_raw(store, 4, "domain:loser.com",
                                 source_api="news_api", company_id=loser_id)
        await _insert_company_file(
            store, loser_id, source_apis=["hacker_news", "news_api"]
        )
        loser_review_id = await _insert_review(
            store, loser_id, status="pending", evidence_ids=[3, 4]
        )

        # Execute cascade merge
        report = await cascade_merge(
            store, winner_id, loser_id, reason="same_company", actor="operator"
        )

        # -- Verify report --
        assert report["winner"] == winner_id
        assert report["loser"] == loser_id
        assert report["signals_reassigned"] == 2
        assert report["reviews_merged"] is True
        assert report["company_file_merged"] is True

        # -- Verify loser's review is rejected with merge reason --
        cursor = await store._db.execute(
            "SELECT status, reason FROM review_items WHERE id = ?",
            (loser_review_id,),
        )
        loser_review = await cursor.fetchone()
        assert loser_review[0] == "rejected"
        assert f"merged_into:{winner_review_id}" in loser_review[1]

        # -- Verify winner's evidence_bundle has merged signal_ids --
        cursor = await store._db.execute(
            "SELECT evidence_bundle FROM review_items WHERE id = ?",
            (winner_review_id,),
        )
        winner_bundle = json.loads((await cursor.fetchone())[0])
        assert sorted(winner_bundle["signal_ids"]) == [1, 2, 3, 4]

        # -- Verify loser's signals are reassigned to winner --
        cursor = await store._db.execute(
            "SELECT company_id FROM signals WHERE id IN (3, 4)"
        )
        reassigned = await cursor.fetchall()
        assert all(r[0] == winner_id for r in reassigned)

        # -- Verify loser's company_file is deleted --
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM company_files WHERE company_id = ?",
            (loser_id,),
        )
        assert (await cursor.fetchone())[0] == 0

        # -- Verify winner's sources are merged --
        cursor = await store._db.execute(
            "SELECT source_apis FROM company_files WHERE company_id = ?",
            (winner_id,),
        )
        winner_sources = json.loads((await cursor.fetchone())[0])
        assert set(winner_sources) == {"github", "hacker_news", "news_api", "sec_edgar"}

        # -- Verify audit_log entry --
        cursor = await store._db.execute(
            """SELECT action_type, entity_type, entity_id, actor, details
               FROM audit_log
               WHERE action_type = 'cascade_merge'"""
        )
        audit = await cursor.fetchone()
        assert audit is not None
        assert audit[0] == "cascade_merge"
        assert audit[1] == "company"
        assert audit[2] == winner_id
        assert audit[3] == "operator"
        audit_details = json.loads(audit[4])
        assert audit_details["signals_reassigned"] == 2
        assert audit_details["reviews_merged"] is True


# =============================================================================
# SCENARIO 3: Identity gate blocks NULL company_id
# =============================================================================

class TestIdentityGateBlocksNull:
    """Scenario 3: Pipeline refuses to run if signals have NULL company_id.

    The identity gate calls validate_company_ids() and raises
    IdentityMigrationRequired if any NULLs exist.
    """

    @pytest.mark.asyncio
    async def test_gate_raises_on_null_company_id(self, store):
        """Inserting a signal with NULL company_id (bypassing identity store)
        should cause the identity gate to raise."""
        # Insert signal with NULL company_id
        await _insert_signal_raw(
            store, 1, "domain:orphan.com", source_api="github", company_id=None
        )

        with pytest.raises(IdentityMigrationRequired) as exc_info:
            await check_identity_integrity(store)

        assert "1 signals have NULL company_id" in str(exc_info.value)
        assert "backfill" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_gate_passes_when_all_populated(self, store):
        """Gate should pass silently when all signals have company_id."""
        await _insert_signal_raw(
            store, 1, "domain:good.com", source_api="github",
            company_id="abcd123456789012"
        )

        # Should not raise
        await check_identity_integrity(store)

    @pytest.mark.asyncio
    async def test_gate_passes_on_empty_db(self, store):
        """Gate should pass silently when no signals exist."""
        await check_identity_integrity(store)


# =============================================================================
# SCENARIO 4: State machine full lifecycle
# =============================================================================

class TestReviewStateLifecycle:
    """Scenario 4: Full review lifecycle pending -> approved -> published.

    Verifies that decision metadata (decided_at, decided_by) is set
    at the appropriate transitions, and that published is terminal.
    """

    @pytest.mark.asyncio
    async def test_full_lifecycle_pending_approved_published(self, store):
        """Walk the happy-path lifecycle: pending -> approved -> published."""
        company_id = "lifecycle_test_co1"

        # Insert signal so evidence bundle references something real
        await _insert_signal_raw(
            store, 1, "domain:lifecycle.com", company_id=company_id
        )

        # Create review item (pending)
        review_id = await create_review_item(store, company_id, [1])

        # Verify initial state
        queue = await get_review_queue(store, status="pending")
        assert len(queue) == 1
        assert queue[0]["id"] == review_id
        assert queue[0]["decided_at"] is None
        assert queue[0]["decided_by"] is None

        # Transition: pending -> approved
        await update_review_status(store, review_id, "approved", actor="analyst_1")

        cursor = await store._db.execute(
            "SELECT status, decided_at, decided_by FROM review_items WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == "approved"
        assert row[1] is not None  # decided_at set
        assert row[2] == "analyst_1"

        # Transition: approved -> published
        await update_review_status(store, review_id, "published", actor="system")

        cursor = await store._db.execute(
            "SELECT status, decided_at, decided_by FROM review_items WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == "published"
        assert row[2] == "system"  # decided_by updated to latest actor

        # Verify terminal: published has no valid outbound transitions
        with pytest.raises(InvalidStateTransition):
            await update_review_status(store, review_id, "pending", actor="system")

        with pytest.raises(InvalidStateTransition):
            await update_review_status(store, review_id, "approved", actor="system")

    @pytest.mark.asyncio
    async def test_rejected_is_terminal(self, store):
        """Rejected status should have no valid outbound transitions."""
        company_id = "terminal_reject_co"
        await _insert_signal_raw(
            store, 10, "domain:reject.com", company_id=company_id
        )

        review_id = await create_review_item(store, company_id, [10])
        await update_review_status(store, review_id, "rejected", actor="analyst_2",
                                   reason="B2B SaaS, not thesis fit")

        with pytest.raises(InvalidStateTransition):
            await update_review_status(store, review_id, "pending", actor="system")


# =============================================================================
# SCENARIO 5: Emergency halt publish_queued -> rejected
# =============================================================================

class TestEmergencyHalt:
    """Scenario 5: Emergency halt from publish_queued to rejected.

    The publish_queued -> rejected transition is the emergency halt path.
    Verify the status, reason, and audit log capture the halt.
    """

    @pytest.mark.asyncio
    async def test_publish_queued_emergency_halt(self, store):
        """Walk the halt path: pending -> approved -> publish_queued -> rejected."""
        company_id = "halt_test_company"
        await _insert_signal_raw(
            store, 20, "domain:halt.com", company_id=company_id
        )

        review_id = await create_review_item(store, company_id, [20])

        # Walk to publish_queued
        await update_review_status(store, review_id, "approved", actor="analyst")
        await update_review_status(store, review_id, "publish_queued", actor="system")

        # Verify intermediate state
        cursor = await store._db.execute(
            "SELECT status FROM review_items WHERE id = ?",
            (review_id,),
        )
        assert (await cursor.fetchone())[0] == "publish_queued"

        # Emergency halt
        await update_review_status(
            store, review_id, "rejected", actor="compliance_officer",
            reason="halt: compliance issue — pending regulatory review"
        )

        # Verify final state
        cursor = await store._db.execute(
            "SELECT status, reason, decided_at, decided_by FROM review_items WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == "rejected"
        assert "halt:" in row[1]
        assert "compliance" in row[1]
        assert row[2] is not None  # decided_at
        assert row[3] == "compliance_officer"

        # Verify audit_log captures the halt transition
        cursor = await store._db.execute(
            """SELECT action_type, entity_type, entity_id, actor, details
               FROM audit_log
               WHERE action_type = 'status_transition'
               AND entity_id = ?
               ORDER BY created_at DESC
               LIMIT 1""",
            (str(review_id),),
        )
        audit = await cursor.fetchone()
        assert audit is not None
        assert audit[0] == "status_transition"
        assert audit[1] == "review_item"
        assert audit[3] == "compliance_officer"

        audit_details = json.loads(audit[4])
        assert audit_details["before"]["status"] == "publish_queued"
        assert audit_details["after"]["status"] == "rejected"
        assert "halt:" in audit_details["reason"]

        # Verify terminal: rejected has no outbound transitions
        with pytest.raises(InvalidStateTransition):
            await update_review_status(store, review_id, "pending", actor="system")


# =============================================================================
# SCENARIO 6: Backfill dry-run + apply on test data
# =============================================================================

class TestBackfillDryRunAndApply:
    """Scenario 6: Backfill company_ids on signals missing them.

    1. Insert signals without company_ids (bypass identity store)
    2. Dry-run reports mappings but does not modify DB
    3. Apply fills all NULLs
    4. Validation confirms no NULLs remain
    """

    @pytest.mark.asyncio
    async def test_backfill_dry_run_then_apply(self, store):
        """Full backfill cycle: dry-run -> apply -> validate."""
        # Insert 3 signals with NULL company_id
        await _insert_signal_raw(store, 1, "domain:alpha.com", company_id=None)
        await _insert_signal_raw(store, 2, "domain:alpha.com",
                                 source_api="sec_edgar", company_id=None)
        await _insert_signal_raw(store, 3, "domain:beta.com", company_id=None)

        # --- Dry-run: should report mappings, NOT modify DB ---
        dry_result = await backfill_company_ids(store, dry_run=True)

        assert dry_result["mode"] == "dry_run"
        assert dry_result["null_count_before"] == 3
        assert len(dry_result["mappings"]) == 3

        # All signals for same canonical_key should map to same company_id
        alpha_ids = {
            m["company_id"]
            for m in dry_result["mappings"]
            if m["canonical_key"] == "domain:alpha.com"
        }
        assert len(alpha_ids) == 1  # deterministic: same key -> same ID

        beta_ids = {
            m["company_id"]
            for m in dry_result["mappings"]
            if m["canonical_key"] == "domain:beta.com"
        }
        assert len(beta_ids) == 1

        # Different keys produce different IDs
        assert alpha_ids != beta_ids

        # Verify DB NOT modified
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id IS NULL"
        )
        assert (await cursor.fetchone())[0] == 3  # still NULL

        # --- Apply: should fill all NULLs ---
        apply_result = await backfill_company_ids(store, dry_run=False)

        assert apply_result["mode"] == "apply"
        assert apply_result["null_count_before"] == 3
        assert apply_result["null_count_after"] == 0
        assert len(apply_result["mappings"]) == 3

        # Verify DB is now fully populated
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id IS NULL"
        )
        assert (await cursor.fetchone())[0] == 0

        # Verify deterministic: alpha signals share one ID, beta has another
        cursor = await store._db.execute(
            "SELECT DISTINCT company_id FROM signals WHERE canonical_key = 'domain:alpha.com'"
        )
        alpha_db_ids = [r[0] for r in await cursor.fetchall()]
        assert len(alpha_db_ids) == 1

        cursor = await store._db.execute(
            "SELECT DISTINCT company_id FROM signals WHERE canonical_key = 'domain:beta.com'"
        )
        beta_db_ids = [r[0] for r in await cursor.fetchall()]
        assert len(beta_db_ids) == 1
        assert alpha_db_ids[0] != beta_db_ids[0]

        # --- Validate: should confirm valid ---
        validation = await validate_company_ids(store)
        assert validation["valid"] is True
        assert validation["null_count"] == 0
        assert validation["total_signals"] == 3

    @pytest.mark.asyncio
    async def test_backfill_idempotent_on_second_run(self, store):
        """Running backfill twice should be a no-op the second time."""
        await _insert_signal_raw(store, 1, "domain:gamma.com", company_id=None)

        # First apply
        result1 = await backfill_company_ids(store, dry_run=False)
        assert result1["null_count_after"] == 0

        # Second apply: nothing to do
        result2 = await backfill_company_ids(store, dry_run=False)
        assert result2["null_count_before"] == 0
        assert result2["mappings"] == []


# =============================================================================
# SCENARIO 7: Merge cascade from pipeline via identity resolution
# =============================================================================

class TestMergeCascadeFromPipeline:
    """Scenario 7: Identity merge triggered by save_signal via wired store.

    1. Register two canonical keys to different entity_ids
    2. Merge the entities via identity_store.merge_entities()
    3. Save a new signal with one of the canonical keys
    4. Verify the signal resolves to the winner's company_id (root resolution)
    """

    @pytest.mark.asyncio
    async def test_signal_resolves_to_merge_winner(self, wired_store):
        """After merging entities, new signals should resolve to the winner."""
        store = wired_store
        id_store = store._identity_store

        key_a = "domain:brand-a.com"
        key_b = "domain:brand-b.com"

        entity_a = EntityIdentityStore.entity_id_for_seed(key_a)
        entity_b = EntityIdentityStore.entity_id_for_seed(key_b)

        # Deterministic winner is lexmin
        winner = min(entity_a, entity_b)
        loser = max(entity_a, entity_b)

        # Register both keys to their respective entities
        async with store.transaction_immediate() as tx:
            await id_store.upsert_strong_key_bindings([
                StrongKeyBinding(strong_key=key_a, entity_id=entity_a,
                                 source_key="test"),
                StrongKeyBinding(strong_key=key_b, entity_id=entity_b,
                                 source_key="test"),
            ], tx)

        # Set up company files and signals for both so cascade_merge has rows
        await _insert_signal_raw(store, 100, key_a, company_id=entity_a)
        await _insert_company_file(store, entity_a, source_apis=["github"])
        await _insert_signal_raw(store, 101, key_b, company_id=entity_b)
        await _insert_company_file(store, entity_b, source_apis=["sec_edgar"])

        # Merge entities at the identity level
        async with store.transaction_immediate() as tx:
            merge_winner = await id_store.merge_entities(
                entity_a, entity_b, reason="confirmed_same_company", tx=tx
            )
        assert merge_winner == winner

        # Now save a new signal using key_b (the loser's key)
        # The identity store should resolve key_b -> entity_b -> root = winner
        new_sig_id = await store.save_signal(
            signal_type="news_mention",
            source_api="news_api",
            canonical_key=key_b,
            confidence=0.6,
            raw_data={"headline": "Brand B raises seed round"},
            company_name="Brand B",
        )

        # Verify the new signal has the winner's company_id
        cursor = await store._db.execute(
            "SELECT company_id FROM signals WHERE id = ?",
            (new_sig_id,),
        )
        actual_company_id = (await cursor.fetchone())[0]
        assert actual_company_id == winner, (
            f"Expected winner {winner} but got {actual_company_id}. "
            f"Root resolution should follow entity_migrations."
        )

    @pytest.mark.asyncio
    async def test_save_signal_generates_consistent_company_id(self, wired_store):
        """Two save_signal calls with the same canonical key should produce
        the same company_id without any merge needed."""
        store = wired_store
        key = "domain:consistent.com"

        sig1 = await store.save_signal(
            signal_type="github_spike",
            source_api="github",
            canonical_key=key,
            confidence=0.7,
            raw_data={"test": 1},
        )

        sig2 = await store.save_signal(
            signal_type="job_posting",
            source_api="job_postings",
            canonical_key=key,
            confidence=0.6,
            raw_data={"test": 2},
        )

        cursor = await store._db.execute(
            "SELECT company_id FROM signals WHERE id IN (?, ?) ORDER BY id",
            (sig1, sig2),
        )
        rows = await cursor.fetchall()
        assert rows[0][0] == rows[1][0], "Same key should yield same company_id"
        assert rows[0][0] == EntityIdentityStore.entity_id_for_seed(key)

    @pytest.mark.asyncio
    async def test_pre_registered_key_resolves_to_existing_entity(self, wired_store):
        """When a canonical key is already bound to an entity_id (e.g., via
        manual registration), save_signal should resolve to that existing
        entity rather than generating a new one from the seed."""
        store = wired_store
        id_store = store._identity_store

        key = "domain:preregistered.com"
        manual_entity = "0000manual0000000"

        # Pre-register key to a manually-assigned entity
        async with store.transaction_immediate() as tx:
            await id_store.upsert_strong_key_bindings([
                StrongKeyBinding(strong_key=key, entity_id=manual_entity,
                                 source_key="manual_import"),
            ], tx)

        # save_signal should discover the existing binding via lookup_strong_keys
        # and use manual_entity (NOT entity_id_for_seed(key))
        seed_entity = EntityIdentityStore.entity_id_for_seed(key)
        assert seed_entity != manual_entity  # sanity: they differ

        sig_id = await store.save_signal(
            signal_type="domain_registration",
            source_api="domain_whois",
            canonical_key=key,
            confidence=0.5,
            raw_data={"registrar": "Namecheap"},
        )

        # Signal should have the pre-registered entity, not the seed-derived one
        cursor = await store._db.execute(
            "SELECT company_id FROM signals WHERE id = ?",
            (sig_id,),
        )
        actual = (await cursor.fetchone())[0]
        assert actual == manual_entity, (
            f"Expected pre-registered {manual_entity} but got {actual}. "
            f"save_signal should prefer existing binding over seed generation."
        )

        # No merges should have occurred
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM entity_migrations"
        )
        assert (await cursor.fetchone())[0] == 0

    @pytest.mark.asyncio
    async def test_post_merge_signals_and_files_consistent(self, wired_store):
        """After merging two entities that each have signals and company files,
        all subsequent lookups should return the winner's company_id, and
        company file sources should be merged."""
        store = wired_store
        id_store = store._identity_store

        key_x = "domain:company-x.com"
        key_y = "domain:company-y.com"

        # Save signals to create both entities with their company files
        sig_x = await store.save_signal(
            signal_type="github_spike", source_api="github",
            canonical_key=key_x, confidence=0.7,
            raw_data={"repo": "company-x/app"},
            company_name="Company X",
        )
        sig_y = await store.save_signal(
            signal_type="job_posting", source_api="job_postings",
            canonical_key=key_y, confidence=0.6,
            raw_data={"title": "Backend Engineer"},
            company_name="Company Y",
        )

        entity_x = EntityIdentityStore.entity_id_for_seed(key_x)
        entity_y = EntityIdentityStore.entity_id_for_seed(key_y)
        winner = min(entity_x, entity_y)
        loser = max(entity_x, entity_y)

        # Merge via identity store + cascade_merge for dependent tables
        async with store.transaction_immediate() as tx:
            await id_store.merge_entities(
                entity_x, entity_y, reason="manual_dedup", tx=tx
            )

        await cascade_merge(
            store, winner, loser,
            reason="manual_dedup", actor="operator"
        )

        # Verify all signals now point to winner
        cursor = await store._db.execute(
            "SELECT company_id FROM signals ORDER BY id"
        )
        all_ids = [r[0] for r in await cursor.fetchall()]
        assert all(cid == winner for cid in all_ids)

        # Verify only winner's company_file remains
        cursor = await store._db.execute(
            "SELECT company_id, source_apis FROM company_files"
        )
        files = await cursor.fetchall()
        assert len(files) == 1
        assert files[0][0] == winner
        merged_sources = json.loads(files[0][1])
        assert "github" in merged_sources
        assert "job_postings" in merged_sources


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

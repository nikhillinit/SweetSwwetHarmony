"""
Tests for thin file manager — promotion rules + company file lifecycle (Task 9).

Covers:
- _parse_source_apis: JSON parsing with validation
- _meets_promotion_criteria: multi-source, trusted source, manual override
- upsert_company_file: create, update, reactivate branches
- check_and_promote_atomic: promotion + ReviewItem creation
- run_promotion_sweep: paginated sweep with composite cursor
- archive_stale_files: stale thin file archival
- Re-promotion: promoted files with no active review + new evidence
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from workflows.thin_file_manager import (
    _parse_source_apis,
    _meets_promotion_criteria,
    upsert_company_file,
    check_and_promote_atomic,
    run_promotion_sweep,
    archive_stale_files,
    TRUSTED_SOURCES,
)


@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with temp file DB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = SignalStore(db_path=path)
    await store.initialize()

    yield store

    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


async def _insert_signal(store, signal_id, company_id, source_api="github",
                          canonical_key=None):
    """Helper: insert a signal row directly.

    Uses signal_id to generate unique values to avoid UNIQUE constraint
    on (canonical_key, signal_type, source_api, detected_at).
    """
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


async def _insert_company_file(store, company_id, source_apis=None,
                                 status="thin", first_seen=None,
                                 last_seen=None, metadata=None):
    """Helper: insert a company_files row directly."""
    if source_apis is None:
        source_apis = ["github"]
    if first_seen is None:
        first_seen = "2026-01-01T00:00:00+00:00"
    if last_seen is None:
        last_seen = "2026-01-15T00:00:00+00:00"
    archived_at = None
    if status == "archived":
        archived_at = last_seen
    await store._db.execute(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, status,
            source_apis, first_seen_at, last_seen_at, archived_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (company_id, "Test Co", f"domain:{company_id}.com", status,
         json.dumps(source_apis), first_seen, last_seen, archived_at,
         json.dumps(metadata) if metadata else None),
    )
    await store._db.commit()


# =============================================================================
# _parse_source_apis
# =============================================================================

class TestParseSourceApis:
    """Tests for JSON source_apis parsing."""

    def test_valid_json_list(self):
        assert _parse_source_apis('["github", "sec_edgar"]') == ["github", "sec_edgar"]

    def test_empty_list(self):
        assert _parse_source_apis('[]') == []

    def test_none_input(self):
        assert _parse_source_apis(None) == []

    def test_empty_string(self):
        assert _parse_source_apis('') == []

    def test_invalid_json(self):
        assert _parse_source_apis('not json') == []

    def test_json_object_not_list(self):
        assert _parse_source_apis('{"a": 1}') == []

    def test_filters_non_strings(self):
        assert _parse_source_apis('[1, "github", null, ""]') == ["github"]

    def test_filters_empty_strings(self):
        assert _parse_source_apis('["", "github"]') == ["github"]


# =============================================================================
# _meets_promotion_criteria
# =============================================================================

class TestPromotionCriteria:
    """Tests for promotion rule evaluation."""

    def test_multi_source_promotes(self):
        """2+ distinct sources should trigger promotion."""
        assert _meets_promotion_criteria(["github", "sec_edgar"]) is True

    def test_single_untrusted_source_no_promote(self):
        """Single untrusted source should not promote."""
        assert _meets_promotion_criteria(["github"]) is False

    def test_trusted_source_promotes(self):
        """Single trusted source (sec_edgar) should promote."""
        assert _meets_promotion_criteria(["sec_edgar"]) is True

    def test_companies_house_trusted(self):
        assert _meets_promotion_criteria(["companies_house"]) is True

    def test_crunchbase_trusted(self):
        assert _meets_promotion_criteria(["crunchbase"]) is True

    def test_manual_override(self):
        """Manual promotion should override all other rules."""
        assert _meets_promotion_criteria(
            ["github"], metadata={"manual_promotion": True}
        ) is True

    def test_manual_false_no_effect(self):
        """manual_promotion=False should not trigger promotion."""
        assert _meets_promotion_criteria(
            ["github"], metadata={"manual_promotion": False}
        ) is False

    def test_empty_sources_no_promote(self):
        assert _meets_promotion_criteria([]) is False

    def test_empty_sources_with_manual(self):
        """Manual override works even with no sources."""
        assert _meets_promotion_criteria(
            [], metadata={"manual_promotion": True}
        ) is True


# =============================================================================
# upsert_company_file
# =============================================================================

class TestUpsertCompanyFile:
    """Tests for company file creation, update, and reactivation."""

    @pytest.mark.asyncio
    async def test_create_new(self, store):
        """New company should create a thin file."""
        result = await upsert_company_file(
            store, "comp1", "Test Co", "domain:test.com", "github"
        )

        assert result == "created"

        cursor = await store._db.execute(
            "SELECT status, source_apis, company_name FROM company_files WHERE company_id = 'comp1'"
        )
        row = await cursor.fetchone()
        assert row[0] == "thin"
        assert json.loads(row[1]) == ["github"]
        assert row[2] == "Test Co"

    @pytest.mark.asyncio
    async def test_update_adds_source(self, store):
        """Existing thin file should get new source appended."""
        await _insert_company_file(store, "comp1", source_apis=["github"])

        result = await upsert_company_file(
            store, "comp1", "Test Co", "domain:test.com", "sec_edgar"
        )

        assert result == "updated"

        cursor = await store._db.execute(
            "SELECT source_apis FROM company_files WHERE company_id = 'comp1'"
        )
        sources = json.loads((await cursor.fetchone())[0])
        assert "github" in sources
        assert "sec_edgar" in sources

    @pytest.mark.asyncio
    async def test_update_deduplicates_source(self, store):
        """Same source API should not duplicate in source_apis."""
        await _insert_company_file(store, "comp1", source_apis=["github"])

        await upsert_company_file(
            store, "comp1", "Test Co", "domain:test.com", "github"
        )

        cursor = await store._db.execute(
            "SELECT source_apis FROM company_files WHERE company_id = 'comp1'"
        )
        sources = json.loads((await cursor.fetchone())[0])
        assert sources.count("github") == 1

    @pytest.mark.asyncio
    async def test_reactivate_archived(self, store):
        """Archived file should be reactivated to thin status."""
        await _insert_company_file(store, "comp1", status="archived",
                                     source_apis=["github"])

        result = await upsert_company_file(
            store, "comp1", "Test Co", "domain:test.com", "sec_edgar"
        )

        assert result == "reactivated"

        cursor = await store._db.execute(
            "SELECT status, archived_at, source_apis FROM company_files WHERE company_id = 'comp1'"
        )
        row = await cursor.fetchone()
        assert row[0] == "thin"
        assert row[1] is None  # archived_at cleared
        sources = json.loads(row[2])
        assert "github" in sources
        assert "sec_edgar" in sources

    @pytest.mark.asyncio
    async def test_reactivate_logs_audit(self, store):
        """Reactivation should create an audit_log entry."""
        await _insert_company_file(store, "comp1", status="archived")

        await upsert_company_file(
            store, "comp1", "Test Co", "domain:test.com", "news_api"
        )

        cursor = await store._db.execute(
            """SELECT action_type, entity_id, details
               FROM audit_log WHERE action_type = 'reactivate'"""
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "reactivate"
        assert row[1] == "comp1"
        details = json.loads(row[2])
        assert details["source_api"] == "news_api"

    @pytest.mark.asyncio
    async def test_update_bumps_last_seen(self, store):
        """Update should bump last_seen_at."""
        await _insert_company_file(store, "comp1",
                                     last_seen="2026-01-01T00:00:00+00:00")

        await upsert_company_file(
            store, "comp1", "Test Co", "domain:test.com", "github"
        )

        cursor = await store._db.execute(
            "SELECT last_seen_at FROM company_files WHERE company_id = 'comp1'"
        )
        last_seen = (await cursor.fetchone())[0]
        assert last_seen > "2026-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_company_name_fallback_to_canonical_key(self, store):
        """If company_name is None, should use canonical_key as fallback."""
        await upsert_company_file(
            store, "comp1", None, "domain:test.com", "github"
        )

        cursor = await store._db.execute(
            "SELECT company_name FROM company_files WHERE company_id = 'comp1'"
        )
        assert (await cursor.fetchone())[0] == "domain:test.com"

    @pytest.mark.asyncio
    async def test_works_with_external_tx(self, store):
        """upsert should work within an external transaction."""
        async with store.transaction_immediate() as tx:
            result = await upsert_company_file(
                store, "comp1", "Co", "domain:co.com", "github", tx=tx
            )

        assert result == "created"

        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM company_files WHERE company_id = 'comp1'"
        )
        assert (await cursor.fetchone())[0] == 1


# =============================================================================
# check_and_promote_atomic
# =============================================================================

class TestCheckAndPromoteAtomic:
    """Tests for atomic promotion + ReviewItem creation."""

    @pytest.mark.asyncio
    async def test_promote_multi_source(self, store):
        """Thin file with 2+ sources should be promoted."""
        await _insert_company_file(store, "comp1",
                                     source_apis=["github", "sec_edgar"])
        await _insert_signal(store, 1, "comp1")
        await _insert_signal(store, 2, "comp1")

        review_id = await check_and_promote_atomic(store, "comp1")

        assert review_id is not None

        # Verify company_file status
        cursor = await store._db.execute(
            "SELECT status, promoted_at FROM company_files WHERE company_id = 'comp1'"
        )
        row = await cursor.fetchone()
        assert row[0] == "promoted"
        assert row[1] is not None

        # Verify review_item created
        cursor = await store._db.execute(
            "SELECT status, evidence_bundle FROM review_items WHERE id = ?",
            (review_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == "pending"
        bundle = json.loads(row[1])
        assert 1 in bundle["signal_ids"]
        assert 2 in bundle["signal_ids"]

    @pytest.mark.asyncio
    async def test_no_promote_single_untrusted(self, store):
        """Single untrusted source should not promote."""
        await _insert_company_file(store, "comp1", source_apis=["github"])
        await _insert_signal(store, 1, "comp1")

        review_id = await check_and_promote_atomic(store, "comp1")

        assert review_id is None

        cursor = await store._db.execute(
            "SELECT status FROM company_files WHERE company_id = 'comp1'"
        )
        assert (await cursor.fetchone())[0] == "thin"

    @pytest.mark.asyncio
    async def test_promote_trusted_single_source(self, store):
        """Single trusted source should promote."""
        await _insert_company_file(store, "comp1", source_apis=["sec_edgar"])
        await _insert_signal(store, 1, "comp1")

        review_id = await check_and_promote_atomic(store, "comp1")

        assert review_id is not None

    @pytest.mark.asyncio
    async def test_promote_manual_override(self, store):
        """Manual promotion metadata should trigger promotion."""
        await _insert_company_file(store, "comp1", source_apis=["github"],
                                     metadata={"manual_promotion": True})
        await _insert_signal(store, 1, "comp1")

        review_id = await check_and_promote_atomic(store, "comp1")

        assert review_id is not None

    @pytest.mark.asyncio
    async def test_already_promoted_no_action(self, store):
        """Promoted company file should not be re-promoted via check_and_promote."""
        await _insert_company_file(store, "comp1", status="promoted",
                                     source_apis=["github", "sec_edgar"])
        await _insert_signal(store, 1, "comp1")

        review_id = await check_and_promote_atomic(store, "comp1")

        assert review_id is None  # Only targets status='thin'

    @pytest.mark.asyncio
    async def test_active_review_prevents_duplicate(self, store):
        """If active review already exists, should return None."""
        await _insert_company_file(store, "comp1",
                                     source_apis=["github", "sec_edgar"])
        await _insert_signal(store, 1, "comp1")

        # First promotion succeeds
        review_id1 = await check_and_promote_atomic(store, "comp1")
        assert review_id1 is not None

        # Manually reset to thin to simulate edge case
        await store._db.execute(
            "UPDATE company_files SET status = 'thin' WHERE company_id = 'comp1'"
        )
        await store._db.commit()

        # Second attempt should return None (active review exists)
        review_id2 = await check_and_promote_atomic(store, "comp1")
        assert review_id2 is None

    @pytest.mark.asyncio
    async def test_no_signals_no_promotion(self, store):
        """Company with no signals should not be promoted."""
        await _insert_company_file(store, "comp1",
                                     source_apis=["github", "sec_edgar"])

        review_id = await check_and_promote_atomic(store, "comp1")

        assert review_id is None

    @pytest.mark.asyncio
    async def test_promote_creates_audit_entry(self, store):
        """Promotion should create an audit_log entry."""
        await _insert_company_file(store, "comp1",
                                     source_apis=["github", "sec_edgar"])
        await _insert_signal(store, 1, "comp1")

        await check_and_promote_atomic(store, "comp1")

        cursor = await store._db.execute(
            """SELECT action_type, entity_id, details
               FROM audit_log WHERE action_type = 'promote'"""
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[1] == "comp1"
        details = json.loads(row[2])
        assert details["source_count"] == 2
        assert details["signal_count"] == 1


# =============================================================================
# run_promotion_sweep
# =============================================================================

class TestPromotionSweep:
    """Tests for paginated promotion sweep."""

    @pytest.mark.asyncio
    async def test_sweep_promotes_eligible(self, store):
        """Sweep should promote thin files meeting criteria."""
        await _insert_company_file(store, "comp1",
                                     source_apis=["github", "sec_edgar"],
                                     last_seen="2026-01-15T00:00:00+00:00")
        await _insert_signal(store, 1, "comp1")

        promoted, last_seen, last_id = await run_promotion_sweep(store)

        assert promoted == 1
        assert last_seen is not None
        assert last_id == "comp1"

    @pytest.mark.asyncio
    async def test_sweep_skips_ineligible(self, store):
        """Sweep should skip thin files not meeting criteria."""
        await _insert_company_file(store, "comp1", source_apis=["github"],
                                     last_seen="2026-01-15T00:00:00+00:00")
        await _insert_signal(store, 1, "comp1")

        promoted, _, _ = await run_promotion_sweep(store)

        assert promoted == 0

    @pytest.mark.asyncio
    async def test_sweep_respects_limit(self, store):
        """Sweep should process at most `limit` candidates."""
        for i in range(5):
            cid = f"comp{i}"
            await _insert_company_file(store, cid,
                                         source_apis=["github", "sec_edgar"],
                                         last_seen=f"2026-01-{10+i:02d}T00:00:00+00:00")
            await _insert_signal(store, i + 1, cid)

        promoted, _, _ = await run_promotion_sweep(store, limit=3)

        # Should process 3 candidates (all eligible)
        assert promoted == 3

    @pytest.mark.asyncio
    async def test_sweep_pagination_cursor(self, store):
        """Second page should start after first page's cursor."""
        for i in range(4):
            cid = f"comp{i}"
            await _insert_company_file(store, cid,
                                         source_apis=["github", "sec_edgar"],
                                         last_seen=f"2026-01-{10+i:02d}T00:00:00+00:00")
            await _insert_signal(store, i + 1, cid)

        # First page
        p1, ls1, ci1 = await run_promotion_sweep(store, limit=2)
        assert p1 == 2

        # Second page using cursors
        p2, ls2, ci2 = await run_promotion_sweep(
            store, last_seen_cursor=ls1, company_id_cursor=ci1, limit=2
        )
        assert p2 == 2

        # Third page — nothing left
        p3, _, _ = await run_promotion_sweep(
            store, last_seen_cursor=ls2, company_id_cursor=ci2, limit=2
        )
        assert p3 == 0

    @pytest.mark.asyncio
    async def test_sweep_empty_database(self, store):
        """Sweep on empty DB should return 0."""
        promoted, ls, ci = await run_promotion_sweep(store)

        assert promoted == 0
        assert ls is None
        assert ci is None

    @pytest.mark.asyncio
    async def test_repromotion_after_rejection(self, store):
        """Promoted file with rejected review + new evidence should re-promote."""
        await _insert_company_file(store, "comp1",
                                     source_apis=["github", "sec_edgar"],
                                     status="promoted",
                                     last_seen="2026-02-01T00:00:00+00:00")
        await _insert_signal(store, 1, "comp1")

        # Insert a rejected review with older decided_at
        await store._db.execute(
            """INSERT INTO review_items
               (company_id, status, evidence_bundle, reason,
                created_at, updated_at, decided_at, decided_by)
               VALUES (?, 'rejected', ?, ?, ?, ?, ?, ?)""",
            ("comp1", json.dumps({"signal_ids": [1], "schema_version": 1}),
             "not a fit",
             "2026-01-10T00:00:00+00:00", "2026-01-10T00:00:00+00:00",
             "2026-01-10T00:00:00+00:00", "analyst"),
        )
        await store._db.commit()

        # last_seen (Feb 1) > decided_at (Jan 10) => re-promotion eligible
        promoted, _, _ = await run_promotion_sweep(store)

        # check_and_promote_atomic only targets status='thin', so re-promotion
        # candidates are identified by sweep but won't promote unless they meet
        # the thin status requirement. The sweep returns them as candidates.
        # Re-promotion should create a new review for this promoted company.
        cursor = await store._db.execute(
            """SELECT COUNT(*) FROM review_items
               WHERE company_id = 'comp1' AND status = 'pending'"""
        )
        pending_count = (await cursor.fetchone())[0]
        # This will be 1 if re-promotion worked
        assert pending_count == 1


# =============================================================================
# archive_stale_files
# =============================================================================

class TestArchiveStaleFiles:
    """Tests for stale thin file archival."""

    @pytest.mark.asyncio
    async def test_archive_stale(self, store):
        """Files older than cutoff should be archived."""
        old_date = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        await _insert_company_file(store, "comp1", last_seen=old_date)

        report = await archive_stale_files(store, days=60)

        assert report["archived_count"] == 1

        cursor = await store._db.execute(
            "SELECT status, archived_at FROM company_files WHERE company_id = 'comp1'"
        )
        row = await cursor.fetchone()
        assert row[0] == "archived"
        assert row[1] is not None

    @pytest.mark.asyncio
    async def test_preserve_recent(self, store):
        """Recent thin files should not be archived."""
        recent = datetime.now(timezone.utc).isoformat()
        await _insert_company_file(store, "comp1", last_seen=recent)

        report = await archive_stale_files(store, days=60)

        assert report["archived_count"] == 0

        cursor = await store._db.execute(
            "SELECT status FROM company_files WHERE company_id = 'comp1'"
        )
        assert (await cursor.fetchone())[0] == "thin"

    @pytest.mark.asyncio
    async def test_only_archives_thin(self, store):
        """Promoted files should not be archived even if stale."""
        old_date = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        await _insert_company_file(store, "comp1", status="promoted",
                                     last_seen=old_date)

        report = await archive_stale_files(store, days=60)

        assert report["archived_count"] == 0

    @pytest.mark.asyncio
    async def test_archive_creates_audit_entry(self, store):
        """Archival should create an audit_log entry with sample IDs."""
        old_date = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        await _insert_company_file(store, "comp1", last_seen=old_date)
        await _insert_company_file(store, "comp2", last_seen=old_date)

        await archive_stale_files(store, days=60)

        cursor = await store._db.execute(
            """SELECT details FROM audit_log
               WHERE action_type = 'archive_stale'"""
        )
        row = await cursor.fetchone()
        assert row is not None
        details = json.loads(row[0])
        assert details["archived_count"] == 2
        assert "comp1" in details["company_ids"]
        assert "comp2" in details["company_ids"]

    @pytest.mark.asyncio
    async def test_archive_empty_database(self, store):
        """Archive on empty DB should return 0."""
        report = await archive_stale_files(store, days=60)

        assert report["archived_count"] == 0

"""
SLO benchmark tests for Phase 1a canonical identity.

Validates two categories:

1. Index existence (hard-fail) -- verify all Phase 1a indexes are present
   in the DB after migrations. These are structural preconditions; if an
   index is missing, queries will regress and the SLO tests are meaningless.

2. SLO benchmarks (soft-fail) -- time critical queries against realistic
   data volumes and compare against target latencies.  Exceeding an SLO
   emits a warning but does NOT fail the test, because CI machines vary
   in speed and transient slowness should not block merges.

Data volumes used for seeding:
    - csv_export:       500 review_items + 500 company_files
    - review_queue:    1000 review_items (800 pending + 200 other)
    - promotion_check:  500 company_files (400 thin + 100 promoted)
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import warnings

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore


# =============================================================================
# SLO TARGETS (milliseconds)
# =============================================================================

SLO_TARGETS = {
    'csv_export_ms': 2000,        # <2s for 500 ReviewItems joined with company_files
    'review_queue_ms': 500,       # <500ms for 1000 pending items
    'promotion_check_ms': 500,    # <500ms for paginated sweep of thin files
}


# =============================================================================
# SEEDING HELPERS
# =============================================================================

def _company_id(n: int) -> str:
    """Deterministic company_id from an integer seed.

    Mirrors EntityIdentityStore.entity_id_for_seed() which uses SHA256[:16].
    """
    return hashlib.sha256(f"company-{n}".encode("utf-8")).hexdigest()[:16]


async def _seed_signals(db, count: int) -> None:
    """Insert *count* signals with deterministic company_ids.

    Each signal gets a unique (canonical_key, signal_type, source_api, detected_at)
    tuple to satisfy the UNIQUE constraint on the signals table.
    """
    sources = ["github", "sec_edgar", "product_hunt", "hacker_news", "news_api"]
    rows = []
    for i in range(count):
        cid = _company_id(i % (count // 2 or 1))  # ~2 signals per company
        source = sources[i % len(sources)]
        canonical_key = f"domain:company{i}.com"
        detected_at = f"2026-01-{(i % 28) + 1:02d}T{i % 24:02d}:00:00+00:00"
        created_at = detected_at
        rows.append((
            "funding", source, canonical_key, f"Company {i}",
            round(0.3 + (i % 7) * 0.1, 2),
            json.dumps({"seed": i}),
            detected_at, created_at, cid,
        ))
    await db.executemany(
        """INSERT INTO signals
           (signal_type, source_api, canonical_key, company_name,
            confidence, raw_data, detected_at, created_at, company_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    await db.commit()


async def _seed_review_items(db, count: int, pending_ratio: float = 0.8) -> None:
    """Insert *count* review_items.

    *pending_ratio* controls the fraction of items with status='pending'.
    Remaining items are split between 'approved' and 'rejected'.

    The UNIQUE partial index idx_review_one_active_per_company limits one
    active review per company_id, so each row gets a unique company_id.
    """
    statuses_other = ["approved", "rejected", "deferred", "published"]
    pending_count = int(count * pending_ratio)
    rows = []
    for i in range(count):
        cid = _company_id(i)
        if i < pending_count:
            status = "pending"
        else:
            # Cycle through non-pending statuses (these don't conflict on the
            # unique partial index since they exclude pending/approved/publish_queued)
            status = statuses_other[(i - pending_count) % len(statuses_other)]
        evidence = json.dumps({"signal_ids": [i, i + 1], "schema_version": 1})
        created_at = f"2026-01-{(i % 28) + 1:02d}T{i % 24:02d}:00:00+00:00"
        updated_at = created_at
        decided_at = None if status == "pending" else created_at
        decided_by = None if status == "pending" else "operator"
        rows.append((
            cid, status, evidence, f"reason-{i}",
            created_at, updated_at, decided_at, decided_by,
        ))
    await db.executemany(
        """INSERT INTO review_items
           (company_id, status, evidence_bundle, reason,
            created_at, updated_at, decided_at, decided_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    await db.commit()


async def _seed_company_files(db, count: int, thin_ratio: float = 0.8) -> None:
    """Insert *count* company_files.

    *thin_ratio* controls the fraction with status='thin'.
    Remaining files are split between 'promoted' and 'archived'.
    """
    thin_count = int(count * thin_ratio)
    rows = []
    for i in range(count):
        cid = _company_id(i)
        if i < thin_count:
            status = "thin"
        else:
            status = "promoted" if (i % 2 == 0) else "archived"
        source_apis = json.dumps(["github", "sec_edgar"][:((i % 2) + 1)])
        first_seen = f"2025-12-{(i % 28) + 1:02d}T00:00:00+00:00"
        last_seen = f"2026-01-{(i % 28) + 1:02d}T00:00:00+00:00"
        promoted_at = last_seen if status == "promoted" else None
        archived_at = last_seen if status == "archived" else None
        rows.append((
            cid, f"Company {i}", f"domain:company{i}.com", status,
            source_apis, first_seen, last_seen, promoted_at, archived_at, None,
        ))
    await db.executemany(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, status,
            source_apis, first_seen_at, last_seen_at, promoted_at, archived_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    await db.commit()


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with all migrations applied (empty data)."""
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
async def seeded_store():
    """SignalStore seeded with realistic volumes for SLO benchmarks.

    - 1000 signals
    -  500 review_items  (for csv_export; 80% pending)
    - 1000 review_items  (for review_queue; 80% pending)
    We combine: seed 1000 review_items total, query subsets.
    -  500 company_files (for promotion_check; 80% thin)
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    s = SignalStore(db_path=path)
    await s.initialize()
    db = s._db

    # Seed data volumes
    await _seed_signals(db, 1000)
    await _seed_review_items(db, 1000, pending_ratio=0.8)
    await _seed_company_files(db, 500, thin_ratio=0.8)

    yield s

    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


# =============================================================================
# HELPER: index existence check
# =============================================================================

async def _index_exists(db, index_name: str) -> bool:
    """Return True if *index_name* exists in sqlite_master."""
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    )
    row = await cursor.fetchone()
    return row is not None


# =============================================================================
# INDEX EXISTENCE TESTS  (hard-fail)
# =============================================================================

class TestIndexExistence:
    """Verify all Phase 1a indexes are present after migrations.

    These are structural preconditions for the SLO benchmarks.
    If any index is missing, performance regresses and
    the corresponding SLO targets become unachievable.
    """

    @pytest.mark.asyncio
    async def test_signals_company_id_index(self, store):
        """idx_signals_company_id must exist on signals(company_id)."""
        assert await _index_exists(store._db, "idx_signals_company_id"), (
            "Missing index idx_signals_company_id on signals(company_id). "
            "Was v28 migration applied?"
        )

    @pytest.mark.asyncio
    async def test_signals_company_created_index(self, store):
        """idx_signals_company_created must exist on signals(company_id, created_at DESC)."""
        assert await _index_exists(store._db, "idx_signals_company_created"), (
            "Missing index idx_signals_company_created on signals(company_id, created_at DESC). "
            "Was v28 migration applied?"
        )

    @pytest.mark.asyncio
    async def test_review_status_created_index(self, store):
        """idx_review_status_created must exist on review_items(status, created_at)."""
        assert await _index_exists(store._db, "idx_review_status_created"), (
            "Missing index idx_review_status_created on review_items(status, created_at). "
            "Was v29 migration applied?"
        )

    @pytest.mark.asyncio
    async def test_review_company_id_index(self, store):
        """idx_review_company_id must exist on review_items(company_id)."""
        assert await _index_exists(store._db, "idx_review_company_id"), (
            "Missing index idx_review_company_id on review_items(company_id). "
            "Was v29 migration applied?"
        )

    @pytest.mark.asyncio
    async def test_company_file_status_seen_index(self, store):
        """idx_company_file_status_seen must exist on company_files(status, last_seen_at, company_id)."""
        assert await _index_exists(store._db, "idx_company_file_status_seen"), (
            "Missing index idx_company_file_status_seen on "
            "company_files(status, last_seen_at, company_id). "
            "Was v29 migration applied?"
        )


# =============================================================================
# SLO HELPER
# =============================================================================

def _report_slo(name: str, elapsed_ms: float, target_ms: float) -> None:
    """Print SLO result and emit a warning if the target was exceeded."""
    passed = elapsed_ms <= target_ms
    tag = "PASS" if passed else "WARN"
    msg = f"SLO {name}: {elapsed_ms:.1f}ms (target: {target_ms}ms) -- {tag}"
    print(msg)
    if not passed:
        warnings.warn(
            f"SLO exceeded for {name}: {elapsed_ms:.1f}ms > {target_ms}ms target. "
            f"This may indicate a missing index or query regression.",
            stacklevel=2,
        )


# =============================================================================
# SLO BENCHMARK TESTS  (soft-fail via warnings)
# =============================================================================

@pytest.mark.slow
class TestSLOBenchmarks:
    """SLO benchmark tests for Phase 1a queries.

    Each test seeds data at the target volume, times the query,
    and reports pass/warn against SLO_TARGETS.  Exceeding an SLO
    emits a warning but does NOT fail the test -- CI machines have
    variable performance and transient slowness must not block merges.
    """

    @pytest.mark.asyncio
    async def test_csv_export_slo(self, seeded_store):
        """CSV export query: 500 review_items LEFT JOIN company_files.

        Target: < 2000ms.

        This mirrors the export path in ops/quality/export.py where
        review items are joined with company file metadata for CSV output.
        """
        db = seeded_store._db
        target = SLO_TARGETS['csv_export_ms']

        start = time.perf_counter()
        cursor = await db.execute(
            """SELECT ri.id, ri.company_id, ri.status, ri.evidence_bundle,
                      cf.company_name, cf.canonical_key, cf.source_apis
               FROM review_items ri
               LEFT JOIN company_files cf ON ri.company_id = cf.company_id
               ORDER BY ri.created_at DESC
               LIMIT 500"""
        )
        rows = await cursor.fetchall()
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Sanity: we should get rows back
        assert len(rows) > 0, "CSV export query returned zero rows"
        assert len(rows) == 500, f"Expected 500 rows, got {len(rows)}"

        _report_slo('csv_export_ms', elapsed_ms, target)

    @pytest.mark.asyncio
    async def test_review_queue_slo(self, seeded_store):
        """Review queue query: pending items ordered by created_at.

        Target: < 500ms.

        This mirrors the triage CLI listing of pending review items
        for operator review.  Uses idx_review_status_created.
        """
        db = seeded_store._db
        target = SLO_TARGETS['review_queue_ms']

        start = time.perf_counter()
        cursor = await db.execute(
            """SELECT id, company_id, status, evidence_bundle, reason,
                      created_at, updated_at, decided_at, decided_by
               FROM review_items
               WHERE status = 'pending'
               ORDER BY created_at DESC
               LIMIT 1000"""
        )
        rows = await cursor.fetchall()
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Sanity: 80% of 1000 seeded items are pending = 800
        assert len(rows) > 0, "Review queue query returned zero rows"
        assert len(rows) == 800, f"Expected 800 pending rows, got {len(rows)}"

        _report_slo('review_queue_ms', elapsed_ms, target)

    @pytest.mark.asyncio
    async def test_promotion_check_slo(self, seeded_store):
        """Promotion sweep query: paginated thin file scan.

        Target: < 500ms.

        This mirrors the sweep in workflows/thin_file_manager.py
        using composite cursor pagination on (last_seen_at, company_id).
        Uses idx_company_file_status_seen.
        """
        db = seeded_store._db
        target = SLO_TARGETS['promotion_check_ms']

        # Simulate a full paginated sweep (page_size=100)
        cursor_last_seen = ''
        cursor_company_id = ''
        total_rows = 0

        start = time.perf_counter()
        while True:
            cursor = await db.execute(
                """SELECT company_id, last_seen_at
                   FROM company_files
                   WHERE status = 'thin'
                   AND (last_seen_at, company_id) > (?, ?)
                   ORDER BY last_seen_at ASC, company_id ASC
                   LIMIT 100""",
                (cursor_last_seen, cursor_company_id),
            )
            page = await cursor.fetchall()
            if not page:
                break
            total_rows += len(page)
            # Advance composite cursor to last row of this page
            cursor_last_seen = page[-1][1]
            cursor_company_id = page[-1][0]
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Sanity: 80% of 500 = 400 thin files
        assert total_rows > 0, "Promotion sweep returned zero rows"
        assert total_rows == 400, f"Expected 400 thin rows, got {total_rows}"

        _report_slo('promotion_check_ms', elapsed_ms, target)


# =============================================================================
# ADDITIONAL STRUCTURAL TESTS
# =============================================================================

class TestPhase1aTableStructure:
    """Verify Phase 1a tables exist and have expected columns."""

    @pytest.mark.asyncio
    async def test_signals_has_company_id_column(self, store):
        """signals table must have company_id column (v28)."""
        cursor = await store._db.execute("PRAGMA table_info(signals)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "company_id" in columns, (
            "signals table missing company_id column. Was v28 migration applied?"
        )

    @pytest.mark.asyncio
    async def test_review_items_table_exists(self, store):
        """review_items table must exist (v29)."""
        cursor = await store._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_items'"
        )
        assert await cursor.fetchone() is not None, (
            "review_items table not found. Was v29 migration applied?"
        )

    @pytest.mark.asyncio
    async def test_company_files_table_exists(self, store):
        """company_files table must exist (v29)."""
        cursor = await store._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_files'"
        )
        assert await cursor.fetchone() is not None, (
            "company_files table not found. Was v29 migration applied?"
        )

    @pytest.mark.asyncio
    async def test_review_items_has_expected_columns(self, store):
        """review_items must have all Phase 1a columns."""
        cursor = await store._db.execute("PRAGMA table_info(review_items)")
        columns = {row[1] for row in await cursor.fetchall()}
        expected = {
            "id", "company_id", "status", "evidence_bundle", "reason",
            "created_at", "updated_at", "decided_at", "decided_by",
        }
        missing = expected - columns
        assert not missing, f"review_items missing columns: {missing}"

    @pytest.mark.asyncio
    async def test_company_files_has_expected_columns(self, store):
        """company_files must have all Phase 1a columns."""
        cursor = await store._db.execute("PRAGMA table_info(company_files)")
        columns = {row[1] for row in await cursor.fetchall()}
        expected = {
            "id", "company_id", "company_name", "canonical_key", "status",
            "source_apis", "first_seen_at", "last_seen_at",
            "promoted_at", "archived_at", "metadata",
        }
        missing = expected - columns
        assert not missing, f"company_files missing columns: {missing}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-W", "all"])

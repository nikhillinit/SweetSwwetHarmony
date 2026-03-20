"""Tests for storage/kg_signal_builder.py.

Comprehensive coverage for the signal ETL builder that populates the
knowledge graph from signals and company_files tables.

Covers:
  - Full build with companies and signals (node/edge counts)
  - Dry-run mode (computes counts without writes)
  - Idempotency (repeated builds produce same live counts)
  - Multi-source signals per company (attribute merging)
  - Orphan signals (no matching company_file)
  - Sector extraction from signal raw_data
  - Location extraction and deduplication
  - Evidence family edges
  - Tombstoning stale nodes / expiring stale edges in full mode
  - get_etl_status() before and after ETL
  - SignalETLReport.to_dict() roundtrip
  - Incremental mode basics
  - Archived companies are excluded
  - Empty database builds
"""

from __future__ import annotations

import json
import os
import sys

import aiosqlite
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.kg_signal_builder import KGSignalBuilder, SignalETLReport
from storage.kg_store import KGEdge, KGNode, KGStore
from storage.migrations.v50_knowledge_graph import V50_KNOWLEDGE_GRAPH_DDL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
    """Create in-memory DB with required tables."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")

    # Create source tables (signals + company_files)
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL,
            source_api TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            company_name TEXT,
            confidence REAL NOT NULL,
            raw_data TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(canonical_key, signal_type, source_api, detected_at)
        );
        CREATE TABLE IF NOT EXISTS company_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT NOT NULL UNIQUE,
            company_name TEXT,
            canonical_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('thin', 'promoted', 'archived')),
            source_apis TEXT NOT NULL DEFAULT '[]',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            promoted_at TEXT,
            archived_at TEXT,
            metadata TEXT
        );
    """)

    # Create KG tables (v50 DDL includes ontology seed data)
    await conn.executescript(V50_KNOWLEDGE_GRAPH_DDL)
    await conn.commit()

    yield conn
    await conn.close()


async def _insert_company(
    db,
    company_id: str,
    company_name: str,
    canonical_key: str,
    *,
    status: str = "thin",
    source_apis: list | None = None,
    first_seen_at: str = "2026-01-01T00:00:00Z",
    last_seen_at: str = "2026-01-15T00:00:00Z",
    metadata: dict | None = None,
):
    """Helper to insert a company_files row."""
    await db.execute(
        "INSERT INTO company_files "
        "(company_id, company_name, canonical_key, status, source_apis, "
        "first_seen_at, last_seen_at, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            company_id,
            company_name,
            canonical_key,
            status,
            json.dumps(source_apis or []),
            first_seen_at,
            last_seen_at,
            json.dumps(metadata or {}),
        ),
    )
    await db.commit()


async def _insert_signal(
    db,
    signal_type: str,
    source_api: str,
    canonical_key: str,
    *,
    company_name: str | None = None,
    confidence: float = 0.6,
    raw_data: dict | None = None,
    detected_at: str = "2026-01-10T00:00:00Z",
    created_at: str = "2026-01-10T00:00:00Z",
):
    """Helper to insert a signals row."""
    cursor = await db.execute(
        "INSERT INTO signals "
        "(signal_type, source_api, canonical_key, company_name, "
        "confidence, raw_data, detected_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            signal_type,
            source_api,
            canonical_key,
            company_name,
            confidence,
            json.dumps(raw_data or {}),
            detected_at,
            created_at,
        ),
    )
    await db.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Tests: Basic build
# ---------------------------------------------------------------------------


class TestBuildFull:
    """Tests for build(mode='full')."""

    async def test_build_with_one_company_one_signal(self, db):
        """Single company + single signal produces expected nodes and edges."""
        await _insert_company(
            db, "co-1", "Acme Corp", "domain:acme.ai",
            source_apis=["sec_edgar"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:acme.ai",
            company_name="Acme Corp",
            raw_data={"company_name": "Acme Corp", "state": "CA"},
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.status == "completed"
        assert report.company_nodes == 1
        assert report.signal_nodes == 1
        assert report.companies_scanned == 1
        assert report.signals_scanned == 1
        # detected_by: company -> signal
        assert report.detected_by_edges >= 1
        assert report.duration_ms >= 0

    async def test_build_populates_run_record(self, db):
        """A completed build writes a kg_runs record with correct status."""
        await _insert_company(db, "co-1", "TestCo", "domain:test.io")
        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        cursor = await db.execute(
            "SELECT status, nodes_upserted, edges_upserted "
            "FROM kg_runs WHERE run_id = ?",
            (report.run_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "completed"
        assert row[1] > 0  # nodes_upserted

    async def test_build_with_no_data_succeeds(self, db):
        """Building with empty source tables completes without error."""
        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.status == "completed"
        assert report.company_nodes == 0
        assert report.signal_nodes == 0
        assert report.detected_by_edges == 0

    async def test_build_excludes_archived_companies(self, db):
        """Archived companies are not scanned."""
        await _insert_company(
            db, "co-active", "Active Co", "domain:active.io", status="thin",
        )
        await _insert_company(
            db, "co-archived", "Archived Co", "domain:archived.io", status="archived",
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.companies_scanned == 1
        assert report.company_nodes == 1

    async def test_build_multiple_companies_and_signals(self, db):
        """Multiple companies each with signals."""
        for i in range(3):
            await _insert_company(
                db, f"co-{i}", f"Company {i}", f"domain:co{i}.io",
            )
            await _insert_signal(
                db, "funding_event", "sec_edgar", f"domain:co{i}.io",
                company_name=f"Company {i}",
                detected_at=f"2026-01-{10 + i}T00:00:00Z",
            )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.company_nodes == 3
        assert report.signal_nodes == 3
        assert report.detected_by_edges == 3


# ---------------------------------------------------------------------------
# Tests: Dry run
# ---------------------------------------------------------------------------


class TestDryRun:

    async def test_dry_run_computes_counts_without_writing(self, db):
        """dry_run=True should compute counts but write nothing to KG."""
        await _insert_company(
            db, "co-1", "TestCo", "domain:test.io",
            source_apis=["github"],
        )
        await _insert_signal(
            db, "github_spike", "github", "domain:test.io",
            company_name="TestCo",
            raw_data={"company_name": "TestCo", "topics": ["food"]},
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full", dry_run=True)

        assert report.status == "dry_run"
        assert report.run_id == "dry_run"
        assert report.company_nodes == 1
        assert report.signal_nodes == 1

        # Verify nothing was written to kg_nodes (beyond ontology seeds)
        cursor = await db.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE source_table = 'signal_etl'"
        )
        count = (await cursor.fetchone())[0]
        assert count == 0

        # Verify no kg_runs were created
        cursor = await db.execute(
            "SELECT COUNT(*) FROM kg_runs"
        )
        run_count = (await cursor.fetchone())[0]
        assert run_count == 0

    async def test_dry_run_has_positive_duration(self, db):
        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full", dry_run=True)
        assert report.duration_ms >= 0


# ---------------------------------------------------------------------------
# Tests: Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:

    async def test_build_twice_produces_same_live_counts(self, db):
        """Running build twice should yield the same number of live nodes/edges.

        Edge IDs are deterministic (SHA1-based via _etl_edge_id), so the
        second build produces the same IDs and _expire_stale finds no stale
        items.
        """
        await _insert_company(
            db, "co-1", "Acme", "domain:acme.io",
            source_apis=["sec_edgar"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:acme.io",
            company_name="Acme",
            raw_data={"company_name": "Acme", "state": "NY"},
        )

        builder = KGSignalBuilder(db)
        first = await builder.build(mode="full")
        second = await builder.build(mode="full")

        assert first.status == "completed"
        assert second.status == "completed"

        # Live node count should be stable
        cursor = await db.execute(
            "SELECT COUNT(*) FROM kg_nodes "
            "WHERE source_table = 'signal_etl' AND is_tombstone = 0"
        )
        live_nodes = (await cursor.fetchone())[0]
        assert live_nodes == first.company_nodes + first.signal_nodes + first.location_nodes

        # No stale items on second run
        assert second.nodes_tombstoned == 0
        assert second.edges_expired == 0

    async def test_build_three_times_no_accumulation(self, db):
        """Three successive builds should not accumulate extra nodes."""
        await _insert_company(db, "co-1", "X", "domain:x.io")
        builder = KGSignalBuilder(db)

        await builder.build(mode="full")
        await builder.build(mode="full")
        report3 = await builder.build(mode="full")

        assert report3.nodes_tombstoned == 0
        assert report3.edges_expired == 0


# ---------------------------------------------------------------------------
# Tests: Multi-source signals
# ---------------------------------------------------------------------------


class TestMultiSourceSignals:

    async def test_company_with_two_signal_sources(self, db):
        """A company with signals from two different sources gets both detected_by edges."""
        await _insert_company(
            db, "co-1", "HealthCo", "domain:health.io",
            source_apis=["sec_edgar", "github"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:health.io",
            company_name="HealthCo",
            raw_data={"company_name": "HealthCo", "state": "TX"},
            detected_at="2026-01-10T00:00:00Z",
        )
        await _insert_signal(
            db, "github_spike", "github", "domain:health.io",
            company_name="HealthCo",
            raw_data={"company_name": "HealthCo", "topics": ["health"]},
            detected_at="2026-01-11T00:00:00Z",
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.company_nodes == 1
        assert report.signal_nodes == 2
        assert report.detected_by_edges == 2

    async def test_multi_source_merges_attributes_by_priority(self, db):
        """Higher priority source wins for scalar attributes."""
        await _insert_company(
            db, "co-1", "MergeCo", "domain:merge.io",
            source_apis=["sec_edgar", "github"],
        )
        # SEC Edgar has higher priority than GitHub
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:merge.io",
            raw_data={"company_name": "SEC Name", "state": "CA"},
            detected_at="2026-01-10T00:00:00Z",
        )
        await _insert_signal(
            db, "github_spike", "github", "domain:merge.io",
            raw_data={"company_name": "GitHub Name", "topics": ["food"]},
            detected_at="2026-01-11T00:00:00Z",
        )

        builder = KGSignalBuilder(db)
        await builder.build(mode="full")

        # The company node label should prefer SEC Edgar name (priority 90 > 30)
        cursor = await db.execute(
            "SELECT label FROM kg_nodes WHERE id = 'co-1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "SEC Name"


# ---------------------------------------------------------------------------
# Tests: Orphan signals (no matching company_file)
# ---------------------------------------------------------------------------


class TestOrphanSignals:

    async def test_signal_without_company_creates_signal_node_no_detected_by(self, db):
        """A signal with no matching company_file still gets a signal node but no detected_by edge."""
        await _insert_signal(
            db, "github_spike", "github", "domain:orphan.io",
            company_name="OrphanCo",
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.signal_nodes == 1
        assert report.company_nodes == 0
        assert report.detected_by_edges == 0

    async def test_mixed_orphan_and_linked_signals(self, db):
        """Some signals link to companies, others do not."""
        await _insert_company(db, "co-1", "Linked", "domain:linked.io")
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:linked.io",
            company_name="Linked",
            detected_at="2026-01-10T00:00:00Z",
        )
        await _insert_signal(
            db, "github_spike", "github", "domain:orphan.io",
            company_name="Orphan",
            detected_at="2026-01-11T00:00:00Z",
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.company_nodes == 1
        assert report.signal_nodes == 2
        assert report.detected_by_edges == 1  # only the linked one


# ---------------------------------------------------------------------------
# Tests: Sector extraction
# ---------------------------------------------------------------------------


class TestSectorExtraction:

    async def test_github_topics_create_sector_edges(self, db):
        """GitHub signal with food topic produces in_sector edge to cpg."""
        await _insert_company(
            db, "co-1", "FoodCo", "domain:food.io",
            source_apis=["github"],
        )
        await _insert_signal(
            db, "github_spike", "github", "domain:food.io",
            raw_data={"company_name": "FoodCo", "topics": ["food", "nutrition"]},
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        # "food" and "nutrition" both map to "cpg" so only 1 sector edge
        assert report.in_sector_edges == 1

        cursor = await db.execute(
            "SELECT target_node_id FROM kg_edges "
            "WHERE edge_type = 'in_sector' AND source_node_id = 'co-1' "
            "AND valid_until IS NULL"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "sector:cpg"

    async def test_sec_edgar_sic_creates_sector_edges(self, db):
        """SEC filing with SIC code in food range creates cpg sector edge."""
        await _insert_company(
            db, "co-1", "BevCo", "domain:bev.io",
            source_apis=["sec_edgar"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:bev.io",
            raw_data={"company_name": "BevCo", "sic_code": "2086"},
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.in_sector_edges == 1

    async def test_multiple_distinct_sectors(self, db):
        """Company with signals from different sectors gets multiple sector edges."""
        await _insert_company(
            db, "co-1", "MultiSec", "domain:multi.io",
            source_apis=["github", "crunchbase"],
        )
        await _insert_signal(
            db, "github_spike", "github", "domain:multi.io",
            raw_data={"company_name": "MultiSec", "topics": ["food"]},
            detected_at="2026-01-10T00:00:00Z",
        )
        await _insert_signal(
            db, "crunchbase_company", "crunchbase", "domain:multi.io",
            raw_data={"company_name": "MultiSec", "categories": ["travel"]},
            detected_at="2026-01-11T00:00:00Z",
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        # cpg from food + travel from travel
        assert report.in_sector_edges == 2


# ---------------------------------------------------------------------------
# Tests: Location extraction and deduplication
# ---------------------------------------------------------------------------


class TestLocationExtraction:

    async def test_sec_edgar_state_creates_location_node_and_edge(self, db):
        """SEC filing with state creates a location node and located_in edge."""
        await _insert_company(
            db, "co-1", "CalCo", "domain:cal.io",
            source_apis=["sec_edgar"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:cal.io",
            raw_data={"company_name": "CalCo", "state": "CA"},
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.location_nodes == 1
        assert report.located_in_edges == 1

        # Check the location node was created
        cursor = await db.execute(
            "SELECT label FROM kg_nodes WHERE node_type = 'location' AND is_tombstone = 0"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "us-california"

    async def test_location_deduplication_across_companies(self, db):
        """Two companies in the same state share one location node."""
        for i in range(2):
            await _insert_company(
                db, f"co-{i}", f"Co{i}", f"domain:co{i}.io",
                source_apis=["sec_edgar"],
            )
            await _insert_signal(
                db, "funding_event", "sec_edgar", f"domain:co{i}.io",
                raw_data={"company_name": f"Co{i}", "state": "NY"},
                detected_at=f"2026-01-{10 + i}T00:00:00Z",
            )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        # Only 1 location node despite 2 companies
        assert report.location_nodes == 1
        # But 2 located_in edges
        assert report.located_in_edges == 2

    async def test_multiple_distinct_locations(self, db):
        """Different states produce distinct location nodes."""
        await _insert_company(
            db, "co-ca", "CalCo", "domain:cal.io",
            source_apis=["sec_edgar"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:cal.io",
            raw_data={"company_name": "CalCo", "state": "CA"},
            detected_at="2026-01-10T00:00:00Z",
        )
        await _insert_company(
            db, "co-tx", "TexCo", "domain:tex.io",
            source_apis=["sec_edgar"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:tex.io",
            raw_data={"company_name": "TexCo", "state": "TX"},
            detected_at="2026-01-11T00:00:00Z",
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.location_nodes == 2
        assert report.located_in_edges == 2


# ---------------------------------------------------------------------------
# Tests: Evidence family edges
# ---------------------------------------------------------------------------


class TestEvidenceFamilyEdges:

    async def test_evidence_family_edge_created_for_company_signal(self, db):
        """A company with a funding_event from sec_edgar gets a regulatory evidence family edge."""
        await _insert_company(
            db, "co-1", "RegCo", "domain:reg.io",
            source_apis=["sec_edgar"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:reg.io",
            company_name="RegCo",
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.has_evidence_edges >= 1

        cursor = await db.execute(
            "SELECT target_node_id, json_extract(properties, '$.family') "
            "FROM kg_edges "
            "WHERE edge_type = 'has_evidence' AND source_node_id = 'co-1' "
            "AND valid_until IS NULL"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "ef:regulatory"
        assert row[1] == "regulatory"

    async def test_evidence_family_deduplication_same_family(self, db):
        """Two signals from the same evidence family produce only one has_evidence edge."""
        await _insert_company(
            db, "co-1", "DevCo", "domain:dev.io",
            source_apis=["github"],
        )
        # Both signals map to "developer" family
        await _insert_signal(
            db, "github_spike", "github", "domain:dev.io",
            company_name="DevCo",
            detected_at="2026-01-10T00:00:00Z",
        )
        await _insert_signal(
            db, "new_repo", "github", "domain:dev.io",
            company_name="DevCo",
            detected_at="2026-01-11T00:00:00Z",
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.has_evidence_edges == 1

    async def test_two_distinct_evidence_families(self, db):
        """Signals from different families produce separate edges."""
        await _insert_company(
            db, "co-1", "MixCo", "domain:mix.io",
            source_apis=["github", "sec_edgar"],
        )
        # developer family
        await _insert_signal(
            db, "github_spike", "github", "domain:mix.io",
            company_name="MixCo",
            detected_at="2026-01-10T00:00:00Z",
        )
        # regulatory family
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:mix.io",
            company_name="MixCo",
            detected_at="2026-01-11T00:00:00Z",
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.has_evidence_edges == 2


# ---------------------------------------------------------------------------
# Tests: Tombstoning and expiring stale items
# ---------------------------------------------------------------------------


class TestTombstoningStale:

    async def test_stale_node_tombstoned_in_full_mode(self, db):
        """A signal_etl node not in the current build gets tombstoned."""
        # First build: creates nodes for co-1
        await _insert_company(db, "co-1", "OldCo", "domain:old.io")
        builder = KGSignalBuilder(db)
        await builder.build(mode="full")

        # Remove the company from source table (simulate removal)
        await db.execute("DELETE FROM company_files WHERE company_id = 'co-1'")
        await db.commit()

        # Second build: co-1 should be tombstoned
        report2 = await builder.build(mode="full")
        assert report2.nodes_tombstoned >= 1

        cursor = await db.execute(
            "SELECT is_tombstone FROM kg_nodes WHERE id = 'co-1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 1

    async def test_stale_edge_expired_in_full_mode(self, db):
        """Signal_etl edges for removed data get expired in full mode."""
        await _insert_company(db, "co-1", "EdgeCo", "domain:edge.io")
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:edge.io",
            company_name="EdgeCo",
        )

        builder = KGSignalBuilder(db)
        await builder.build(mode="full")

        # Verify there are live edges
        cursor = await db.execute(
            "SELECT COUNT(*) FROM kg_edges "
            "WHERE valid_until IS NULL AND json_extract(properties, '$.layer') = 'signal_etl'"
        )
        edge_count_before = (await cursor.fetchone())[0]
        assert edge_count_before > 0

        # Remove source data
        await db.execute("DELETE FROM company_files")
        await db.execute("DELETE FROM signals")
        await db.commit()

        # Second build should expire stale edges
        report2 = await builder.build(mode="full")
        assert report2.edges_expired >= 1

    async def test_no_tombstone_when_data_unchanged(self, db):
        """No tombstoning or expiring when data is the same across builds."""
        await _insert_company(db, "co-1", "StableCo", "domain:stable.io")
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:stable.io",
            company_name="StableCo",
        )

        builder = KGSignalBuilder(db)
        await builder.build(mode="full")
        report2 = await builder.build(mode="full")

        assert report2.nodes_tombstoned == 0
        assert report2.edges_expired == 0


# ---------------------------------------------------------------------------
# Tests: get_etl_status()
# ---------------------------------------------------------------------------


class TestGetETLStatus:

    async def test_status_before_any_build(self, db):
        """Status before any build returns empty counts."""
        builder = KGSignalBuilder(db)
        status = await builder.get_etl_status()

        assert status["node_counts"] == {}
        assert status["edge_counts"] == {}
        assert status["last_run"] is None
        assert status["source_tables"]["signals"] == 0
        assert status["source_tables"]["company_files"] == 0

    async def test_status_after_build(self, db):
        """Status after a build reflects actual counts."""
        await _insert_company(
            db, "co-1", "StatusCo", "domain:status.io",
            source_apis=["sec_edgar"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:status.io",
            company_name="StatusCo",
            raw_data={"company_name": "StatusCo", "state": "CA"},
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")
        status = await builder.get_etl_status()

        # Node counts by type
        assert "company" in status["node_counts"]
        assert status["node_counts"]["company"] >= 1
        assert "signal" in status["node_counts"]

        # Edge counts by type
        assert "detected_by" in status["edge_counts"]

        # Last run info
        assert status["last_run"] is not None
        assert status["last_run"]["run_id"] == report.run_id
        assert status["last_run"]["status"] == "completed"
        assert status["last_run"]["mode"] == "full"

        # Source table sizes
        assert status["source_tables"]["signals"] == 1
        assert status["source_tables"]["company_files"] == 1

    async def test_status_source_tables_count_only_non_archived(self, db):
        """company_files count excludes archived companies."""
        await _insert_company(db, "co-1", "Active", "domain:active.io", status="thin")
        await _insert_company(db, "co-2", "Archived", "domain:old.io", status="archived")

        builder = KGSignalBuilder(db)
        status = await builder.get_etl_status()

        assert status["source_tables"]["company_files"] == 1


# ---------------------------------------------------------------------------
# Tests: SignalETLReport.to_dict()
# ---------------------------------------------------------------------------


class TestSignalETLReportToDict:

    def test_to_dict_has_all_fields(self):
        """to_dict() returns all expected keys."""
        report = SignalETLReport(
            run_id="test-run",
            mode="full",
            status="completed",
            company_nodes=5,
            signal_nodes=10,
            location_nodes=2,
            detected_by_edges=8,
            in_sector_edges=3,
            located_in_edges=2,
            has_evidence_edges=4,
            nodes_tombstoned=1,
            edges_expired=2,
            signals_scanned=15,
            companies_scanned=6,
            warnings=["test warning"],
            duration_ms=123.45,
        )

        d = report.to_dict()
        assert d["run_id"] == "test-run"
        assert d["mode"] == "full"
        assert d["status"] == "completed"
        assert d["company_nodes"] == 5
        assert d["signal_nodes"] == 10
        assert d["location_nodes"] == 2
        assert d["detected_by_edges"] == 8
        assert d["in_sector_edges"] == 3
        assert d["located_in_edges"] == 2
        assert d["has_evidence_edges"] == 4
        assert d["nodes_tombstoned"] == 1
        assert d["edges_expired"] == 2
        assert d["signals_scanned"] == 15
        assert d["companies_scanned"] == 6
        assert d["warnings"] == ["test warning"]
        assert d["duration_ms"] == 123.45

    def test_to_dict_roundtrip_json(self):
        """to_dict() output survives JSON roundtrip."""
        report = SignalETLReport(
            run_id="rt-1", mode="full", status="completed",
            company_nodes=1, signal_nodes=2,
        )
        d = report.to_dict()
        serialized = json.dumps(d)
        deserialized = json.loads(serialized)
        assert deserialized == d

    def test_to_dict_defaults(self):
        """Default values are 0 / empty for optional fields."""
        report = SignalETLReport(run_id="min", mode="full", status="running")
        d = report.to_dict()
        assert d["company_nodes"] == 0
        assert d["warnings"] == []
        assert d["duration_ms"] == 0


# ---------------------------------------------------------------------------
# Tests: Incremental mode
# ---------------------------------------------------------------------------


class TestIncrementalMode:

    async def test_incremental_scans_all_on_first_run(self, db):
        """Incremental mode with no prior watermark scans all signals."""
        await _insert_company(db, "co-1", "IncrCo", "domain:incr.io")
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:incr.io",
            company_name="IncrCo",
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="incremental")

        assert report.status == "completed"
        assert report.signals_scanned == 1
        assert report.signal_nodes == 1

    async def test_incremental_does_not_tombstone(self, db):
        """Incremental mode does not tombstone or expire stale items."""
        await _insert_company(db, "co-1", "StayCo", "domain:stay.io")
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:stay.io",
            company_name="StayCo",
        )

        builder = KGSignalBuilder(db)
        await builder.build(mode="full")

        # Remove source data
        await db.execute("DELETE FROM company_files")
        await db.execute("DELETE FROM signals")
        await db.commit()

        # Incremental build should NOT tombstone/expire
        report = await builder.build(mode="incremental")
        assert report.nodes_tombstoned == 0
        assert report.edges_expired == 0


# ---------------------------------------------------------------------------
# Tests: Node property correctness
# ---------------------------------------------------------------------------


class TestNodeProperties:

    async def test_company_node_has_layer_signal_etl(self, db):
        """Company nodes have properties.layer = 'signal_etl'."""
        await _insert_company(db, "co-1", "LayerCo", "domain:layer.io")

        builder = KGSignalBuilder(db)
        await builder.build(mode="full")

        cursor = await db.execute(
            "SELECT json_extract(properties, '$.layer') FROM kg_nodes WHERE id = 'co-1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "signal_etl"

    async def test_signal_node_has_correct_properties(self, db):
        """Signal nodes contain signal_type, source_api, confidence, canonical_key."""
        await _insert_signal(
            db, "github_spike", "github", "domain:sig.io",
            company_name="SigCo",
            confidence=0.72,
        )

        builder = KGSignalBuilder(db)
        await builder.build(mode="full")

        cursor = await db.execute(
            "SELECT properties FROM kg_nodes "
            "WHERE node_type = 'signal' AND is_tombstone = 0"
        )
        row = await cursor.fetchone()
        assert row is not None
        props = json.loads(row[0])
        assert props["signal_type"] == "github_spike"
        assert props["source_api"] == "github"
        assert props["confidence"] == 0.72
        assert props["canonical_key"] == "domain:sig.io"
        assert props["layer"] == "signal_etl"

    async def test_company_node_includes_merged_domain(self, db):
        """When a signal provides a domain, it appears in company node properties."""
        await _insert_company(
            db, "co-1", "DomCo", "domain:dom.io",
            source_apis=["sec_edgar"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:dom.io",
            raw_data={"company_name": "DomCo", "domain": "dom.io", "state": "WA"},
        )

        builder = KGSignalBuilder(db)
        await builder.build(mode="full")

        cursor = await db.execute(
            "SELECT json_extract(properties, '$.domain') FROM kg_nodes WHERE id = 'co-1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "dom.io"

    async def test_location_node_has_source_table_signal_etl(self, db):
        """Location nodes carry source_table='signal_etl'."""
        await _insert_company(
            db, "co-1", "LocCo", "domain:loc.io",
            source_apis=["sec_edgar"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:loc.io",
            raw_data={"company_name": "LocCo", "state": "FL"},
        )

        builder = KGSignalBuilder(db)
        await builder.build(mode="full")

        cursor = await db.execute(
            "SELECT source_table FROM kg_nodes "
            "WHERE node_type = 'location' AND is_tombstone = 0"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "signal_etl"


# ---------------------------------------------------------------------------
# Tests: Edge weight correctness
# ---------------------------------------------------------------------------


class TestEdgeWeights:

    async def test_detected_by_edge_weight_equals_signal_confidence(self, db):
        """detected_by edge weight matches the signal's confidence score."""
        await _insert_company(db, "co-1", "WeightCo", "domain:weight.io")
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:weight.io",
            company_name="WeightCo",
            confidence=0.85,
        )

        builder = KGSignalBuilder(db)
        await builder.build(mode="full")

        cursor = await db.execute(
            "SELECT weight FROM kg_edges "
            "WHERE edge_type = 'detected_by' AND source_node_id = 'co-1' "
            "AND valid_until IS NULL"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == pytest.approx(0.85)

    async def test_sector_and_location_edges_have_unit_weight(self, db):
        """in_sector and located_in edges have weight 1.0."""
        await _insert_company(
            db, "co-1", "UnitW", "domain:unitw.io",
            source_apis=["sec_edgar"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:unitw.io",
            raw_data={"company_name": "UnitW", "state": "CA", "sic_code": "2086"},
        )

        builder = KGSignalBuilder(db)
        await builder.build(mode="full")

        for edge_type in ("in_sector", "located_in"):
            cursor = await db.execute(
                "SELECT weight FROM kg_edges WHERE edge_type = ? AND valid_until IS NULL",
                (edge_type,),
            )
            row = await cursor.fetchone()
            if row:
                assert row[0] == pytest.approx(1.0), f"{edge_type} weight should be 1.0"


# ---------------------------------------------------------------------------
# Tests: Run source lifecycle
# ---------------------------------------------------------------------------


class TestRunSourceLifecycle:

    async def test_greenhouse_jobs_populates_kg_via_etl(self, db):
        """A previously uncovered source (greenhouse_jobs) now populates KG output
        through the full ETL path with company_domain extraction."""
        await _insert_company(
            db, "co-gh", "GreenCo", "domain:greenco.com",
            source_apis=["greenhouse_jobs"],
        )
        await _insert_signal(
            db, "new_job_posting", "greenhouse_jobs", "domain:greenco.com",
            company_name="GreenCo",
            raw_data={
                "company_name": "GreenCo",
                "company_domain": "https://greenco.com/careers",
                "locations": ["NY"],
            },
        )

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        assert report.status == "completed"
        assert report.signal_nodes >= 1
        assert report.detected_by_edges >= 1

        # Signal node exists
        cursor = await db.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE node_type = 'signal' AND is_tombstone = 0"
        )
        assert (await cursor.fetchone())[0] >= 1

        # Company node has extracted domain in properties
        cursor = await db.execute(
            "SELECT json_extract(properties, '$.domain') FROM kg_nodes WHERE id = 'co-gh'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "greenco.com"

        # detected_by edge exists
        cursor = await db.execute(
            "SELECT COUNT(*) FROM kg_edges "
            "WHERE edge_type = 'detected_by' AND source_node_id = 'co-gh' "
            "AND valid_until IS NULL"
        )
        assert (await cursor.fetchone())[0] >= 1

    async def test_build_creates_run_source_record(self, db):
        """A build creates a kg_run_sources row for signal_etl."""
        await _insert_company(db, "co-1", "SrcCo", "domain:src.io")

        builder = KGSignalBuilder(db)
        report = await builder.build(mode="full")

        cursor = await db.execute(
            "SELECT source_name, refresh_strategy, status "
            "FROM kg_run_sources WHERE run_id = ?",
            (report.run_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "signal_etl"
        assert row[1] == "full_recompute"
        assert row[2] == "completed"

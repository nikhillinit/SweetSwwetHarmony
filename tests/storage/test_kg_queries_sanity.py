"""KG builder-to-query integration sanity tests.

Validates the end-to-end path:
    source tables -> KGSignalBuilder.build() -> KGQueryEngine

These are NOT duplicates of the unit tests in test_kg_queries.py (which insert
KG nodes/edges directly). These tests verify that the ETL builder produces data
the query engine can consume correctly.
"""

from __future__ import annotations

import json
import os
import sys

import aiosqlite
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.kg_signal_builder import KGSignalBuilder
from storage.kg_store import KGStore
from storage.kg_queries import KGQueryEngine
from storage.migrations.v50_knowledge_graph import V50_KNOWLEDGE_GRAPH_DDL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
    """In-memory DB with source tables (signals, company_files) and KG schema."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")

    # Source tables (same schema as test_kg_signal_builder)
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

    # KG tables (v50 DDL includes ontology seed data)
    await conn.executescript(V50_KNOWLEDGE_GRAPH_DDL)
    await conn.commit()

    yield conn
    await conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
):
    """Insert a company_files row."""
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
            json.dumps({}),
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
    """Insert a signals row."""
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


async def _build_and_query(db):
    """Run ETL build and return a KGQueryEngine."""
    builder = KGSignalBuilder(db)
    report = await builder.build(mode="full")
    assert report.status == "completed"
    kg = KGStore(db)
    engine = KGQueryEngine(kg, db)
    return report, engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCompanyEvidenceChainViaETL:
    """ETL-produced nodes are visible through company_evidence_chain()."""

    async def test_single_signal_appears_in_chain(self, db):
        await _insert_company(
            db, "co-alpha", "Alpha Corp", "domain:alpha.ai",
            source_apis=["sec_edgar"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:alpha.ai",
            company_name="Alpha Corp",
            confidence=0.75,
            raw_data={"company_name": "Alpha Corp", "state": "CA"},
        )

        report, engine = await _build_and_query(db)

        # Builder should produce at least 1 company and 1 signal node
        assert report.company_nodes >= 1
        assert report.signal_nodes >= 1

        # Query engine should find the company node
        # The company node ID is deterministic based on source_table + source_id
        # Find the company node by scanning kg_nodes
        async with db.execute(
            "SELECT id FROM kg_nodes WHERE node_type='company' AND label='Alpha Corp'"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None, "Company node not created by ETL"
        company_node_id = row[0]

        chain = await engine.company_evidence_chain(company_node_id)
        assert chain is not None
        assert len(chain.signals) >= 1
        assert chain.weighted_score > 0

    async def test_two_signals_same_company(self, db):
        await _insert_company(
            db, "co-beta", "Beta Inc", "domain:beta.io",
            source_apis=["sec_edgar", "github"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:beta.io",
            company_name="Beta Inc", confidence=0.8,
            raw_data={"company_name": "Beta Inc"},
        )
        await _insert_signal(
            db, "github_trending", "github", "domain:beta.io",
            company_name="Beta Inc", confidence=0.6,
            raw_data={"company_name": "Beta Inc"},
            detected_at="2026-01-11T00:00:00Z",
        )

        report, engine = await _build_and_query(db)

        assert report.signal_nodes >= 2

        async with db.execute(
            "SELECT id FROM kg_nodes WHERE node_type='company' AND label='Beta Inc'"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        company_node_id = row[0]

        chain = await engine.company_evidence_chain(company_node_id)
        assert chain is not None
        assert len(chain.signals) >= 2


class TestFindDataGapsViaETL:
    """Companies with thin evidence appear in find_data_gaps()."""

    async def test_single_source_company_is_gap(self, db):
        await _insert_company(
            db, "co-thin", "Thin Co", "domain:thin.co",
            source_apis=["github"],
        )
        await _insert_signal(
            db, "github_trending", "github", "domain:thin.co",
            company_name="Thin Co", confidence=0.5,
            raw_data={"company_name": "Thin Co"},
        )

        _, engine = await _build_and_query(db)

        # min_evidence=2 means 1-signal company is a gap
        gaps = await engine.find_data_gaps(min_evidence=2)
        gap_labels = [g["company_label"] for g in gaps]
        assert "Thin Co" in gap_labels

    async def test_multi_source_excluded_from_gap(self, db):
        await _insert_company(
            db, "co-rich", "Rich Co", "domain:rich.co",
            source_apis=["sec_edgar", "github"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:rich.co",
            company_name="Rich Co", confidence=0.8,
            raw_data={"company_name": "Rich Co"},
        )
        await _insert_signal(
            db, "github_trending", "github", "domain:rich.co",
            company_name="Rich Co", confidence=0.6,
            raw_data={"company_name": "Rich Co"},
            detected_at="2026-01-11T00:00:00Z",
        )

        _, engine = await _build_and_query(db)

        gaps = await engine.find_data_gaps(min_evidence=2)
        gap_labels = [g["company_label"] for g in gaps]
        assert "Rich Co" not in gap_labels


class TestRankByEvidenceStrengthViaETL:
    """rank_by_evidence_strength() orders ETL-built companies correctly."""

    async def test_multi_source_ranks_higher(self, db):
        # Company with 2 sources (should rank higher)
        await _insert_company(
            db, "co-strong", "Strong Co", "domain:strong.co",
            source_apis=["sec_edgar", "github"],
        )
        await _insert_signal(
            db, "funding_event", "sec_edgar", "domain:strong.co",
            company_name="Strong Co", confidence=0.8,
            raw_data={"company_name": "Strong Co", "state": "NY"},
        )
        await _insert_signal(
            db, "github_trending", "github", "domain:strong.co",
            company_name="Strong Co", confidence=0.7,
            raw_data={"company_name": "Strong Co"},
            detected_at="2026-01-11T00:00:00Z",
        )

        # Company with 1 source (should rank lower)
        await _insert_company(
            db, "co-weak", "Weak Co", "domain:weak.co",
            source_apis=["github"],
        )
        await _insert_signal(
            db, "github_trending", "github", "domain:weak.co",
            company_name="Weak Co", confidence=0.5,
            raw_data={"company_name": "Weak Co"},
        )

        _, engine = await _build_and_query(db)

        ranked = await engine.rank_by_evidence_strength()
        labels = [r["company_label"] for r in ranked]

        assert "Strong Co" in labels
        assert "Weak Co" in labels
        assert labels.index("Strong Co") < labels.index("Weak Co")

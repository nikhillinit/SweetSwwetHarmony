"""Tests for storage/kg_queries.py — KGQueryEngine composable graph queries.

Covers:
  - company_evidence_chain: signals, families, sectors, locations, weighted_score
  - detect_conflicts: stage and sector disagreements
  - find_data_gaps: thin evidence detection
  - sector_cluster: companies grouped by sector
  - find_duplicate_candidates: shared location+sector pairs
  - rank_by_evidence_strength: multi-source ordering
  - ego_graph: subgraph extraction
  - founder_network / investor_portfolio: Phase 3+ stubs
  - EvidenceChain / ConflictRecord serialization
"""

import json
import os
import sys
import tempfile

import pytest
import pytest_asyncio
import aiosqlite

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.kg_store import KGEdge, KGNode, KGStore, kg_edge_id, kg_node_id
from storage.kg_queries import KGQueryEngine, EvidenceChain, ConflictRecord
from storage.migrations.v50_knowledge_graph import V50_KNOWLEDGE_GRAPH_DDL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
    """Fresh aiosqlite connection with v50 KG schema applied."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(V50_KNOWLEDGE_GRAPH_DDL)
    yield conn
    await conn.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def kg(db):
    """KGStore backed by a fresh DB."""
    return KGStore(db)


@pytest_asyncio.fixture
async def engine(kg, db):
    """KGQueryEngine backed by a fresh DB."""
    return KGQueryEngine(kg, db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _insert_company(kg: KGStore, cid: str, label: str, *, properties=None):
    """Insert a company node in the signal_etl layer."""
    await kg.upsert_node(KGNode(
        id=cid,
        node_type="company",
        label=label,
        source_table="signal_etl",
        source_id=cid,
        properties=properties or {},
    ))


async def _insert_signal(kg: KGStore, sid: str, label: str, *, signal_type="funding",
                         source_api="sec_edgar", confidence=0.8):
    """Insert a signal node."""
    await kg.upsert_node(KGNode(
        id=sid,
        node_type="signal",
        label=label,
        source_table="signal_etl",
        source_id=sid,
        properties={
            "signal_type": signal_type,
            "source_api": source_api,
            "confidence": confidence,
            "detected_at": "2026-03-19T00:00:00Z",
        },
    ))


async def _insert_edge(kg: KGStore, source: str, target: str, edge_type: str, weight: float = 1.0):
    """Insert a directed edge."""
    await kg.upsert_edge(KGEdge(
        id=kg_edge_id(),
        edge_type=edge_type,
        source_node_id=source,
        target_node_id=target,
        weight=weight,
        is_directed=True,
    ))


async def _insert_location(kg: KGStore, loc_id: str, label: str):
    """Insert a location node."""
    await kg.upsert_node(KGNode(
        id=loc_id,
        node_type="location",
        label=label,
        source_table="ontology",
        source_id=loc_id,
    ))


async def _insert_evidence_family(kg: KGStore, ef_id: str, label: str):
    """Insert an evidence_family node (beyond seed data)."""
    await kg.upsert_node(KGNode(
        id=ef_id,
        node_type="evidence_family",
        label=label,
        source_table="ontology",
        source_id=ef_id,
    ))


# ---------------------------------------------------------------------------
# company_evidence_chain
# ---------------------------------------------------------------------------

class TestCompanyEvidenceChain:
    """Tests for KGQueryEngine.company_evidence_chain."""

    @pytest.mark.asyncio
    async def test_nonexistent_company_returns_none(self, engine):
        result = await engine.company_evidence_chain("no-such-company")
        assert result is None

    @pytest.mark.asyncio
    async def test_non_company_node_returns_none(self, kg, engine):
        """A node that exists but is not of type 'company' should return None."""
        # sector:cpg is seeded by the DDL as a sector node
        result = await engine.company_evidence_chain("sector:cpg")
        assert result is None

    @pytest.mark.asyncio
    async def test_company_with_no_signals(self, kg, engine):
        """Company with no edges should return an EvidenceChain with empty signals."""
        await _insert_company(kg, "comp_empty", "Empty Co")
        chain = await engine.company_evidence_chain("comp_empty")
        assert chain is not None
        assert chain.company_id == "comp_empty"
        assert chain.company_label == "Empty Co"
        assert chain.signals == []
        assert chain.source_count == 0
        assert chain.weighted_score == 0.0
        assert chain.evidence_families == []
        assert chain.sectors == []
        assert chain.locations == []

    @pytest.mark.asyncio
    async def test_company_with_signals_and_metadata(self, kg, engine):
        """Full chain with signals, sectors, locations, evidence families."""
        await _insert_company(kg, "comp1", "Acme Inc")
        await _insert_signal(kg, "signal:1", "SEC filing", source_api="sec_edgar", confidence=0.7)
        await _insert_signal(kg, "signal:2", "GitHub spike", source_api="github", confidence=0.6)
        await _insert_edge(kg, "comp1", "signal:1", "detected_by", weight=0.7)
        await _insert_edge(kg, "comp1", "signal:2", "detected_by", weight=0.6)

        # Sector: use seeded ontology node
        await _insert_edge(kg, "comp1", "sector:cpg", "in_sector")

        # Location
        await _insert_location(kg, "location:us_ny", "New York, US")
        await _insert_edge(kg, "comp1", "location:us_ny", "located_in")

        # Evidence family: use seeded ontology node
        await _insert_edge(kg, "comp1", "ef:regulatory", "has_evidence")

        chain = await engine.company_evidence_chain("comp1")
        assert chain is not None
        assert chain.company_id == "comp1"
        assert chain.company_label == "Acme Inc"
        assert len(chain.signals) == 2
        assert chain.source_count == 2  # sec_edgar + github
        assert "Consumer CPG" in chain.sectors
        assert "New York, US" in chain.locations
        assert "regulatory" in chain.evidence_families

    @pytest.mark.asyncio
    async def test_weighted_score_single_source(self, kg, engine):
        """Single source: weighted_score = avg weight, no diversity bonus."""
        await _insert_company(kg, "comp_single", "Solo Co")
        await _insert_signal(kg, "signal:s1", "Only signal", source_api="github", confidence=0.8)
        await _insert_edge(kg, "comp_single", "signal:s1", "detected_by", weight=0.8)

        chain = await engine.company_evidence_chain("comp_single")
        # 1 signal, weight=0.8, 1 source → diversity_bonus = max(0, (1-1)*0.1) = 0
        # weighted_score = min(1.0, 0.8/1 + 0) = 0.8
        assert chain.weighted_score == pytest.approx(0.8)
        assert chain.source_count == 1

    @pytest.mark.asyncio
    async def test_weighted_score_multi_source_diversity_bonus(self, kg, engine):
        """Multiple sources get a diversity bonus of 0.1 per extra source."""
        await _insert_company(kg, "comp_multi", "Multi Co")
        await _insert_signal(kg, "signal:m1", "Signal 1", source_api="sec_edgar", confidence=0.5)
        await _insert_signal(kg, "signal:m2", "Signal 2", source_api="github", confidence=0.6)
        await _insert_signal(kg, "signal:m3", "Signal 3", source_api="hacker_news", confidence=0.4)
        await _insert_edge(kg, "comp_multi", "signal:m1", "detected_by", weight=0.5)
        await _insert_edge(kg, "comp_multi", "signal:m2", "detected_by", weight=0.6)
        await _insert_edge(kg, "comp_multi", "signal:m3", "detected_by", weight=0.4)

        chain = await engine.company_evidence_chain("comp_multi")
        # 3 signals, total_weight = 1.5, avg = 0.5, 3 sources → bonus = 0.2
        # weighted_score = min(1.0, 0.5 + 0.2) = 0.7
        assert chain.source_count == 3
        assert chain.weighted_score == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_weighted_score_capped_at_one(self, kg, engine):
        """Weighted score should be capped at 1.0."""
        await _insert_company(kg, "comp_max", "Max Co")
        await _insert_signal(kg, "signal:x1", "Sig1", source_api="sec_edgar", confidence=0.9)
        await _insert_signal(kg, "signal:x2", "Sig2", source_api="github", confidence=0.9)
        await _insert_signal(kg, "signal:x3", "Sig3", source_api="hacker_news", confidence=0.9)
        await _insert_signal(kg, "signal:x4", "Sig4", source_api="crunchbase", confidence=0.9)
        await _insert_edge(kg, "comp_max", "signal:x1", "detected_by", weight=0.95)
        await _insert_edge(kg, "comp_max", "signal:x2", "detected_by", weight=0.95)
        await _insert_edge(kg, "comp_max", "signal:x3", "detected_by", weight=0.95)
        await _insert_edge(kg, "comp_max", "signal:x4", "detected_by", weight=0.95)

        chain = await engine.company_evidence_chain("comp_max")
        # avg_weight = 0.95, 4 sources → bonus = 0.3
        # min(1.0, 0.95 + 0.3) = 1.0
        assert chain.weighted_score == 1.0

    @pytest.mark.asyncio
    async def test_signals_contain_expected_fields(self, kg, engine):
        """Each signal dict should have signal_id, signal_type, source_api, confidence, detected_at."""
        await _insert_company(kg, "comp_fields", "Fields Co")
        await _insert_signal(kg, "signal:f1", "Filing signal",
                             signal_type="form_d", source_api="sec_edgar", confidence=0.75)
        await _insert_edge(kg, "comp_fields", "signal:f1", "detected_by", weight=0.75)

        chain = await engine.company_evidence_chain("comp_fields")
        assert len(chain.signals) == 1
        sig = chain.signals[0]
        assert sig["signal_id"] == "signal:f1"
        assert sig["signal_type"] == "form_d"
        assert sig["source_api"] == "sec_edgar"
        assert sig["confidence"] == 0.75
        assert sig["detected_at"] == "2026-03-19T00:00:00Z"

    @pytest.mark.asyncio
    async def test_multiple_evidence_families(self, kg, engine):
        """Chain should list all linked evidence families."""
        await _insert_company(kg, "comp_ef", "EF Co")
        # Use seeded evidence family nodes
        await _insert_edge(kg, "comp_ef", "ef:developer", "has_evidence")
        await _insert_edge(kg, "comp_ef", "ef:hiring", "has_evidence")

        chain = await engine.company_evidence_chain("comp_ef")
        assert sorted(chain.evidence_families) == ["developer", "hiring"]

    @pytest.mark.asyncio
    async def test_multiple_sectors(self, kg, engine):
        """Chain should list all linked sectors."""
        await _insert_company(kg, "comp_sectors", "SectorCo")
        await _insert_edge(kg, "comp_sectors", "sector:cpg", "in_sector")
        await _insert_edge(kg, "comp_sectors", "sector:health_tech", "in_sector")

        chain = await engine.company_evidence_chain("comp_sectors")
        assert sorted(chain.sectors) == ["Consumer CPG", "Consumer Health Tech"]

    @pytest.mark.asyncio
    async def test_multiple_locations(self, kg, engine):
        """Chain should list all linked locations."""
        await _insert_company(kg, "comp_locs", "LocCo")
        await _insert_location(kg, "location:us_ca", "San Francisco, US")
        await _insert_location(kg, "location:uk_london", "London, UK")
        await _insert_edge(kg, "comp_locs", "location:us_ca", "located_in")
        await _insert_edge(kg, "comp_locs", "location:uk_london", "located_in")

        chain = await engine.company_evidence_chain("comp_locs")
        assert sorted(chain.locations) == ["London, UK", "San Francisco, US"]


# ---------------------------------------------------------------------------
# detect_conflicts
# ---------------------------------------------------------------------------

class TestDetectConflicts:
    """Tests for KGQueryEngine.detect_conflicts."""

    @pytest.mark.asyncio
    async def test_no_conflicts_returns_empty(self, kg, engine):
        """Company with consistent data returns no conflicts."""
        await _insert_company(kg, "comp_ok", "OK Co", properties={
            "claims": {"sec_edgar": {"stage": "Seed"}}
        })
        conflicts = await engine.detect_conflicts()
        assert conflicts == []

    @pytest.mark.asyncio
    async def test_stage_conflict(self, kg, engine):
        """Sources disagreeing on stage produce a stage conflict."""
        await _insert_company(kg, "comp_stage", "StageConflict Co", properties={
            "claims": {
                "sec_edgar": {"stage": "Seed"},
                "crunchbase": {"stage": "Series A"},
            }
        })
        conflicts = await engine.detect_conflicts()
        stage_conflicts = [c for c in conflicts if c.field_name == "stage"]
        assert len(stage_conflicts) == 1
        assert stage_conflicts[0].company_id == "comp_stage"
        assert stage_conflicts[0].values == {"sec_edgar": "Seed", "crunchbase": "Series A"}

    @pytest.mark.asyncio
    async def test_sector_conflict_multiple_sectors(self, kg, engine):
        """Company linked to multiple distinct sectors is a sector conflict."""
        await _insert_company(kg, "comp_sec", "SectorConflict Co")
        await _insert_edge(kg, "comp_sec", "sector:cpg", "in_sector")
        await _insert_edge(kg, "comp_sec", "sector:travel", "in_sector")

        conflicts = await engine.detect_conflicts()
        sector_conflicts = [c for c in conflicts if c.field_name == "sector"]
        assert len(sector_conflicts) == 1
        assert sector_conflicts[0].company_id == "comp_sec"
        assert len(sector_conflicts[0].values) == 2

    @pytest.mark.asyncio
    async def test_both_stage_and_sector_conflict(self, kg, engine):
        """Company can have both stage and sector conflicts simultaneously."""
        await _insert_company(kg, "comp_both", "BothConflict Co", properties={
            "claims": {
                "sec_edgar": {"stage": "Pre-Seed"},
                "github": {"stage": "Seed"},
            }
        })
        await _insert_edge(kg, "comp_both", "sector:cpg", "in_sector")
        await _insert_edge(kg, "comp_both", "sector:marketplace", "in_sector")

        conflicts = await engine.detect_conflicts()
        fields = {c.field_name for c in conflicts if c.company_id == "comp_both"}
        assert "stage" in fields
        assert "sector" in fields

    @pytest.mark.asyncio
    async def test_no_conflict_single_sector(self, kg, engine):
        """Company in exactly one sector is not a sector conflict."""
        await _insert_company(kg, "comp_one_sec", "OneSector Co")
        await _insert_edge(kg, "comp_one_sec", "sector:cpg", "in_sector")

        conflicts = await engine.detect_conflicts()
        sector_conflicts = [c for c in conflicts if c.company_id == "comp_one_sec"]
        assert len(sector_conflicts) == 0

    @pytest.mark.asyncio
    async def test_no_conflict_same_stage_across_sources(self, kg, engine):
        """Sources agreeing on the same stage is not a conflict."""
        await _insert_company(kg, "comp_agree", "AgreeCo", properties={
            "claims": {
                "sec_edgar": {"stage": "Seed"},
                "crunchbase": {"stage": "Seed"},
            }
        })
        conflicts = await engine.detect_conflicts()
        stage_conflicts = [c for c in conflicts if c.company_id == "comp_agree" and c.field_name == "stage"]
        assert len(stage_conflicts) == 0

    @pytest.mark.asyncio
    async def test_limit_parameter(self, kg, engine):
        """detect_conflicts respects the limit parameter."""
        for i in range(5):
            await _insert_company(kg, f"comp_lim_{i}", f"Limit Co {i}", properties={
                "claims": {
                    "source_a": {"stage": "Seed"},
                    "source_b": {"stage": "Series A"},
                }
            })
        conflicts = await engine.detect_conflicts(limit=2)
        assert len(conflicts) <= 2


# ---------------------------------------------------------------------------
# find_data_gaps
# ---------------------------------------------------------------------------

class TestFindDataGaps:
    """Tests for KGQueryEngine.find_data_gaps."""

    @pytest.mark.asyncio
    async def test_thin_evidence_is_a_gap(self, kg, engine):
        """Company with only 1 source should appear as a gap (min_evidence=2)."""
        await _insert_company(kg, "comp_thin", "Thin Co")
        await _insert_signal(kg, "signal:thin1", "Only signal", source_api="github", confidence=0.5)
        await _insert_edge(kg, "comp_thin", "signal:thin1", "detected_by")

        gaps = await engine.find_data_gaps(min_evidence=2)
        matching = [g for g in gaps if g["company_id"] == "comp_thin"]
        assert len(matching) == 1
        assert matching[0]["source_count"] == 1
        assert matching[0]["sources"] == ["github"]
        assert matching[0]["signal_count"] == 1

    @pytest.mark.asyncio
    async def test_sufficient_evidence_excluded(self, kg, engine):
        """Company with 2+ sources should NOT appear as a gap (min_evidence=2)."""
        await _insert_company(kg, "comp_rich", "Rich Co")
        await _insert_signal(kg, "signal:r1", "Sig 1", source_api="sec_edgar", confidence=0.7)
        await _insert_signal(kg, "signal:r2", "Sig 2", source_api="github", confidence=0.6)
        await _insert_edge(kg, "comp_rich", "signal:r1", "detected_by")
        await _insert_edge(kg, "comp_rich", "signal:r2", "detected_by")

        gaps = await engine.find_data_gaps(min_evidence=2)
        matching = [g for g in gaps if g["company_id"] == "comp_rich"]
        assert len(matching) == 0

    @pytest.mark.asyncio
    async def test_company_with_no_signals_is_a_gap(self, kg, engine):
        """Company with zero signals has 0 sources — always a gap."""
        await _insert_company(kg, "comp_zero", "ZeroCo")

        gaps = await engine.find_data_gaps(min_evidence=1)
        matching = [g for g in gaps if g["company_id"] == "comp_zero"]
        assert len(matching) == 1
        assert matching[0]["source_count"] == 0
        assert matching[0]["signal_count"] == 0

    @pytest.mark.asyncio
    async def test_multiple_signals_same_source_still_a_gap(self, kg, engine):
        """Multiple signals from the same source still counts as 1 unique source."""
        await _insert_company(kg, "comp_dup_src", "DupSrc Co")
        await _insert_signal(kg, "signal:ds1", "Sig A", source_api="github", confidence=0.5)
        await _insert_signal(kg, "signal:ds2", "Sig B", source_api="github", confidence=0.6)
        await _insert_edge(kg, "comp_dup_src", "signal:ds1", "detected_by")
        await _insert_edge(kg, "comp_dup_src", "signal:ds2", "detected_by")

        gaps = await engine.find_data_gaps(min_evidence=2)
        matching = [g for g in gaps if g["company_id"] == "comp_dup_src"]
        assert len(matching) == 1
        assert matching[0]["source_count"] == 1
        assert matching[0]["signal_count"] == 2


# ---------------------------------------------------------------------------
# sector_cluster
# ---------------------------------------------------------------------------

class TestSectorCluster:
    """Tests for KGQueryEngine.sector_cluster."""

    @pytest.mark.asyncio
    async def test_nonexistent_sector_returns_empty(self, engine):
        result = await engine.sector_cluster("sector:nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_sector_with_companies(self, kg, engine):
        """Companies linked to a sector appear in the cluster."""
        await _insert_company(kg, "comp_cpg1", "Snack Co")
        await _insert_company(kg, "comp_cpg2", "Drink Co")
        await _insert_edge(kg, "comp_cpg1", "sector:cpg", "in_sector")
        await _insert_edge(kg, "comp_cpg2", "sector:cpg", "in_sector")

        # Add signals for ordering
        await _insert_signal(kg, "signal:cpg1", "Sig", source_api="sec_edgar", confidence=0.7)
        await _insert_signal(kg, "signal:cpg2a", "Sig A", source_api="github", confidence=0.6)
        await _insert_signal(kg, "signal:cpg2b", "Sig B", source_api="sec_edgar", confidence=0.5)
        await _insert_edge(kg, "comp_cpg1", "signal:cpg1", "detected_by")
        await _insert_edge(kg, "comp_cpg2", "signal:cpg2a", "detected_by")
        await _insert_edge(kg, "comp_cpg2", "signal:cpg2b", "detected_by")

        cluster = await engine.sector_cluster("sector:cpg")
        assert len(cluster) == 2
        # Sorted by signal_count descending — comp_cpg2 has 2 signals, comp_cpg1 has 1
        assert cluster[0]["company_id"] == "comp_cpg2"
        assert cluster[0]["signal_count"] == 2
        assert cluster[1]["company_id"] == "comp_cpg1"
        assert cluster[1]["signal_count"] == 1

    @pytest.mark.asyncio
    async def test_sector_cluster_limit(self, kg, engine):
        """Limit parameter caps the returned results."""
        for i in range(5):
            cid = f"comp_cl_{i}"
            await _insert_company(kg, cid, f"Cluster Co {i}")
            await _insert_edge(kg, cid, "sector:health_tech", "in_sector")

        cluster = await engine.sector_cluster("sector:health_tech", limit=2)
        assert len(cluster) <= 2

    @pytest.mark.asyncio
    async def test_sector_cluster_returns_labels(self, kg, engine):
        """Each result should include company_label."""
        await _insert_company(kg, "comp_label", "Labeled Co")
        await _insert_edge(kg, "comp_label", "sector:travel", "in_sector")

        cluster = await engine.sector_cluster("sector:travel")
        assert len(cluster) == 1
        assert cluster[0]["company_label"] == "Labeled Co"


# ---------------------------------------------------------------------------
# find_duplicate_candidates
# ---------------------------------------------------------------------------

class TestFindDuplicateCandidates:
    """Tests for KGQueryEngine.find_duplicate_candidates."""

    @pytest.mark.asyncio
    async def test_shared_location_and_sector(self, kg, engine):
        """Two companies sharing location + sector should be duplicate candidates."""
        await _insert_company(kg, "comp_dup_a", "DupA Co")
        await _insert_company(kg, "comp_dup_b", "DupB Co")
        await _insert_location(kg, "location:sf", "San Francisco")

        # Both share location and sector
        await _insert_edge(kg, "comp_dup_a", "location:sf", "located_in")
        await _insert_edge(kg, "comp_dup_b", "location:sf", "located_in")
        await _insert_edge(kg, "comp_dup_a", "sector:cpg", "in_sector")
        await _insert_edge(kg, "comp_dup_b", "sector:cpg", "in_sector")

        dupes = await engine.find_duplicate_candidates()
        assert len(dupes) >= 1
        pair = dupes[0]
        assert {pair["company_a"], pair["company_b"]} == {"comp_dup_a", "comp_dup_b"}
        assert pair["location"] == "San Francisco"
        # shared_connections should have at least 2 entries (located_in + in_sector)
        assert len(pair["shared_connections"]) >= 2

    @pytest.mark.asyncio
    async def test_no_duplicates_when_only_location_shared(self, kg, engine):
        """Sharing only 1 edge type (location) is not enough for duplicate status (need >= 2)."""
        await _insert_company(kg, "comp_nd_a", "NoDupA Co")
        await _insert_company(kg, "comp_nd_b", "NoDupB Co")
        await _insert_location(kg, "location:la", "Los Angeles")

        await _insert_edge(kg, "comp_nd_a", "location:la", "located_in")
        await _insert_edge(kg, "comp_nd_b", "location:la", "located_in")
        # No shared sector or other edge

        dupes = await engine.find_duplicate_candidates()
        # Only 1 shared connection (location) — below the >= 2 threshold
        matching = [d for d in dupes
                    if {d["company_a"], d["company_b"]} == {"comp_nd_a", "comp_nd_b"}]
        assert len(matching) == 0

    @pytest.mark.asyncio
    async def test_no_duplicates_empty_graph(self, engine):
        """Empty graph with no location nodes yields no duplicates."""
        dupes = await engine.find_duplicate_candidates()
        assert dupes == []


# ---------------------------------------------------------------------------
# rank_by_evidence_strength
# ---------------------------------------------------------------------------

class TestRankByEvidenceStrength:
    """Tests for KGQueryEngine.rank_by_evidence_strength."""

    @pytest.mark.asyncio
    async def test_ordering_by_strength(self, kg, engine):
        """Companies are sorted by evidence_strength descending."""
        # Company A: 2 sources, high confidence
        await _insert_company(kg, "comp_strong", "Strong Co")
        await _insert_signal(kg, "signal:str1", "S1", source_api="sec_edgar", confidence=0.9)
        await _insert_signal(kg, "signal:str2", "S2", source_api="github", confidence=0.8)
        await _insert_edge(kg, "comp_strong", "signal:str1", "detected_by")
        await _insert_edge(kg, "comp_strong", "signal:str2", "detected_by")

        # Company B: 1 source, low confidence
        await _insert_company(kg, "comp_weak", "Weak Co")
        await _insert_signal(kg, "signal:wk1", "W1", source_api="github", confidence=0.3)
        await _insert_edge(kg, "comp_weak", "signal:wk1", "detected_by")

        rankings = await engine.rank_by_evidence_strength()
        assert len(rankings) >= 2
        ids = [r["company_id"] for r in rankings]
        strong_idx = ids.index("comp_strong")
        weak_idx = ids.index("comp_weak")
        assert strong_idx < weak_idx

    @pytest.mark.asyncio
    async def test_min_sources_filter(self, kg, engine):
        """Companies below min_sources are excluded from the ranking."""
        await _insert_company(kg, "comp_filt", "Filtered Co")
        await _insert_signal(kg, "signal:f1", "S1", source_api="github", confidence=0.5)
        await _insert_edge(kg, "comp_filt", "signal:f1", "detected_by")

        rankings = await engine.rank_by_evidence_strength(min_sources=2)
        matching = [r for r in rankings if r["company_id"] == "comp_filt"]
        assert len(matching) == 0

    @pytest.mark.asyncio
    async def test_min_sources_passes(self, kg, engine):
        """Company meeting min_sources is included."""
        await _insert_company(kg, "comp_pass", "Pass Co")
        await _insert_signal(kg, "signal:p1", "S1", source_api="sec_edgar", confidence=0.6)
        await _insert_signal(kg, "signal:p2", "S2", source_api="github", confidence=0.7)
        await _insert_edge(kg, "comp_pass", "signal:p1", "detected_by")
        await _insert_edge(kg, "comp_pass", "signal:p2", "detected_by")

        rankings = await engine.rank_by_evidence_strength(min_sources=2)
        matching = [r for r in rankings if r["company_id"] == "comp_pass"]
        assert len(matching) == 1
        assert matching[0]["source_count"] == 2

    @pytest.mark.asyncio
    async def test_ranking_fields(self, kg, engine):
        """Each ranking dict has expected fields."""
        await _insert_company(kg, "comp_rf", "RFCo")
        await _insert_signal(kg, "signal:rf1", "Sig", source_api="github", confidence=0.65)
        await _insert_edge(kg, "comp_rf", "signal:rf1", "detected_by")

        rankings = await engine.rank_by_evidence_strength()
        matching = [r for r in rankings if r["company_id"] == "comp_rf"]
        assert len(matching) == 1
        r = matching[0]
        assert "company_label" in r
        assert "source_count" in r
        assert "signal_count" in r
        assert "avg_confidence" in r
        assert "evidence_strength" in r

    @pytest.mark.asyncio
    async def test_evidence_strength_capped_at_one(self, kg, engine):
        """Evidence strength should be capped at 1.0."""
        await _insert_company(kg, "comp_cap", "CapCo")
        for i, api in enumerate(["sec_edgar", "github", "hacker_news", "crunchbase"]):
            sid = f"signal:cap_{i}"
            await _insert_signal(kg, sid, f"Sig{i}", source_api=api, confidence=0.95)
            await _insert_edge(kg, "comp_cap", sid, "detected_by")

        rankings = await engine.rank_by_evidence_strength()
        matching = [r for r in rankings if r["company_id"] == "comp_cap"]
        assert len(matching) == 1
        assert matching[0]["evidence_strength"] == 1.0


# ---------------------------------------------------------------------------
# ego_graph
# ---------------------------------------------------------------------------

class TestEgoGraph:
    """Tests for KGQueryEngine.ego_graph."""

    @pytest.mark.asyncio
    async def test_ego_graph_structure(self, kg, engine):
        """Ego graph returns nodes, edges, and metadata."""
        await _insert_company(kg, "comp_ego", "Ego Co")
        await _insert_signal(kg, "signal:ego1", "Sig", source_api="github", confidence=0.5)
        await _insert_edge(kg, "comp_ego", "signal:ego1", "detected_by")
        await _insert_edge(kg, "comp_ego", "sector:cpg", "in_sector")

        graph = await engine.ego_graph("comp_ego", depth=1)
        assert graph["center"] == "comp_ego"
        assert graph["depth"] == 1
        assert "nodes" in graph
        assert "edges" in graph
        assert "node_count" in graph
        assert "edge_count" in graph
        assert graph["node_count"] == len(graph["nodes"])
        assert graph["edge_count"] == len(graph["edges"])

    @pytest.mark.asyncio
    async def test_ego_graph_finds_neighbors(self, kg, engine):
        """Depth-1 ego graph includes direct neighbors."""
        await _insert_company(kg, "comp_eg2", "EgoCo2")
        await _insert_signal(kg, "signal:eg2_1", "SigA", source_api="github", confidence=0.5)
        await _insert_edge(kg, "comp_eg2", "signal:eg2_1", "detected_by")

        graph = await engine.ego_graph("comp_eg2", depth=1)
        node_ids = {n["id"] for n in graph["nodes"]}
        assert "comp_eg2" in node_ids
        assert "signal:eg2_1" in node_ids

    @pytest.mark.asyncio
    async def test_ego_graph_depth_two(self, kg, engine):
        """Depth-2 traversal reaches 2-hop neighbors via outgoing directed edges."""
        await _insert_company(kg, "comp_d2", "Depth2Co")
        await _insert_signal(kg, "signal:d2_1", "Sig", source_api="sec_edgar", confidence=0.6)
        await _insert_edge(kg, "comp_d2", "signal:d2_1", "detected_by")
        await _insert_edge(kg, "comp_d2", "sector:health_tech", "in_sector")

        graph = await engine.ego_graph("comp_d2", depth=2)
        node_ids = {n["id"] for n in graph["nodes"]}
        assert "comp_d2" in node_ids
        # Directed edges are traversed outgoing: company -> sector, company -> signal
        assert "sector:health_tech" in node_ids
        assert "signal:d2_1" in node_ids
        # Depth field should be present on each node
        depths = {n["id"]: n["depth"] for n in graph["nodes"]}
        assert depths["comp_d2"] == 0
        assert depths.get("sector:health_tech", -1) >= 1
        assert depths.get("signal:d2_1", -1) >= 1

    @pytest.mark.asyncio
    async def test_ego_graph_depth_two_via_undirected(self, kg, engine):
        """Depth-2 traversal crosses undirected edges in both directions.

        co_founded_with is an undirected edge type, so the bidirectional view
        makes it traversable from either endpoint.
        """
        # Create two founders and an undirected co_founded_with edge
        await kg.upsert_node(KGNode(
            id="founder:alice", node_type="founder", label="Alice",
            source_table="signal_etl", source_id="founder:alice",
        ))
        await kg.upsert_node(KGNode(
            id="founder:bob", node_type="founder", label="Bob",
            source_table="signal_etl", source_id="founder:bob",
        ))
        # co_founded_with is undirected — stored with source < target
        await kg.upsert_edge(KGEdge(
            id=kg_edge_id(),
            edge_type="co_founded_with",
            source_node_id="founder:alice",
            target_node_id="founder:bob",
            weight=1.0,
            is_directed=False,
        ))
        # Add a company linked to Bob
        await _insert_company(kg, "comp_bob", "Bob's Co")
        await _insert_edge(kg, "comp_bob", "founder:bob", "founded_by")

        # Traverse from Alice at depth 2: Alice -> Bob (via undirected) -> comp_bob (via incoming)
        # Note: traverse uses bidirectional view, so undirected edges are seen from both sides
        graph = await engine.ego_graph("founder:alice", depth=2)
        node_ids = {n["id"] for n in graph["nodes"]}
        assert "founder:alice" in node_ids
        assert "founder:bob" in node_ids

    @pytest.mark.asyncio
    async def test_ego_graph_nonexistent_node(self, engine):
        """Ego graph of nonexistent node returns empty structure."""
        graph = await engine.ego_graph("nonexistent", depth=2)
        assert graph["center"] == "nonexistent"
        assert graph["nodes"] == []
        assert graph["edges"] == []
        assert graph["node_count"] == 0
        assert graph["edge_count"] == 0


# ---------------------------------------------------------------------------
# founder_network / investor_portfolio (Phase 3+ stubs)
# ---------------------------------------------------------------------------

class TestPhase3Stubs:
    """Tests for founder_network and investor_portfolio — currently empty (Phase 3+)."""

    @pytest.mark.asyncio
    async def test_founder_network_no_data(self, engine):
        """Without founder nodes/edges, founder_network returns empty list."""
        result = await engine.founder_network("founder:nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_investor_portfolio_no_data(self, engine):
        """Without investor nodes/edges, investor_portfolio returns empty list."""
        result = await engine.investor_portfolio("investor:nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_founder_network_with_data(self, kg, engine):
        """With founder nodes and founded_by edges, founder_network returns companies."""
        # Create founder and companies
        await kg.upsert_node(KGNode(
            id="founder:jdoe", node_type="founder", label="Jane Doe",
            source_table="signal_etl", source_id="founder:jdoe",
        ))
        await _insert_company(kg, "comp_fn1", "FounderCo1")
        await _insert_company(kg, "comp_fn2", "FounderCo2")
        # founded_by edges: company → founder (incoming to founder)
        await _insert_edge(kg, "comp_fn1", "founder:jdoe", "founded_by", weight=1.0)
        await _insert_edge(kg, "comp_fn2", "founder:jdoe", "founded_by", weight=0.9)

        result = await engine.founder_network("founder:jdoe")
        assert len(result) == 2
        ids = {r["company_id"] for r in result}
        assert ids == {"comp_fn1", "comp_fn2"}

    @pytest.mark.asyncio
    async def test_investor_portfolio_with_data(self, kg, engine):
        """With investor nodes and backed_by edges, investor_portfolio returns companies."""
        await kg.upsert_node(KGNode(
            id="investor:vcfund", node_type="investor", label="VC Fund A",
            source_table="signal_etl", source_id="investor:vcfund",
        ))
        await _insert_company(kg, "comp_ip1", "InvesteeCo1")
        await _insert_edge(kg, "comp_ip1", "investor:vcfund", "backed_by", weight=1.0)

        result = await engine.investor_portfolio("investor:vcfund")
        assert len(result) == 1
        assert result[0]["company_id"] == "comp_ip1"
        assert result[0]["company_label"] == "InvesteeCo1"


# ---------------------------------------------------------------------------
# Serialization: EvidenceChain.to_dict / ConflictRecord.to_dict
# ---------------------------------------------------------------------------

class TestSerialization:
    """Tests for to_dict() on EvidenceChain and ConflictRecord."""

    def test_evidence_chain_to_dict(self):
        """EvidenceChain.to_dict() produces expected dict structure."""
        chain = EvidenceChain(
            company_id="comp1",
            company_label="Acme",
            signals=[{"signal_id": "signal:1", "confidence": 0.7}],
            evidence_families=["regulatory"],
            sectors=["Consumer CPG"],
            locations=["New York, US"],
            source_count=2,
            weighted_score=0.75,
        )
        d = chain.to_dict()
        assert d["company_id"] == "comp1"
        assert d["company_label"] == "Acme"
        assert d["signals"] == [{"signal_id": "signal:1", "confidence": 0.7}]
        assert d["evidence_families"] == ["regulatory"]
        assert d["sectors"] == ["Consumer CPG"]
        assert d["locations"] == ["New York, US"]
        assert d["source_count"] == 2
        assert d["weighted_score"] == 0.75

    def test_evidence_chain_to_dict_json_serializable(self):
        """EvidenceChain.to_dict() output is JSON-serializable."""
        chain = EvidenceChain(
            company_id="comp1",
            company_label="Test",
            signals=[],
            source_count=0,
            weighted_score=0.0,
        )
        serialized = json.dumps(chain.to_dict())
        assert isinstance(serialized, str)

    def test_conflict_record_to_dict(self):
        """ConflictRecord.to_dict() produces expected dict structure."""
        rec = ConflictRecord(
            company_id="comp2",
            company_label="Conflict Co",
            field_name="stage",
            values={"sec_edgar": "Seed", "crunchbase": "Series A"},
        )
        d = rec.to_dict()
        assert d["company_id"] == "comp2"
        assert d["company_label"] == "Conflict Co"
        assert d["field"] == "stage"
        assert d["values"] == {"sec_edgar": "Seed", "crunchbase": "Series A"}

    def test_conflict_record_to_dict_json_serializable(self):
        """ConflictRecord.to_dict() output is JSON-serializable."""
        rec = ConflictRecord(
            company_id="comp2",
            company_label=None,
            field_name="sector",
            values={"a": "cpg", "b": "health_tech"},
        )
        serialized = json.dumps(rec.to_dict())
        assert isinstance(serialized, str)

    def test_evidence_chain_defaults(self):
        """EvidenceChain defaults are all empty/zero."""
        chain = EvidenceChain(company_id="x", company_label=None)
        d = chain.to_dict()
        assert d["signals"] == []
        assert d["evidence_families"] == []
        assert d["sectors"] == []
        assert d["locations"] == []
        assert d["source_count"] == 0
        assert d["weighted_score"] == 0.0

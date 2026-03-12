"""Tests for storage/kg_store.py — KGStore CRUD, traversal, validation, crash recovery.

Covers PR1 validation plan items:
  #1  Schema conformance
  #11 Cycle safety (JSON array visited)
  #14 View liveness
  #18 Crash recovery
  #21 Traversal false-positive (A10 vs A1)
  #22 Undirected path (bidirectional view)
  #25 Graph validate (named checks, --fail-fast, JSON output)
"""

import json
import os
import sys
import tempfile

import pytest
import pytest_asyncio
import aiosqlite

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.kg_store import (
    KGEdge,
    KGNode,
    KGStore,
    ValidationResult,
    kg_edge_id,
    kg_node_id,
)
from storage.migrations.v50_knowledge_graph import (
    UNDIRECTED_EDGE_TYPES,
    V50_KNOWLEDGE_GRAPH_DDL,
    VALID_EDGE_TYPES,
    VALID_NODE_TYPES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def kg_db():
    """Fresh aiosqlite connection with v50 schema applied."""
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
async def store(kg_db):
    """KGStore backed by a fresh DB."""
    return KGStore(kg_db)


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

class TestIDGeneration:
    def test_kg_node_id_deterministic(self):
        a = kg_node_id("company", "abc")
        b = kg_node_id("company", "abc")
        assert a == b
        assert a == "abc"

    def test_kg_node_id_different_seeds(self):
        a = kg_node_id("company", "abc")
        b = kg_node_id("company", "xyz")
        assert a != b

    def test_kg_node_id_prefixes_non_company_nodes(self):
        assert kg_node_id("signal", "s1") == "signal:s1"
        assert kg_node_id("sector", "consumer_cpg") == "sector:consumer_cpg"

    def test_kg_node_id_rejects_empty_parts(self):
        with pytest.raises(ValueError):
            kg_node_id("", "abc")
        with pytest.raises(ValueError):
            kg_node_id("company", "")

    def test_kg_edge_id_unique(self):
        ids = {kg_edge_id() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# Ontology seed data
# ---------------------------------------------------------------------------

class TestOntologySeeds:
    @pytest.mark.asyncio
    async def test_sector_seeds_loaded(self, kg_db):
        cursor = await kg_db.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE node_type = 'sector'"
        )
        row = await cursor.fetchone()
        assert row[0] == 4

    @pytest.mark.asyncio
    async def test_evidence_family_seeds_loaded(self, kg_db):
        cursor = await kg_db.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE node_type = 'evidence_family'"
        )
        row = await cursor.fetchone()
        assert row[0] == 6

    @pytest.mark.asyncio
    async def test_seed_ids_match_expected(self, kg_db):
        cursor = await kg_db.execute(
            "SELECT id FROM kg_nodes WHERE node_type = 'sector' ORDER BY id"
        )
        ids = [r[0] for r in await cursor.fetchall()]
        assert "sector:cpg" in ids
        assert "sector:health_tech" in ids

    @pytest.mark.asyncio
    async def test_seeds_are_not_tombstoned(self, kg_db):
        cursor = await kg_db.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE source_table = 'ontology' AND is_tombstone = 1"
        )
        row = await cursor.fetchone()
        assert row[0] == 0


# ---------------------------------------------------------------------------
# Node CRUD
# ---------------------------------------------------------------------------

class TestNodeCRUD:
    @pytest.mark.asyncio
    async def test_upsert_and_get_node(self, store):
        nid = kg_node_id("company", "acme")
        node = KGNode(id=nid, node_type="company", label="Acme Corp",
                       source_table="entity_aliases", source_id="ent123")
        await store.upsert_node(node, run_id="run1")

        fetched = await store.get_node(nid)
        assert fetched is not None
        assert fetched.label == "Acme Corp"
        assert fetched.node_type == "company"
        assert fetched.source_table == "entity_aliases"
        assert fetched.last_run_id == "run1"

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, store):
        nid = kg_node_id("company", "acme")
        node = KGNode(id=nid, node_type="company", label="Acme v1")
        await store.upsert_node(node, run_id="run1")

        node2 = KGNode(id=nid, node_type="company", label="Acme v2")
        await store.upsert_node(node2, run_id="run2")

        fetched = await store.get_node(nid)
        assert fetched.label == "Acme v2"
        assert fetched.last_run_id == "run2"

    @pytest.mark.asyncio
    async def test_tombstone_node(self, store):
        nid = kg_node_id("company", "dead")
        node = KGNode(id=nid, node_type="company", label="Dead Co")
        await store.upsert_node(node)

        result = await store.tombstone_node(nid, run_id="run1")
        assert result is True

        fetched = await store.get_node(nid)
        assert fetched.is_tombstone is True

    @pytest.mark.asyncio
    async def test_tombstone_nonexistent(self, store):
        result = await store.tombstone_node("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_count_nodes(self, store):
        for i in range(3):
            nid = kg_node_id("company", f"co{i}")
            await store.upsert_node(KGNode(id=nid, node_type="company", label=f"Co {i}"))

        # Ontology seeds + 3 company nodes
        total = await store.count_nodes()
        assert total == 10 + 3  # 4 sectors + 6 evidence_families + 3 companies

        company_count = await store.count_nodes(node_type="company")
        assert company_count == 3

    @pytest.mark.asyncio
    async def test_node_with_properties(self, store):
        nid = kg_node_id("company", "props")
        props = {"revenue": 1000000, "employees": 50}
        node = KGNode(id=nid, node_type="company", label="PropCo",
                       properties=props)
        await store.upsert_node(node)

        fetched = await store.get_node(nid)
        assert fetched.properties == props

    @pytest.mark.asyncio
    async def test_get_nonexistent_node(self, store):
        fetched = await store.get_node("doesnotexist")
        assert fetched is None


# ---------------------------------------------------------------------------
# Edge CRUD
# ---------------------------------------------------------------------------

class TestEdgeCRUD:
    @pytest.mark.asyncio
    async def test_upsert_and_get_edge(self, store):
        n1 = kg_node_id("company", "a")
        n2 = kg_node_id("signal", "s1")
        await store.upsert_node(KGNode(id=n1, node_type="company", label="A"))
        await store.upsert_node(KGNode(id=n2, node_type="signal", label="S1"))

        eid = kg_edge_id()
        edge = KGEdge(id=eid, edge_type="detected_by",
                       source_node_id=n1, target_node_id=n2)
        await store.upsert_edge(edge, run_id="run1")

        fetched = await store.get_edge(eid)
        assert fetched is not None
        assert fetched.edge_type == "detected_by"
        assert fetched.is_directed is True
        assert fetched.last_run_id == "run1"

    @pytest.mark.asyncio
    async def test_expire_edge(self, store):
        n1 = kg_node_id("company", "a")
        n2 = kg_node_id("signal", "s1")
        await store.upsert_node(KGNode(id=n1, node_type="company", label="A"))
        await store.upsert_node(KGNode(id=n2, node_type="signal", label="S1"))

        eid = kg_edge_id()
        edge = KGEdge(id=eid, edge_type="detected_by",
                       source_node_id=n1, target_node_id=n2)
        await store.upsert_edge(edge)

        result = await store.expire_edge(eid, run_id="run1")
        assert result is True

        fetched = await store.get_edge(eid)
        assert fetched.valid_until is not None

    @pytest.mark.asyncio
    async def test_expire_nonexistent(self, store):
        result = await store.expire_edge("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_count_edges(self, store):
        n1 = kg_node_id("company", "a")
        n2 = kg_node_id("signal", "s1")
        n3 = kg_node_id("signal", "s2")
        await store.upsert_node(KGNode(id=n1, node_type="company", label="A"))
        await store.upsert_node(KGNode(id=n2, node_type="signal", label="S1"))
        await store.upsert_node(KGNode(id=n3, node_type="signal", label="S2"))

        e1 = kg_edge_id()
        e2 = kg_edge_id()
        await store.upsert_edge(KGEdge(id=e1, edge_type="detected_by",
                                        source_node_id=n1, target_node_id=n2))
        await store.upsert_edge(KGEdge(id=e2, edge_type="detected_by",
                                        source_node_id=n1, target_node_id=n3))

        assert await store.count_edges() == 2
        assert await store.count_edges(edge_type="detected_by") == 2
        assert await store.count_edges(edge_type="in_sector") == 0

    @pytest.mark.asyncio
    async def test_self_loop_rejected(self, store):
        """CHECK(source_node_id != target_node_id) prevents self-loops."""
        nid = kg_node_id("company", "self")
        await store.upsert_node(KGNode(id=nid, node_type="company", label="Self"))
        eid = kg_edge_id()
        with pytest.raises(Exception):
            await store.upsert_edge(KGEdge(
                id=eid, edge_type="merged_into",
                source_node_id=nid, target_node_id=nid,
            ))


# ---------------------------------------------------------------------------
# Undirected edge symmetry (#8, #22)
# ---------------------------------------------------------------------------

class TestUndirectedEdges:
    @pytest.mark.asyncio
    async def test_symmetry_enforced(self, store):
        """Undirected edges auto-swap to source < target."""
        n1 = kg_node_id("founder", "alice")
        n2 = kg_node_id("founder", "bob")
        # Ensure n1 > n2 or vice versa; we pass in reversed order
        hi, lo = max(n1, n2), min(n1, n2)
        await store.upsert_node(KGNode(id=n1, node_type="founder", label="Alice"))
        await store.upsert_node(KGNode(id=n2, node_type="founder", label="Bob"))

        eid = kg_edge_id()
        # Pass hi as source — should be swapped
        await store.upsert_edge(KGEdge(
            id=eid, edge_type="co_founded_with",
            source_node_id=hi, target_node_id=lo,
        ))

        fetched = await store.get_edge(eid)
        assert fetched.source_node_id == lo
        assert fetched.target_node_id == hi
        assert fetched.is_directed is False

    @pytest.mark.asyncio
    async def test_bidirectional_traversal(self, store):
        """Undirected edges appear in both directions via bidirectional view (#22)."""
        n1 = kg_node_id("founder", "alice")
        n2 = kg_node_id("founder", "bob")
        await store.upsert_node(KGNode(id=n1, node_type="founder", label="Alice"))
        await store.upsert_node(KGNode(id=n2, node_type="founder", label="Bob"))

        eid = kg_edge_id()
        await store.upsert_edge(KGEdge(
            id=eid, edge_type="co_founded_with",
            source_node_id=n1, target_node_id=n2,
        ))

        # Traverse from Alice should find Bob
        neighbors_alice = await store.get_neighbors(n1, direction="both")
        assert any(nb["neighbor_id"] == n2 for nb in neighbors_alice)

        # Traverse from Bob should also find Alice
        neighbors_bob = await store.get_neighbors(n2, direction="both")
        assert any(nb["neighbor_id"] == n1 for nb in neighbors_bob)


# ---------------------------------------------------------------------------
# Cycle-safe traversal (#11, #21)
# ---------------------------------------------------------------------------

class TestTraversal:
    @pytest.mark.asyncio
    async def test_cycle_terminates(self, store):
        """A->B->A cycle terminates cleanly (#11)."""
        nA = kg_node_id("company", "A")
        nB = kg_node_id("company", "B")
        await store.upsert_node(KGNode(id=nA, node_type="company", label="A"))
        await store.upsert_node(KGNode(id=nB, node_type="company", label="B"))

        e1 = kg_edge_id()
        e2 = kg_edge_id()
        await store.upsert_edge(KGEdge(id=e1, edge_type="merged_into",
                                        source_node_id=nA, target_node_id=nB))
        await store.upsert_edge(KGEdge(id=e2, edge_type="merged_into",
                                        source_node_id=nB, target_node_id=nA))

        result = await store.traverse(nA, max_depth=5)
        # Should terminate, including both A and B
        node_ids = {r["node_id"] for r in result}
        assert nA in node_ids
        assert nB in node_ids
        # Should NOT duplicate entries
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_no_false_positive_a10_a1(self, store):
        """Node 'A10' does NOT block traversal to 'A1' (#21).

        With INSTR-based visited tracking, 'A10' contains 'A1' as substring.
        JSON array approach avoids this.
        """
        # Use literal IDs to test the exact scenario
        nA1 = "A1______________"[:16]  # pad to 16 chars
        nA10 = "A10_____________"[:16]
        nA10target = "A10target_______"[:16]
        await store.upsert_node(KGNode(id=nA1, node_type="company", label="A1"))
        await store.upsert_node(KGNode(id=nA10, node_type="company", label="A10"))
        await store.upsert_node(KGNode(id=nA10target, node_type="company", label="A10 Target"))

        # A1 -> A10, A10 -> A10target
        await store.upsert_edge(KGEdge(id=kg_edge_id(), edge_type="merged_into",
                                        source_node_id=nA1, target_node_id=nA10))
        await store.upsert_edge(KGEdge(id=kg_edge_id(), edge_type="merged_into",
                                        source_node_id=nA10, target_node_id=nA10target))

        result = await store.traverse(nA1, max_depth=5)
        node_ids = {r["node_id"] for r in result}
        # All three nodes should be reachable
        assert nA1 in node_ids
        assert nA10 in node_ids
        assert nA10target in node_ids

    @pytest.mark.asyncio
    async def test_max_depth_respected(self, store):
        """Traversal stops at max_depth."""
        nodes = []
        for i in range(5):
            nid = kg_node_id("company", f"chain{i}")
            await store.upsert_node(KGNode(id=nid, node_type="company", label=f"Chain {i}"))
            nodes.append(nid)

        for i in range(len(nodes) - 1):
            await store.upsert_edge(KGEdge(id=kg_edge_id(), edge_type="merged_into",
                                            source_node_id=nodes[i], target_node_id=nodes[i + 1]))

        result = await store.traverse(nodes[0], max_depth=2)
        depths = {r["depth"] for r in result}
        assert max(depths) <= 2

    @pytest.mark.asyncio
    async def test_traverse_with_edge_type_filter(self, store):
        """Edge type filter limits traversal."""
        n1 = kg_node_id("company", "co1")
        n2 = kg_node_id("signal", "s1")
        n3 = kg_node_id("investor", "inv1")
        await store.upsert_node(KGNode(id=n1, node_type="company", label="Co1"))
        await store.upsert_node(KGNode(id=n2, node_type="signal", label="S1"))
        await store.upsert_node(KGNode(id=n3, node_type="investor", label="Inv1"))

        await store.upsert_edge(KGEdge(id=kg_edge_id(), edge_type="detected_by",
                                        source_node_id=n1, target_node_id=n2))
        await store.upsert_edge(KGEdge(id=kg_edge_id(), edge_type="backed_by",
                                        source_node_id=n1, target_node_id=n3))

        result = await store.traverse(n1, edge_type="detected_by")
        node_ids = {r["node_id"] for r in result}
        assert n2 in node_ids
        assert n3 not in node_ids

    @pytest.mark.asyncio
    async def test_traverse_empty_graph(self, store):
        """Traversal from a node with no edges returns just the start node."""
        nid = kg_node_id("company", "lonely")
        await store.upsert_node(KGNode(id=nid, node_type="company", label="Lonely"))

        result = await store.traverse(nid)
        assert len(result) == 1
        assert result[0]["node_id"] == nid
        assert result[0]["depth"] == 0


# ---------------------------------------------------------------------------
# Runs CRUD
# ---------------------------------------------------------------------------

class TestRunsCRUD:
    @pytest.mark.asyncio
    async def test_create_and_get_run(self, store):
        run = await store.create_run(mode="full")
        assert run.status == "running"
        assert run.mode == "full"

        fetched = await store.get_run(run.run_id)
        assert fetched is not None
        assert fetched.run_id == run.run_id

    @pytest.mark.asyncio
    async def test_complete_run(self, store):
        run = await store.create_run()
        await store.complete_run(
            run.run_id, status="completed",
            nodes_upserted=10, edges_upserted=5,
        )

        fetched = await store.get_run(run.run_id)
        assert fetched.status == "completed"
        assert fetched.nodes_upserted == 10
        assert fetched.completed_at is not None

    @pytest.mark.asyncio
    async def test_list_runs(self, store):
        for _ in range(3):
            await store.create_run()

        runs = await store.list_runs(limit=10)
        assert len(runs) == 3

    @pytest.mark.asyncio
    async def test_get_nonexistent_run(self, store):
        fetched = await store.get_run("doesnotexist")
        assert fetched is None


# ---------------------------------------------------------------------------
# Run source lifecycle
# ---------------------------------------------------------------------------

class TestRunSourceLifecycle:
    @pytest.mark.asyncio
    async def test_create_and_complete_source(self, store):
        run = await store.create_run()
        sid = await store.create_run_source(
            run.run_id, "signals", refresh_strategy="full_recompute",
        )
        assert sid is not None

        await store.complete_run_source(
            sid, status="completed", rows_scanned=100, rows_written=50,
            duration_ms=1234.5,
        )

        sources = await store.get_run_sources(run.run_id)
        assert len(sources) == 1
        assert sources[0].status == "completed"
        assert sources[0].rows_scanned == 100
        assert sources[0].duration_ms == 1234.5

    @pytest.mark.asyncio
    async def test_failed_source(self, store):
        run = await store.create_run()
        sid = await store.create_run_source(run.run_id, "entity_migrations")
        await store.complete_run_source(
            sid, status="failed", error_text="Connection timeout",
        )

        sources = await store.get_run_sources(run.run_id)
        assert sources[0].status == "failed"
        assert sources[0].error_text == "Connection timeout"


# ---------------------------------------------------------------------------
# Crash recovery (#18)
# ---------------------------------------------------------------------------

class TestCrashRecovery:
    @pytest.mark.asyncio
    async def test_stale_runs_recovered(self, kg_db):
        """Runs stuck in 'running' for >1 hour are marked 'failed'."""
        # Insert a run with started_at 2 hours ago
        await kg_db.execute("""
            INSERT INTO kg_runs (run_id, mode, started_at, status)
            VALUES ('stale1', 'full', datetime('now', '-2 hours'), 'running')
        """)
        await kg_db.commit()

        store = KGStore(kg_db)
        recovered = await store.recover_stale_runs()
        assert recovered == 1

        cursor = await kg_db.execute(
            "SELECT status FROM kg_runs WHERE run_id = 'stale1'"
        )
        row = await cursor.fetchone()
        assert row[0] == "failed"

    @pytest.mark.asyncio
    async def test_recent_running_not_recovered(self, kg_db):
        """Runs started < 1 hour ago are NOT marked failed."""
        await kg_db.execute("""
            INSERT INTO kg_runs (run_id, mode, started_at, status)
            VALUES ('recent1', 'full', datetime('now', '-30 minutes'), 'running')
        """)
        await kg_db.commit()

        store = KGStore(kg_db)
        recovered = await store.recover_stale_runs()
        assert recovered == 0

        cursor = await kg_db.execute(
            "SELECT status FROM kg_runs WHERE run_id = 'recent1'"
        )
        row = await cursor.fetchone()
        assert row[0] == "running"


# ---------------------------------------------------------------------------
# Validation (#25)
# ---------------------------------------------------------------------------

class TestValidation:
    @pytest.mark.asyncio
    async def test_validate_empty_graph_all_pass(self, store):
        """All checks pass on a freshly-seeded empty graph."""
        results = await store.validate()
        for r in results:
            assert r.status == "pass", f"{r.check} failed: {r.details}"

    @pytest.mark.asyncio
    async def test_validate_orphan_edges_detected(self, store, kg_db):
        """Orphan edges are caught."""
        # Insert an edge referencing non-existent nodes (bypass FK with PRAGMA off)
        await kg_db.execute("PRAGMA foreign_keys=OFF")
        await kg_db.execute("""
            INSERT INTO kg_edges (id, edge_type, source_node_id, target_node_id,
                                   is_directed, created_at, updated_at)
            VALUES ('orphan1', 'detected_by', 'ghost_src', 'ghost_tgt',
                    1, '2026-01-01', '2026-01-01')
        """)
        await kg_db.commit()
        await kg_db.execute("PRAGMA foreign_keys=ON")

        results = await store.validate()
        orphan = next(r for r in results if r.check == "orphan_edges")
        assert orphan.status == "fail"

    @pytest.mark.asyncio
    async def test_validate_tombstone_edges_detected(self, store):
        """Live edges touching tombstoned nodes are caught."""
        n1 = kg_node_id("company", "alive")
        n2 = kg_node_id("company", "dead")
        await store.upsert_node(KGNode(id=n1, node_type="company", label="Alive"))
        await store.upsert_node(KGNode(id=n2, node_type="company", label="Dead"))

        eid = kg_edge_id()
        await store.upsert_edge(KGEdge(id=eid, edge_type="merged_into",
                                        source_node_id=n1, target_node_id=n2))
        # Tombstone the target
        await store.tombstone_node(n2)

        results = await store.validate()
        tomb = next(r for r in results if r.check == "tombstone_edges")
        assert tomb.status == "fail"

    @pytest.mark.asyncio
    async def test_validate_symmetry_detected(self, store, kg_db):
        """Undirected edges with source > target are caught."""
        n1 = kg_node_id("founder", "alice")
        n2 = kg_node_id("founder", "bob")
        await store.upsert_node(KGNode(id=n1, node_type="founder", label="Alice"))
        await store.upsert_node(KGNode(id=n2, node_type="founder", label="Bob"))

        hi, lo = max(n1, n2), min(n1, n2)
        # Bypass upsert_edge's auto-swap by inserting directly
        await kg_db.execute("""
            INSERT INTO kg_edges (id, edge_type, source_node_id, target_node_id,
                                   is_directed, created_at, updated_at)
            VALUES ('bad_sym', 'co_founded_with', :hi, :lo, 0,
                    '2026-01-01', '2026-01-01')
        """, {"hi": hi, "lo": lo})
        await kg_db.commit()

        results = await store.validate()
        sym = next(r for r in results if r.check == "symmetry")
        assert sym.status == "fail"

    @pytest.mark.asyncio
    async def test_validate_fail_fast(self, store, kg_db):
        """--fail-fast stops on first failure."""
        # Create orphan edge
        await kg_db.execute("PRAGMA foreign_keys=OFF")
        await kg_db.execute("""
            INSERT INTO kg_edges (id, edge_type, source_node_id, target_node_id,
                                   is_directed, created_at, updated_at)
            VALUES ('orphan_ff', 'detected_by', 'no_src', 'no_tgt',
                    1, '2026-01-01', '2026-01-01')
        """)
        await kg_db.commit()
        await kg_db.execute("PRAGMA foreign_keys=ON")

        results = await store.validate(fail_fast=True)
        # Should stop after the first failing check
        assert any(r.status == "fail" for r in results)
        # Should have fewer results than full run
        full_results = await store.validate(fail_fast=False)
        assert len(results) <= len(full_results)

    @pytest.mark.asyncio
    async def test_validate_returns_json_serializable(self, store):
        """ValidationResult.to_dict() produces JSON-serializable output."""
        results = await store.validate()
        data = [r.to_dict() for r in results]
        json_str = json.dumps(data)
        assert isinstance(json_str, str)


# ---------------------------------------------------------------------------
# View liveness (#14)
# ---------------------------------------------------------------------------

class TestViewLiveness:
    @pytest.mark.asyncio
    async def test_expired_edges_excluded_from_views(self, store, kg_db):
        """Views only return edges where valid_until IS NULL."""
        n1 = kg_node_id("company", "a")
        n2 = kg_node_id("signal", "s1")
        n3 = kg_node_id("signal", "s2")
        await store.upsert_node(KGNode(id=n1, node_type="company", label="A"))
        await store.upsert_node(KGNode(id=n2, node_type="signal", label="S1"))
        await store.upsert_node(KGNode(id=n3, node_type="signal", label="S2"))

        e1 = kg_edge_id()
        e2 = kg_edge_id()
        await store.upsert_edge(KGEdge(id=e1, edge_type="detected_by",
                                        source_node_id=n1, target_node_id=n2))
        await store.upsert_edge(KGEdge(id=e2, edge_type="detected_by",
                                        source_node_id=n1, target_node_id=n3))

        # Expire one edge
        await store.expire_edge(e1)

        # Undirected view should only have e2
        cursor = await kg_db.execute(
            "SELECT id FROM kg_edges_undirected ORDER BY id"
        )
        live_ids = [r[0] for r in await cursor.fetchall()]
        assert e1 not in live_ids
        assert e2 in live_ids

        # Bidirectional view should only have e2
        cursor = await kg_db.execute(
            "SELECT DISTINCT id FROM kg_edges_bidirectional ORDER BY id"
        )
        bidir_ids = [r[0] for r in await cursor.fetchall()]
        assert e1 not in bidir_ids
        assert e2 in bidir_ids


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    @pytest.mark.asyncio
    async def test_stats_on_empty_graph(self, store):
        stats = await store.get_stats()
        # Seeds: 4 sectors + 6 evidence_families
        assert stats.live_nodes == 10
        assert stats.tombstoned_nodes == 0
        assert stats.live_edges == 0
        assert stats.total_runs == 0
        assert stats.last_run is None

    @pytest.mark.asyncio
    async def test_stats_after_mutations(self, store):
        n1 = kg_node_id("company", "co1")
        n2 = kg_node_id("signal", "s1")
        await store.upsert_node(KGNode(id=n1, node_type="company", label="Co"))
        await store.upsert_node(KGNode(id=n2, node_type="signal", label="S"))
        await store.upsert_edge(KGEdge(
            id=kg_edge_id(), edge_type="detected_by",
            source_node_id=n1, target_node_id=n2,
        ))

        run = await store.create_run()
        await store.complete_run(run.run_id, nodes_upserted=2, edges_upserted=1)

        stats = await store.get_stats()
        assert stats.live_nodes == 12  # 10 seeds + 2
        assert stats.live_edges == 1
        assert stats.total_runs == 1
        assert stats.last_run is not None
        assert stats.last_run["run_id"] == run.run_id
        assert stats.nodes_by_type.get("company") == 1
        assert stats.edges_by_type.get("detected_by") == 1


# ---------------------------------------------------------------------------
# Evidence + Provenance
# ---------------------------------------------------------------------------

class TestEvidenceAndProvenance:
    @pytest.mark.asyncio
    async def test_add_edge_evidence(self, store):
        n1 = kg_node_id("company", "ev1")
        n2 = kg_node_id("signal", "ev2")
        await store.upsert_node(KGNode(id=n1, node_type="company", label="Ev1"))
        await store.upsert_node(KGNode(id=n2, node_type="signal", label="Ev2"))

        eid = kg_edge_id()
        await store.upsert_edge(KGEdge(id=eid, edge_type="detected_by",
                                        source_node_id=n1, target_node_id=n2))

        ev_id = await store.add_edge_evidence(eid, "signals", detail={"signal_id": 42})
        assert ev_id is not None

    @pytest.mark.asyncio
    async def test_log_provenance(self, store):
        run = await store.create_run()
        nid = kg_node_id("company", "prov")
        await store.upsert_node(KGNode(id=nid, node_type="company", label="Prov"))

        await store.log_provenance(
            run.run_id, "create", node_id=nid,
            detail={"source": "entity_aliases"},
        )

        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM kg_provenance WHERE run_id = ?",
            (run.run_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == 1


# ---------------------------------------------------------------------------
# Neighbors
# ---------------------------------------------------------------------------

class TestNeighbors:
    @pytest.mark.asyncio
    async def test_outgoing_neighbors(self, store):
        n1 = kg_node_id("company", "hub")
        n2 = kg_node_id("signal", "spoke1")
        n3 = kg_node_id("signal", "spoke2")
        await store.upsert_node(KGNode(id=n1, node_type="company", label="Hub"))
        await store.upsert_node(KGNode(id=n2, node_type="signal", label="Spoke1"))
        await store.upsert_node(KGNode(id=n3, node_type="signal", label="Spoke2"))

        await store.upsert_edge(KGEdge(id=kg_edge_id(), edge_type="detected_by",
                                        source_node_id=n1, target_node_id=n2))
        await store.upsert_edge(KGEdge(id=kg_edge_id(), edge_type="detected_by",
                                        source_node_id=n1, target_node_id=n3))

        neighbors = await store.get_neighbors(n1, direction="outgoing")
        assert len(neighbors) == 2
        ids = {nb["neighbor_id"] for nb in neighbors}
        assert n2 in ids
        assert n3 in ids

    @pytest.mark.asyncio
    async def test_incoming_neighbors(self, store):
        n1 = kg_node_id("company", "source")
        n2 = kg_node_id("signal", "target")
        await store.upsert_node(KGNode(id=n1, node_type="company", label="Source"))
        await store.upsert_node(KGNode(id=n2, node_type="signal", label="Target"))

        await store.upsert_edge(KGEdge(id=kg_edge_id(), edge_type="detected_by",
                                        source_node_id=n1, target_node_id=n2))

        neighbors = await store.get_neighbors(n2, direction="incoming")
        assert len(neighbors) == 1
        assert neighbors[0]["neighbor_id"] == n1

    @pytest.mark.asyncio
    async def test_neighbors_exclude_tombstoned(self, store):
        n1 = kg_node_id("company", "live")
        n2 = kg_node_id("company", "dead_nb")
        await store.upsert_node(KGNode(id=n1, node_type="company", label="Live"))
        await store.upsert_node(KGNode(id=n2, node_type="company", label="Dead"))

        await store.upsert_edge(KGEdge(id=kg_edge_id(), edge_type="merged_into",
                                        source_node_id=n1, target_node_id=n2))
        await store.tombstone_node(n2)

        neighbors = await store.get_neighbors(n1, direction="outgoing")
        assert len(neighbors) == 0

    @pytest.mark.asyncio
    async def test_neighbors_filter_by_edge_type(self, store):
        n1 = kg_node_id("company", "multi")
        n2 = kg_node_id("signal", "sig")
        n3 = kg_node_id("investor", "inv")
        await store.upsert_node(KGNode(id=n1, node_type="company", label="Multi"))
        await store.upsert_node(KGNode(id=n2, node_type="signal", label="Sig"))
        await store.upsert_node(KGNode(id=n3, node_type="investor", label="Inv"))

        await store.upsert_edge(KGEdge(id=kg_edge_id(), edge_type="detected_by",
                                        source_node_id=n1, target_node_id=n2))
        await store.upsert_edge(KGEdge(id=kg_edge_id(), edge_type="backed_by",
                                        source_node_id=n1, target_node_id=n3))

        neighbors = await store.get_neighbors(n1, edge_type="backed_by")
        assert len(neighbors) == 1
        assert neighbors[0]["neighbor_id"] == n3


# ---------------------------------------------------------------------------
# Schema constraint tests
# ---------------------------------------------------------------------------

class TestSchemaConstraints:
    @pytest.mark.asyncio
    async def test_invalid_node_type_rejected(self, kg_db):
        """CHECK constraint rejects invalid node_type."""
        with pytest.raises(Exception):
            await kg_db.execute("""
                INSERT INTO kg_nodes (id, node_type, is_tombstone, created_at, updated_at)
                VALUES ('bad', 'invalid_type', 0, '2026-01-01', '2026-01-01')
            """)

    @pytest.mark.asyncio
    async def test_invalid_edge_type_rejected(self, kg_db):
        """CHECK constraint rejects invalid edge_type."""
        # First create valid nodes
        await kg_db.execute("""
            INSERT INTO kg_nodes (id, node_type, is_tombstone, created_at, updated_at)
            VALUES ('n1', 'company', 0, '2026-01-01', '2026-01-01')
        """)
        await kg_db.execute("""
            INSERT INTO kg_nodes (id, node_type, is_tombstone, created_at, updated_at)
            VALUES ('n2', 'signal', 0, '2026-01-01', '2026-01-01')
        """)
        await kg_db.commit()

        with pytest.raises(Exception):
            await kg_db.execute("""
                INSERT INTO kg_edges (id, edge_type, source_node_id, target_node_id,
                                       is_directed, created_at, updated_at)
                VALUES ('e1', 'invalid_edge', 'n1', 'n2', 1, '2026-01-01', '2026-01-01')
            """)

    @pytest.mark.asyncio
    async def test_invalid_run_status_rejected(self, kg_db):
        """CHECK constraint rejects invalid run status."""
        with pytest.raises(Exception):
            await kg_db.execute("""
                INSERT INTO kg_runs (run_id, mode, started_at, status)
                VALUES ('bad_run', 'full', '2026-01-01', 'invalid')
            """)

    @pytest.mark.asyncio
    async def test_duplicate_source_unique_index(self, kg_db):
        """UNIQUE index on (source_table, source_id) prevents duplicates."""
        await kg_db.execute("""
            INSERT INTO kg_nodes (id, node_type, source_table, source_id,
                                   is_tombstone, created_at, updated_at)
            VALUES ('dup1', 'company', 'entity_aliases', 'ent1',
                    0, '2026-01-01', '2026-01-01')
        """)
        await kg_db.commit()

        with pytest.raises(Exception):
            await kg_db.execute("""
                INSERT INTO kg_nodes (id, node_type, source_table, source_id,
                                       is_tombstone, created_at, updated_at)
                VALUES ('dup2', 'company', 'entity_aliases', 'ent1',
                        0, '2026-01-01', '2026-01-01')
            """)

    @pytest.mark.asyncio
    async def test_natural_edge_unique_index(self, kg_db):
        """UNIQUE index on (edge_type, source, target) WHERE valid_until IS NULL."""
        await kg_db.execute("""
            INSERT INTO kg_nodes (id, node_type, is_tombstone, created_at, updated_at)
            VALUES ('na', 'company', 0, '2026-01-01', '2026-01-01')
        """)
        await kg_db.execute("""
            INSERT INTO kg_nodes (id, node_type, is_tombstone, created_at, updated_at)
            VALUES ('nb', 'signal', 0, '2026-01-01', '2026-01-01')
        """)
        await kg_db.execute("""
            INSERT INTO kg_edges (id, edge_type, source_node_id, target_node_id,
                                   is_directed, created_at, updated_at)
            VALUES ('e_nat1', 'detected_by', 'na', 'nb', 1, '2026-01-01', '2026-01-01')
        """)
        await kg_db.commit()

        with pytest.raises(Exception):
            await kg_db.execute("""
                INSERT INTO kg_edges (id, edge_type, source_node_id, target_node_id,
                                       is_directed, created_at, updated_at)
                VALUES ('e_nat2', 'detected_by', 'na', 'nb', 1, '2026-01-01', '2026-01-01')
            """)

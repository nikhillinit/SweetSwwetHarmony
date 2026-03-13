"""Tests for storage/kg_builder.py.

These tests focus on the current-repo, v50-compatible architecture materializer:
- it must populate architecture nodes/edges without introducing new enums
- it should be idempotent on repeated builds
- stale architecture rows should be tombstoned/expired on full recompute
- Step 4 subphases use intermediate nodes (not variant arrays)
- Feature flag nodes contain no current_value (env-independent)
- Edges carry provenance_type classification
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.kg_builder import (
    ARCH_EDGE_TYPE,
    ARCH_LAYER,
    ARCH_SOURCE_TABLE,
    REPO_ROOT,
    SUPPORTED_COLLECTOR_FILES,
    BuildSourcePayload,
    KGArchitectureBuilder,
)
from storage.kg_store import KGEdge, KGNode, KGStore
from storage.signal_store import SignalStore


@pytest_asyncio.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    try:
        yield s
    finally:
        await s.close()
        try:
            os.unlink(path)
        except OSError:
            pass


class TestKGArchitectureBuilder:
    async def test_build_materializes_v50_compatible_architecture_layer(self, store):
        builder = KGArchitectureBuilder(store._db)
        report = await builder.build()

        assert report.status == "completed"
        assert report.nodes_upserted > 50
        assert report.edges_upserted > 100
        assert report.details["collector_count"] == 17
        assert report.details["weighted_signal_types"] == 18

        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE source_table = ? AND is_tombstone = 0",
            (ARCH_SOURCE_TABLE,),
        )
        architecture_nodes = (await cursor.fetchone())[0]
        assert architecture_nodes >= 90

        cursor = await store._db.execute(
            """
            SELECT node_type,
                   json_extract(properties, '$.layer'),
                   json_extract(properties, '$.arch_kind')
            FROM kg_nodes
            WHERE id = ? AND is_tombstone = 0
            """,
            ("decision_gate:verification_gate",),
        )
        gate_row = await cursor.fetchone()
        assert gate_row == ("organization", ARCH_LAYER, "decision_gate")

        cursor = await store._db.execute(
            """
            SELECT node_type,
                   json_extract(properties, '$.layer'),
                   json_extract(properties, '$.arch_kind')
            FROM kg_nodes
            WHERE id = ? AND is_tombstone = 0
            """,
            ("collector:github",),
        )
        collector_row = await cursor.fetchone()
        assert collector_row == ("organization", ARCH_LAYER, "collector")

        cursor = await store._db.execute(
            """
            SELECT COUNT(*)
            FROM kg_edges
            WHERE valid_until IS NULL
              AND json_extract(properties, '$.layer') = ?
              AND edge_type = ?
            """,
            (ARCH_LAYER, ARCH_EDGE_TYPE),
        )
        live_arch_edges = (await cursor.fetchone())[0]
        assert live_arch_edges >= 150

        cursor = await store._db.execute(
            """
            SELECT COUNT(*)
            FROM kg_edges
            WHERE valid_until IS NULL
              AND json_extract(properties, '$.arch_edge_kind') = 'produces'
            """,
        )
        assert (await cursor.fetchone())[0] > 0

        cursor = await store._db.execute(
            """
            SELECT COUNT(*)
            FROM kg_edges
            WHERE valid_until IS NULL
              AND source_node_id = 'collector:github'
              AND target_node_id = 'pipeline_stage:collectors'
              AND json_extract(properties, '$.arch_edge_kind') = 'emits_to'
            """,
        )
        assert (await cursor.fetchone())[0] == 1

        cursor = await store._db.execute(
            """
            SELECT COUNT(*)
            FROM kg_edge_evidence
            WHERE edge_id IN (
                SELECT id
                FROM kg_edges
                WHERE valid_until IS NULL
                  AND json_extract(properties, '$.layer') = ?
            )
            """,
            (ARCH_LAYER,),
        )
        evidence_rows = (await cursor.fetchone())[0]
        assert evidence_rows >= live_arch_edges

    async def test_build_is_idempotent_for_live_architecture_counts(self, store):
        builder = KGArchitectureBuilder(store._db)
        first = await builder.build()
        second = await builder.build()

        assert first.status == "completed"
        assert second.status == "completed"
        assert second.nodes_tombstoned == 0
        assert second.edges_expired == 0

        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE source_table = ? AND is_tombstone = 0",
            (ARCH_SOURCE_TABLE,),
        )
        node_count_after_second = (await cursor.fetchone())[0]

        cursor = await store._db.execute(
            """
            SELECT COUNT(*)
            FROM kg_edges
            WHERE valid_until IS NULL
              AND json_extract(properties, '$.layer') = ?
            """,
            (ARCH_LAYER,),
        )
        edge_count_after_second = (await cursor.fetchone())[0]

        assert node_count_after_second >= 90
        assert edge_count_after_second >= 150

    async def test_build_tombstones_and_expires_stale_architecture_rows(self, store):
        kg = KGStore(store._db)
        stale_node = KGNode(
            id="organization:stale_component",
            node_type="organization",
            label="Stale Component",
            properties={"layer": ARCH_LAYER, "arch_kind": "component"},
            source_table=ARCH_SOURCE_TABLE,
            source_id="organization:stale_component",
        )
        await kg.upsert_node(stale_node)
        stale_edge = KGEdge(
            id="arch_edge:stale_component",
            edge_type=ARCH_EDGE_TYPE,
            source_node_id="organization:stale_component",
            target_node_id="sector:cpg",
            properties={"layer": ARCH_LAYER, "arch_edge_kind": "stale_test"},
        )
        await kg.upsert_edge(stale_edge)

        builder = KGArchitectureBuilder(store._db)
        report = await builder.build()

        assert report.nodes_tombstoned >= 1
        assert report.edges_expired >= 1

        cursor = await store._db.execute(
            "SELECT is_tombstone FROM kg_nodes WHERE id = 'organization:stale_component'"
        )
        assert (await cursor.fetchone())[0] == 1

        cursor = await store._db.execute(
            "SELECT valid_until FROM kg_edges WHERE id = 'arch_edge:stale_component'"
        )
        valid_until = (await cursor.fetchone())[0]
        assert valid_until is not None

    async def test_build_surfaces_current_signal_family_drift_warnings(self, store):
        builder = KGArchitectureBuilder(store._db)
        report = await builder.build()

        assert any("cofounder_search" in warning for warning in report.warnings)
        assert any("commit_spike" in warning for warning in report.warnings)

    # -- Test A: Step 4 subphase nodes (Mod 2) -------------------------

    async def test_step_4_subphases_create_intermediate_nodes_and_edges(self, store):
        """Step 4 unlocks use intermediate subphase nodes instead of variant arrays."""
        builder = KGArchitectureBuilder(store._db)
        await builder.build()

        # Assert subphase nodes exist
        for sub_id in ("activation_subphase:4A", "activation_subphase:4B"):
            cursor = await store._db.execute(
                """
                SELECT json_extract(properties, '$.arch_kind'),
                       json_extract(properties, '$.parent_step')
                FROM kg_nodes
                WHERE id = ? AND is_tombstone = 0
                """,
                (sub_id,),
            )
            row = await cursor.fetchone()
            assert row is not None, f"Subphase node {sub_id} not found"
            assert row[0] == "activation_subphase"
            assert row[1] == 4

        # Assert 2 distinct unlocks edges to MERGE_WRITES_ENABLED from subphases
        cursor = await store._db.execute(
            """
            SELECT source_node_id, json_extract(properties, '$.recommended_value')
            FROM kg_edges
            WHERE valid_until IS NULL
              AND json_extract(properties, '$.arch_edge_kind') = 'unlocks'
              AND target_node_id = 'feature_flag:MERGE_WRITES_ENABLED'
              AND source_node_id LIKE 'activation_subphase:4%'
            ORDER BY source_node_id
            """
        )
        rows = await cursor.fetchall()
        assert rows == [
            ("activation_subphase:4A", "shadow"),
            ("activation_subphase:4B", "active"),
        ]

        # Assert contains_subphase edges from step 4 to both subphases
        cursor = await store._db.execute(
            """
            SELECT target_node_id
            FROM kg_edges
            WHERE valid_until IS NULL
              AND source_node_id = 'activation_step:4'
              AND json_extract(properties, '$.arch_edge_kind') = 'contains_subphase'
            ORDER BY target_node_id
            """
        )
        targets = [row[0] for row in await cursor.fetchall()]
        assert "activation_subphase:4A" in targets
        assert "activation_subphase:4B" in targets

    # -- Test C: Provenance classification (Mod 6) -------------------------

    async def test_edges_carry_provenance_type_classification(self, store):
        """Edges from collector->signal_type have code_extracted provenance,
        while activation_step/subphase->feature_flag have curated_ontology."""
        builder = KGArchitectureBuilder(store._db)
        await builder.build()

        # Collector produces edges should be code_extracted
        cursor = await store._db.execute(
            """
            SELECT json_extract(properties, '$.provenance_type')
            FROM kg_edges
            WHERE valid_until IS NULL
              AND json_extract(properties, '$.arch_edge_kind') = 'produces'
            LIMIT 5
            """
        )
        rows = await cursor.fetchall()
        assert len(rows) > 0
        for row in rows:
            assert row[0] == "code_extracted"

        # Unlock edges should be curated_ontology
        cursor = await store._db.execute(
            """
            SELECT json_extract(properties, '$.provenance_type')
            FROM kg_edges
            WHERE valid_until IS NULL
              AND json_extract(properties, '$.arch_edge_kind') = 'unlocks'
            LIMIT 5
            """
        )
        rows = await cursor.fetchall()
        assert len(rows) > 0
        for row in rows:
            assert row[0] == "curated_ontology"

        # Gate edges should be curated_ontology
        cursor = await store._db.execute(
            """
            SELECT json_extract(properties, '$.provenance_type')
            FROM kg_edges
            WHERE valid_until IS NULL
              AND json_extract(properties, '$.arch_edge_kind') = 'gates'
            LIMIT 5
            """
        )
        rows = await cursor.fetchall()
        assert len(rows) > 0
        for row in rows:
            assert row[0] == "curated_ontology"

    async def test_build_report_includes_ontology_drift_warnings(self, store):
        """Build report contains ontology drift warning with curated/extracted counts."""
        builder = KGArchitectureBuilder(store._db)
        report = await builder.build()

        assert any("Ontology edge provenance" in w for w in report.warnings)

    # -- Test D: No current_value in architecture nodes (Mod 3) -----------

    async def test_feature_flag_nodes_do_not_contain_current_value(self, store):
        """Feature flag nodes must NOT include current_value in properties."""
        builder = KGArchitectureBuilder(store._db)
        await builder.build()

        cursor = await store._db.execute(
            """
            SELECT id, properties
            FROM kg_nodes
            WHERE is_tombstone = 0
              AND json_extract(properties, '$.arch_kind') = 'feature_flag'
            """
        )
        rows = await cursor.fetchall()
        assert len(rows) > 0
        for node_id, props_raw in rows:
            props = json.loads(props_raw)
            assert "current_value" not in props, (
                f"Node {node_id} still has current_value={props.get('current_value')}"
            )

    # -- Test B: Failed source cleanup (from fix bundle) -------------------

    async def test_extract_signal_types_captures_novel_literals_from_collector_source(self, store, monkeypatch):
        test_dir = REPO_ROOT / "tmp_kg_builder_tests"
        test_dir.mkdir(exist_ok=True)
        rel_path = Path("tmp_kg_builder_tests") / "novel_signal_collector.py"
        collector_path = REPO_ROOT / rel_path
        collector_path.write_text(
            """
from verification.verification_gate_v2 import Signal


def build_signal():
    return Signal(
        id="demo",
        signal_type="novel_launch_signal",
        confidence=0.8,
        source_api="demo",
    )


def _classify_signal_type(payload):
    if payload:
        return "fresh_signal_family"
    return "community_mention"


PAGE_TYPE_SIGNALS = {
    "pricing": "pricing_change",
    "launch": "launch_refresh",
}
            """.strip(),
            encoding="utf-8",
        )

        try:
            monkeypatch.setitem(SUPPORTED_COLLECTOR_FILES, "github", str(rel_path))

            builder = KGArchitectureBuilder(store._db)
            signal_types = builder._extract_signal_types_for_collector("github", known_signal_types={"community_mention"})

            assert "novel_launch_signal" in signal_types
            assert "fresh_signal_family" in signal_types
            assert "pricing_change" in signal_types
            assert "launch_refresh" in signal_types
        finally:
            try:
                collector_path.unlink()
            except FileNotFoundError:
                pass

    async def test_build_marks_failed_source_rows_when_a_payload_raises(self, store, monkeypatch):
        builder = KGArchitectureBuilder(store._db)

        broken_payload = BuildSourcePayload(
            source_name="feature_flags_and_delivery",
            nodes=[
                KGNode(
                    id="feature_flag:BROKEN",
                    node_type="not_a_valid_node_type",
                    label="BROKEN",
                    properties={"layer": ARCH_LAYER, "arch_kind": "feature_flag"},
                    source_table=ARCH_SOURCE_TABLE,
                    source_id="feature_flag:BROKEN",
                )
            ],
        )
        monkeypatch.setattr(builder, "_build_feature_payload", lambda: broken_payload)

        with pytest.raises(RuntimeError, match="KG architecture build failed"):
            await builder.build()

        cursor = await store._db.execute(
            """
            SELECT status, error_text
            FROM kg_run_sources
            WHERE source_name = 'feature_flags_and_delivery'
            ORDER BY id DESC
            LIMIT 1
            """
        )
        status, error_text = await cursor.fetchone()
        assert status == "failed"
        assert "CHECK constraint failed" in (error_text or "")

    async def test_builder_rejects_cross_checkout_repo_root(self, store, tmp_path):
        with pytest.raises(ValueError, match="Cross-checkout graph builds are not supported"):
            KGArchitectureBuilder(store._db, repo_root=tmp_path)


class TestKGStoreArchHelpers:
    """Test E: KGStore helper methods for architecture layer queries."""

    async def test_list_live_node_ids_by_source(self, store):
        kg = KGStore(store._db)
        # Seed test data
        await kg.upsert_node(KGNode(
            id="test:a", node_type="organization", label="A",
            source_table="test_source", source_id="test:a",
        ))
        await kg.upsert_node(KGNode(
            id="test:b", node_type="organization", label="B",
            source_table="test_source", source_id="test:b",
        ))
        await kg.upsert_node(KGNode(
            id="test:c", node_type="organization", label="C",
            source_table="other_source", source_id="test:c",
        ))
        # Tombstone one
        await kg.tombstone_node("test:b")

        ids = await kg.list_live_node_ids_by_source("test_source")
        assert ids == {"test:a"}

    async def test_list_live_edge_ids_by_layer(self, store):
        kg = KGStore(store._db)
        # Need nodes for edges
        await kg.upsert_node(KGNode(
            id="n1", node_type="organization", label="N1",
            source_table="test", source_id="n1",
        ))
        await kg.upsert_node(KGNode(
            id="n2", node_type="organization", label="N2",
            source_table="test", source_id="n2",
        ))
        # Seed edges
        await kg.upsert_edge(KGEdge(
            id="e1", edge_type="has_evidence",
            source_node_id="n1", target_node_id="n2",
            properties={"layer": "architecture"},
        ))
        await kg.upsert_edge(KGEdge(
            id="e2", edge_type="has_evidence",
            source_node_id="n2", target_node_id="n1",
            properties={"layer": "other_layer"},
        ))
        # Expire one
        await kg.expire_edge("e1")

        ids = await kg.list_live_edge_ids_by_layer("architecture")
        assert ids == set()  # e1 was expired

        ids = await kg.list_live_edge_ids_by_layer("other_layer")
        assert ids == {"e2"}

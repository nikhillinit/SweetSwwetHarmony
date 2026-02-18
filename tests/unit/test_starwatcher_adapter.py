"""Tests for the Starwatcher adapter contract.

Validates that the adapter correctly converts Discovery Engine data
into the Starwatcher v9.1.6 constellation schema, including status
mappings, node structure, layout determinism, and edge validation.
"""

from __future__ import annotations

import os
import sqlite3
import sys

# Ensure project root is importable
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest

from dashboard.adapters.starwatcher_adapter import (
    STATUS_ID_MAP,
    STATUS_LABEL_MAP,
    VALID_STATUS_IDS,
    CompanyNode,
    Connection,
    ConstellationProps,
    SignalEntry,
    build_constellation_props,
    compute_polar_position,
    derive_edges,
    to_status_id,
    to_status_label,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _create_test_db(db_path: str, signals=None, processing=None):
    """Create a minimal test database with signals and signal_processing tables.

    Args:
        db_path: Path for the SQLite file.
        signals: List of dicts with signal row data.
        processing: List of dicts with signal_processing row data.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY,
            signal_type TEXT,
            source_api TEXT,
            canonical_key TEXT,
            company_name TEXT,
            confidence REAL,
            raw_data TEXT,
            detected_at TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_processing (
            signal_id INTEGER PRIMARY KEY,
            processing_status TEXT,
            notion_page_id TEXT,
            processed_at TEXT,
            error_message TEXT
        )
    """)

    if signals:
        for sig in signals:
            conn.execute(
                """
                INSERT INTO signals
                    (id, signal_type, source_api, canonical_key,
                     company_name, confidence, raw_data, detected_at, created_at)
                VALUES (:id, :signal_type, :source_api, :canonical_key,
                        :company_name, :confidence, :raw_data,
                        :detected_at, :created_at)
                """,
                sig,
            )

    if processing:
        for proc in processing:
            conn.execute(
                """
                INSERT INTO signal_processing
                    (signal_id, processing_status, notion_page_id,
                     processed_at, error_message)
                VALUES (:signal_id, :processing_status, :notion_page_id,
                        :processed_at, :error_message)
                """,
                proc,
            )

    conn.commit()
    conn.close()


def _make_signal(
    sig_id=1,
    signal_type="trending_repo",
    source_api="github",
    canonical_key="domain:acme.ai",
    company_name="Acme Inc",
    confidence=0.75,
    detected_at="2026-02-01T12:00:00+00:00",
    created_at="2026-02-01T12:00:00+00:00",
    raw_data="{}",
):
    """Build a signal row dict with sensible defaults."""
    return {
        "id": sig_id,
        "signal_type": signal_type,
        "source_api": source_api,
        "canonical_key": canonical_key,
        "company_name": company_name,
        "confidence": confidence,
        "raw_data": raw_data,
        "detected_at": detected_at,
        "created_at": created_at,
    }


def _make_processing(signal_id=1, status="processed", notion_page_id=None):
    """Build a signal_processing row dict with sensible defaults."""
    return {
        "signal_id": signal_id,
        "processing_status": status,
        "notion_page_id": notion_page_id,
        "processed_at": "2026-02-01T12:30:00+00:00",
        "error_message": None,
    }


# ── Test 1: Status ID mapping for all labels ────────────────────────────────


class TestStatusIdMappingAllLabels:
    """Test that all 8 Notion labels plus the Dilligence typo variant map
    correctly via to_status_id()."""

    @pytest.mark.parametrize(
        "label, expected_id",
        [
            ("Source", "source"),
            ("Initial Meeting / Call", "initial_meeting"),
            ("Dilligence", "diligence"),
            ("Diligence", "diligence"),
            ("Tracking", "tracking"),
            ("Committed", "committed"),
            ("Funded", "funded"),
            ("Passed", "passed"),
            ("Lost", "lost"),
        ],
    )
    def test_label_maps_to_expected_id(self, label, expected_id):
        """Each Notion label should map to the correct stable internal ID."""
        assert to_status_id(label) == expected_id

    def test_dilligence_typo_matches_contract_spelling(self):
        """Both 'Dilligence' (Notion typo) and 'Diligence' (correct) map to
        the same internal ID."""
        assert to_status_id("Dilligence") == to_status_id("Diligence")

    def test_unknown_label_falls_back_to_source(self):
        """An unrecognized label should default to 'source'."""
        assert to_status_id("NonExistentStatus") == "source"
        assert to_status_id("") == "source"

    def test_all_map_entries_covered(self):
        """STATUS_ID_MAP should have exactly 9 entries (8 labels + typo)."""
        assert len(STATUS_ID_MAP) == 9


# ── Test 2: Status label reverse mapping ────────────────────────────────────


class TestStatusLabelReverseMapping:
    """Test that all 8 stable IDs map back to display labels via
    to_status_label()."""

    @pytest.mark.parametrize(
        "status_id, expected_label",
        [
            ("source", "Source"),
            ("initial_meeting", "Initial Meeting / Call"),
            ("diligence", "Diligence"),
            ("tracking", "Tracking"),
            ("committed", "Committed"),
            ("funded", "Funded"),
            ("passed", "Passed"),
            ("lost", "Lost"),
        ],
    )
    def test_id_maps_to_expected_label(self, status_id, expected_label):
        """Each stable ID should map back to its canonical display label."""
        assert to_status_label(status_id) == expected_label

    def test_unknown_id_falls_back_to_source(self):
        """An unrecognized ID should default to 'Source'."""
        assert to_status_label("nonexistent") == "Source"
        assert to_status_label("") == "Source"

    def test_reverse_map_has_eight_entries(self):
        """STATUS_LABEL_MAP should have exactly 8 entries (one per status)."""
        assert len(STATUS_LABEL_MAP) == 8

    def test_round_trip_for_all_labels(self):
        """label -> ID -> label should produce a canonical label for every
        entry in STATUS_ID_MAP."""
        for label, status_id in STATUS_ID_MAP.items():
            recovered = to_status_label(status_id)
            # The recovered label is the canonical form (e.g. "Diligence"
            # not "Dilligence"), which is expected.
            assert recovered == STATUS_LABEL_MAP[status_id]


# ── Test 3: CompanyNode required fields ──────────────────────────────────────


class TestCompanyNodeRequiredFields:
    """Test that a CompanyNode always has the required fields:
    id, name, posX, posY, thesisScore, status, thesisRationale, signals."""

    def test_minimal_node_has_all_required_fields(self):
        """A node constructed with only required args should expose all
        required attributes."""
        node = CompanyNode(
            id="node-1",
            name="Acme",
            posX=100.0,
            posY=200.0,
            thesisScore=0.8,
            status="source",
            thesisRationale="High confidence signal",
        )
        assert node.id == "node-1"
        assert node.name == "Acme"
        assert isinstance(node.posX, float)
        assert isinstance(node.posY, float)
        assert isinstance(node.thesisScore, float)
        assert node.status == "source"
        assert isinstance(node.thesisRationale, str)
        assert isinstance(node.signals, list)

    def test_signals_defaults_to_empty_list(self):
        """When signals is not provided, it should default to an empty list."""
        node = CompanyNode(
            id="node-2",
            name="Beta Corp",
            posX=0.0,
            posY=0.0,
            thesisScore=0.5,
            status="tracking",
            thesisRationale="Moderate signal",
        )
        assert node.signals == []

    def test_node_from_db_has_all_fields(self, tmp_path):
        """A node built from the database via build_constellation_props
        should have all required fields populated."""
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path, signals=[_make_signal()])

        props = build_constellation_props(db_path=db_path)

        assert len(props.nodes) == 1
        node = props.nodes[0]
        assert node.id is not None and node.id != ""
        assert node.name is not None and node.name != ""
        assert isinstance(node.posX, float)
        assert isinstance(node.posY, float)
        assert isinstance(node.thesisScore, float)
        assert node.status in VALID_STATUS_IDS
        assert isinstance(node.thesisRationale, str) and len(node.thesisRationale) > 0
        assert isinstance(node.signals, list)


# ── Test 4: ConstellationProps required fields ───────────────────────────────


class TestConstellationPropsRequiredFields:
    """Test that ConstellationProps always has loadingState, error,
    fatalError, and emptyState (even when None)."""

    def test_default_construction_has_required_fields(self):
        """A default ConstellationProps should expose all four fields."""
        props = ConstellationProps(nodes=[], edges=[])
        assert hasattr(props, "loadingState")
        assert hasattr(props, "error")
        assert hasattr(props, "fatalError")
        assert hasattr(props, "emptyState")

    def test_default_values_are_correct(self):
        """Default values should be idle/None/None/None."""
        props = ConstellationProps(nodes=[], edges=[])
        assert props.loadingState == "idle"
        assert props.error is None
        assert props.fatalError is None
        assert props.emptyState is None

    def test_from_empty_db(self, tmp_path):
        """When the database has no signals, the props should still carry
        all four required fields."""
        db_path = str(tmp_path / "empty.db")
        _create_test_db(db_path)  # tables exist but no rows

        props = build_constellation_props(db_path=db_path)

        assert props.loadingState == "idle"
        assert props.error is None
        assert props.fatalError is None
        # emptyState should be set since there are no signals
        assert props.emptyState is not None

    def test_from_missing_db(self, tmp_path):
        """When the database file does not exist, props should still carry
        all four fields."""
        db_path = str(tmp_path / "nonexistent.db")

        props = build_constellation_props(db_path=db_path)

        assert props.loadingState == "idle"
        assert props.error is None
        assert props.fatalError is None
        assert props.emptyState is not None


# ── Test 5: Empty state logic ────────────────────────────────────────────────


class TestEmptyStateLogic:
    """Test that emptyState is populated when no nodes exist and None when
    nodes are present."""

    def test_empty_state_when_no_signals(self, tmp_path):
        """emptyState should be a dict with type/title/message when the
        database has no signals."""
        db_path = str(tmp_path / "empty.db")
        _create_test_db(db_path)

        props = build_constellation_props(db_path=db_path)

        assert props.nodes == []
        assert props.emptyState is not None
        assert "type" in props.emptyState
        assert "title" in props.emptyState
        assert "message" in props.emptyState

    def test_empty_state_when_db_missing(self, tmp_path):
        """emptyState should describe the missing database."""
        db_path = str(tmp_path / "missing.db")

        props = build_constellation_props(db_path=db_path)

        assert props.emptyState is not None
        assert props.emptyState["type"] == "initial"

    def test_no_empty_state_when_signals_exist(self, tmp_path):
        """emptyState should be None when nodes are present."""
        db_path = str(tmp_path / "populated.db")
        _create_test_db(db_path, signals=[_make_signal()])

        props = build_constellation_props(db_path=db_path)

        assert len(props.nodes) > 0
        assert props.emptyState is None


# ── Test 6: Thesis score bounds ──────────────────────────────────────────────


class TestThesisScoreBounds:
    """Test that thesisScore is always clamped to [0.0, 1.0]."""

    @pytest.mark.parametrize(
        "raw_confidence, expected_clamped",
        [
            (-0.5, 0.0),
            (0.0, 0.0),
            (0.5, 0.5),
            (1.0, 1.0),
            (1.5, 1.0),
        ],
    )
    def test_thesis_score_clamped_via_db(self, tmp_path, raw_confidence, expected_clamped):
        """thesisScore built from DB confidence should be clamped to [0.0, 1.0]."""
        db_path = str(tmp_path / "score_test.db")
        _create_test_db(
            db_path,
            signals=[_make_signal(confidence=raw_confidence)],
        )

        props = build_constellation_props(db_path=db_path)

        assert len(props.nodes) == 1
        score = props.nodes[0].thesisScore
        assert 0.0 <= score <= 1.0, f"Score {score} is out of [0.0, 1.0]"
        assert score == pytest.approx(expected_clamped, abs=1e-4)

    def test_compute_polar_clamps_internally(self):
        """compute_polar_position should also clamp the thesis score it uses
        for radius calculation."""
        # Negative score
        px1, py1 = compute_polar_position("source", -0.5, "key:a")
        # Score of 0.0 should give same position as negative
        px2, py2 = compute_polar_position("source", 0.0, "key:a")
        assert px1 == pytest.approx(px2, abs=1e-6)
        assert py1 == pytest.approx(py2, abs=1e-6)

        # Score above 1.0
        px3, py3 = compute_polar_position("source", 1.5, "key:b")
        px4, py4 = compute_polar_position("source", 1.0, "key:b")
        assert px3 == pytest.approx(px4, abs=1e-6)
        assert py3 == pytest.approx(py4, abs=1e-6)


# ── Test 7: Status values only valid ─────────────────────────────────────────


class TestStatusValuesOnlyValid:
    """Test that the status field only contains valid PipelineStatusId strings
    from VALID_STATUS_IDS."""

    def test_valid_status_ids_contents(self):
        """VALID_STATUS_IDS should contain exactly the 8 expected IDs."""
        expected = {
            "source",
            "initial_meeting",
            "diligence",
            "tracking",
            "committed",
            "funded",
            "passed",
            "lost",
        }
        assert VALID_STATUS_IDS == expected

    def test_node_status_from_db_is_valid(self, tmp_path):
        """Every node built from the database should have a status in
        VALID_STATUS_IDS."""
        db_path = str(tmp_path / "status_test.db")
        signals = [
            _make_signal(sig_id=1, canonical_key="domain:alpha.com", company_name="Alpha"),
            _make_signal(sig_id=2, canonical_key="domain:beta.com", company_name="Beta"),
            _make_signal(sig_id=3, canonical_key="domain:gamma.com", company_name="Gamma"),
        ]
        _create_test_db(db_path, signals=signals)

        props = build_constellation_props(db_path=db_path)

        for node in props.nodes:
            assert node.status in VALID_STATUS_IDS, (
                f"Node '{node.name}' has invalid status '{node.status}'"
            )

    def test_all_status_id_map_values_are_valid(self):
        """Every value produced by STATUS_ID_MAP should be in VALID_STATUS_IDS."""
        for label, status_id in STATUS_ID_MAP.items():
            assert status_id in VALID_STATUS_IDS, (
                f"STATUS_ID_MAP['{label}'] = '{status_id}' not in VALID_STATUS_IDS"
            )

    def test_default_fallback_is_valid(self):
        """The fallback value returned by to_status_id for unknown labels
        should also be a valid status ID."""
        fallback = to_status_id("UnknownLabel")
        assert fallback in VALID_STATUS_IDS


# ── Test 8: Layout determinism ───────────────────────────────────────────────


class TestLayoutDeterminism:
    """Test that the same input produces the same (posX, posY) coordinates
    across multiple calls."""

    def test_compute_polar_position_deterministic(self):
        """Calling compute_polar_position with the same args multiple times
        should always produce the same result."""
        args = ("tracking", 0.65, "domain:example.com")
        results = [compute_polar_position(*args) for _ in range(10)]

        first = results[0]
        for r in results[1:]:
            assert r[0] == pytest.approx(first[0], abs=1e-10)
            assert r[1] == pytest.approx(first[1], abs=1e-10)

    def test_build_constellation_deterministic(self, tmp_path):
        """Building the constellation from the same database twice should
        produce identical node positions."""
        db_path = str(tmp_path / "determinism.db")
        signals = [
            _make_signal(sig_id=1, canonical_key="domain:acme.ai", company_name="Acme"),
            _make_signal(
                sig_id=2,
                canonical_key="domain:beta.io",
                company_name="Beta",
                confidence=0.4,
            ),
        ]
        _create_test_db(db_path, signals=signals)

        props1 = build_constellation_props(db_path=db_path)
        props2 = build_constellation_props(db_path=db_path)

        assert len(props1.nodes) == len(props2.nodes)
        for n1, n2 in zip(
            sorted(props1.nodes, key=lambda n: n.id),
            sorted(props2.nodes, key=lambda n: n.id),
        ):
            assert n1.posX == pytest.approx(n2.posX, abs=1e-10)
            assert n1.posY == pytest.approx(n2.posY, abs=1e-10)

    def test_different_keys_produce_different_positions(self):
        """Different canonical keys in the same sector should produce
        different angular positions (via deterministic hash jitter)."""
        pos_a = compute_polar_position("source", 0.5, "domain:alpha.com")
        pos_b = compute_polar_position("source", 0.5, "domain:beta.com")

        # They should differ (extremely unlikely for SHA256 to collide)
        assert pos_a != pos_b


# ── Test 9: Edge validation ──────────────────────────────────────────────────


class TestEdgeValidation:
    """Test that all edge source/target IDs exist in the nodes list."""

    def test_edges_reference_existing_nodes(self, tmp_path):
        """Every edge source and target should correspond to a node ID in
        the constellation."""
        db_path = str(tmp_path / "edges.db")
        now = "2026-02-01T12:00:00+00:00"
        one_hour_later = "2026-02-01T13:00:00+00:00"

        signals = [
            _make_signal(
                sig_id=1,
                canonical_key="domain:acme.ai",
                company_name="Acme",
                source_api="github",
                detected_at=now,
            ),
            _make_signal(
                sig_id=2,
                canonical_key="domain:beta.io",
                company_name="Beta",
                source_api="github",
                detected_at=one_hour_later,
            ),
        ]
        _create_test_db(db_path, signals=signals)

        props = build_constellation_props(db_path=db_path)
        node_ids = {node.id for node in props.nodes}

        for edge in props.edges:
            assert edge.source in node_ids, (
                f"Edge source '{edge.source}' not found in node IDs: {node_ids}"
            )
            assert edge.target in node_ids, (
                f"Edge target '{edge.target}' not found in node IDs: {node_ids}"
            )

    def test_derive_edges_only_uses_existing_node_ids(self):
        """derive_edges should only produce edges between nodes that are
        in the provided nodes list."""
        node_a = CompanyNode(
            id="node-a", name="A", posX=0, posY=0,
            thesisScore=0.5, status="source", thesisRationale="test",
        )
        node_b = CompanyNode(
            id="node-b", name="B", posX=100, posY=100,
            thesisScore=0.6, status="tracking", thesisRationale="test",
        )

        signals_by_company = {
            "node-a": [
                {"source_api": "github", "detected_at": "2026-02-01T10:00:00+00:00"},
            ],
            "node-b": [
                {"source_api": "github", "detected_at": "2026-02-01T11:00:00+00:00"},
            ],
            "node-c": [  # node-c does NOT exist in the nodes list
                {"source_api": "github", "detected_at": "2026-02-01T10:30:00+00:00"},
            ],
        }

        edges = derive_edges([node_a, node_b], signals_by_company)
        node_ids = {"node-a", "node-b"}

        for edge in edges:
            assert edge.source in node_ids
            assert edge.target in node_ids

    def test_no_edges_when_no_shared_sources(self, tmp_path):
        """When companies do not share a signal source, there should be
        no edges."""
        db_path = str(tmp_path / "no_edges.db")
        signals = [
            _make_signal(
                sig_id=1,
                canonical_key="domain:acme.ai",
                company_name="Acme",
                source_api="github",
                detected_at="2026-02-01T10:00:00+00:00",
            ),
            _make_signal(
                sig_id=2,
                canonical_key="domain:beta.io",
                company_name="Beta",
                source_api="sec_edgar",
                detected_at="2026-02-01T10:00:00+00:00",
            ),
        ]
        _create_test_db(db_path, signals=signals)

        props = build_constellation_props(db_path=db_path)

        assert len(props.edges) == 0

    def test_no_edges_when_temporal_gap_exceeds_24h(self, tmp_path):
        """When companies share a source but signals are more than 24h apart,
        no edge should be created."""
        db_path = str(tmp_path / "temporal_gap.db")
        signals = [
            _make_signal(
                sig_id=1,
                canonical_key="domain:acme.ai",
                company_name="Acme",
                source_api="github",
                detected_at="2026-02-01T10:00:00+00:00",
            ),
            _make_signal(
                sig_id=2,
                canonical_key="domain:beta.io",
                company_name="Beta",
                source_api="github",
                detected_at="2026-02-03T10:00:00+00:00",  # 48h later
            ),
        ]
        _create_test_db(db_path, signals=signals)

        props = build_constellation_props(db_path=db_path)

        assert len(props.edges) == 0

    def test_edge_strength_bounds(self, tmp_path):
        """Edge strength should be between 0.1 and 1.0."""
        db_path = str(tmp_path / "strength.db")
        signals = [
            _make_signal(
                sig_id=1,
                canonical_key="domain:acme.ai",
                company_name="Acme",
                source_api="github",
                detected_at="2026-02-01T12:00:00+00:00",
            ),
            _make_signal(
                sig_id=2,
                canonical_key="domain:beta.io",
                company_name="Beta",
                source_api="github",
                detected_at="2026-02-01T13:00:00+00:00",
            ),
        ]
        _create_test_db(db_path, signals=signals)

        props = build_constellation_props(db_path=db_path)

        for edge in props.edges:
            assert 0.1 <= edge.strength <= 1.0, (
                f"Edge strength {edge.strength} out of [0.1, 1.0]"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

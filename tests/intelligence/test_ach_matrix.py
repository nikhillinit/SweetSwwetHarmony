"""
Tests for the Deterministic ACH Engine.

Covers: hypothesis catalog, rubric, canonical hash, builder,
reproducibility, scenario tests, recency buckets.
"""

import asyncio
import hashlib
import json

import aiosqlite
import pytest

from intelligence.ach_matrix import (
    ACHBuilder,
    ACHMatrix,
    BUILDER_VERSION,
    CellScore,
    EVIDENCE_DEFS,
    EvidenceItem,
    HYPOTHESES,
    HYPOTHESIS_IDS,
    RUBRIC_VERSION,
    _apply_rubric,
    _normalize,
    _recency_bucket,
    compute_inputs_hash,
    get_latest_ach,
    store_ach_analysis,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db():
    """In-memory SQLite database with required schema."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=OFF")

    # Create required tables
    await conn.executescript("""
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL,
            source_api TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            company_name TEXT,
            company_id TEXT,
            confidence REAL NOT NULL,
            raw_data TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE thesis_classifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_key TEXT NOT NULL,
            signal_id INTEGER,
            keyword_score REAL,
            llm_score REAL,
            category TEXT,
            rationale TEXT,
            stage_estimate TEXT,
            negative_keywords INTEGER,
            competitor_flag INTEGER,
            classified_at TEXT NOT NULL
        );

        CREATE TABLE precedents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT NOT NULL,
            human_label TEXT NOT NULL,
            similarity REAL
        );

        CREATE TABLE thesis_exemplars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_key TEXT NOT NULL,
            exemplar_key TEXT,
            category TEXT,
            similarity_score REAL,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE ach_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT NOT NULL,
            review_id INTEGER,
            builder_version TEXT NOT NULL,
            rubric_version TEXT NOT NULL,
            inputs_hash TEXT NOT NULL,
            matrix_json TEXT NOT NULL,
            top_hypothesis TEXT,
            top_score REAL,
            bull_summary TEXT,
            bear_summary TEXT,
            differentiator_count INTEGER DEFAULT 0,
            evidence_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(review_id) REFERENCES review_items(id) ON DELETE SET NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_ach_cache_identity
            ON ach_analyses(company_id, builder_version, rubric_version, inputs_hash);

        CREATE INDEX IF NOT EXISTS idx_ach_review
            ON ach_analyses(review_id);

        CREATE INDEX IF NOT EXISTS idx_ach_latest
            ON ach_analyses(company_id, created_at DESC, id DESC);
    """)
    yield conn
    await conn.close()


async def _seed_company(db, company_id="comp_abc", canonical_key="domain:test.ai",
                        confidence=0.8, source_api="github", detected_at="2026-01-15T00:00:00Z"):
    """Seed a signal for a company."""
    await db.execute(
        """INSERT INTO signals (signal_type, source_api, canonical_key, company_name,
                               company_id, confidence, raw_data, detected_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("github_spike", source_api, canonical_key, "Test Co",
         company_id, confidence, '{}', detected_at, "2026-01-15T00:00:00Z"),
    )
    await db.commit()


async def _seed_thesis(db, canonical_key="domain:test.ai", keyword_score=0.8,
                       llm_score=0.75, category="consumer_cpg", rationale="Good fit",
                       stage="seed", neg_kw=0, competitor=0):
    """Seed a thesis classification."""
    await db.execute(
        """INSERT INTO thesis_classifications
           (canonical_key, keyword_score, llm_score, category, rationale,
            stage_estimate, negative_keywords, competitor_flag, classified_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (canonical_key, keyword_score, llm_score, category, rationale,
         stage, neg_kw, competitor, "2026-01-15T00:00:00Z"),
    )
    await db.commit()


# =============================================================================
# HYPOTHESIS CATALOG TESTS
# =============================================================================

class TestHypothesisCatalog:
    def test_five_hypotheses(self):
        assert len(HYPOTHESES) == 5

    def test_unique_ids(self):
        ids = [h.id for h in HYPOTHESES]
        assert len(ids) == len(set(ids))

    def test_ids_match_constant(self):
        assert HYPOTHESIS_IDS == [h.id for h in HYPOTHESES]

    def test_all_have_descriptions(self):
        for h in HYPOTHESES:
            assert h.label
            assert h.description


# =============================================================================
# RUBRIC TESTS
# =============================================================================

class TestRubric:
    def test_fourteen_evidence_types(self):
        assert len(EVIDENCE_DEFS) == 14

    def test_rubric_covers_all_evidence(self):
        """Every evidence type maps to cells for all hypotheses."""
        evidence = [
            EvidenceItem("E1", "kw", 0.8),
            EvidenceItem("E2", "llm", 0.7),
            EvidenceItem("E3", "cat", "consumer_cpg"),
            EvidenceItem("E4", "comp", True),
            EvidenceItem("E5", "tp_sim", 0.75),
            EvidenceItem("E6", "fp_sim", 0.3),
            EvidenceItem("E7", "src_cnt", 3),
            EvidenceItem("E8", "multi", True),
            EvidenceItem("E9", "stage", "seed"),
            EvidenceItem("E10", "neg_kw", False),
            EvidenceItem("E11", "exemp", 0.8),
            EvidenceItem("E12", "max_conf", 0.85),
            EvidenceItem("E13", "recency", "recent"),
            EvidenceItem("E14", "rationale", True),
        ]
        cells = _apply_rubric(evidence)
        # 14 evidence x 5 hypotheses = 70 cells
        assert len(cells) == 70

    def test_valid_cell_scores(self):
        """All cell scores are valid CellScore values or None."""
        evidence = [
            EvidenceItem("E1", "kw", 0.8),
            EvidenceItem("E12", "conf", 0.5),
        ]
        # Fill missing evidence as unavailable
        all_ids = set(EVIDENCE_DEFS.keys())
        present_ids = {e.evidence_id for e in evidence}
        for eid in all_ids - present_ids:
            evidence.append(EvidenceItem(eid, EVIDENCE_DEFS[eid], None, available=False))

        cells = _apply_rubric(evidence)
        for cell in cells:
            assert cell.score is None or cell.score in (
                CellScore.CONSISTENT,
                CellScore.INCONSISTENT,
                CellScore.NEUTRAL,
            )


# =============================================================================
# CANONICAL HASH TESTS
# =============================================================================

class TestCanonicalHash:
    def test_same_evidence_same_hash(self):
        ev = [
            EvidenceItem("E1", "kw", 0.8),
            EvidenceItem("E2", "llm", 0.7),
        ]
        h1 = compute_inputs_hash(ev, "1.0.0", "1.0.0")
        h2 = compute_inputs_hash(ev, "1.0.0", "1.0.0")
        assert h1 == h2

    def test_different_evidence_different_hash(self):
        ev1 = [EvidenceItem("E1", "kw", 0.8)]
        ev2 = [EvidenceItem("E1", "kw", 0.5)]
        h1 = compute_inputs_hash(ev1, "1.0.0", "1.0.0")
        h2 = compute_inputs_hash(ev2, "1.0.0", "1.0.0")
        assert h1 != h2

    def test_different_versions_different_hash(self):
        ev = [EvidenceItem("E1", "kw", 0.8)]
        h1 = compute_inputs_hash(ev, "1.0.0", "1.0.0")
        h2 = compute_inputs_hash(ev, "1.0.1", "1.0.0")
        assert h1 != h2

    def test_float_normalization(self):
        """Floats are rounded to 6 decimal places for stability."""
        ev1 = [EvidenceItem("E1", "kw", 0.8000001)]
        ev2 = [EvidenceItem("E1", "kw", 0.8000002)]
        h1 = compute_inputs_hash(ev1, "1.0.0", "1.0.0")
        h2 = compute_inputs_hash(ev2, "1.0.0", "1.0.0")
        # Both round to 0.8 at 6dp
        assert h1 == h2

    def test_null_handling(self):
        """None values hash consistently."""
        ev = [EvidenceItem("E1", "kw", None)]
        h1 = compute_inputs_hash(ev, "1.0.0", "1.0.0")
        h2 = compute_inputs_hash(ev, "1.0.0", "1.0.0")
        assert h1 == h2

    def test_hash_is_16_chars(self):
        ev = [EvidenceItem("E1", "kw", 0.8)]
        h = compute_inputs_hash(ev, "1.0.0", "1.0.0")
        assert len(h) == 16

    def test_evidence_order_independent(self):
        """Hash is the same regardless of evidence order."""
        ev1 = [EvidenceItem("E2", "llm", 0.7), EvidenceItem("E1", "kw", 0.8)]
        ev2 = [EvidenceItem("E1", "kw", 0.8), EvidenceItem("E2", "llm", 0.7)]
        h1 = compute_inputs_hash(ev1, "1.0.0", "1.0.0")
        h2 = compute_inputs_hash(ev2, "1.0.0", "1.0.0")
        assert h1 == h2


# =============================================================================
# BUILDER TESTS
# =============================================================================

class TestACHBuilder:
    @pytest.mark.asyncio
    async def test_build_with_full_evidence(self, db):
        """Builder produces valid matrix with all evidence available."""
        await _seed_company(db)
        await _seed_thesis(db)

        builder = ACHBuilder()
        matrix = await builder.build("comp_abc", db)

        assert matrix.company_id == "comp_abc"
        assert len(matrix.hypotheses) == 5
        assert len(matrix.evidence) == 14
        assert len(matrix.cells) == 70  # 14 x 5
        assert matrix.builder_version == BUILDER_VERSION
        assert matrix.rubric_version == RUBRIC_VERSION
        assert matrix.inputs_hash
        assert matrix.top_hypothesis in HYPOTHESIS_IDS

    @pytest.mark.asyncio
    async def test_build_with_no_data(self, db):
        """Builder handles company with no signals gracefully."""
        builder = ACHBuilder()
        matrix = await builder.build("nonexistent", db)

        assert matrix.company_id == "nonexistent"
        # E7 (source_count=0) and E8 (multi_source=False) are still "available"
        # because COUNT(*) returns 0, not NULL — these produce valid evidence
        assert matrix.evidence_count <= 2
        # Most evidence should be NOT_AVAILABLE
        unavailable = sum(1 for e in matrix.evidence if not e.available)
        assert unavailable >= 12

    @pytest.mark.asyncio
    async def test_build_with_partial_evidence(self, db):
        """Builder handles company with signals but no thesis."""
        await _seed_company(db)
        # No thesis classification seeded

        builder = ACHBuilder()
        matrix = await builder.build("comp_abc", db)

        # Should have some evidence (signal-derived) but not thesis-derived
        assert matrix.evidence_count > 0
        assert matrix.evidence_count < 14

    @pytest.mark.asyncio
    async def test_reproducibility(self, db):
        """Same DB state produces identical matrix and hash."""
        await _seed_company(db)
        await _seed_thesis(db)

        builder = ACHBuilder()
        m1 = await builder.build("comp_abc", db)
        m2 = await builder.build("comp_abc", db)

        assert m1.inputs_hash == m2.inputs_hash
        assert m1.top_hypothesis == m2.top_hypothesis
        assert m1.top_score == m2.top_score
        assert m1.hypothesis_scores == m2.hypothesis_scores


# =============================================================================
# SCENARIO TESTS
# =============================================================================

class TestACHScenarios:
    @pytest.mark.asyncio
    async def test_strong_fit_company_h1_wins(self, db):
        """Strong consumer fit → H1 should score highest."""
        await _seed_company(db, confidence=0.85)
        await _seed_thesis(db, keyword_score=0.85, llm_score=0.8,
                          category="consumer_cpg", stage="seed")

        builder = ACHBuilder()
        matrix = await builder.build("comp_abc", db)

        assert matrix.top_hypothesis == "H1"
        assert matrix.hypothesis_scores["H1"] > matrix.hypothesis_scores["H3"]

    @pytest.mark.asyncio
    async def test_b2b_disguise_h3_wins(self, db):
        """B2B appearing as consumer → H3 should score high."""
        await _seed_company(db, confidence=0.5)
        await _seed_thesis(db, keyword_score=0.3, llm_score=0.2,
                          category="b2b", stage="seed", neg_kw=1)

        builder = ACHBuilder()
        matrix = await builder.build("comp_abc", db)

        assert matrix.hypothesis_scores["H3"] > matrix.hypothesis_scores["H1"]

    @pytest.mark.asyncio
    async def test_too_early_h4_wins(self, db):
        """No traction, single source → H4 should score high."""
        await _seed_company(db, confidence=0.3)
        # No thesis, no case law, single source

        builder = ACHBuilder()
        matrix = await builder.build("comp_abc", db)

        assert matrix.hypothesis_scores["H4"] >= matrix.hypothesis_scores["H1"]

    @pytest.mark.asyncio
    async def test_funded_series_b_h5_wins(self, db):
        """Series B+ company → H5 should score high."""
        await _seed_company(db, confidence=0.7)
        await _seed_thesis(db, keyword_score=0.7, llm_score=0.6,
                          category="consumer_cpg", stage="series b")

        builder = ACHBuilder()
        matrix = await builder.build("comp_abc", db)

        assert matrix.hypothesis_scores["H5"] > 0


# =============================================================================
# RECENCY BUCKET TESTS
# =============================================================================

class TestRecencyBuckets:
    def test_recent_bucket(self):
        """Within 30 days → recent."""
        assert _recency_bucket("2026-01-01T00:00:00Z", "2026-01-20T00:00:00Z") == "recent"

    def test_established_bucket(self):
        """30-90 days → established."""
        assert _recency_bucket("2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z") == "established"

    def test_mature_bucket(self):
        """90+ days → mature."""
        assert _recency_bucket("2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z") == "mature"

    def test_none_inputs(self):
        assert _recency_bucket(None, "2026-01-01T00:00:00Z") is None
        assert _recency_bucket("2026-01-01T00:00:00Z", None) is None

    def test_relative_to_company_timeline(self):
        """Bucket is relative to company's own timeline, not wall-clock."""
        # Same delta = same bucket, regardless of absolute dates
        b1 = _recency_bucket("2025-06-01T00:00:00Z", "2025-06-15T00:00:00Z")
        b2 = _recency_bucket("2026-01-01T00:00:00Z", "2026-01-15T00:00:00Z")
        assert b1 == b2 == "recent"


# =============================================================================
# STORAGE TESTS
# =============================================================================

class TestACHStorage:
    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, db):
        """Store ACH analysis and retrieve it."""
        await _seed_company(db)
        await _seed_thesis(db)

        builder = ACHBuilder()
        matrix = await builder.build("comp_abc", db)

        ach_id = await store_ach_analysis(db, matrix, review_id=None)
        assert ach_id > 0

        latest = await get_latest_ach(db, "comp_abc")
        assert latest is not None
        assert latest["company_id"] == "comp_abc"
        assert latest["inputs_hash"] == matrix.inputs_hash

    @pytest.mark.asyncio
    async def test_duplicate_insert_ignored(self, db):
        """INSERT OR IGNORE prevents duplicate analyses."""
        await _seed_company(db)
        await _seed_thesis(db)

        builder = ACHBuilder()
        matrix = await builder.build("comp_abc", db)

        id1 = await store_ach_analysis(db, matrix)
        id2 = await store_ach_analysis(db, matrix)
        assert id1 == id2  # Same row returned

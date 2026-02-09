"""
Tests for the Tribunal Narrator.

Covers: bull case, bear case, differentiators, summary,
citation integrity, no fabricated citations.
"""

import re

import pytest

from intelligence.ach_matrix import (
    ACHCell,
    ACHMatrix,
    CellScore,
    EvidenceItem,
    HYPOTHESES,
    HYPOTHESIS_IDS,
)
from intelligence.tribunal import (
    Differentiator,
    TribunalSummary,
    find_differentiators,
    narrate_bear_case,
    narrate_bull_case,
    narrate_summary,
)


# =============================================================================
# HELPERS
# =============================================================================

def _make_matrix(
    evidence: list[EvidenceItem],
    cells: list[ACHCell],
    company_id: str = "comp_test",
    top_hypothesis: str = "H1",
    top_score: float = 3.0,
) -> ACHMatrix:
    """Build a minimal ACHMatrix for testing."""
    scores = {hid: 0.0 for hid in HYPOTHESIS_IDS}
    for c in cells:
        if c.score is not None:
            scores[c.hypothesis_id] += c.score
    return ACHMatrix(
        company_id=company_id,
        hypotheses=list(HYPOTHESES),
        evidence=evidence,
        cells=cells,
        hypothesis_scores=scores,
        top_hypothesis=top_hypothesis,
        top_score=top_score,
        inputs_hash="abcd1234abcd1234",
        builder_version="1.0.0",
        rubric_version="1.0.0",
        evidence_count=sum(1 for e in evidence if e.available),
    )


def _make_strong_fit_matrix() -> ACHMatrix:
    """A matrix where H1 is strongly supported."""
    evidence = [
        EvidenceItem("E1", "Keyword score", 0.85),
        EvidenceItem("E2", "LLM fit score", 0.8),
        EvidenceItem("E3", "LLM category", "consumer_cpg"),
        EvidenceItem("E7", "Distinct source count", 3),
        EvidenceItem("E8", "Multi-source flag", True),
        EvidenceItem("E12", "Max signal confidence", 0.85),
        EvidenceItem("E13", "Signal recency bucket", "recent"),
    ]
    C = CellScore.CONSISTENT
    I = CellScore.INCONSISTENT
    N = CellScore.NEUTRAL
    cells = [
        # E1: kw=0.85 → H1:C, H2:N, H3:I, H4:N, H5:N
        ACHCell("E1", "H1", C), ACHCell("E1", "H2", N), ACHCell("E1", "H3", I),
        ACHCell("E1", "H4", N), ACHCell("E1", "H5", N),
        # E2: llm=0.8 → H1:C, H2:N, H3:I, H4:N, H5:N
        ACHCell("E2", "H1", C), ACHCell("E2", "H2", N), ACHCell("E2", "H3", I),
        ACHCell("E2", "H4", N), ACHCell("E2", "H5", N),
        # E3: consumer_cpg → H1:C, H2:N, H3:I, H4:N, H5:N
        ACHCell("E3", "H1", C), ACHCell("E3", "H2", N), ACHCell("E3", "H3", I),
        ACHCell("E3", "H4", N), ACHCell("E3", "H5", N),
        # E7: 3 sources → H1:C, H2:N, H3:N, H4:I, H5:N
        ACHCell("E7", "H1", C), ACHCell("E7", "H2", N), ACHCell("E7", "H3", N),
        ACHCell("E7", "H4", I), ACHCell("E7", "H5", N),
        # E8: multi=True → H1:C, H2:N, H3:N, H4:I, H5:N
        ACHCell("E8", "H1", C), ACHCell("E8", "H2", N), ACHCell("E8", "H3", N),
        ACHCell("E8", "H4", I), ACHCell("E8", "H5", N),
        # E12: 0.85 → H1:C, H2:N, H3:N, H4:I, H5:N
        ACHCell("E12", "H1", C), ACHCell("E12", "H2", N), ACHCell("E12", "H3", N),
        ACHCell("E12", "H4", I), ACHCell("E12", "H5", N),
        # E13: recent → H1:C, H2:N, H3:N, H4:I, H5:N
        ACHCell("E13", "H1", C), ACHCell("E13", "H2", N), ACHCell("E13", "H3", N),
        ACHCell("E13", "H4", I), ACHCell("E13", "H5", N),
    ]
    return _make_matrix(evidence, cells)


def _make_b2b_disguise_matrix() -> ACHMatrix:
    """A matrix where H3 (B2B in Disguise) is supported."""
    evidence = [
        EvidenceItem("E1", "Keyword score", 0.3),
        EvidenceItem("E3", "LLM category", "b2b"),
        EvidenceItem("E10", "Negative keyword hit", True),
    ]
    C = CellScore.CONSISTENT
    I = CellScore.INCONSISTENT
    N = CellScore.NEUTRAL
    cells = [
        # E1: low kw → H1:I, H3:C
        ACHCell("E1", "H1", I), ACHCell("E1", "H2", N), ACHCell("E1", "H3", C),
        ACHCell("E1", "H4", N), ACHCell("E1", "H5", N),
        # E3: b2b → H1:I, H3:C
        ACHCell("E3", "H1", I), ACHCell("E3", "H2", N), ACHCell("E3", "H3", C),
        ACHCell("E3", "H4", N), ACHCell("E3", "H5", N),
        # E10: neg_kw → H1:I, H3:C
        ACHCell("E10", "H1", I), ACHCell("E10", "H2", N), ACHCell("E10", "H3", C),
        ACHCell("E10", "H4", N), ACHCell("E10", "H5", N),
    ]
    return _make_matrix(evidence, cells, top_hypothesis="H3", top_score=3.0)


def _extract_citations(text: str) -> set[str]:
    """Extract all [E{n}] citations from text."""
    return set(re.findall(r'\[E\d+\]', text))


# =============================================================================
# BULL CASE TESTS
# =============================================================================

class TestNarrateBullCase:
    def test_cites_evidence_ids(self):
        matrix = _make_strong_fit_matrix()
        bull = narrate_bull_case(matrix)
        citations = _extract_citations(bull)
        assert len(citations) > 0
        assert "[E1]" in citations

    def test_only_consistent_evidence(self):
        """Bull case only cites evidence consistent with H1."""
        matrix = _make_strong_fit_matrix()
        bull = narrate_bull_case(matrix)
        # All cited evidence should be consistent with H1
        assert "Bull case:" in bull

    def test_empty_when_no_consistent(self):
        """Bull case is empty when no evidence is consistent with H1."""
        evidence = [
            EvidenceItem("E1", "Keyword score", 0.2),
        ]
        cells = [
            ACHCell("E1", "H1", CellScore.INCONSISTENT),
            ACHCell("E1", "H2", CellScore.NEUTRAL),
            ACHCell("E1", "H3", CellScore.CONSISTENT),
            ACHCell("E1", "H4", CellScore.NEUTRAL),
            ACHCell("E1", "H5", CellScore.NEUTRAL),
        ]
        matrix = _make_matrix(evidence, cells)
        bull = narrate_bull_case(matrix)
        assert bull == ""

    def test_empty_with_empty_matrix(self):
        matrix = _make_matrix([], [])
        assert narrate_bull_case(matrix) == ""


# =============================================================================
# BEAR CASE TESTS
# =============================================================================

class TestNarrateBearCase:
    def test_cites_evidence_ids(self):
        matrix = _make_b2b_disguise_matrix()
        bear = narrate_bear_case(matrix)
        citations = _extract_citations(bear)
        assert len(citations) > 0

    def test_includes_inconsistent_and_competing(self):
        """Bear case cites evidence inconsistent with H1 or consistent with competing."""
        matrix = _make_b2b_disguise_matrix()
        bear = narrate_bear_case(matrix)
        assert "Bear case:" in bear

    def test_empty_when_no_concerns(self):
        """Bear case is empty when all evidence supports H1."""
        evidence = [
            EvidenceItem("E14", "Thesis rationale present", True),
        ]
        cells = [
            ACHCell("E14", "H1", CellScore.CONSISTENT),
            ACHCell("E14", "H2", CellScore.NEUTRAL),
            ACHCell("E14", "H3", CellScore.NEUTRAL),
            ACHCell("E14", "H4", CellScore.NEUTRAL),
            ACHCell("E14", "H5", CellScore.NEUTRAL),
        ]
        matrix = _make_matrix(evidence, cells)
        bear = narrate_bear_case(matrix)
        assert bear == ""

    def test_empty_with_empty_matrix(self):
        matrix = _make_matrix([], [])
        assert narrate_bear_case(matrix) == ""


# =============================================================================
# DIFFERENTIATOR TESTS
# =============================================================================

class TestFindDifferentiators:
    def test_correct_count(self):
        matrix = _make_strong_fit_matrix()
        diffs = find_differentiators(matrix)
        assert len(diffs) > 0

    def test_identifies_competing_hypotheses(self):
        """Differentiators identify which hypotheses are favored/opposed."""
        matrix = _make_strong_fit_matrix()
        diffs = find_differentiators(matrix)
        for d in diffs:
            assert len(d.favors) > 0
            assert len(d.opposes) > 0

    def test_empty_for_empty_matrix(self):
        matrix = _make_matrix([], [])
        assert find_differentiators(matrix) == []


# =============================================================================
# SUMMARY TESTS
# =============================================================================

class TestNarrateSummary:
    def test_all_fields_present(self):
        matrix = _make_strong_fit_matrix()
        summary = narrate_summary(matrix)
        assert isinstance(summary, TribunalSummary)
        assert summary.bull_summary
        assert summary.top_hypothesis
        assert summary.top_score is not None
        assert summary.differentiator_count >= 0

    def test_top_hypothesis_matches_matrix(self):
        matrix = _make_strong_fit_matrix()
        summary = narrate_summary(matrix)
        assert summary.top_hypothesis == matrix.top_hypothesis


# =============================================================================
# CITATION INTEGRITY TESTS
# =============================================================================

class TestCitationIntegrity:
    def test_bull_citations_map_to_available_evidence(self):
        """Every [E{n}] in bull output maps to available evidence."""
        matrix = _make_strong_fit_matrix()
        bull = narrate_bull_case(matrix)
        available_ids = {e.evidence_id for e in matrix.evidence if e.available}

        for match in re.findall(r'\[E(\d+)\]', bull):
            eid = f"E{match}"
            assert eid in available_ids, f"Citation [{eid}] not in available evidence"

    def test_bear_citations_map_to_available_evidence(self):
        """Every [E{n}] in bear output maps to available evidence."""
        matrix = _make_b2b_disguise_matrix()
        bear = narrate_bear_case(matrix)
        available_ids = {e.evidence_id for e in matrix.evidence if e.available}

        for match in re.findall(r'\[E(\d+)\]', bear):
            eid = f"E{match}"
            assert eid in available_ids, f"Citation [{eid}] not in available evidence"

    def test_no_fabricated_citations_bull(self):
        """No [E{n}] where evidence is NOT_AVAILABLE."""
        evidence = [
            EvidenceItem("E1", "Keyword score", 0.8),
            EvidenceItem("E5", "TP sim", None, available=False),
        ]
        cells = [
            ACHCell("E1", "H1", CellScore.CONSISTENT),
            ACHCell("E1", "H2", CellScore.NEUTRAL),
            ACHCell("E1", "H3", CellScore.INCONSISTENT),
            ACHCell("E1", "H4", CellScore.NEUTRAL),
            ACHCell("E1", "H5", CellScore.NEUTRAL),
            ACHCell("E5", "H1", None),
            ACHCell("E5", "H2", None),
            ACHCell("E5", "H3", None),
            ACHCell("E5", "H4", None),
            ACHCell("E5", "H5", None),
        ]
        matrix = _make_matrix(evidence, cells)
        bull = narrate_bull_case(matrix)
        # E5 should NOT appear since it's unavailable
        assert "[E5]" not in bull

    def test_no_citation_beyond_evidence_count(self):
        """No [E{n}] where n > number of evidence items."""
        matrix = _make_strong_fit_matrix()
        bull = narrate_bull_case(matrix)
        bear = narrate_bear_case(matrix)
        all_text = bull + bear

        for match in re.findall(r'\[E(\d+)\]', all_text):
            n = int(match)
            assert n <= 14, f"Citation [E{n}] exceeds evidence count"

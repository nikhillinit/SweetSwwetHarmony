"""
Tribunal Narrator — Template-based ACH narrative generation.

Produces bull/bear case narratives and differentiator analysis
from an ACH matrix. No LLM calls — purely template-based.

Citation invariant: every [E{n}] in output MUST map to an evidence
item with score != NOT_AVAILABLE in the matrix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from intelligence.ach_matrix import ACHMatrix, CellScore, HYPOTHESIS_IDS


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Differentiator:
    """An evidence item that distinguishes between hypotheses."""
    evidence_id: str
    evidence_label: str
    favors: list[str]     # Hypothesis IDs where CONSISTENT
    opposes: list[str]    # Hypothesis IDs where INCONSISTENT


@dataclass
class TribunalSummary:
    """Complete tribunal narrative output."""
    bull_summary: str
    bear_summary: str
    differentiators: list[Differentiator]
    differentiator_count: int
    top_hypothesis: Optional[str]
    top_score: Optional[float]


# =============================================================================
# EVIDENCE-SPECIFIC TEMPLATES
# =============================================================================

_EVIDENCE_TEMPLATES = {
    "E1": {
        "bull": "Keyword score of {value:.0%} strongly aligns with thesis [E1]",
        "bear": "Low keyword score of {value:.0%} suggests poor thesis alignment [E1]",
    },
    "E2": {
        "bull": "LLM classified with {value:.0%} confidence as thesis-fit [E2]",
        "bear": "LLM classified with only {value:.0%} confidence [E2]",
    },
    "E3": {
        "bull": "Categorized as {value} — core thesis category [E3]",
        "bear": "Categorized as {value} — outside core thesis [E3]",
    },
    "E4": {
        "bull": "Identified as competitor to known portfolio company [E4]",
        "bear": "No competitor relationship detected [E4]",
    },
    "E5": {
        "bull": "Matched case-law wins with {value:.0%} similarity [E5]",
        "bear": "Low similarity to known true positives ({value:.0%}) [E5]",
    },
    "E6": {
        "bull": "Low similarity to known false positives ({value:.0%}) [E6]",
        "bear": "High similarity to known false positives ({value:.0%}) [E6]",
    },
    "E7": {
        "bull": "Signals from {value} distinct sources [E7]",
        "bear": "Only {value} source(s) detected — limited verification [E7]",
    },
    "E8": {
        "bull": "Multi-source verification confirmed [E8]",
        "bear": "Single-source signal — no independent verification [E8]",
    },
    "E9": {
        "bull": "Stage estimate: {value} — within target window [E9]",
        "bear": "Stage estimate: {value} — outside target window [E9]",
    },
    "E10": {
        "bull": "No negative keyword flags detected [E10]",
        "bear": "Negative keyword flags present — possible misclassification [E10]",
    },
    "E11": {
        "bull": "Strong exemplar match with {value:.0%} similarity [E11]",
        "bear": "Low exemplar similarity ({value:.0%}) [E11]",
    },
    "E12": {
        "bull": "Peak signal confidence of {value:.0%} [E12]",
        "bear": "Low peak signal confidence ({value:.0%}) [E12]",
    },
    "E13": {
        "bull": "Signal recency: {value} — active deal signal [E13]",
        "bear": "Signal recency: {value} [E13]",
    },
    "E14": {
        "bull": "Thesis rationale documented by classifier [E14]",
        "bear": "No thesis rationale available [E14]",
    },
}


# =============================================================================
# NARRATOR FUNCTIONS
# =============================================================================

def narrate_bull_case(matrix: ACHMatrix) -> str:
    """Generate bull case citing consistent evidence for top hypothesis.

    Returns string with [E{n}] citations for each supporting point.
    Only cites evidence that is CONSISTENT with H1 (Strong Thesis Fit).
    """
    if not matrix.cells:
        return ""

    cell_map = _build_cell_map(matrix)
    ev_map = {e.evidence_id: e for e in matrix.evidence}
    points: list[str] = []

    for e in matrix.evidence:
        if not e.available:
            continue
        cell_score = cell_map.get((e.evidence_id, "H1"))
        if cell_score == CellScore.CONSISTENT:
            template = _EVIDENCE_TEMPLATES.get(e.evidence_id, {}).get("bull")
            if template:
                try:
                    text = template.format(value=e.raw_value)
                except (ValueError, TypeError, KeyError):
                    text = f"Evidence {e.evidence_id} supports thesis fit [{e.evidence_id}]"
                points.append(text)

    if not points:
        return ""

    result = "Bull case: " + ". ".join(points) + "."
    _validate_citations(result, matrix)
    return result


def narrate_bear_case(matrix: ACHMatrix) -> str:
    """Generate bear case citing inconsistent evidence and competing hypotheses.

    Returns string with [E{n}] citations for each concerning point.
    Cites evidence that is INCONSISTENT with H1 or CONSISTENT with H3/H4/H5.
    """
    if not matrix.cells:
        return ""

    cell_map = _build_cell_map(matrix)
    points: list[str] = []

    for e in matrix.evidence:
        if not e.available:
            continue

        # Check if inconsistent with H1
        h1_score = cell_map.get((e.evidence_id, "H1"))
        if h1_score == CellScore.INCONSISTENT:
            template = _EVIDENCE_TEMPLATES.get(e.evidence_id, {}).get("bear")
            if template:
                try:
                    text = template.format(value=e.raw_value)
                except (ValueError, TypeError, KeyError):
                    text = f"Evidence {e.evidence_id} raises concerns [{e.evidence_id}]"
                points.append(text)
                continue

        # Check if consistent with competing hypotheses (H3, H4, H5)
        for competing in ("H3", "H4", "H5"):
            comp_score = cell_map.get((e.evidence_id, competing))
            if comp_score == CellScore.CONSISTENT and h1_score != CellScore.CONSISTENT:
                template = _EVIDENCE_TEMPLATES.get(e.evidence_id, {}).get("bear")
                if template:
                    try:
                        text = template.format(value=e.raw_value)
                    except (ValueError, TypeError, KeyError):
                        text = f"Evidence {e.evidence_id} supports {competing} [{e.evidence_id}]"
                    points.append(text)
                break

    if not points:
        return ""

    result = "Bear case: " + ". ".join(points) + "."
    _validate_citations(result, matrix)
    return result


def find_differentiators(matrix: ACHMatrix) -> list[Differentiator]:
    """Find evidence that distinguishes between hypotheses.

    An evidence item is a differentiator if it is CONSISTENT with some
    hypotheses and INCONSISTENT with others.
    """
    if not matrix.cells:
        return []

    cell_map = _build_cell_map(matrix)
    ev_map = {e.evidence_id: e for e in matrix.evidence}
    differentiators: list[Differentiator] = []

    for e in matrix.evidence:
        if not e.available:
            continue

        favors: list[str] = []
        opposes: list[str] = []

        for hid in HYPOTHESIS_IDS:
            score = cell_map.get((e.evidence_id, hid))
            if score == CellScore.CONSISTENT:
                favors.append(hid)
            elif score == CellScore.INCONSISTENT:
                opposes.append(hid)

        # A differentiator must both favor AND oppose at least one hypothesis
        if favors and opposes:
            differentiators.append(Differentiator(
                evidence_id=e.evidence_id,
                evidence_label=e.label,
                favors=favors,
                opposes=opposes,
            ))

    return differentiators


def narrate_summary(matrix: ACHMatrix) -> TribunalSummary:
    """Full tribunal summary: bull, bear, differentiators, top hypothesis."""
    bull = narrate_bull_case(matrix)
    bear = narrate_bear_case(matrix)
    diffs = find_differentiators(matrix)

    return TribunalSummary(
        bull_summary=bull,
        bear_summary=bear,
        differentiators=diffs,
        differentiator_count=len(diffs),
        top_hypothesis=matrix.top_hypothesis,
        top_score=matrix.top_score,
    )


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _build_cell_map(matrix: ACHMatrix) -> dict[tuple[str, str], Optional[int]]:
    """Build lookup map from (evidence_id, hypothesis_id) -> score."""
    return {
        (c.evidence_id, c.hypothesis_id): c.score
        for c in matrix.cells
    }


def _validate_citations(text: str, matrix: ACHMatrix) -> None:
    """Validate that every [E{n}] citation maps to available evidence.

    Raises ValueError if a citation references unavailable or nonexistent evidence.
    """
    citations = set(re.findall(r'\[E(\d+)\]', text))
    available_ids = {e.evidence_id for e in matrix.evidence if e.available}

    for n in citations:
        eid = f"E{n}"
        if eid not in available_ids:
            raise ValueError(
                f"Citation [{eid}] in narrative does not map to available evidence. "
                f"Available: {sorted(available_ids)}"
            )

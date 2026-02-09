"""Semantic filter — exemplar veto + case-law context for pipeline decisions.

Provides:
- check_exemplar_veto: Check if a signal matches a known TP exemplar closely
  enough to override a HELD thesis decision (advisory veto).
- get_case_law_context: Retrieve similar precedents for a signal (informational).

Veto semantics (Phase 3):
  - Applies ONLY to HELD signals (low thesis score but not hard-rejected).
  - Does NOT override: hard disqualifiers (Web3, B2B), explicit operator rejection.
  - Effect: HELD → QUALIFIED (signal continues to confidence routing).
  - Advisory: logged in shadow_computations for tuning visibility.

Phase 3 — case-law + exemplars.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

VETO_THRESHOLD = float(os.environ.get("EXEMPLAR_VETO_THRESHOLD", "0.75"))


@dataclass
class SemanticVetoResult:
    """Result of exemplar veto check."""
    veto_active: bool  # True if veto should override HELD → QUALIFIED
    max_similarity: float  # Highest exemplar similarity score
    matched_exemplar: Optional[str]  # Best match exemplar_key (or None)
    matched_category: Optional[str]  # Best match category (or None)
    threshold: float  # Threshold used for veto decision


@dataclass
class CaseLawContext:
    """Case-law context for a signal (informational, not routing)."""
    top_win_similarity: float
    top_loss_similarity: float
    win_count: int
    loss_count: int
    wins_summary: list  # List of (company_name, similarity, label_reason)
    losses_summary: list


def check_exemplar_veto(
    query_text: str,
    exemplar_matcher,
    exemplars: list,
    threshold: Optional[float] = None,
) -> SemanticVetoResult:
    """Check if a signal should be rescued from HELD via exemplar veto.

    Args:
        query_text: Corpus text for the signal (from build_corpus_text).
        exemplar_matcher: Initialized ExemplarMatcher instance.
        exemplars: List of dicts from thesis_exemplars table.
        threshold: Override veto threshold (default: EXEMPLAR_VETO_THRESHOLD env).

    Returns:
        SemanticVetoResult with veto_active=True if signal should be rescued.
    """
    t = threshold if threshold is not None else VETO_THRESHOLD

    if not query_text or not query_text.strip() or not exemplars:
        return SemanticVetoResult(
            veto_active=False, max_similarity=0.0,
            matched_exemplar=None, matched_category=None, threshold=t,
        )

    result = exemplar_matcher.match(query_text, exemplars, threshold=0.0)

    return SemanticVetoResult(
        veto_active=result.max_similarity >= t,
        max_similarity=result.max_similarity,
        matched_exemplar=result.best_match.exemplar_key if result.best_match else None,
        matched_category=result.best_match.category if result.best_match else None,
        threshold=t,
    )


def get_case_law_context(
    query_text: str,
    case_law_retriever,
    precedents: list,
    top_k: int = 3,
) -> CaseLawContext:
    """Get case-law context for a signal (wins + losses).

    Args:
        query_text: Corpus text for the signal.
        case_law_retriever: Initialized CaseLawRetriever instance.
        precedents: List of dicts from precedents table.
        top_k: Number of wins/losses to include.

    Returns:
        CaseLawContext with top wins/losses summaries.
    """
    if not query_text or not query_text.strip() or not precedents:
        return CaseLawContext(
            top_win_similarity=0.0, top_loss_similarity=0.0,
            win_count=0, loss_count=0,
            wins_summary=[], losses_summary=[],
        )

    result = case_law_retriever.find_similar(query_text, precedents, top_k, top_k)

    wins_summary = [
        (m.company_name, round(m.similarity_score, 3), m.label_reason)
        for m in result.wins
    ]
    losses_summary = [
        (m.company_name, round(m.similarity_score, 3), m.label_reason)
        for m in result.losses
    ]

    return CaseLawContext(
        top_win_similarity=result.max_similarity_tp,
        top_loss_similarity=result.max_similarity_fp,
        win_count=len(result.wins),
        loss_count=len(result.losses),
        wins_summary=wins_summary,
        losses_summary=losses_summary,
    )

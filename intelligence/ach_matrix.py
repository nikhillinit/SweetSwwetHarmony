"""
Deterministic ACH (Analysis of Competing Hypotheses) Engine.

Provides a fully deterministic matrix analysis for triage decisions:
- 5 hardcoded hypotheses (RUBRIC_VERSION = "1.0.0")
- 14 evidence types mapped from existing tables
- Scoring rubric: (evidence_id, threshold) -> {hypothesis: CellScore}
- ACHBuilder.build(company_id, db) -> ACHMatrix

Same DB state + same builder/rubric versions -> identical inputs_hash + matrix.
No LLM calls, no external APIs — purely SQL + deterministic logic.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional

logger = logging.getLogger(__name__)

BUILDER_VERSION = "1.0.0"
RUBRIC_VERSION = "1.0.0"


# =============================================================================
# CELL SCORES
# =============================================================================

class CellScore(IntEnum):
    """ACH cell score: how evidence relates to a hypothesis."""
    INCONSISTENT = -1
    NEUTRAL = 0
    CONSISTENT = 1


# None represents NOT_AVAILABLE (excluded from scoring)


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass(frozen=True)
class Hypothesis:
    """A competing hypothesis in the ACH matrix."""
    id: str
    label: str
    description: str


@dataclass
class EvidenceItem:
    """A single piece of evidence gathered from the database."""
    evidence_id: str       # E1..E14
    label: str
    raw_value: Any         # The actual value (float, str, int, bool, None)
    available: bool = True  # False if data was missing


@dataclass
class ACHCell:
    """A single cell in the ACH matrix: evidence vs hypothesis."""
    evidence_id: str
    hypothesis_id: str
    score: Optional[int]  # CellScore value or None for NOT_AVAILABLE


@dataclass
class ACHMatrix:
    """Complete ACH analysis result."""
    company_id: str
    hypotheses: list[Hypothesis]
    evidence: list[EvidenceItem]
    cells: list[ACHCell]
    hypothesis_scores: dict[str, float]  # hypothesis_id -> sum of scores
    top_hypothesis: Optional[str]
    top_score: Optional[float]
    inputs_hash: str
    builder_version: str
    rubric_version: str
    evidence_count: int  # Number of available evidence items


# =============================================================================
# HYPOTHESIS CATALOG
# =============================================================================

HYPOTHESES = [
    Hypothesis(
        id="H1",
        label="Strong Thesis Fit",
        description="Pre-seed to Series A consumer in CPG, health tech, travel, or marketplaces",
    ),
    Hypothesis(
        id="H2",
        label="Weak Thesis Fit",
        description="Consumer-adjacent but outside core thesis categories",
    ),
    Hypothesis(
        id="H3",
        label="B2B in Disguise",
        description="Appears consumer but primarily serves businesses",
    ),
    Hypothesis(
        id="H4",
        label="Too Early / No Traction",
        description="Lacks product-market fit signals or meaningful traction",
    ),
    Hypothesis(
        id="H5",
        label="Already Funded Series B+",
        description="Beyond the target stage window (Series B or later)",
    ),
]

HYPOTHESIS_IDS = [h.id for h in HYPOTHESES]


# =============================================================================
# EVIDENCE TYPE DEFINITIONS
# =============================================================================

EVIDENCE_DEFS = {
    "E1":  "Keyword score",
    "E2":  "LLM fit score",
    "E3":  "LLM category",
    "E4":  "Competitor flag",
    "E5":  "Case-law TP max similarity",
    "E6":  "Case-law FP max similarity",
    "E7":  "Distinct source count",
    "E8":  "Multi-source flag",
    "E9":  "Stage estimate",
    "E10": "Negative keyword hit",
    "E11": "Exemplar similarity",
    "E12": "Max signal confidence",
    "E13": "Signal recency bucket",
    "E14": "Thesis rationale present",
}


# =============================================================================
# CANONICAL HASH
# =============================================================================

def _normalize(value: Any) -> Any:
    """Normalize evidence values for stable hashing."""
    if value is None:
        return None
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return sorted(value) if all(isinstance(x, str) for x in value) else value
    return value


def compute_inputs_hash(
    evidence: list[EvidenceItem],
    builder_version: str,
    rubric_version: str,
) -> str:
    """Deterministic hash of all inputs to ACH computation."""
    payload = {
        "builder_version": builder_version,
        "rubric_version": rubric_version,
        "evidence": sorted(
            [
                {"id": e.evidence_id, "value": _normalize(e.raw_value)}
                for e in evidence
            ],
            key=lambda x: x["id"],
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


# =============================================================================
# RECENCY BUCKETING (deterministic, relative to company timeline)
# =============================================================================

def _recency_bucket(first_signal_at: Optional[str], latest_signal_at: Optional[str]) -> Optional[str]:
    """Bucket signal recency relative to company's own timeline.

    - "recent": first signal within 30 days of most recent
    - "established": first signal 30-90 days before most recent
    - "mature": first signal 90+ days before most recent
    """
    if not first_signal_at or not latest_signal_at:
        return None
    try:
        from datetime import datetime
        first = datetime.fromisoformat(first_signal_at.replace("Z", "+00:00"))
        latest = datetime.fromisoformat(latest_signal_at.replace("Z", "+00:00"))
        delta_days = (latest - first).days
        if delta_days < 30:
            return "recent"
        elif delta_days < 90:
            return "established"
        else:
            return "mature"
    except (ValueError, TypeError):
        return None


# =============================================================================
# SCORING RUBRIC
# =============================================================================

def _apply_rubric(evidence: list[EvidenceItem]) -> list[ACHCell]:
    """Deterministic mapping of evidence to hypothesis scores.

    Returns a list of ACHCells for all (evidence, hypothesis) combinations.
    """
    cells: list[ACHCell] = []
    ev_map = {e.evidence_id: e for e in evidence}

    for eid in EVIDENCE_DEFS:
        e = ev_map.get(eid)
        if not e or not e.available:
            # NOT_AVAILABLE for all hypotheses
            for hid in HYPOTHESIS_IDS:
                cells.append(ACHCell(evidence_id=eid, hypothesis_id=hid, score=None))
            continue

        scores = _score_evidence(eid, e.raw_value)
        for hid in HYPOTHESIS_IDS:
            cells.append(ACHCell(
                evidence_id=eid,
                hypothesis_id=hid,
                score=scores.get(hid),
            ))

    return cells


def _score_evidence(eid: str, value: Any) -> dict[str, Optional[int]]:
    """Score a single evidence item against all hypotheses.

    Returns dict of hypothesis_id -> CellScore (or None for N/A).
    """
    C = CellScore.CONSISTENT
    I = CellScore.INCONSISTENT
    N = CellScore.NEUTRAL

    # Default all to NEUTRAL
    scores: dict[str, Optional[int]] = {hid: N for hid in HYPOTHESIS_IDS}

    if eid == "E1":  # Keyword score (0-1)
        v = _to_float(value)
        if v is None:
            return {hid: None for hid in HYPOTHESIS_IDS}
        if v >= 0.7:
            scores["H1"] = C
            scores["H2"] = N
            scores["H3"] = I
        elif v >= 0.4:
            scores["H1"] = N
            scores["H2"] = C
        else:
            scores["H1"] = I
            scores["H3"] = C

    elif eid == "E2":  # LLM fit score (0-1)
        v = _to_float(value)
        if v is None:
            return {hid: None for hid in HYPOTHESIS_IDS}
        if v >= 0.7:
            scores["H1"] = C
            scores["H3"] = I
        elif v >= 0.4:
            scores["H2"] = C
        else:
            scores["H1"] = I
            scores["H2"] = I

    elif eid == "E3":  # LLM category
        v = str(value).lower() if value else ""
        consumer_cats = {"consumer_cpg", "consumer_health", "travel_hospitality", "consumer_marketplace"}
        adjacent_cats = {"consumer_other", "consumer_adjacent"}
        b2b_cats = {"b2b", "enterprise", "developer_tools", "saas"}
        if v in consumer_cats:
            scores["H1"] = C
            scores["H2"] = N
            scores["H3"] = I
        elif v in adjacent_cats:
            scores["H1"] = N
            scores["H2"] = C
        elif v in b2b_cats:
            scores["H1"] = I
            scores["H3"] = C
        else:
            scores["H1"] = N

    elif eid == "E4":  # Competitor flag (bool)
        v = _to_bool(value)
        if v is None:
            return {hid: None for hid in HYPOTHESIS_IDS}
        if v:
            scores["H1"] = C
            scores["H4"] = I
        else:
            pass  # All neutral

    elif eid == "E5":  # Case-law TP max similarity (0-1)
        v = _to_float(value)
        if v is None:
            return {hid: None for hid in HYPOTHESIS_IDS}
        if v >= 0.7:
            scores["H1"] = C
            scores["H3"] = I
        elif v >= 0.4:
            scores["H1"] = N

    elif eid == "E6":  # Case-law FP max similarity (0-1)
        v = _to_float(value)
        if v is None:
            return {hid: None for hid in HYPOTHESIS_IDS}
        if v >= 0.7:
            scores["H1"] = I
            scores["H3"] = C
        elif v >= 0.4:
            scores["H2"] = N
            scores["H3"] = N

    elif eid == "E7":  # Distinct source count (int)
        v = _to_int(value)
        if v is None:
            return {hid: None for hid in HYPOTHESIS_IDS}
        if v >= 3:
            scores["H1"] = C
            scores["H4"] = I
        elif v == 2:
            scores["H1"] = N
            scores["H4"] = N
        else:
            scores["H4"] = C

    elif eid == "E8":  # Multi-source flag (bool)
        v = _to_bool(value)
        if v is None:
            return {hid: None for hid in HYPOTHESIS_IDS}
        if v:
            scores["H1"] = C
            scores["H4"] = I
        else:
            scores["H4"] = C

    elif eid == "E9":  # Stage estimate
        v = str(value).lower() if value else ""
        early_stages = {"pre-seed", "seed", "seed+", "seed +", "series a", "series_a"}
        late_stages = {"series b", "series_b", "series c", "series_c", "series d", "series_d", "ipo"}
        if v in early_stages:
            scores["H1"] = C
            scores["H5"] = I
        elif v in late_stages:
            scores["H1"] = I
            scores["H5"] = C
        else:
            pass  # Unknown stage, neutral

    elif eid == "E10":  # Negative keyword hit (bool)
        v = _to_bool(value)
        if v is None:
            return {hid: None for hid in HYPOTHESIS_IDS}
        if v:
            scores["H1"] = I
            scores["H3"] = C
        else:
            scores["H1"] = N

    elif eid == "E11":  # Exemplar similarity (0-1)
        v = _to_float(value)
        if v is None:
            return {hid: None for hid in HYPOTHESIS_IDS}
        if v >= 0.7:
            scores["H1"] = C
        elif v >= 0.4:
            scores["H1"] = N
        else:
            scores["H1"] = I
            scores["H2"] = C

    elif eid == "E12":  # Max signal confidence (0-1)
        v = _to_float(value)
        if v is None:
            return {hid: None for hid in HYPOTHESIS_IDS}
        if v >= 0.7:
            scores["H1"] = C
            scores["H4"] = I
        elif v >= 0.4:
            scores["H1"] = N
        else:
            scores["H4"] = C

    elif eid == "E13":  # Signal recency bucket
        v = str(value).lower() if value else ""
        if v == "recent":
            scores["H1"] = C
            scores["H4"] = I
        elif v == "established":
            scores["H1"] = N
        elif v == "mature":
            scores["H1"] = N
            scores["H5"] = N
        else:
            return {hid: None for hid in HYPOTHESIS_IDS}

    elif eid == "E14":  # Thesis rationale present (bool)
        v = _to_bool(value)
        if v is None:
            return {hid: None for hid in HYPOTHESIS_IDS}
        if v:
            scores["H1"] = C
        else:
            scores["H1"] = N

    return scores


# =============================================================================
# TYPE COERCION HELPERS
# =============================================================================

def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return None


# =============================================================================
# ACH BUILDER
# =============================================================================

class ACHBuilder:
    """Builds a deterministic ACH matrix from database state.

    Usage:
        builder = ACHBuilder()
        matrix = await builder.build(company_id, db)
    """

    async def build(self, company_id: str, db) -> ACHMatrix:
        """Build ACH matrix for a company.

        Args:
            company_id: The company_id to analyze.
            db: An aiosqlite connection (store._db).

        Returns:
            ACHMatrix with deterministic scores and inputs_hash.
        """
        evidence = await self._gather_evidence(company_id, db)
        cells = _apply_rubric(evidence)
        inputs_hash = compute_inputs_hash(evidence, BUILDER_VERSION, RUBRIC_VERSION)

        # Compute hypothesis scores (sum of non-None cells)
        hypothesis_scores: dict[str, float] = {hid: 0.0 for hid in HYPOTHESIS_IDS}
        for cell in cells:
            if cell.score is not None:
                hypothesis_scores[cell.hypothesis_id] += cell.score

        # Determine top hypothesis
        top_hypothesis = None
        top_score = None
        if hypothesis_scores:
            best_id = max(hypothesis_scores, key=lambda k: hypothesis_scores[k])
            top_hypothesis = best_id
            top_score = hypothesis_scores[best_id]

        evidence_count = sum(1 for e in evidence if e.available)

        return ACHMatrix(
            company_id=company_id,
            hypotheses=list(HYPOTHESES),
            evidence=evidence,
            cells=cells,
            hypothesis_scores=hypothesis_scores,
            top_hypothesis=top_hypothesis,
            top_score=top_score,
            inputs_hash=inputs_hash,
            builder_version=BUILDER_VERSION,
            rubric_version=RUBRIC_VERSION,
            evidence_count=evidence_count,
        )

    async def _gather_evidence(self, company_id: str, db) -> list[EvidenceItem]:
        """Gather all 14 evidence items from the database."""
        evidence: list[EvidenceItem] = []

        # Get canonical_key for thesis lookups
        cursor = await db.execute(
            "SELECT canonical_key FROM signals WHERE company_id = ? LIMIT 1",
            (company_id,),
        )
        row = await cursor.fetchone()
        canonical_key = row[0] if row else None

        # E1-E4: thesis_classifications
        tc_data = await self._get_thesis_classification(canonical_key, db)
        evidence.append(EvidenceItem("E1", "Keyword score", tc_data.get("keyword_score"), tc_data.get("keyword_score") is not None))
        evidence.append(EvidenceItem("E2", "LLM fit score", tc_data.get("llm_score"), tc_data.get("llm_score") is not None))
        evidence.append(EvidenceItem("E3", "LLM category", tc_data.get("category"), tc_data.get("category") is not None))
        evidence.append(EvidenceItem("E4", "Competitor flag", tc_data.get("competitor_flag"), tc_data.get("competitor_flag") is not None))

        # E5-E6: case-law precedents
        cl_data = await self._get_case_law(company_id, db)
        evidence.append(EvidenceItem("E5", "Case-law TP max similarity", cl_data.get("tp_max_sim"), cl_data.get("tp_max_sim") is not None))
        evidence.append(EvidenceItem("E6", "Case-law FP max similarity", cl_data.get("fp_max_sim"), cl_data.get("fp_max_sim") is not None))

        # E7-E8: signal diversity
        sd_data = await self._get_signal_diversity(company_id, db)
        evidence.append(EvidenceItem("E7", "Distinct source count", sd_data.get("source_count"), sd_data.get("source_count") is not None))
        evidence.append(EvidenceItem("E8", "Multi-source flag", sd_data.get("multi_source"), sd_data.get("multi_source") is not None))

        # E9-E10: stage + negative keywords from thesis_classifications
        evidence.append(EvidenceItem("E9", "Stage estimate", tc_data.get("stage_estimate"), tc_data.get("stage_estimate") is not None))
        evidence.append(EvidenceItem("E10", "Negative keyword hit", tc_data.get("negative_keywords"), tc_data.get("negative_keywords") is not None))

        # E11: exemplar similarity
        ex_data = await self._get_exemplar_similarity(canonical_key, db)
        evidence.append(EvidenceItem("E11", "Exemplar similarity", ex_data, ex_data is not None))

        # E12: max signal confidence
        max_conf = await self._get_max_confidence(company_id, db)
        evidence.append(EvidenceItem("E12", "Max signal confidence", max_conf, max_conf is not None))

        # E13: signal recency bucket (relative to company timeline)
        recency = await self._get_recency_bucket(company_id, db)
        evidence.append(EvidenceItem("E13", "Signal recency bucket", recency, recency is not None))

        # E14: thesis rationale present
        has_rationale = tc_data.get("rationale_present")
        evidence.append(EvidenceItem("E14", "Thesis rationale present", has_rationale, has_rationale is not None))

        return evidence

    async def _get_thesis_classification(self, canonical_key: Optional[str], db) -> dict:
        """Fetch latest thesis classification for the company."""
        if not canonical_key:
            return {}
        cursor = await db.execute(
            """SELECT keyword_score, llm_score, category, rationale,
                      stage_estimate, negative_keywords, competitor_flag
               FROM thesis_classifications
               WHERE canonical_key = ?
               ORDER BY classified_at DESC LIMIT 1""",
            (canonical_key,),
        )
        row = await cursor.fetchone()
        if not row:
            return {}
        return {
            "keyword_score": row[0],
            "llm_score": row[1],
            "category": row[2],
            "rationale_present": bool(row[3]) if row[3] is not None else None,
            "stage_estimate": row[4],
            "negative_keywords": bool(row[5]) if row[5] is not None else None,
            "competitor_flag": bool(row[6]) if row[6] is not None else None,
        }

    async def _get_case_law(self, company_id: str, db) -> dict:
        """Fetch case-law precedent similarities."""
        # TP max similarity
        cursor = await db.execute(
            """SELECT MAX(similarity) FROM precedents
               WHERE company_id = ? AND human_label = 'TP'""",
            (company_id,),
        )
        row = await cursor.fetchone()
        tp_max = row[0] if row and row[0] is not None else None

        # FP max similarity
        cursor = await db.execute(
            """SELECT MAX(similarity) FROM precedents
               WHERE company_id = ? AND human_label = 'FP'""",
            (company_id,),
        )
        row = await cursor.fetchone()
        fp_max = row[0] if row and row[0] is not None else None

        return {"tp_max_sim": tp_max, "fp_max_sim": fp_max}

    async def _get_signal_diversity(self, company_id: str, db) -> dict:
        """Fetch signal source diversity metrics."""
        cursor = await db.execute(
            """SELECT COUNT(DISTINCT source_api) FROM signals
               WHERE company_id = ?""",
            (company_id,),
        )
        row = await cursor.fetchone()
        count = row[0] if row else 0
        return {
            "source_count": count,
            "multi_source": count >= 2,
        }

    async def _get_exemplar_similarity(self, canonical_key: Optional[str], db) -> Optional[float]:
        """Fetch best exemplar similarity score."""
        if not canonical_key:
            return None
        try:
            cursor = await db.execute(
                """SELECT MAX(similarity_score) FROM thesis_exemplars
                   WHERE canonical_key = ? AND is_active = 1""",
                (canonical_key,),
            )
            row = await cursor.fetchone()
            return row[0] if row and row[0] is not None else None
        except Exception:
            return None

    async def _get_max_confidence(self, company_id: str, db) -> Optional[float]:
        """Fetch maximum signal confidence for the company."""
        cursor = await db.execute(
            "SELECT MAX(confidence) FROM signals WHERE company_id = ?",
            (company_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] is not None else None

    async def _get_recency_bucket(self, company_id: str, db) -> Optional[str]:
        """Compute recency bucket from signal timestamps."""
        cursor = await db.execute(
            """SELECT MIN(detected_at), MAX(detected_at)
               FROM signals WHERE company_id = ?""",
            (company_id,),
        )
        row = await cursor.fetchone()
        if not row or not row[0]:
            return None
        return _recency_bucket(row[0], row[1])


# =============================================================================
# ACH STORAGE HELPERS
# =============================================================================

async def store_ach_analysis(
    db,
    matrix: ACHMatrix,
    review_id: Optional[int] = None,
) -> int:
    """Store ACH analysis result.

    Uses INSERT OR IGNORE for cache identity (company_id + versions + hash).
    Returns the row id.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    matrix_json = json.dumps({
        "hypotheses": [{"id": h.id, "label": h.label, "description": h.description} for h in matrix.hypotheses],
        "evidence": [
            {"id": e.evidence_id, "label": e.label, "raw_value": _normalize(e.raw_value), "available": e.available}
            for e in matrix.evidence
        ],
        "cells": [
            {"evidence_id": c.evidence_id, "hypothesis_id": c.hypothesis_id, "score": c.score}
            for c in matrix.cells
        ],
        "hypothesis_scores": matrix.hypothesis_scores,
    })

    # INSERT OR IGNORE on unique constraint (company_id, builder_version, rubric_version, inputs_hash)
    cursor = await db.execute(
        """INSERT OR IGNORE INTO ach_analyses (
            company_id, review_id, builder_version, rubric_version,
            inputs_hash, matrix_json, top_hypothesis, top_score,
            bull_summary, bear_summary, differentiator_count,
            evidence_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            matrix.company_id,
            review_id,
            matrix.builder_version,
            matrix.rubric_version,
            matrix.inputs_hash,
            matrix_json,
            matrix.top_hypothesis,
            matrix.top_score,
            None,  # bull_summary set later by tribunal
            None,  # bear_summary set later by tribunal
            0,     # differentiator_count set later
            matrix.evidence_count,
            now,
        ),
    )
    await db.commit()

    # Fetch the stored row (may be existing due to OR IGNORE)
    cursor = await db.execute(
        """SELECT id FROM ach_analyses
           WHERE company_id = ? AND builder_version = ? AND rubric_version = ? AND inputs_hash = ?""",
        (matrix.company_id, matrix.builder_version, matrix.rubric_version, matrix.inputs_hash),
    )
    row = await cursor.fetchone()
    return row[0] if row else 0


async def update_ach_narratives(
    db,
    ach_id: int,
    bull_summary: str,
    bear_summary: str,
    differentiator_count: int,
) -> None:
    """Update narratives on an existing ACH analysis row."""
    await db.execute(
        """UPDATE ach_analyses
           SET bull_summary = ?, bear_summary = ?, differentiator_count = ?
           WHERE id = ?""",
        (bull_summary, bear_summary, differentiator_count, ach_id),
    )
    await db.commit()


async def get_latest_ach(db, company_id: str) -> Optional[dict]:
    """Fetch the latest ACH analysis for a company."""
    cursor = await db.execute(
        """SELECT id, company_id, review_id, builder_version, rubric_version,
                  inputs_hash, matrix_json, top_hypothesis, top_score,
                  bull_summary, bear_summary, differentiator_count,
                  evidence_count, created_at
           FROM ach_analyses
           WHERE company_id = ?
           ORDER BY created_at DESC, id DESC
           LIMIT 1""",
        (company_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "company_id": row[1],
        "review_id": row[2],
        "builder_version": row[3],
        "rubric_version": row[4],
        "inputs_hash": row[5],
        "matrix_json": row[6],
        "top_hypothesis": row[7],
        "top_score": row[8],
        "bull_summary": row[9],
        "bear_summary": row[10],
        "differentiator_count": row[11],
        "evidence_count": row[12],
        "created_at": row[13],
    }

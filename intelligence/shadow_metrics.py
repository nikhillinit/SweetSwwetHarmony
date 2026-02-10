"""
Shadow Metrics Calculator — Disagreement analysis for shadow entity comparison.

Computes:
- over_merge_rate: over_merges / total_phase_g_merges
- over_split_rate: over_splits / total_phase1a_groups
- agreement_rate: agreements / total_signals
- Stratification by collector, confidence_band, key_type prefix

Joins shadow_disagreements with signal_quality_metrics for labeled-pair precision.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


async def compute_shadow_metrics(
    store: "SignalStore",
    shadow_run_id: int,
) -> Dict[str, Any]:
    """Compute metrics for a completed shadow entity run.

    Args:
        store: SignalStore for DB access.
        shadow_run_id: The shadow_entity_runs.id to compute metrics for.

    Returns:
        Dict with overall and stratified metrics.
    """
    db = store._db

    # Fetch the shadow run summary
    cursor = await db.execute(
        """
        SELECT total_signals, phase1a_groups, phase_g_groups, agreements, disagreements
        FROM shadow_entity_runs WHERE id = ?
        """,
        (shadow_run_id,),
    )
    run_row = await cursor.fetchone()
    if not run_row:
        return {}

    total_signals, phase1a_groups, phase_g_groups, agreements, total_disagreements = run_row

    # Fetch all disagreements for this run
    cursor = await db.execute(
        """
        SELECT disagreement_type, collector, confidence_band, canonical_key_type, signal_id
        FROM shadow_disagreements
        WHERE shadow_run_id = ?
        """,
        (shadow_run_id,),
    )
    disagree_rows = await cursor.fetchall()

    # Count by type
    over_merges = sum(1 for r in disagree_rows if r[0] == "over_merge")
    over_splits = sum(1 for r in disagree_rows if r[0] == "over_split")

    # Overall rates
    overall = {
        "total_signals": total_signals,
        "agreements": agreements,
        "disagreements": total_disagreements,
        "over_merges": over_merges,
        "over_splits": over_splits,
        "agreement_rate": agreements / total_signals if total_signals > 0 else 1.0,
        "over_merge_rate": over_merges / phase_g_groups if phase_g_groups > 0 else 0.0,
        "over_split_rate": over_splits / phase1a_groups if phase1a_groups > 0 else 0.0,
    }

    # Stratify by collector
    by_collector: Dict[str, Dict[str, int]] = defaultdict(lambda: {"over_merge": 0, "over_split": 0, "total": 0})
    for r in disagree_rows:
        dtype, collector = r[0], r[1] or "unknown"
        by_collector[collector][dtype] += 1
        by_collector[collector]["total"] += 1

    # Stratify by confidence_band
    by_band: Dict[str, Dict[str, int]] = defaultdict(lambda: {"over_merge": 0, "over_split": 0, "total": 0})
    for r in disagree_rows:
        dtype, band = r[0], r[2] or "unknown"
        by_band[band][dtype] += 1
        by_band[band]["total"] += 1

    # Stratify by key_type
    by_key_type: Dict[str, Dict[str, int]] = defaultdict(lambda: {"over_merge": 0, "over_split": 0, "total": 0})
    for r in disagree_rows:
        dtype, key_type = r[0], r[3] or "unknown"
        by_key_type[key_type][dtype] += 1
        by_key_type[key_type]["total"] += 1

    # Labeled-pair precision: join with signal_quality_metrics
    signal_ids = [r[4] for r in disagree_rows]
    labeled_precision = await _compute_labeled_precision(db, signal_ids)

    return {
        "overall": overall,
        "by_collector": dict(by_collector),
        "by_confidence_band": dict(by_band),
        "by_key_type": dict(by_key_type),
        "labeled_precision": labeled_precision,
    }


async def _compute_labeled_precision(
    db: Any,
    signal_ids: list[int],
) -> Dict[str, Any]:
    """Compute precision against labeled signals in disagreements."""
    if not signal_ids:
        return {"total_labeled": 0, "tp_in_disagreements": 0, "fp_in_disagreements": 0}

    placeholders = ",".join("?" * len(signal_ids))
    cursor = await db.execute(
        f"""
        SELECT sqm.signal_id, sqm.label
        FROM signal_quality_metrics sqm
        WHERE sqm.signal_id IN ({placeholders})
          AND sqm.label IN ('TP', 'FP')
        """,
        signal_ids,
    )
    rows = await cursor.fetchall()

    tp_count = sum(1 for r in rows if r[1] == "TP")
    fp_count = sum(1 for r in rows if r[1] == "FP")

    return {
        "total_labeled": len(rows),
        "tp_in_disagreements": tp_count,
        "fp_in_disagreements": fp_count,
    }

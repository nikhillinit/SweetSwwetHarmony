"""
Quality stats (FP/TP rates) over recent windows.

We intentionally compute from the underlying tables rather than persisting
aggregates, so metrics are always reproducible.

Note: ``fp_rate`` is technically the false discovery rate (FDR) = FP / (TP + FP),
i.e. 1 - precision over surfaced candidates.  ADJ and UNSURE labels are excluded
from the denominator so that thesis-adjacent companies don't inflate the FP rate.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class CollectorStats:
    source_api: str
    labeled_signals: int
    fp: int
    tp: int
    unsure: int
    adj: int
    decided: int
    fp_rate: float


def _iso_days_ago(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat()


def get_overall_stats(conn: sqlite3.Connection, *, days: int = 30) -> Dict[str, float]:
    """
    Overall stats across all labeled signals whose detected_at is within `days`.

    ``fp_rate`` = FP / decided  (where decided = TP + FP).
    ``adj_rate`` = ADJ / (decided + ADJ) — companion metric for adjacent drift visibility.
    ``decision_rate`` = decided / labeled — how much of the labeling is decisive vs parking.
    """
    since = _iso_days_ago(days)

    row = conn.execute(
        """
        SELECT
            COUNT(*) AS labeled,
            SUM(CASE WHEN sqm.human_label = 'FP' THEN 1 ELSE 0 END) AS fp,
            SUM(CASE WHEN sqm.human_label = 'TP' THEN 1 ELSE 0 END) AS tp,
            SUM(CASE WHEN sqm.human_label = 'UNSURE' THEN 1 ELSE 0 END) AS unsure,
            SUM(CASE WHEN sqm.human_label = 'ADJ' THEN 1 ELSE 0 END) AS adj
        FROM signals s
        JOIN signal_quality_metrics sqm ON sqm.signal_id = s.id
        WHERE s.detected_at >= ?
        """,
        (since,),
    ).fetchone()

    labeled = int(row["labeled"] or 0)
    fp = int(row["fp"] or 0)
    tp = int(row["tp"] or 0)
    unsure = int(row["unsure"] or 0)
    adj = int(row["adj"] or 0)
    decided = tp + fp
    fp_rate = (fp / decided) if decided else 0.0
    adj_rate = (adj / (decided + adj)) if (decided + adj) else 0.0
    decision_rate = (decided / labeled) if labeled else 0.0

    return {
        "days": float(days),
        "labeled": float(labeled),
        "decided": float(decided),
        "fp": float(fp),
        "tp": float(tp),
        "unsure": float(unsure),
        "adj": float(adj),
        "fp_rate": float(fp_rate),
        "adj_rate": float(adj_rate),
        "decision_rate": float(decision_rate),
    }


def get_stats_by_source_api(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    min_labeled: int = 10,
) -> List[CollectorStats]:
    """
    Stats by signals.source_api for recent labeled signals.

    ``min_labeled`` threshold applies to ``decided`` (TP + FP) to prevent sources
    with many ADJ/UNSURE labels but few decisive labels from showing misleading rates.
    """
    since = _iso_days_ago(days)

    rows = conn.execute(
        """
        SELECT
            s.source_api AS source_api,
            COUNT(*) AS labeled,
            SUM(CASE WHEN sqm.human_label = 'FP' THEN 1 ELSE 0 END) AS fp,
            SUM(CASE WHEN sqm.human_label = 'TP' THEN 1 ELSE 0 END) AS tp,
            SUM(CASE WHEN sqm.human_label = 'UNSURE' THEN 1 ELSE 0 END) AS unsure,
            SUM(CASE WHEN sqm.human_label = 'ADJ' THEN 1 ELSE 0 END) AS adj
        FROM signals s
        JOIN signal_quality_metrics sqm ON sqm.signal_id = s.id
        WHERE s.detected_at >= ?
        GROUP BY s.source_api
        HAVING (SUM(CASE WHEN sqm.human_label IN ('TP','FP') THEN 1 ELSE 0 END)) > 0
        ORDER BY
            SUM(CASE WHEN sqm.human_label = 'FP' THEN 1 ELSE 0 END) * 1.0
            / (SUM(CASE WHEN sqm.human_label IN ('TP','FP') THEN 1 ELSE 0 END)) DESC,
            COUNT(*) DESC
        """,
        (since,),
    ).fetchall()

    out: List[CollectorStats] = []
    for r in rows:
        labeled = int(r["labeled"] or 0)
        fp = int(r["fp"] or 0)
        tp = int(r["tp"] or 0)
        unsure = int(r["unsure"] or 0)
        adj = int(r["adj"] or 0)
        decided = tp + fp
        if decided < min_labeled:
            continue
        fp_rate = fp / decided if decided else 0.0
        out.append(
            CollectorStats(
                source_api=str(r["source_api"]),
                labeled_signals=labeled,
                fp=fp,
                tp=tp,
                unsure=unsure,
                adj=adj,
                decided=decided,
                fp_rate=float(fp_rate),
            )
        )
    return out

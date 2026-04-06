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
from statistics import mean
from typing import Any, Dict, List

from verification.verification_gate_v2 import VerificationGate


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


def _frozen_quality_stats(overall: Dict[str, float]) -> Dict[str, Any]:
    return {
        "labeled": int(overall["labeled"]),
        "decided": int(overall["decided"]),
        "tp": int(overall["tp"]),
        "fp": int(overall["fp"]),
        "unsure": int(overall["unsure"]),
        "adj": int(overall["adj"]),
        "fp_rate": float(overall["fp_rate"]),
    }


def _latest_thesis_row_mismatches(conn: sqlite3.Connection, *, days: int) -> int:
    since = _iso_days_ago(days)
    row = conn.execute(
        """
        WITH by_id AS (
            SELECT signal_id, id AS max_id
            FROM (
                SELECT
                    signal_id,
                    id,
                    ROW_NUMBER() OVER (PARTITION BY signal_id ORDER BY id DESC) AS rn
                FROM thesis_classifications
            )
            WHERE rn = 1
        ),
        by_time AS (
            SELECT signal_id, id AS max_time_id
            FROM (
                SELECT
                    signal_id,
                    id,
                    ROW_NUMBER() OVER (PARTITION BY signal_id ORDER BY classified_at DESC, id DESC) AS rn
                FROM thesis_classifications
            )
            WHERE rn = 1
        )
        SELECT COUNT(*)
        FROM by_id i
        JOIN signals s ON s.id = i.signal_id
        JOIN by_time t ON t.signal_id=i.signal_id
        WHERE s.detected_at >= ?
          AND i.max_id != t.max_time_id
        """
        ,
        (since,),
    ).fetchone()
    return int(row[0] or 0)


def _latest_decisive_thesis_rows(conn: sqlite3.Connection, *, days: int) -> list[sqlite3.Row]:
    since = _iso_days_ago(days)
    return conn.execute(
        """
        WITH latest_tc AS (
            SELECT tc.*
            FROM thesis_classifications tc
            JOIN (
                SELECT signal_id, MAX(id) AS max_id
                FROM thesis_classifications
                GROUP BY signal_id
            ) tmax ON tmax.signal_id = tc.signal_id AND tmax.max_id = tc.id
        )
        SELECT sqm.signal_id, sqm.human_label, tc.thesis_fit_score, tc.thesis_match
        FROM signal_quality_metrics sqm
        JOIN latest_tc tc ON tc.signal_id = sqm.signal_id
        JOIN signals s ON s.id = sqm.signal_id
        WHERE s.detected_at >= ?
          AND sqm.human_label IN ('TP', 'FP')
          AND tc.thesis_fit_score IS NOT NULL
        ORDER BY sqm.signal_id
        """,
        (since,),
    ).fetchall()


def _rank_auc(tp_scores: list[float], fp_scores: list[float]) -> float:
    values = [(s, "TP") for s in tp_scores] + [(s, "FP") for s in fp_scores]
    values.sort(key=lambda x: x[0])
    rank_sum_pos = 0.0
    i = 0
    while i < len(values):
        j = i
        while j < len(values) and values[j][0] == values[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            if values[k][1] == "TP":
                rank_sum_pos += avg_rank
        i = j
    n_pos = len(tp_scores)
    n_neg = len(fp_scores)
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def build_router_diagnostic_summary(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    days: int = 90,
    high_confidence_threshold: float | None = None,
) -> Dict[str, Any]:
    """Build the frozen router-diagnostic summary contract for the learning loop."""
    threshold = (
        float(high_confidence_threshold)
        if high_confidence_threshold is not None
        else float(VerificationGate.HIGH_CONFIDENCE_THRESHOLD)
    )
    overall = get_overall_stats(conn, days=days)
    mismatches = _latest_thesis_row_mismatches(conn, days=days)
    rows = _latest_decisive_thesis_rows(conn, days=days)

    tp_scores = [float(r["thesis_fit_score"]) for r in rows if r["human_label"] == "TP"]
    fp_scores = [float(r["thesis_fit_score"]) for r in rows if r["human_label"] == "FP"]

    summary: Dict[str, Any] = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "db_path": db_path,
        "window_days": days,
        "quality_stats": _frozen_quality_stats(overall),
        "join_coverage": {
            "decisive_joined_rows": len(rows),
            "tp_rows": len(tp_scores),
            "fp_rows": len(fp_scores),
            "latest_row_mismatches": mismatches,
        },
        "discrimination": {
            "auc": None,
            "tp_mean": None,
            "fp_mean": None,
            "mean_separation": None,
            "score_max": None,
            "threshold_0_7": {"tp": 0, "fp": 0, "fn": 0, "tn": 0},
        },
        "branch_recommendation": {"name": "diagnostic_cannot_be_computed", "reason": []},
        "reproduction": {
            "quality_stats_command": f"python -m ops.cli quality --db {db_path} stats --days {days}",
            "notes": [
                "Metrics are based on the latest thesis_classifications row per signal_id.",
                "Discrimination metrics are computed from TP/FP rows joined to latest thesis_fit_score values in the selected detected_at window.",
            ],
        },
    }

    decided = int(overall["decided"])
    if mismatches != 0 or not tp_scores or not fp_scores or len(rows) != decided:
        summary["branch_recommendation"]["reason"] = [
            "decisive-label joins or latest-row integrity are not credible enough to evaluate branch predicates"
        ]
        return summary

    tp_mean = mean(tp_scores)
    fp_mean = mean(fp_scores)
    auc = _rank_auc(tp_scores, fp_scores)
    score_max = max(tp_scores + fp_scores)
    threshold_counts = {
        "tp": sum(1 for s in tp_scores if s >= threshold),
        "fp": sum(1 for s in fp_scores if s >= threshold),
        "fn": sum(1 for s in tp_scores if s < threshold),
        "tn": sum(1 for s in fp_scores if s < threshold),
    }
    mean_sep = tp_mean - fp_mean

    summary["discrimination"] = {
        "auc": auc,
        "tp_mean": tp_mean,
        "fp_mean": fp_mean,
        "mean_separation": mean_sep,
        "score_max": score_max,
        "threshold_0_7": threshold_counts,
    }

    if mean_sep < 0.05 or auc < 0.65:
        branch = "score_collapse_confirmed"
        reasons = [
            "mean separation below 0.05 or AUC below 0.65",
            f"mean_separation={mean_sep:.6f}",
            f"auc={auc:.6f}",
        ]
    elif score_max < threshold:
        branch = "threshold_ceiling_only"
        reasons = [
            "separation is acceptable but score_max is below the high-confidence threshold",
            f"score_max={score_max:.6f}",
            f"high_confidence_threshold={threshold:.6f}",
        ]
    else:
        branch = "no_routing_problem_detected"
        reasons = [
            "separation is acceptable and the threshold is reachable",
            f"mean_separation={mean_sep:.6f}",
            f"auc={auc:.6f}",
            f"score_max={score_max:.6f}",
        ]

    summary["branch_recommendation"] = {"name": branch, "reason": reasons}
    return summary


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

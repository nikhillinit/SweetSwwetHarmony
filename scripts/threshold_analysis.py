#!/usr/bin/env python3
"""
Threshold Analysis Script - Diagnostic for keyword matcher recall ceiling.

Sweeps thresholds across the ground truth dataset and outputs:
- Precision-recall curve points
- Best achievable recall under precision constraints
- Thresholds corresponding to operational cutoffs (0.1, 0.3, 0.4)

This validates the hypothesis that keyword-only thresholds cannot achieve
meaningful recall, justifying the ML classifier addition.

Usage:
    python scripts/threshold_analysis.py --ground-truth datasets/thesis_ground_truth.jsonl
    python scripts/threshold_analysis.py --ground-truth datasets/thesis_ground_truth.jsonl --out threshold_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.thesis_matcher import ThesisMatcher


def load_ground_truth(path: str) -> List[Dict[str, Any]]:
    """Load JSONL ground truth dataset."""
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def score_dataset(
    records: List[Dict[str, Any]],
    matcher: ThesisMatcher,
) -> List[Tuple[float, int]]:
    """Score all records and return (score, label) pairs.

    Returns:
        List of (keyword_score, binary_label) tuples
    """
    scored = []
    skipped = 0

    for record in records:
        label_str = record.get("expected_classification", record.get("label", ""))
        if label_str not in ("POSITIVE", "NEGATIVE"):
            skipped += 1
            continue

        label = 1 if label_str == "POSITIVE" else 0

        text = record.get("description", "") or ""
        company_name = record.get("company_name")
        domain = record.get("website") or record.get("domain")

        fit = matcher.score(text, company_name=company_name, domain_name=domain)
        scored.append((fit.score, label))

    if skipped:
        print(f"Skipped {skipped} records with invalid labels", file=sys.stderr)

    return scored


def compute_pr_at_threshold(
    scored: List[Tuple[float, int]],
    threshold: float,
) -> Dict[str, Any]:
    """Compute precision, recall, F1 at a given threshold."""
    tp = sum(1 for s, l in scored if s >= threshold and l == 1)
    fp = sum(1 for s, l in scored if s >= threshold and l == 0)
    fn = sum(1 for s, l in scored if s < threshold and l == 1)
    tn = sum(1 for s, l in scored if s < threshold and l == 0)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "threshold": round(threshold, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


def sweep_thresholds(
    scored: List[Tuple[float, int]],
    n_points: int = 50,
) -> List[Dict[str, Any]]:
    """Sweep thresholds from 0.0 to max_score."""
    if not scored:
        return []

    max_score = max(s for s, _ in scored)
    step = max(max_score / n_points, 0.001)

    points = []
    threshold = 0.0
    while threshold <= max_score + step:
        points.append(compute_pr_at_threshold(scored, threshold))
        threshold += step

    return points


def best_recall_under_precision(
    pr_curve: List[Dict[str, Any]],
    min_precision: float,
) -> Dict[str, Any]:
    """Find best achievable recall under a precision constraint."""
    candidates = [p for p in pr_curve if p["precision"] >= min_precision]
    if not candidates:
        return {"min_precision": min_precision, "achievable": False}

    best = max(candidates, key=lambda p: p["recall"])
    return {
        "min_precision": min_precision,
        "achievable": True,
        "best_recall": best["recall"],
        "at_threshold": best["threshold"],
        "precision_at_point": best["precision"],
        "f1_at_point": best["f1"],
    }


def main():
    parser = argparse.ArgumentParser(description="Threshold analysis for keyword matcher")
    parser.add_argument(
        "--ground-truth",
        required=True,
        help="Path to ground truth JSONL file",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON file (defaults to stdout)",
    )
    parser.add_argument(
        "--n-points",
        type=int,
        default=50,
        help="Number of threshold sweep points",
    )
    args = parser.parse_args()

    # Load data
    records = load_ground_truth(args.ground_truth)
    print(f"Loaded {len(records)} ground truth records", file=sys.stderr)

    # Score
    matcher = ThesisMatcher()
    scored = score_dataset(records, matcher)
    print(f"Scored {len(scored)} records", file=sys.stderr)

    # Stats
    pos_scores = [s for s, l in scored if l == 1]
    neg_scores = [s for s, l in scored if l == 0]

    score_stats = {
        "total": len(scored),
        "positive_count": len(pos_scores),
        "negative_count": len(neg_scores),
        "positive_scores": {
            "mean": round(sum(pos_scores) / len(pos_scores), 4) if pos_scores else 0,
            "min": round(min(pos_scores), 4) if pos_scores else 0,
            "max": round(max(pos_scores), 4) if pos_scores else 0,
        },
        "negative_scores": {
            "mean": round(sum(neg_scores) / len(neg_scores), 4) if neg_scores else 0,
            "min": round(min(neg_scores), 4) if neg_scores else 0,
            "max": round(max(neg_scores), 4) if neg_scores else 0,
        },
    }

    # PR curve
    pr_curve = sweep_thresholds(scored, args.n_points)

    # Operational thresholds
    operational = {
        "is_fit_0.4": compute_pr_at_threshold(scored, 0.4),
        "qualified_0.3": compute_pr_at_threshold(scored, 0.3),
        "held_0.1": compute_pr_at_threshold(scored, 0.1),
    }

    # Best recall under precision constraints
    recall_analysis = {
        "recall_at_precision_0.50": best_recall_under_precision(pr_curve, 0.50),
        "recall_at_precision_0.70": best_recall_under_precision(pr_curve, 0.70),
        "recall_at_precision_0.80": best_recall_under_precision(pr_curve, 0.80),
    }

    # Best F1
    best_f1 = max(pr_curve, key=lambda p: p["f1"]) if pr_curve else {}

    report = {
        "score_stats": score_stats,
        "operational_thresholds": operational,
        "recall_analysis": recall_analysis,
        "best_f1": best_f1,
        "pr_curve_points": len(pr_curve),
        "pr_curve": pr_curve,
    }

    output = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(output)
        print(f"Report written to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()

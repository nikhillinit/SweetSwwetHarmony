#!/usr/bin/env python3
"""
Thesis Matcher Evaluation on Ground Truth

Phase 0C: Data-Driven Tuning

Evaluates thesis matcher accuracy by running it on ground truth labeled
companies and comparing predictions to human labels.

Metrics computed:
- Precision: Of predicted positives, how many are actually positive?
- Recall: Of actual positives, how many did we predict?
- F1 Score: Harmonic mean of precision and recall
- Confusion matrix by routing decision

Usage:
    python scripts/thesis_eval.py \\
        --ground-truth ground_truth.jsonl \\
        --out eval_results.jsonl

    # Just print metrics
    python scripts/thesis_eval.py \\
        --ground-truth ground_truth.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.thesis_matcher import ThesisMatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    """Evaluation result for a single company."""
    company_name: str
    gt_label: str  # POSITIVE or NEGATIVE
    gt_status: str
    predicted_score: float
    predicted_routing: str  # QUALIFIED, HELD, REJECTED
    predicted_thesis: str
    predicted_fit: bool  # score >= 0.4
    description: str
    website: str


def load_ground_truth(path: str) -> List[Dict]:
    """Load ground truth from JSONL file."""
    records = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def route_score(score: float) -> str:
    """Determine routing decision from score."""
    if score >= 0.3:
        return "QUALIFIED"
    if score >= 0.1:
        return "HELD"
    return "REJECTED"


def evaluate(
    ground_truth: List[Dict],
    matcher: ThesisMatcher,
    min_desc_len: int = 10,
) -> Tuple[List[EvalResult], Dict]:
    """
    Evaluate thesis matcher on ground truth.

    Args:
        ground_truth: List of ground truth records
        matcher: ThesisMatcher instance
        min_desc_len: Minimum description length to evaluate

    Returns:
        Tuple of (results list, metrics dict)
    """
    results = []
    skipped_no_desc = 0

    for gt in ground_truth:
        company_name = gt.get('company_name', '')
        description = gt.get('description', '')
        website = gt.get('website', '')
        gt_label = gt.get('label', 'UNKNOWN')
        gt_status = gt.get('status', '')

        # Use company name + website domain as text if no description
        text = description
        if len(text) < min_desc_len:
            # Fallback to company name
            text = company_name
            if website:
                # Extract domain for context
                from urllib.parse import urlparse
                try:
                    parsed = urlparse(website if website.startswith('http') else 'https://' + website)
                    domain = parsed.netloc.replace('www.', '')
                    text = f"{company_name} {domain}"
                except:
                    pass

        if len(text) < min_desc_len:
            skipped_no_desc += 1
            continue

        # Run thesis matcher
        fit = matcher.score(text, company_name=company_name)

        results.append(EvalResult(
            company_name=company_name,
            gt_label=gt_label,
            gt_status=gt_status,
            predicted_score=fit.score,
            predicted_routing=route_score(fit.score),
            predicted_thesis=fit.thesis.value,
            predicted_fit=fit.is_fit,
            description=description[:100] if description else '',
            website=website,
        ))

    # Compute metrics
    metrics = compute_metrics(results)
    metrics['skipped_no_desc'] = skipped_no_desc
    metrics['evaluated'] = len(results)

    return results, metrics


def compute_metrics(results: List[EvalResult]) -> Dict:
    """Compute evaluation metrics."""
    # True/False Positive/Negative counts
    # POSITIVE label = thesis fit (should be qualified/held)
    # NEGATIVE label = thesis reject (should be rejected)

    # For is_fit threshold (score >= 0.4)
    tp_fit = sum(1 for r in results if r.gt_label == 'POSITIVE' and r.predicted_fit)
    fp_fit = sum(1 for r in results if r.gt_label == 'NEGATIVE' and r.predicted_fit)
    fn_fit = sum(1 for r in results if r.gt_label == 'POSITIVE' and not r.predicted_fit)
    tn_fit = sum(1 for r in results if r.gt_label == 'NEGATIVE' and not r.predicted_fit)

    # For routing (QUALIFIED/HELD vs REJECTED)
    # Predicted positive = QUALIFIED or HELD
    tp_route = sum(1 for r in results if r.gt_label == 'POSITIVE' and r.predicted_routing != 'REJECTED')
    fp_route = sum(1 for r in results if r.gt_label == 'NEGATIVE' and r.predicted_routing != 'REJECTED')
    fn_route = sum(1 for r in results if r.gt_label == 'POSITIVE' and r.predicted_routing == 'REJECTED')
    tn_route = sum(1 for r in results if r.gt_label == 'NEGATIVE' and r.predicted_routing == 'REJECTED')

    # Compute precision, recall, F1
    def safe_div(a, b):
        return a / b if b > 0 else 0.0

    precision_fit = safe_div(tp_fit, tp_fit + fp_fit)
    recall_fit = safe_div(tp_fit, tp_fit + fn_fit)
    f1_fit = safe_div(2 * precision_fit * recall_fit, precision_fit + recall_fit)

    precision_route = safe_div(tp_route, tp_route + fp_route)
    recall_route = safe_div(tp_route, tp_route + fn_route)
    f1_route = safe_div(2 * precision_route * recall_route, precision_route + recall_route)

    # Confusion matrix by routing
    confusion = defaultdict(lambda: defaultdict(int))
    for r in results:
        confusion[r.gt_label][r.predicted_routing] += 1

    # Score distribution by label
    pos_scores = [r.predicted_score for r in results if r.gt_label == 'POSITIVE']
    neg_scores = [r.predicted_score for r in results if r.gt_label == 'NEGATIVE']

    return {
        'total': len(results),
        'positive_count': sum(1 for r in results if r.gt_label == 'POSITIVE'),
        'negative_count': sum(1 for r in results if r.gt_label == 'NEGATIVE'),

        # is_fit metrics (score >= 0.4)
        'is_fit': {
            'precision': round(precision_fit, 3),
            'recall': round(recall_fit, 3),
            'f1': round(f1_fit, 3),
            'tp': tp_fit,
            'fp': fp_fit,
            'fn': fn_fit,
            'tn': tn_fit,
        },

        # Routing metrics (QUALIFIED/HELD vs REJECTED)
        'routing': {
            'precision': round(precision_route, 3),
            'recall': round(recall_route, 3),
            'f1': round(f1_route, 3),
            'tp': tp_route,
            'fp': fp_route,
            'fn': fn_route,
            'tn': tn_route,
        },

        # Confusion matrix
        'confusion': {label: dict(routings) for label, routings in confusion.items()},

        # Score stats
        'positive_scores': {
            'mean': round(sum(pos_scores) / len(pos_scores), 3) if pos_scores else 0,
            'min': round(min(pos_scores), 3) if pos_scores else 0,
            'max': round(max(pos_scores), 3) if pos_scores else 0,
        },
        'negative_scores': {
            'mean': round(sum(neg_scores) / len(neg_scores), 3) if neg_scores else 0,
            'min': round(min(neg_scores), 3) if neg_scores else 0,
            'max': round(max(neg_scores), 3) if neg_scores else 0,
        },
    }


def print_metrics(metrics: Dict):
    """Print evaluation metrics."""
    print("\n" + "=" * 60)
    print("THESIS MATCHER EVALUATION RESULTS")
    print("=" * 60)

    print(f"\nDataset:")
    print(f"  Evaluated:  {metrics['evaluated']}")
    print(f"  Skipped:    {metrics['skipped_no_desc']} (no description)")
    print(f"  POSITIVE:   {metrics['positive_count']}")
    print(f"  NEGATIVE:   {metrics['negative_count']}")

    print(f"\n--- is_fit Metrics (score >= 0.4) ---")
    m = metrics['is_fit']
    print(f"  Precision:  {m['precision']:.1%}")
    print(f"  Recall:     {m['recall']:.1%}")
    print(f"  F1 Score:   {m['f1']:.1%}")
    print(f"  TP={m['tp']} FP={m['fp']} FN={m['fn']} TN={m['tn']}")

    print(f"\n--- Routing Metrics (QUALIFIED/HELD vs REJECTED) ---")
    m = metrics['routing']
    print(f"  Precision:  {m['precision']:.1%}")
    print(f"  Recall:     {m['recall']:.1%}")
    print(f"  F1 Score:   {m['f1']:.1%}")
    print(f"  TP={m['tp']} FP={m['fp']} FN={m['fn']} TN={m['tn']}")

    print(f"\n--- Confusion Matrix (Label x Routing) ---")
    confusion = metrics['confusion']
    print(f"{'Label':<12} {'QUALIFIED':>10} {'HELD':>10} {'REJECTED':>10}")
    print("-" * 45)
    for label in ['POSITIVE', 'NEGATIVE']:
        if label in confusion:
            q = confusion[label].get('QUALIFIED', 0)
            h = confusion[label].get('HELD', 0)
            r = confusion[label].get('REJECTED', 0)
            print(f"{label:<12} {q:>10} {h:>10} {r:>10}")

    print(f"\n--- Score Distribution ---")
    print(f"  POSITIVE: mean={metrics['positive_scores']['mean']:.2f}, "
          f"min={metrics['positive_scores']['min']:.2f}, "
          f"max={metrics['positive_scores']['max']:.2f}")
    print(f"  NEGATIVE: mean={metrics['negative_scores']['mean']:.2f}, "
          f"min={metrics['negative_scores']['min']:.2f}, "
          f"max={metrics['negative_scores']['max']:.2f}")


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate thesis matcher on ground truth labels",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--ground-truth",
        type=str,
        required=True,
        help="Path to ground truth JSONL file",
    )
    parser.add_argument(
        "--out",
        type=str,
        help="Output JSONL file for detailed results",
    )
    parser.add_argument(
        "--min-desc-len",
        type=int,
        default=5,
        help="Minimum text length to evaluate (default: 5)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load ground truth
    logger.info(f"Loading ground truth from {args.ground_truth}")
    ground_truth = load_ground_truth(args.ground_truth)
    logger.info(f"Loaded {len(ground_truth)} records")

    # Initialize matcher
    logger.info("Initializing ThesisMatcher...")
    matcher = ThesisMatcher(v2_enablement="disabled")

    # Evaluate
    logger.info("Running evaluation...")
    results, metrics = evaluate(ground_truth, matcher, min_desc_len=args.min_desc_len)

    # Print metrics
    print_metrics(metrics)

    # Write detailed results
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            for r in results:
                f.write(json.dumps({
                    'company_name': r.company_name,
                    'gt_label': r.gt_label,
                    'gt_status': r.gt_status,
                    'predicted_score': round(r.predicted_score, 3),
                    'predicted_routing': r.predicted_routing,
                    'predicted_thesis': r.predicted_thesis,
                    'predicted_fit': r.predicted_fit,
                    'description': r.description,
                    'website': r.website,
                }, ensure_ascii=False) + '\n')
        print(f"\nDetailed results written to {args.out}")

    # Write metrics summary
    metrics_file = args.out.replace('.jsonl', '_metrics.json') if args.out else None
    if metrics_file:
        with open(metrics_file, 'w') as f:
            json.dump(metrics, f, indent=2)
        print(f"Metrics written to {metrics_file}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Train ML Thesis Classifier.

Trains a TF-IDF + LogisticRegression binary classifier for thesis fit
rescue. Uses the shared build_ml_text() to prevent training/serving skew.

Outputs:
- Trained model to models/thesis_classifier.joblib
- Evaluation metrics to models/thesis_classifier_metrics.json
- Per-category stratified analysis (Review 3 requirement)

Usage:
    python scripts/train_thesis_model.py \\
        --ground-truth datasets/thesis_ground_truth.jsonl \\
        --out models/thesis_classifier.joblib

    python scripts/train_thesis_model.py \\
        --ground-truth datasets/thesis_ground_truth.jsonl \\
        --out models/thesis_classifier.joblib \\
        --category-analysis
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.ml_text_builder import build_ml_text
from utils.ml_thesis_model import MLThesisModel


def load_ground_truth(path: str) -> List[Dict[str, Any]]:
    """Load JSONL ground truth dataset."""
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def prepare_training_data(
    records: List[Dict[str, Any]],
) -> Tuple[List[str], List[int], List[Optional[str]]]:
    """Prepare training data from ground truth records.

    Uses build_ml_text() for consistent text construction (fixes
    training/serving skew identified in Review 1).

    Returns:
        Tuple of (texts, labels, categories)
    """
    texts = []
    labels = []
    categories = []
    skipped = 0

    for record in records:
        label_str = record.get("expected_classification", record.get("label", ""))
        if label_str not in ("POSITIVE", "NEGATIVE"):
            skipped += 1
            continue

        description = record.get("description", "") or ""
        company_name = record.get("company_name")
        domain = record.get("website") or record.get("domain")

        # Use shared text builder (prevents training/serving skew)
        text = build_ml_text(description, company_name, domain)
        if not text:
            skipped += 1
            continue

        texts.append(text)
        labels.append(1 if label_str == "POSITIVE" else 0)
        categories.append(record.get("expected_category", record.get("category")))

    if skipped:
        print(f"Skipped {skipped} records (invalid label or empty text)", file=sys.stderr)

    return texts, labels, categories


def category_analysis(
    texts: List[str],
    labels: List[int],
    categories: List[Optional[str]],
    model: MLThesisModel,
) -> Dict[str, Any]:
    """Per-category stratified analysis (Review 3 requirement).

    Validates the "one model fits all" assumption by checking if
    recall varies significantly across thesis categories.
    """
    from sklearn.metrics import precision_score, recall_score, f1_score

    cat_results = {}

    # Group by category
    cat_data: Dict[str, List[Tuple[str, int]]] = {}
    for text, label, cat in zip(texts, labels, categories):
        cat_key = cat or "unknown"
        if cat_key not in cat_data:
            cat_data[cat_key] = []
        cat_data[cat_key].append((text, label))

    for cat, data in sorted(cat_data.items()):
        cat_texts = [t for t, _ in data]
        cat_labels = [l for _, l in data]

        if sum(cat_labels) == 0 or sum(cat_labels) == len(cat_labels):
            cat_results[cat] = {
                "count": len(data),
                "positive": sum(cat_labels),
                "note": "All same class - cannot compute meaningful metrics",
            }
            continue

        preds = [1 if model.predict_proba(t) > 0.5 else 0 for t in cat_texts]
        cat_results[cat] = {
            "count": len(data),
            "positive": sum(cat_labels),
            "negative": len(cat_labels) - sum(cat_labels),
            "precision": round(precision_score(cat_labels, preds, zero_division=0), 4),
            "recall": round(recall_score(cat_labels, preds, zero_division=0), 4),
            "f1": round(f1_score(cat_labels, preds, zero_division=0), 4),
        }

    return cat_results


def main():
    parser = argparse.ArgumentParser(description="Train ML thesis classifier")
    parser.add_argument(
        "--ground-truth",
        required=True,
        help="Path to ground truth JSONL file",
    )
    parser.add_argument(
        "--out",
        default="models/thesis_classifier.joblib",
        help="Output model path",
    )
    parser.add_argument(
        "--metrics-out",
        default=None,
        help="Output metrics JSON path (default: <out>.metrics.json)",
    )
    parser.add_argument(
        "--category-analysis",
        action="store_true",
        help="Run per-category stratified analysis",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility",
    )
    args = parser.parse_args()

    metrics_out = args.metrics_out or args.out.replace(".joblib", "_metrics.json")

    # Load and prepare data
    records = load_ground_truth(args.ground_truth)
    print(f"Loaded {len(records)} ground truth records", file=sys.stderr)

    texts, labels, categories = prepare_training_data(records)
    print(
        f"Prepared {len(texts)} training samples "
        f"({sum(labels)} positive, {len(labels) - sum(labels)} negative)",
        file=sys.stderr,
    )

    if len(texts) < 20:
        print("ERROR: Too few samples for training", file=sys.stderr)
        sys.exit(1)

    # Data quality report
    label_dist = Counter(labels)
    cat_dist = Counter(c for c in categories if c)
    print(f"Label distribution: {dict(label_dist)}", file=sys.stderr)
    print(f"Category distribution: {dict(cat_dist)}", file=sys.stderr)

    # Train
    model = MLThesisModel()
    metrics = model.train(texts, labels, random_state=args.seed)

    print(f"\nTraining Results:", file=sys.stderr)
    print(f"  Precision: {metrics.precision:.4f}", file=sys.stderr)
    print(f"  Recall:    {metrics.recall:.4f}", file=sys.stderr)
    print(f"  F1:        {metrics.f1:.4f}", file=sys.stderr)
    print(f"  Accuracy:  {metrics.accuracy:.4f}", file=sys.stderr)
    print(f"  CV F1:     {metrics.cv_f1_mean:.4f} ± {metrics.cv_f1_std:.4f}", file=sys.stderr)

    # Save model
    model_id = model.save(args.out)
    print(f"\nModel saved to {args.out} (model_id={model_id})", file=sys.stderr)

    # Feature importances
    importances = model.get_feature_importances(top_n=20)

    # Category analysis (Review 3)
    cat_report = {}
    if args.category_analysis:
        print("\nPer-category analysis:", file=sys.stderr)
        cat_report = category_analysis(texts, labels, categories, model)
        for cat, stats in cat_report.items():
            if "precision" in stats:
                print(
                    f"  {cat}: P={stats['precision']:.3f} R={stats['recall']:.3f} "
                    f"F1={stats['f1']:.3f} (n={stats['count']})",
                    file=sys.stderr,
                )
            else:
                print(f"  {cat}: {stats['note']} (n={stats['count']})", file=sys.stderr)

    # Save metrics
    metrics_report = {
        "model_id": model_id,
        "model_version": model.__version__,
        "metrics": asdict(metrics),
        "top_features": importances,
        "category_analysis": cat_report,
        "data_quality": {
            "total_records": len(records),
            "training_samples": len(texts),
            "skipped": len(records) - len(texts),
            "label_distribution": dict(label_dist),
            "category_distribution": dict(cat_dist),
        },
    }

    with open(metrics_out, "w") as f:
        json.dump(metrics_report, f, indent=2)
    print(f"Metrics saved to {metrics_out}", file=sys.stderr)


if __name__ == "__main__":
    main()

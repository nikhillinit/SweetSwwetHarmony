"""
Thesis Classification Evaluation Harness

Evaluates keyword matcher and LLM classifier accuracy against ground truth datasets.
Provides per-class metrics, confusion matrices, and trend tracking.

Usage:
    from utils.thesis_evaluator import ThesisEvaluator, KeywordEvaluator

    # Evaluate keyword matcher only
    evaluator = KeywordEvaluator()
    result = await evaluator.evaluate("datasets/thesis_sample.jsonl")
    print(f"Accuracy: {result.accuracy:.1%}")

    # Compare keyword vs LLM
    evaluator = ThesisEvaluator()
    comparison = await evaluator.evaluate_both("datasets/thesis_sample.jsonl")
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.thesis_matcher import ThesisMatcher, ThesisFit

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ClassMetrics:
    """Per-class precision, recall, F1 metrics."""
    precision: float
    recall: float
    f1: float
    support: int  # Number of true samples for this class

    def to_dict(self) -> Dict[str, Any]:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "support": self.support,
        }


@dataclass
class ThesisEvaluationResult:
    """Result of a thesis classification evaluation run."""
    run_id: str
    evaluator_type: str  # "keyword" or "llm"
    dataset_path: str
    total_samples: int
    accuracy: float
    per_class_metrics: Dict[str, ClassMetrics]  # QUALIFIED/HELD/REJECTED
    confusion_matrix: Dict[str, Dict[str, int]]  # {actual: {predicted: count}}
    timestamp: str
    latency_ms: Optional[int] = None  # Total evaluation time
    avg_latency_ms: Optional[float] = None  # Per-sample latency (for LLM)
    token_usage: Optional[Dict[str, int]] = None  # {input_tokens, output_tokens}
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "evaluator_type": self.evaluator_type,
            "dataset_path": self.dataset_path,
            "total_samples": self.total_samples,
            "accuracy": round(self.accuracy, 4),
            "per_class_metrics": {
                k: v.to_dict() for k, v in self.per_class_metrics.items()
            },
            "confusion_matrix": self.confusion_matrix,
            "timestamp": self.timestamp,
            "latency_ms": self.latency_ms,
            "avg_latency_ms": round(self.avg_latency_ms, 2) if self.avg_latency_ms else None,
            "token_usage": self.token_usage,
            "errors": self.errors,
        }


@dataclass
class EvaluationComparison:
    """Side-by-side comparison of keyword vs LLM evaluation."""
    keyword_result: ThesisEvaluationResult
    llm_result: Optional[ThesisEvaluationResult]
    accuracy_delta: Optional[float]  # LLM - keyword
    per_class_deltas: Dict[str, Dict[str, float]]  # {class: {metric: delta}}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "keyword_result": self.keyword_result.to_dict(),
            "llm_result": self.llm_result.to_dict() if self.llm_result else None,
            "accuracy_delta": round(self.accuracy_delta, 4) if self.accuracy_delta else None,
            "per_class_deltas": self.per_class_deltas,
        }


# =============================================================================
# CONSTANTS
# =============================================================================

VALID_LABELS = {"QUALIFIED", "HELD", "REJECTED"}

# Classification thresholds for keyword matcher
KEYWORD_QUALIFIED_THRESHOLD = 0.3  # score >= 0.3 AND no negatives → QUALIFIED
KEYWORD_HELD_THRESHOLD = 0.0  # score < 0.3 → HELD


# =============================================================================
# DATASET LOADING
# =============================================================================

def load_evaluation_dataset(path: str | Path) -> List[Dict[str, Any]]:
    """
    Load JSONL evaluation dataset.

    Args:
        path: Path to JSONL file

    Returns:
        List of sample dicts with input, target, id, metadata
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                sample = json.loads(line)
                samples.append(sample)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON on line {line_num}: {e}")

    return samples


# =============================================================================
# METRIC CALCULATION
# =============================================================================

def calculate_metrics(
    predictions: List[str],
    targets: List[str],
) -> Tuple[float, Dict[str, ClassMetrics], Dict[str, Dict[str, int]]]:
    """
    Calculate accuracy, per-class metrics, and confusion matrix.

    Args:
        predictions: List of predicted labels
        targets: List of ground truth labels

    Returns:
        Tuple of (accuracy, per_class_metrics, confusion_matrix)
    """
    if len(predictions) != len(targets):
        raise ValueError("predictions and targets must have same length")

    if len(predictions) == 0:
        return 0.0, {}, {}

    # Calculate accuracy
    correct = sum(1 for p, t in zip(predictions, targets) if p == t)
    accuracy = correct / len(predictions)

    # Build confusion matrix
    confusion: Dict[str, Dict[str, int]] = {
        label: {l: 0 for l in VALID_LABELS} for label in VALID_LABELS
    }

    for pred, target in zip(predictions, targets):
        if target in confusion and pred in confusion[target]:
            confusion[target][pred] += 1

    # Calculate per-class metrics
    per_class: Dict[str, ClassMetrics] = {}

    for label in VALID_LABELS:
        # True positives: predicted label AND actual label
        tp = confusion[label][label]

        # False positives: predicted label but actual was different
        fp = sum(
            confusion[actual][label]
            for actual in VALID_LABELS
            if actual != label
        )

        # False negatives: actual label but predicted different
        fn = sum(
            confusion[label][pred]
            for pred in VALID_LABELS
            if pred != label
        )

        # Support: total actual samples of this class
        support = sum(confusion[label].values())

        # Calculate precision, recall, F1
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        per_class[label] = ClassMetrics(
            precision=precision,
            recall=recall,
            f1=f1,
            support=support,
        )

    return accuracy, per_class, confusion


# =============================================================================
# KEYWORD EVALUATOR
# =============================================================================

class KeywordEvaluator:
    """
    Evaluates ThesisMatcher keyword classifier against ground truth.

    Classification mapping:
    - score >= 0.3 AND no negative keywords → QUALIFIED
    - score < 0.3 → HELD
    - negative keywords found → REJECTED
    - category == "excluded" from metadata → REJECTED
    """

    def __init__(self):
        self.matcher = ThesisMatcher()

    def classify_sample(self, sample: Dict[str, Any]) -> str:
        """
        Classify a single sample using keyword matcher.

        Args:
            sample: Dataset sample with input text

        Returns:
            Classification label: QUALIFIED, HELD, or REJECTED
        """
        input_text = sample.get("input", "")
        metadata = sample.get("metadata", {})

        # Check for explicit exclusion in metadata
        sector = metadata.get("sector", "").lower()
        excluded_sectors = {"crypto", "developer_tools", "b2b_saas", "services", "b2b_marketplace"}
        if sector in excluded_sectors:
            return "REJECTED"

        # Run keyword matcher
        fit = self.matcher.score(input_text)

        # Check for negative keywords (exclusion signals)
        if fit.negative_keywords:
            return "REJECTED"

        # Check thesis fit score
        if fit.score >= KEYWORD_QUALIFIED_THRESHOLD:
            return "QUALIFIED"
        else:
            return "HELD"

    async def evaluate(
        self,
        dataset_path: str | Path,
    ) -> ThesisEvaluationResult:
        """
        Evaluate keyword matcher on dataset.

        Args:
            dataset_path: Path to JSONL dataset

        Returns:
            ThesisEvaluationResult with metrics
        """
        start_time = time.time()
        run_id = f"kw_{uuid.uuid4().hex[:8]}"

        # Load dataset
        samples = load_evaluation_dataset(dataset_path)

        # Classify all samples
        predictions = []
        targets = []
        errors = []

        for sample in samples:
            try:
                pred = self.classify_sample(sample)
                predictions.append(pred)
                targets.append(sample["target"])
            except Exception as e:
                errors.append(f"Sample {sample.get('id')}: {str(e)}")
                predictions.append("HELD")  # Default on error
                targets.append(sample["target"])

        # Calculate metrics
        accuracy, per_class, confusion = calculate_metrics(predictions, targets)

        latency_ms = int((time.time() - start_time) * 1000)

        return ThesisEvaluationResult(
            run_id=run_id,
            evaluator_type="keyword",
            dataset_path=str(dataset_path),
            total_samples=len(samples),
            accuracy=accuracy,
            per_class_metrics=per_class,
            confusion_matrix=confusion,
            timestamp=datetime.now(timezone.utc).isoformat(),
            latency_ms=latency_ms,
            errors=errors,
        )

    def evaluate_sync(self, dataset_path: str | Path) -> ThesisEvaluationResult:
        """Synchronous version of evaluate."""
        import asyncio
        return asyncio.run(self.evaluate(dataset_path))


# =============================================================================
# LLM EVALUATOR
# =============================================================================

class LLMEvaluator:
    """
    Evaluates LLM classifier against ground truth.

    Classification mapping:
    - thesis_match=True AND thesis_fit_score >= 0.3 → QUALIFIED
    - thesis_match=True AND thesis_fit_score < 0.3 → HELD
    - thesis_match=False OR category="excluded" → REJECTED
    """

    def __init__(
        self,
        model: str = "gemini-2.0-flash",
        api_key: Optional[str] = None,
    ):
        self.model = model
        self.api_key = api_key
        self._classifier = None

    @property
    def classifier(self):
        """Lazy-load LLM classifier."""
        if self._classifier is None:
            from consumer.thesis_filter.llm_classifier import LLMClassifier
            self._classifier = LLMClassifier(
                model=self.model,
                api_key=self.api_key,
            )
        return self._classifier

    def _parse_input_to_signal(self, input_text: str) -> Dict[str, Any]:
        """Parse dataset input text into signal_data format for classifier."""
        lines = input_text.strip().split("\n")
        signal_data = {
            "title": "",
            "url": "",
            "source_api": "evaluation",
            "source_context": "",
        }

        for line in lines:
            if line.startswith("Company:"):
                signal_data["title"] = line.replace("Company:", "").strip()
            elif line.startswith("Description:"):
                signal_data["source_context"] = line.replace("Description:", "").strip()
            elif line.startswith("Website:"):
                signal_data["url"] = line.replace("Website:", "").strip()
            elif line.startswith("Sector:"):
                # Add sector to context
                signal_data["source_context"] += f" Sector: {line.replace('Sector:', '').strip()}"

        return signal_data

    def classify_result_to_label(self, result) -> str:
        """
        Convert LLM classification result to evaluation label.

        Args:
            result: ThesisClassification from LLM classifier

        Returns:
            Label: QUALIFIED, HELD, or REJECTED
        """
        # Excluded category → REJECTED
        if result.category == "excluded":
            return "REJECTED"

        # No thesis match → REJECTED
        if not result.thesis_match:
            return "REJECTED"

        # Thesis match with high enough score → QUALIFIED
        if result.thesis_fit_score >= 0.3:
            return "QUALIFIED"
        else:
            return "HELD"

    async def evaluate(
        self,
        dataset_path: str | Path,
    ) -> ThesisEvaluationResult:
        """
        Evaluate LLM classifier on dataset.

        Args:
            dataset_path: Path to JSONL dataset

        Returns:
            ThesisEvaluationResult with metrics
        """
        start_time = time.time()
        run_id = f"llm_{uuid.uuid4().hex[:8]}"

        # Load dataset
        samples = load_evaluation_dataset(dataset_path)

        # Classify all samples
        predictions = []
        targets = []
        errors = []
        total_input_tokens = 0
        total_output_tokens = 0
        sample_latencies = []

        for sample in samples:
            try:
                # Parse input to signal format
                signal_data = self._parse_input_to_signal(sample["input"])

                # Classify with LLM
                sample_start = time.time()
                result = await self.classifier.classify(signal_data)
                sample_latency = int((time.time() - sample_start) * 1000)
                sample_latencies.append(sample_latency)

                # Track token usage
                if result.input_tokens:
                    total_input_tokens += result.input_tokens
                if result.output_tokens:
                    total_output_tokens += result.output_tokens

                # Convert to evaluation label
                pred = self.classify_result_to_label(result)
                predictions.append(pred)
                targets.append(sample["target"])

            except Exception as e:
                errors.append(f"Sample {sample.get('id')}: {str(e)}")
                predictions.append("HELD")  # Default on error
                targets.append(sample["target"])

        # Calculate metrics
        accuracy, per_class, confusion = calculate_metrics(predictions, targets)

        latency_ms = int((time.time() - start_time) * 1000)
        avg_latency = sum(sample_latencies) / len(sample_latencies) if sample_latencies else None

        return ThesisEvaluationResult(
            run_id=run_id,
            evaluator_type="llm",
            dataset_path=str(dataset_path),
            total_samples=len(samples),
            accuracy=accuracy,
            per_class_metrics=per_class,
            confusion_matrix=confusion,
            timestamp=datetime.now(timezone.utc).isoformat(),
            latency_ms=latency_ms,
            avg_latency_ms=avg_latency,
            token_usage={
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
            errors=errors,
        )


# =============================================================================
# THESIS EVALUATOR (ORCHESTRATOR)
# =============================================================================

class ThesisEvaluator:
    """
    Orchestrates keyword and LLM evaluation with comparison.
    """

    def __init__(
        self,
        llm_model: str = "gemini-2.0-flash",
        llm_api_key: Optional[str] = None,
    ):
        self.keyword_evaluator = KeywordEvaluator()
        self.llm_evaluator = LLMEvaluator(
            model=llm_model,
            api_key=llm_api_key,
        )

    async def evaluate_keyword(
        self,
        dataset_path: str | Path,
    ) -> ThesisEvaluationResult:
        """Evaluate keyword matcher only."""
        return await self.keyword_evaluator.evaluate(dataset_path)

    async def evaluate_llm(
        self,
        dataset_path: str | Path,
    ) -> ThesisEvaluationResult:
        """Evaluate LLM classifier only."""
        return await self.llm_evaluator.evaluate(dataset_path)

    async def evaluate_both(
        self,
        dataset_path: str | Path,
        skip_llm: bool = False,
    ) -> EvaluationComparison:
        """
        Evaluate both keyword and LLM classifiers.

        Args:
            dataset_path: Path to JSONL dataset
            skip_llm: If True, only run keyword evaluation

        Returns:
            EvaluationComparison with side-by-side results
        """
        # Run keyword evaluation
        keyword_result = await self.keyword_evaluator.evaluate(dataset_path)

        llm_result = None
        accuracy_delta = None
        per_class_deltas: Dict[str, Dict[str, float]] = {}

        if not skip_llm:
            try:
                llm_result = await self.llm_evaluator.evaluate(dataset_path)

                # Calculate deltas
                accuracy_delta = llm_result.accuracy - keyword_result.accuracy

                for label in VALID_LABELS:
                    kw_metrics = keyword_result.per_class_metrics.get(label)
                    llm_metrics = llm_result.per_class_metrics.get(label)

                    if kw_metrics and llm_metrics:
                        per_class_deltas[label] = {
                            "precision": llm_metrics.precision - kw_metrics.precision,
                            "recall": llm_metrics.recall - kw_metrics.recall,
                            "f1": llm_metrics.f1 - kw_metrics.f1,
                        }

            except Exception as e:
                logger.error(f"LLM evaluation failed: {e}")
                # Continue with keyword-only results

        return EvaluationComparison(
            keyword_result=keyword_result,
            llm_result=llm_result,
            accuracy_delta=accuracy_delta,
            per_class_deltas=per_class_deltas,
        )


# =============================================================================
# OUTPUT FORMATTING
# =============================================================================

def format_evaluation_result(result: ThesisEvaluationResult) -> str:
    """Format evaluation result as human-readable text."""
    lines = [
        f"Thesis Evaluation Results ({result.evaluator_type.upper()})",
        "=" * 50,
        f"Dataset: {result.dataset_path}",
        f"Samples: {result.total_samples}",
        f"Timestamp: {result.timestamp}",
        f"",
        f"Overall Accuracy: {result.accuracy:.1%}",
        f"",
        "Per-Class Metrics:",
    ]

    for label in ["QUALIFIED", "HELD", "REJECTED"]:
        metrics = result.per_class_metrics.get(label)
        if metrics:
            lines.append(f"  {label}:")
            lines.append(f"    Precision: {metrics.precision:.2f}")
            lines.append(f"    Recall:    {metrics.recall:.2f}")
            lines.append(f"    F1:        {metrics.f1:.2f}")
            lines.append(f"    Support:   {metrics.support}")

    lines.append("")
    lines.append("Confusion Matrix (rows=actual, cols=predicted):")
    lines.append(f"              {'QUALIFIED':>10} {'HELD':>10} {'REJECTED':>10}")

    for actual in ["QUALIFIED", "HELD", "REJECTED"]:
        row = result.confusion_matrix.get(actual, {})
        q = row.get("QUALIFIED", 0)
        h = row.get("HELD", 0)
        r = row.get("REJECTED", 0)
        lines.append(f"  {actual:10} {q:>10} {h:>10} {r:>10}")

    if result.latency_ms:
        lines.append("")
        lines.append(f"Total Latency: {result.latency_ms}ms")

    if result.avg_latency_ms:
        lines.append(f"Avg per Sample: {result.avg_latency_ms:.1f}ms")

    if result.token_usage:
        lines.append(f"Tokens: {result.token_usage.get('input_tokens', 0)} in, "
                     f"{result.token_usage.get('output_tokens', 0)} out")

    if result.errors:
        lines.append("")
        lines.append(f"Errors ({len(result.errors)}):")
        for err in result.errors[:5]:
            lines.append(f"  - {err}")

    return "\n".join(lines)


def format_comparison(comparison: EvaluationComparison) -> str:
    """Format side-by-side comparison as human-readable text."""
    kw = comparison.keyword_result
    llm = comparison.llm_result

    lines = [
        "Thesis Evaluation Results",
        "=" * 60,
        f"Dataset: {kw.dataset_path} ({kw.total_samples} samples)",
        f"Timestamp: {kw.timestamp}",
        "",
        f"{'':20} {'KEYWORD':>12} {'LLM':>12} {'DELTA':>12}",
        "-" * 60,
    ]

    # Overall accuracy
    kw_acc = f"{kw.accuracy:.1%}"
    if llm:
        llm_acc = f"{llm.accuracy:.1%}"
        delta = comparison.accuracy_delta or 0
        delta_str = f"{delta:+.1%}" if delta else "N/A"
    else:
        llm_acc = "N/A"
        delta_str = "N/A"

    lines.append(f"{'Overall Accuracy':20} {kw_acc:>12} {llm_acc:>12} {delta_str:>12}")
    lines.append("")
    lines.append("Per-Class Metrics:")

    for label in ["QUALIFIED", "HELD", "REJECTED"]:
        lines.append(f"  {label}:")

        kw_m = kw.per_class_metrics.get(label)
        llm_m = llm.per_class_metrics.get(label) if llm else None
        deltas = comparison.per_class_deltas.get(label, {})

        for metric in ["precision", "recall", "f1"]:
            kw_val = getattr(kw_m, metric, 0) if kw_m else 0
            llm_val = getattr(llm_m, metric, 0) if llm_m else 0
            delta_val = deltas.get(metric, 0)

            kw_str = f"{kw_val:.2f}"
            llm_str = f"{llm_val:.2f}" if llm else "N/A"
            delta_str = f"{delta_val:+.2f}" if llm else "N/A"

            lines.append(f"    {metric.capitalize():14} {kw_str:>12} {llm_str:>12} {delta_str:>12}")

    # Trend indicator
    if comparison.accuracy_delta:
        if comparison.accuracy_delta > 0.01:
            trend = "LLM BETTER"
        elif comparison.accuracy_delta < -0.01:
            trend = "KEYWORD BETTER"
        else:
            trend = "SIMILAR"
        lines.append("")
        lines.append(f"Trend: {trend} (delta: {comparison.accuracy_delta:+.1%})")

    return "\n".join(lines)


# =============================================================================
# CLI (for testing)
# =============================================================================

if __name__ == "__main__":
    import asyncio
    import sys

    async def main():
        dataset_path = sys.argv[1] if len(sys.argv) > 1 else "datasets/thesis_sample.jsonl"

        print(f"Evaluating keyword matcher on {dataset_path}...")
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate(dataset_path)
        print(format_evaluation_result(result))

    asyncio.run(main())

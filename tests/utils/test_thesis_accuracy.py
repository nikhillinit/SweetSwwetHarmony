"""
Tests for thesis classifier accuracy.

Phase C5: Regression tests to ensure keyword matcher accuracy >= 0.88.
"""

import pytest

from utils.thesis_evaluator import KeywordEvaluator


class TestThesisAccuracy:
    """Regression tests for thesis classification accuracy."""

    @pytest.mark.asyncio
    async def test_keyword_accuracy_meets_target(self):
        """C5.5: Keyword matcher achieves >= 88% accuracy on thesis_sample.jsonl."""
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate("datasets/thesis_sample.jsonl")

        # Target accuracy is 88%
        assert result.accuracy >= 0.88, (
            f"Keyword matcher accuracy {result.accuracy:.1%} below 88% target. "
            f"Confusion matrix: {result.confusion_matrix}"
        )

    @pytest.mark.asyncio
    async def test_qualified_recall_high(self):
        """QUALIFIED class should have high recall (catch most good prospects)."""
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate("datasets/thesis_sample.jsonl")

        qualified_metrics = result.per_class_metrics.get("QUALIFIED")
        assert qualified_metrics is not None
        assert qualified_metrics.recall >= 0.90, (
            f"QUALIFIED recall {qualified_metrics.recall:.2f} below 90% target"
        )

    @pytest.mark.asyncio
    async def test_rejected_precision_high(self):
        """REJECTED class should have high precision (don't reject good prospects)."""
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate("datasets/thesis_sample.jsonl")

        rejected_metrics = result.per_class_metrics.get("REJECTED")
        assert rejected_metrics is not None
        assert rejected_metrics.precision >= 0.80, (
            f"REJECTED precision {rejected_metrics.precision:.2f} below 80% target"
        )

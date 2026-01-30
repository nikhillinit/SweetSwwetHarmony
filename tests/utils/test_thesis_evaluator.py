"""
Tests for thesis classification evaluation harness.

Tests KeywordEvaluator, LLMEvaluator, metric calculations, and formatting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pytest

from utils.thesis_evaluator import (
    ClassMetrics,
    ThesisEvaluationResult,
    EvaluationComparison,
    KeywordEvaluator,
    LLMEvaluator,
    ThesisEvaluator,
    calculate_metrics,
    load_evaluation_dataset,
    format_evaluation_result,
    format_comparison,
    VALID_LABELS,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def sample_dataset_path():
    """Path to sample dataset."""
    return Path(__file__).parent.parent.parent / "datasets" / "thesis_sample.jsonl"


@pytest.fixture
def minimal_dataset(tmp_path):
    """Create minimal dataset for testing."""
    samples = [
        {"input": "Company: Test1\nDescription: D2C meal kit", "target": "QUALIFIED", "id": "1", "metadata": {"company_name": "Test1", "sector": "consumer_cpg"}},
        {"input": "Company: Test2\nDescription: B2B enterprise SaaS", "target": "REJECTED", "id": "2", "metadata": {"company_name": "Test2", "sector": "b2b_saas"}},
        {"input": "Company: Test3\nDescription: Some startup", "target": "HELD", "id": "3", "metadata": {"company_name": "Test3", "sector": "unknown"}},
    ]

    dataset_file = tmp_path / "test.jsonl"
    with open(dataset_file, "w") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")

    return dataset_file


# =============================================================================
# TESTS: METRIC CALCULATION
# =============================================================================

class TestMetricCalculation:
    """Tests for calculate_metrics function."""

    def test_perfect_predictions(self):
        """Perfect predictions should give 100% accuracy."""
        predictions = ["QUALIFIED", "HELD", "REJECTED"]
        targets = ["QUALIFIED", "HELD", "REJECTED"]

        accuracy, per_class, confusion = calculate_metrics(predictions, targets)

        assert accuracy == 1.0
        assert per_class["QUALIFIED"].precision == 1.0
        assert per_class["QUALIFIED"].recall == 1.0
        assert per_class["QUALIFIED"].f1 == 1.0

    def test_all_wrong_predictions(self):
        """All wrong predictions should give 0% accuracy."""
        predictions = ["REJECTED", "QUALIFIED", "HELD"]
        targets = ["QUALIFIED", "HELD", "REJECTED"]

        accuracy, per_class, confusion = calculate_metrics(predictions, targets)

        assert accuracy == 0.0

    def test_partial_accuracy(self):
        """Partial correct predictions."""
        predictions = ["QUALIFIED", "QUALIFIED", "REJECTED", "REJECTED"]
        targets = ["QUALIFIED", "HELD", "REJECTED", "REJECTED"]

        accuracy, per_class, confusion = calculate_metrics(predictions, targets)

        assert accuracy == 0.75  # 3/4 correct

    def test_confusion_matrix_structure(self):
        """Confusion matrix should have correct structure."""
        predictions = ["QUALIFIED", "HELD", "REJECTED"]
        targets = ["QUALIFIED", "HELD", "REJECTED"]

        _, _, confusion = calculate_metrics(predictions, targets)

        # All labels should be present
        for label in VALID_LABELS:
            assert label in confusion
            for pred_label in VALID_LABELS:
                assert pred_label in confusion[label]

    def test_confusion_matrix_counts(self):
        """Confusion matrix counts should be correct."""
        predictions = ["QUALIFIED", "QUALIFIED", "HELD"]
        targets = ["QUALIFIED", "REJECTED", "HELD"]

        _, _, confusion = calculate_metrics(predictions, targets)

        # True positive for QUALIFIED
        assert confusion["QUALIFIED"]["QUALIFIED"] == 1

        # False positive: predicted QUALIFIED but was REJECTED
        assert confusion["REJECTED"]["QUALIFIED"] == 1

        # True positive for HELD
        assert confusion["HELD"]["HELD"] == 1

    def test_precision_calculation(self):
        """Precision should be TP / (TP + FP)."""
        predictions = ["QUALIFIED", "QUALIFIED", "QUALIFIED"]
        targets = ["QUALIFIED", "QUALIFIED", "REJECTED"]  # 1 FP

        _, per_class, _ = calculate_metrics(predictions, targets)

        # 2 TP, 1 FP → precision = 2/3
        assert per_class["QUALIFIED"].precision == pytest.approx(2/3, rel=0.01)

    def test_recall_calculation(self):
        """Recall should be TP / (TP + FN)."""
        predictions = ["QUALIFIED", "HELD", "HELD"]
        targets = ["QUALIFIED", "QUALIFIED", "HELD"]  # 1 FN for QUALIFIED

        _, per_class, _ = calculate_metrics(predictions, targets)

        # 1 TP, 1 FN for QUALIFIED → recall = 1/2
        assert per_class["QUALIFIED"].recall == pytest.approx(0.5, rel=0.01)

    def test_f1_calculation(self):
        """F1 should be harmonic mean of precision and recall."""
        predictions = ["QUALIFIED", "QUALIFIED", "HELD"]
        targets = ["QUALIFIED", "REJECTED", "HELD"]

        _, per_class, _ = calculate_metrics(predictions, targets)

        # For QUALIFIED: precision=0.5 (1 TP, 1 FP), recall=1.0 (1 TP, 0 FN)
        # F1 = 2 * 0.5 * 1.0 / (0.5 + 1.0) = 2/3
        assert per_class["QUALIFIED"].f1 == pytest.approx(2/3, rel=0.01)

    def test_empty_predictions(self):
        """Empty predictions should return zero metrics."""
        accuracy, per_class, confusion = calculate_metrics([], [])

        assert accuracy == 0.0
        assert per_class == {}
        assert confusion == {}

    def test_length_mismatch_raises(self):
        """Mismatched lengths should raise ValueError."""
        with pytest.raises(ValueError):
            calculate_metrics(["QUALIFIED"], ["QUALIFIED", "HELD"])

    def test_support_count(self):
        """Support should count true samples per class."""
        predictions = ["QUALIFIED", "QUALIFIED", "HELD", "REJECTED"]
        targets = ["QUALIFIED", "QUALIFIED", "QUALIFIED", "REJECTED"]

        _, per_class, _ = calculate_metrics(predictions, targets)

        assert per_class["QUALIFIED"].support == 3  # 3 actual QUALIFIED
        assert per_class["REJECTED"].support == 1   # 1 actual REJECTED
        assert per_class["HELD"].support == 0       # 0 actual HELD


# =============================================================================
# TESTS: DATASET LOADING
# =============================================================================

class TestDatasetLoading:
    """Tests for load_evaluation_dataset function."""

    def test_load_sample_dataset(self, sample_dataset_path):
        """Should load sample dataset successfully."""
        samples = load_evaluation_dataset(sample_dataset_path)

        assert len(samples) >= 20
        assert all("input" in s for s in samples)
        assert all("target" in s for s in samples)

    def test_load_nonexistent_raises(self):
        """Loading non-existent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_evaluation_dataset("nonexistent.jsonl")

    def test_load_minimal_dataset(self, minimal_dataset):
        """Should load minimal dataset."""
        samples = load_evaluation_dataset(minimal_dataset)
        assert len(samples) == 3


# =============================================================================
# TESTS: KEYWORD EVALUATOR
# =============================================================================

class TestKeywordEvaluator:
    """Tests for KeywordEvaluator class."""

    def test_classify_consumer_cpg_qualified(self):
        """Consumer CPG samples should be QUALIFIED."""
        evaluator = KeywordEvaluator()

        sample = {
            "input": "Company: TestCo\nDescription: D2C meal kit delivery with plant-based options",
            "metadata": {"sector": "consumer_cpg"}
        }

        result = evaluator.classify_sample(sample)
        assert result == "QUALIFIED"

    def test_classify_b2b_rejected(self):
        """B2B samples should be REJECTED."""
        evaluator = KeywordEvaluator()

        sample = {
            "input": "Company: TestCo\nDescription: Enterprise SaaS platform",
            "metadata": {"sector": "b2b_saas"}
        }

        result = evaluator.classify_sample(sample)
        assert result == "REJECTED"

    def test_classify_crypto_rejected(self):
        """Crypto samples should be REJECTED."""
        evaluator = KeywordEvaluator()

        sample = {
            "input": "Company: TestCo\nDescription: Blockchain crypto trading platform",
            "metadata": {"sector": "crypto"}
        }

        result = evaluator.classify_sample(sample)
        assert result == "REJECTED"

    def test_classify_weak_signal_held(self):
        """Weak signals with low score should be HELD."""
        evaluator = KeywordEvaluator()

        sample = {
            "input": "Company: TestCo\nDescription: Some generic startup",
            "metadata": {"sector": "unknown"}
        }

        result = evaluator.classify_sample(sample)
        assert result == "HELD"

    def test_classify_with_negative_keywords_rejected(self):
        """Samples with negative keywords should be REJECTED."""
        evaluator = KeywordEvaluator()

        sample = {
            "input": "Company: TestCo\nDescription: Food delivery with enterprise B2B API platform",
            "metadata": {"sector": "consumer_cpg"}
        }

        # Has "enterprise" and "b2b" as negative keywords
        result = evaluator.classify_sample(sample)
        assert result == "REJECTED"

    @pytest.mark.asyncio
    async def test_evaluate_sample_dataset(self, sample_dataset_path):
        """Should evaluate sample dataset and return metrics."""
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate(sample_dataset_path)

        assert result.evaluator_type == "keyword"
        assert result.total_samples >= 20
        assert 0.0 <= result.accuracy <= 1.0
        assert "QUALIFIED" in result.per_class_metrics
        assert "HELD" in result.per_class_metrics
        assert "REJECTED" in result.per_class_metrics

    @pytest.mark.asyncio
    async def test_evaluate_minimal_dataset(self, minimal_dataset):
        """Should evaluate minimal dataset."""
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate(minimal_dataset)

        assert result.total_samples == 3

    @pytest.mark.asyncio
    async def test_evaluate_generates_run_id(self, minimal_dataset):
        """Each evaluation should generate unique run_id."""
        evaluator = KeywordEvaluator()

        result1 = await evaluator.evaluate(minimal_dataset)
        result2 = await evaluator.evaluate(minimal_dataset)

        assert result1.run_id != result2.run_id
        assert result1.run_id.startswith("kw_")

    @pytest.mark.asyncio
    async def test_evaluate_records_timestamp(self, minimal_dataset):
        """Evaluation should record timestamp."""
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate(minimal_dataset)

        assert result.timestamp is not None
        assert "T" in result.timestamp  # ISO format

    @pytest.mark.asyncio
    async def test_evaluate_records_latency(self, minimal_dataset):
        """Evaluation should record latency."""
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate(minimal_dataset)

        assert result.latency_ms is not None
        assert result.latency_ms >= 0


# =============================================================================
# TESTS: LLM EVALUATOR
# =============================================================================

class TestLLMEvaluator:
    """Tests for LLMEvaluator class."""

    def test_parse_input_to_signal(self):
        """Should parse dataset input to signal format."""
        evaluator = LLMEvaluator()

        input_text = "Company: TestCo\nDescription: A test company\nSector: Consumer CPG"
        signal_data = evaluator._parse_input_to_signal(input_text)

        assert signal_data["title"] == "TestCo"
        assert "A test company" in signal_data["source_context"]
        assert "Consumer CPG" in signal_data["source_context"]

    def test_classify_result_excluded_rejected(self):
        """Excluded category should map to REJECTED."""
        evaluator = LLMEvaluator()

        # Mock result
        class MockResult:
            category = "excluded"
            thesis_match = False
            thesis_fit_score = 0.0

        result = MockResult()
        label = evaluator.classify_result_to_label(result)

        assert label == "REJECTED"

    def test_classify_result_no_match_rejected(self):
        """No thesis match should map to REJECTED."""
        evaluator = LLMEvaluator()

        class MockResult:
            category = "consumer_cpg"
            thesis_match = False
            thesis_fit_score = 0.2

        result = MockResult()
        label = evaluator.classify_result_to_label(result)

        assert label == "REJECTED"

    def test_classify_result_high_score_qualified(self):
        """High score with thesis match should map to QUALIFIED."""
        evaluator = LLMEvaluator()

        class MockResult:
            category = "consumer_cpg"
            thesis_match = True
            thesis_fit_score = 0.75

        result = MockResult()
        label = evaluator.classify_result_to_label(result)

        assert label == "QUALIFIED"

    def test_classify_result_low_score_held(self):
        """Low score with thesis match should map to HELD."""
        evaluator = LLMEvaluator()

        class MockResult:
            category = "consumer_cpg"
            thesis_match = True
            thesis_fit_score = 0.25

        result = MockResult()
        label = evaluator.classify_result_to_label(result)

        assert label == "HELD"


# =============================================================================
# TESTS: THESIS EVALUATOR (ORCHESTRATOR)
# =============================================================================

class TestThesisEvaluator:
    """Tests for ThesisEvaluator orchestrator."""

    @pytest.mark.asyncio
    async def test_evaluate_keyword(self, minimal_dataset):
        """Should run keyword evaluation."""
        evaluator = ThesisEvaluator()
        result = await evaluator.evaluate_keyword(minimal_dataset)

        assert result.evaluator_type == "keyword"

    @pytest.mark.asyncio
    async def test_evaluate_both_keyword_only(self, minimal_dataset):
        """Should run comparison with keyword only when skip_llm=True."""
        evaluator = ThesisEvaluator()
        comparison = await evaluator.evaluate_both(minimal_dataset, skip_llm=True)

        assert comparison.keyword_result is not None
        assert comparison.llm_result is None
        assert comparison.accuracy_delta is None


# =============================================================================
# TESTS: DATA CLASSES
# =============================================================================

class TestDataClasses:
    """Tests for data class methods."""

    def test_class_metrics_to_dict(self):
        """ClassMetrics should serialize to dict."""
        metrics = ClassMetrics(precision=0.8, recall=0.7, f1=0.746, support=10)
        d = metrics.to_dict()

        assert d["precision"] == 0.8
        assert d["recall"] == 0.7
        assert d["f1"] == 0.746
        assert d["support"] == 10

    def test_evaluation_result_to_dict(self):
        """ThesisEvaluationResult should serialize to dict."""
        result = ThesisEvaluationResult(
            run_id="test_123",
            evaluator_type="keyword",
            dataset_path="test.jsonl",
            total_samples=10,
            accuracy=0.8,
            per_class_metrics={
                "QUALIFIED": ClassMetrics(0.8, 0.9, 0.85, 5)
            },
            confusion_matrix={"QUALIFIED": {"QUALIFIED": 5}},
            timestamp="2024-01-01T00:00:00Z",
        )

        d = result.to_dict()

        assert d["run_id"] == "test_123"
        assert d["accuracy"] == 0.8
        assert "QUALIFIED" in d["per_class_metrics"]


# =============================================================================
# TESTS: OUTPUT FORMATTING
# =============================================================================

class TestOutputFormatting:
    """Tests for output formatting functions."""

    def test_format_evaluation_result(self):
        """Should format result as readable text."""
        result = ThesisEvaluationResult(
            run_id="test_123",
            evaluator_type="keyword",
            dataset_path="test.jsonl",
            total_samples=10,
            accuracy=0.8,
            per_class_metrics={
                "QUALIFIED": ClassMetrics(0.8, 0.9, 0.85, 5),
                "HELD": ClassMetrics(0.7, 0.6, 0.65, 3),
                "REJECTED": ClassMetrics(0.9, 0.8, 0.85, 2),
            },
            confusion_matrix={
                "QUALIFIED": {"QUALIFIED": 5, "HELD": 0, "REJECTED": 0},
                "HELD": {"QUALIFIED": 1, "HELD": 2, "REJECTED": 0},
                "REJECTED": {"QUALIFIED": 0, "HELD": 0, "REJECTED": 2},
            },
            timestamp="2024-01-01T00:00:00Z",
            latency_ms=100,
        )

        output = format_evaluation_result(result)

        assert "KEYWORD" in output
        assert "80.0%" in output  # accuracy
        assert "QUALIFIED" in output
        assert "Confusion Matrix" in output

    def test_format_comparison(self):
        """Should format comparison as readable text."""
        kw_result = ThesisEvaluationResult(
            run_id="kw_123",
            evaluator_type="keyword",
            dataset_path="test.jsonl",
            total_samples=10,
            accuracy=0.8,
            per_class_metrics={
                "QUALIFIED": ClassMetrics(0.8, 0.9, 0.85, 5),
                "HELD": ClassMetrics(0.7, 0.6, 0.65, 3),
                "REJECTED": ClassMetrics(0.9, 0.8, 0.85, 2),
            },
            confusion_matrix={},
            timestamp="2024-01-01T00:00:00Z",
        )

        comparison = EvaluationComparison(
            keyword_result=kw_result,
            llm_result=None,
            accuracy_delta=None,
            per_class_deltas={},
        )

        output = format_comparison(comparison)

        assert "KEYWORD" in output
        assert "LLM" in output
        assert "DELTA" in output


# =============================================================================
# TESTS: INTEGRATION WITH SAMPLE DATASET
# =============================================================================

class TestIntegrationWithSampleDataset:
    """Integration tests using the actual sample dataset."""

    @pytest.mark.asyncio
    async def test_keyword_evaluator_on_sample_dataset(self, sample_dataset_path):
        """Keyword evaluator should achieve reasonable accuracy on sample dataset."""
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate(sample_dataset_path)

        # Should have reasonable accuracy (at least 70%)
        assert result.accuracy >= 0.70, f"Accuracy too low: {result.accuracy:.1%}"

        # Should have all classes in metrics
        assert "QUALIFIED" in result.per_class_metrics
        assert "HELD" in result.per_class_metrics
        assert "REJECTED" in result.per_class_metrics

        # REJECTED should have high precision (few false positives for exclusions)
        assert result.per_class_metrics["REJECTED"].precision >= 0.8

    @pytest.mark.asyncio
    async def test_confusion_matrix_sums_to_total(self, sample_dataset_path):
        """Confusion matrix row sums should equal class support."""
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate(sample_dataset_path)

        for label in VALID_LABELS:
            row_sum = sum(result.confusion_matrix[label].values())
            support = result.per_class_metrics[label].support
            assert row_sum == support, f"{label}: row_sum={row_sum} != support={support}"

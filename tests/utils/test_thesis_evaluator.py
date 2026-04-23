"""
Tests for thesis classification evaluation harness.

Tests KeywordEvaluator, LLMEvaluator, metric calculations, and formatting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List
from unittest.mock import AsyncMock, Mock

import pytest

from utils.thesis_evaluator import (
    ClassMetrics,
    LLMSampleEvaluation,
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
        """No thesis match with score below ambiguous range should map to REJECTED."""
        evaluator = LLMEvaluator()

        class MockResult:
            category = "consumer_cpg"
            thesis_match = False
            thesis_fit_score = 0.15

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

    def test_classify_result_ambiguous_score_range_held(self):
        """Score in 0.20-0.29 (ambiguous distribution) maps to HELD regardless of thesis_match."""
        evaluator = LLMEvaluator()

        class MockResult:
            category = "consumer_health_tech"
            thesis_match = False
            thesis_fit_score = 0.22

        label = evaluator.classify_result_to_label(MockResult())
        assert label == "HELD"

    def test_classify_result_ambiguous_score_range_with_match_held(self):
        """Score in 0.20-0.29 maps to HELD even when thesis_match is True."""
        evaluator = LLMEvaluator()

        class MockResult:
            category = "consumer_health_tech"
            thesis_match = True
            thesis_fit_score = 0.25

        label = evaluator.classify_result_to_label(MockResult())
        assert label == "HELD"

    def test_classify_result_below_ambiguous_range_rejected(self):
        """Score below 0.20 with thesis_match=False stays REJECTED."""
        evaluator = LLMEvaluator()

        class MockResult:
            category = "consumer_health_tech"
            thesis_match = False
            thesis_fit_score = 0.15

        label = evaluator.classify_result_to_label(MockResult())
        assert label == "REJECTED"

    def test_classify_result_excluded_overrides_ambiguous_range(self):
        """category=excluded still maps to REJECTED even if score is in 0.20-0.29."""
        evaluator = LLMEvaluator()

        class MockResult:
            category = "excluded"
            thesis_match = False
            thesis_fit_score = 0.25

        label = evaluator.classify_result_to_label(MockResult())
        assert label == "REJECTED"

    @pytest.mark.asyncio
    async def test_evaluate_sample_uses_shared_parse_and_label_path(self):
        """Per-sample evaluation should return parsed signal data and derived label."""
        evaluator = LLMEvaluator()
        mock_classifier = Mock()
        mock_result = Mock()
        mock_result.thesis_match = True
        mock_result.thesis_fit_score = 0.8
        mock_result.category = "consumer_cpg"
        mock_result.input_tokens = 10
        mock_result.output_tokens = 5
        mock_classifier.classify = AsyncMock(return_value=mock_result)
        evaluator._classifier = mock_classifier

        sample = {
            "id": "sample_1",
            "input": "Company: TestCo\nDescription: D2C meal kit\nWebsite: https://example.com\nSector: consumer_cpg",
            "target": "QUALIFIED",
        }

        result = await evaluator.evaluate_sample(sample)

        assert isinstance(result, LLMSampleEvaluation)
        assert result.sample_id == "sample_1"
        assert result.prediction == "QUALIFIED"
        assert result.match is True
        assert result.signal_data["title"] == "TestCo"
        assert "D2C meal kit" in result.signal_data["source_context"]
        assert result.classification is mock_result

    @pytest.mark.asyncio
    async def test_evaluate_sample_falls_back_to_held_on_exception(self):
        """Unexpected per-sample exceptions should keep aggregate eval stable."""
        evaluator = LLMEvaluator()
        mock_classifier = Mock()
        mock_classifier.classify = AsyncMock(side_effect=RuntimeError("boom"))
        evaluator._classifier = mock_classifier

        sample = {
            "id": "sample_2",
            "input": "Company: TestCo\nDescription: D2C meal kit",
            "target": "REJECTED",
        }

        result = await evaluator.evaluate_sample(sample)

        assert result.prediction == "HELD"
        assert result.match is False
        assert result.classification is None
        assert result.error == "boom"

    @pytest.mark.asyncio
    async def test_evaluate_sample_captures_classifier_operational_errors(self):
        """Graceful classifier failures should still surface as evaluation errors."""
        evaluator = LLMEvaluator()
        mock_classifier = Mock()
        mock_result = Mock()
        mock_result.thesis_match = False
        mock_result.thesis_fit_score = 0.0
        mock_result.category = "excluded"
        mock_result.input_tokens = None
        mock_result.output_tokens = None
        mock_result.classification_status = "error_api"
        mock_result.rationale = "Classification failed: upstream error"
        mock_classifier.classify = AsyncMock(return_value=mock_result)
        evaluator._classifier = mock_classifier

        sample = {
            "id": "sample_3",
            "input": "Company: TestCo\nDescription: D2C meal kit",
            "target": "REJECTED",
        }

        result = await evaluator.evaluate_sample(sample)

        assert result.prediction == "REJECTED"
        assert result.match is True
        assert result.error == "Classification failed: upstream error"

    @pytest.mark.asyncio
    async def test_evaluate_samples_fail_fast_on_operational_preflight(self, minimal_dataset):
        """Preflight operational failures should stop the LLM pass after the first sample."""
        evaluator = LLMEvaluator()
        mock_classifier = Mock()
        mock_result = Mock()
        mock_result.thesis_match = False
        mock_result.thesis_fit_score = 0.0
        mock_result.category = "excluded"
        mock_result.input_tokens = None
        mock_result.output_tokens = None
        mock_result.classification_status = "error_rate_limit"
        mock_result.rationale = "Rate limit exceeded: ClientError"
        mock_classifier.classify = AsyncMock(return_value=mock_result)
        evaluator._classifier = mock_classifier

        samples, sample_evaluations = await evaluator.evaluate_samples(
            minimal_dataset,
            fail_fast_on_operational_failure=True,
        )
        result = evaluator.build_result_from_samples(
            minimal_dataset,
            samples,
            sample_evaluations,
        )

        assert len(samples) == 3
        assert len(sample_evaluations) == 1
        assert mock_classifier.classify.await_count == 1
        assert result.total_samples == 3
        assert result.attempted_sample_count == 1
        assert result.run_state == "blocked_execution"
        assert result.llm_execution_error_count == 1
        assert result.accuracy is None
        assert result.blocked_reason is not None
        assert "rate limiting/quota" in result.blocked_reason.lower()

    @pytest.mark.asyncio
    async def test_evaluate_samples_stops_after_first_operational_failure(self, minimal_dataset):
        """A partial run with later operational failure should still be treated as blocked execution."""
        evaluator = LLMEvaluator()
        mock_classifier = Mock()

        success_result = Mock()
        success_result.thesis_match = True
        success_result.thesis_fit_score = 0.8
        success_result.category = "consumer_cpg"
        success_result.input_tokens = 10
        success_result.output_tokens = 5
        success_result.classification_status = "success"
        success_result.rationale = "Strong consumer fit"

        rate_limited_result = Mock()
        rate_limited_result.thesis_match = False
        rate_limited_result.thesis_fit_score = 0.0
        rate_limited_result.category = "excluded"
        rate_limited_result.input_tokens = None
        rate_limited_result.output_tokens = None
        rate_limited_result.classification_status = "error_rate_limit"
        rate_limited_result.rationale = "Rate limit exceeded: RateLimitError"

        mock_classifier.classify = AsyncMock(
            side_effect=[success_result, rate_limited_result]
        )
        evaluator._classifier = mock_classifier

        samples, sample_evaluations = await evaluator.evaluate_samples(
            minimal_dataset,
            fail_fast_on_operational_failure=True,
        )
        result = evaluator.build_result_from_samples(
            minimal_dataset,
            samples,
            sample_evaluations,
        )

        assert len(samples) == 3
        assert len(sample_evaluations) == 2
        assert mock_classifier.classify.await_count == 2
        assert result.run_state == "blocked_execution"
        assert result.attempted_sample_count == 2
        assert result.llm_execution_error_count == 1
        assert result.accuracy is None
        assert result.blocked_reason is not None
        assert "after 2 attempted samples" in result.blocked_reason.lower()

    def test_build_result_from_samples_derives_blocked_reason_from_error_text(self, minimal_dataset):
        """Fallback-only operational failures should still produce a specific blocked reason."""
        evaluator = LLMEvaluator()
        samples = load_evaluation_dataset(minimal_dataset)
        sample_evaluations = [
            LLMSampleEvaluation(
                sample_id="1",
                target="QUALIFIED",
                prediction="HELD",
                match=False,
                signal_data={},
                classification=None,
                error="Rate limit exceeded: RateLimitError",
                latency_ms=1,
            ),
        ]

        result = evaluator.build_result_from_samples(
            minimal_dataset,
            samples,
            sample_evaluations,
        )

        assert result.run_state == "blocked_execution"
        assert result.attempted_sample_count == 1
        assert result.blocked_reason is not None
        assert "rate limiting/quota" in result.blocked_reason.lower()


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

    @pytest.mark.asyncio
    async def test_evaluate_llm_records_graceful_classifier_failures(self, minimal_dataset):
        """Aggregate LLM evaluation should retain classifier-level operational errors."""
        evaluator = ThesisEvaluator()
        mock_result = Mock()
        mock_result.thesis_match = False
        mock_result.thesis_fit_score = 0.0
        mock_result.category = "excluded"
        mock_result.input_tokens = None
        mock_result.output_tokens = None
        mock_result.classification_status = "error_api"
        mock_result.rationale = "Classification failed: upstream error"
        evaluator.llm_evaluator._classifier = Mock()
        evaluator.llm_evaluator._classifier.classify = AsyncMock(return_value=mock_result)

        result = await evaluator.evaluate_llm(minimal_dataset)

        assert len(result.errors) == 3
        assert all("Classification failed: upstream error" in err for err in result.errors)
        assert result.run_state == "blocked_execution"
        assert result.llm_execution_error_count == 3
        assert result.accuracy is None
        assert result.per_class_metrics == {}
        assert result.confusion_matrix == {}

    @pytest.mark.asyncio
    async def test_evaluate_llm_parse_failures_do_not_mark_blocked_execution(self, minimal_dataset):
        """Model/output parse failures should not be treated as execution-blocked runs."""
        evaluator = ThesisEvaluator()
        mock_result = Mock()
        mock_result.thesis_match = False
        mock_result.thesis_fit_score = 0.0
        mock_result.category = "excluded"
        mock_result.input_tokens = None
        mock_result.output_tokens = None
        mock_result.classification_status = "error_parse"
        mock_result.rationale = "Failed to parse response: JSONDecodeError"
        evaluator.llm_evaluator._classifier = Mock()
        evaluator.llm_evaluator._classifier.classify = AsyncMock(return_value=mock_result)

        result = await evaluator.evaluate_llm(minimal_dataset)

        assert len(result.errors) == 3
        assert result.run_state == "completed"
        assert result.llm_execution_error_count == 0

    @pytest.mark.asyncio
    async def test_evaluate_llm_success_preserves_completed_run_state(self, minimal_dataset):
        """Healthy runs should keep the normal completed state with zero execution errors."""
        evaluator = ThesisEvaluator()
        mock_result = Mock()
        mock_result.thesis_match = True
        mock_result.thesis_fit_score = 0.8
        mock_result.category = "consumer_cpg"
        mock_result.input_tokens = 10
        mock_result.output_tokens = 5
        mock_result.classification_status = "success"
        mock_result.rationale = "Strong consumer fit"
        evaluator.llm_evaluator._classifier = Mock()
        evaluator.llm_evaluator._classifier.classify = AsyncMock(return_value=mock_result)

        result = await evaluator.evaluate_llm(minimal_dataset)

        assert result.run_state == "completed"
        assert result.llm_execution_error_count == 0


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

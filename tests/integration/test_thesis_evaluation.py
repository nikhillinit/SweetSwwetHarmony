"""
Integration tests for thesis classification evaluation harness.

Tests the full flow: dataset loading → evaluation → storage → retrieval.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from storage.signal_store import SignalStore
from utils.thesis_evaluator import (
    KeywordEvaluator,
    ThesisEvaluator,
    ThesisEvaluationResult,
    EvaluationComparison,
    load_evaluation_dataset,
    format_evaluation_result,
    format_comparison,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def sample_dataset_path():
    """Path to sample dataset."""
    return Path(__file__).parent.parent.parent / "datasets" / "thesis_sample.jsonl"


@pytest.fixture
async def store(tmp_path):
    """Create initialized SignalStore with temp database."""
    db_path = tmp_path / "test_signals.db"
    store = SignalStore(str(db_path))
    await store.initialize()
    yield store
    await store.close()


@pytest.fixture
def custom_dataset(tmp_path):
    """Create a custom test dataset."""
    samples = [
        # QUALIFIED - Consumer CPG
        {
            "input": "Company: MealPrepCo\nDescription: D2C meal kit subscription service with plant-based options\nSector: Consumer CPG",
            "target": "QUALIFIED",
            "id": "custom_001",
            "metadata": {"company_name": "MealPrepCo", "sector": "consumer_cpg"}
        },
        # QUALIFIED - Health Tech
        {
            "input": "Company: FitApp\nDescription: Fitness app for personalized workout tracking and wellness\nSector: Consumer Health Tech",
            "target": "QUALIFIED",
            "id": "custom_002",
            "metadata": {"company_name": "FitApp", "sector": "consumer_health_tech"}
        },
        # QUALIFIED - Travel
        {
            "input": "Company: StayLocal\nDescription: Hotel booking platform for boutique experiences\nSector: Travel & Hospitality",
            "target": "QUALIFIED",
            "id": "custom_003",
            "metadata": {"company_name": "StayLocal", "sector": "travel_hospitality"}
        },
        # REJECTED - B2B
        {
            "input": "Company: SaaSCo\nDescription: Enterprise B2B SaaS analytics platform\nSector: B2B SaaS",
            "target": "REJECTED",
            "id": "custom_004",
            "metadata": {"company_name": "SaaSCo", "sector": "b2b_saas"}
        },
        # REJECTED - Crypto
        {
            "input": "Company: CryptoDEX\nDescription: Decentralized cryptocurrency exchange with DeFi\nSector: Crypto",
            "target": "REJECTED",
            "id": "custom_005",
            "metadata": {"company_name": "CryptoDEX", "sector": "crypto"}
        },
        # HELD - Weak signal
        {
            "input": "Company: StartupX\nDescription: Some new startup company\nSector: Unknown",
            "target": "HELD",
            "id": "custom_006",
            "metadata": {"company_name": "StartupX", "sector": "unknown"}
        },
    ]

    dataset_file = tmp_path / "custom_test.jsonl"
    with open(dataset_file, "w") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")

    return dataset_file


# =============================================================================
# TESTS: END-TO-END EVALUATION FLOW
# =============================================================================

class TestEndToEndEvaluation:
    """Test complete evaluation flow from dataset to results."""

    @pytest.mark.asyncio
    async def test_full_keyword_evaluation_flow(self, sample_dataset_path, store):
        """Test complete keyword evaluation → save → retrieve flow."""
        # Step 1: Run evaluation
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate(sample_dataset_path)

        # Verify result structure
        assert isinstance(result, ThesisEvaluationResult)
        assert result.evaluator_type == "keyword"
        assert result.total_samples >= 20
        assert 0.0 <= result.accuracy <= 1.0

        # Step 2: Save to database
        row_id = await store.save_thesis_evaluation(
            run_id=result.run_id,
            evaluator_type=result.evaluator_type,
            dataset_path=result.dataset_path,
            accuracy=result.accuracy,
            per_class_metrics={
                k: v.to_dict() for k, v in result.per_class_metrics.items()
            },
            confusion_matrix=result.confusion_matrix,
            latency_ms=result.latency_ms,
            errors=result.errors,
        )

        assert row_id > 0

        # Step 3: Retrieve from database
        retrieved = await store.get_thesis_evaluations(
            evaluator_type="keyword",
            limit=1,
        )

        assert len(retrieved) == 1
        assert retrieved[0]["run_id"] == result.run_id
        assert retrieved[0]["accuracy"] == pytest.approx(result.accuracy, rel=0.001)
        assert retrieved[0]["evaluator_type"] == "keyword"

    @pytest.mark.asyncio
    async def test_custom_dataset_evaluation(self, custom_dataset, store):
        """Test evaluation with custom dataset."""
        # Run evaluation on custom dataset
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate(custom_dataset)

        # Should have 6 samples
        assert result.total_samples == 6

        # Should correctly classify most samples
        # (3 QUALIFIED, 2 REJECTED, 1 HELD)
        assert result.accuracy >= 0.5  # At least 50% correct

        # REJECTED should have good precision (B2B and crypto are obvious)
        rejected_metrics = result.per_class_metrics.get("REJECTED")
        if rejected_metrics and rejected_metrics.support > 0:
            assert rejected_metrics.precision >= 0.8

    @pytest.mark.asyncio
    async def test_multiple_evaluations_tracking(self, custom_dataset, store):
        """Test tracking multiple evaluation runs."""
        evaluator = KeywordEvaluator()

        # Run multiple evaluations
        run_ids = []
        for i in range(3):
            result = await evaluator.evaluate(custom_dataset)

            await store.save_thesis_evaluation(
                run_id=result.run_id,
                evaluator_type=result.evaluator_type,
                dataset_path=str(custom_dataset),
                accuracy=result.accuracy,
                per_class_metrics={
                    k: v.to_dict() for k, v in result.per_class_metrics.items()
                },
                confusion_matrix=result.confusion_matrix,
            )
            run_ids.append(result.run_id)

        # Should retrieve all three
        results = await store.get_thesis_evaluations(limit=10)
        assert len(results) == 3

        # Most recent should be first
        assert results[0]["run_id"] == run_ids[-1]


# =============================================================================
# TESTS: THESIS EVALUATOR ORCHESTRATOR
# =============================================================================

class TestThesisEvaluatorOrchestrator:
    """Test ThesisEvaluator orchestrator integration."""

    @pytest.mark.asyncio
    async def test_evaluate_keyword_via_orchestrator(self, sample_dataset_path):
        """Test running keyword evaluation through orchestrator."""
        evaluator = ThesisEvaluator()
        result = await evaluator.evaluate_keyword(sample_dataset_path)

        assert result.evaluator_type == "keyword"
        assert result.total_samples >= 20

    @pytest.mark.asyncio
    async def test_evaluate_both_keyword_only(self, sample_dataset_path):
        """Test comparison with keyword only (skip LLM)."""
        evaluator = ThesisEvaluator()
        comparison = await evaluator.evaluate_both(sample_dataset_path, skip_llm=True)

        assert isinstance(comparison, EvaluationComparison)
        assert comparison.keyword_result is not None
        assert comparison.llm_result is None
        assert comparison.accuracy_delta is None


# =============================================================================
# TESTS: OUTPUT FORMATTING
# =============================================================================

class TestOutputFormatting:
    """Test output formatting functions."""

    @pytest.mark.asyncio
    async def test_format_evaluation_result_roundtrip(self, sample_dataset_path):
        """Test that formatted output contains key information."""
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate(sample_dataset_path)

        output = format_evaluation_result(result)

        # Should contain key information
        assert "KEYWORD" in output
        assert f"{result.accuracy:.1%}" in output or f"{result.accuracy * 100:.1f}%" in output
        assert "QUALIFIED" in output
        assert "HELD" in output
        assert "REJECTED" in output
        assert "Confusion Matrix" in output

    @pytest.mark.asyncio
    async def test_format_comparison_output(self, sample_dataset_path):
        """Test comparison output formatting."""
        evaluator = ThesisEvaluator()
        comparison = await evaluator.evaluate_both(sample_dataset_path, skip_llm=True)

        output = format_comparison(comparison)

        # Should contain comparison headers
        assert "KEYWORD" in output
        assert "LLM" in output
        assert "DELTA" in output


# =============================================================================
# TESTS: BASELINE AND TREND TRACKING
# =============================================================================

class TestBaselineTracking:
    """Test baseline comparison and trend tracking."""

    @pytest.mark.asyncio
    async def test_get_baseline_after_evaluation(self, custom_dataset, store):
        """Test getting baseline after running evaluation."""
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate(custom_dataset)

        # Save result
        await store.save_thesis_evaluation(
            run_id=result.run_id,
            evaluator_type="keyword",
            dataset_path=str(custom_dataset),
            accuracy=result.accuracy,
            per_class_metrics={
                k: v.to_dict() for k, v in result.per_class_metrics.items()
            },
            confusion_matrix=result.confusion_matrix,
        )

        # Get baseline
        baseline = await store.get_thesis_baseline("keyword")

        assert baseline is not None
        assert baseline["run_id"] == result.run_id
        assert baseline["accuracy"] == pytest.approx(result.accuracy, rel=0.001)

    @pytest.mark.asyncio
    async def test_baseline_filters_by_type(self, custom_dataset, store):
        """Test that baseline correctly filters by evaluator type."""
        evaluator = KeywordEvaluator()
        result = await evaluator.evaluate(custom_dataset)

        # Save as keyword
        await store.save_thesis_evaluation(
            run_id=result.run_id,
            evaluator_type="keyword",
            dataset_path=str(custom_dataset),
            accuracy=0.80,
            per_class_metrics={},
            confusion_matrix={},
        )

        # Save another as "llm" (mock)
        await store.save_thesis_evaluation(
            run_id="mock_llm_run",
            evaluator_type="llm",
            dataset_path=str(custom_dataset),
            accuracy=0.90,
            per_class_metrics={},
            confusion_matrix={},
        )

        # Get keyword baseline
        kw_baseline = await store.get_thesis_baseline("keyword")
        assert kw_baseline["accuracy"] == pytest.approx(0.80, rel=0.01)

        # Get LLM baseline
        llm_baseline = await store.get_thesis_baseline("llm")
        assert llm_baseline["accuracy"] == pytest.approx(0.90, rel=0.01)


# =============================================================================
# TESTS: DATASET LOADING
# =============================================================================

class TestDatasetLoading:
    """Test dataset loading integration."""

    def test_load_sample_dataset(self, sample_dataset_path):
        """Test loading the actual sample dataset."""
        samples = load_evaluation_dataset(sample_dataset_path)

        assert len(samples) >= 20

        # Check structure
        for sample in samples:
            assert "input" in sample
            assert "target" in sample
            assert "id" in sample
            assert "metadata" in sample

            # Target should be valid
            assert sample["target"] in {"QUALIFIED", "HELD", "REJECTED"}

    def test_load_custom_dataset(self, custom_dataset):
        """Test loading custom dataset."""
        samples = load_evaluation_dataset(custom_dataset)

        assert len(samples) == 6


# =============================================================================
# TESTS: ERROR HANDLING
# =============================================================================

class TestErrorHandling:
    """Test error handling in evaluation pipeline."""

    @pytest.mark.asyncio
    async def test_evaluate_nonexistent_dataset(self):
        """Evaluating non-existent dataset should raise error."""
        evaluator = KeywordEvaluator()

        with pytest.raises(FileNotFoundError):
            await evaluator.evaluate("nonexistent_dataset.jsonl")

    @pytest.mark.asyncio
    async def test_store_uninitalized_raises(self, tmp_path):
        """Using uninitialized store should raise error."""
        db_path = tmp_path / "uninitialized.db"
        store = SignalStore(str(db_path))

        # Don't initialize - should raise
        with pytest.raises(RuntimeError):
            await store.save_thesis_evaluation(
                run_id="test",
                evaluator_type="keyword",
                dataset_path="test.jsonl",
                accuracy=0.8,
                per_class_metrics={},
                confusion_matrix={},
            )

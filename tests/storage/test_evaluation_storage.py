"""
Tests for thesis evaluation storage methods in SignalStore.
"""

from __future__ import annotations

import pytest

from storage.signal_store import SignalStore


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
async def store(tmp_path):
    """Create initialized SignalStore with temp database."""
    db_path = tmp_path / "test_signals.db"
    store = SignalStore(str(db_path))
    await store.initialize()
    yield store
    await store.close()


# =============================================================================
# TESTS: SAVE THESIS EVALUATION
# =============================================================================

class TestSaveThesisEvaluation:
    """Tests for save_thesis_evaluation method."""

    @pytest.mark.asyncio
    async def test_save_keyword_evaluation(self, store):
        """Should save keyword evaluation run."""
        row_id = await store.save_thesis_evaluation(
            run_id="kw_test123",
            evaluator_type="keyword",
            dataset_path="datasets/test.jsonl",
            accuracy=0.85,
            per_class_metrics={
                "QUALIFIED": {"precision": 0.8, "recall": 0.9, "f1": 0.85, "support": 10},
                "HELD": {"precision": 0.7, "recall": 0.6, "f1": 0.65, "support": 5},
                "REJECTED": {"precision": 0.9, "recall": 0.95, "f1": 0.92, "support": 8},
            },
            confusion_matrix={
                "QUALIFIED": {"QUALIFIED": 9, "HELD": 1, "REJECTED": 0},
                "HELD": {"QUALIFIED": 2, "HELD": 3, "REJECTED": 0},
                "REJECTED": {"QUALIFIED": 0, "HELD": 0, "REJECTED": 8},
            },
            latency_ms=150,
        )

        assert row_id > 0

    @pytest.mark.asyncio
    async def test_save_llm_evaluation(self, store):
        """Should save LLM evaluation run with token usage."""
        row_id = await store.save_thesis_evaluation(
            run_id="llm_test456",
            evaluator_type="llm",
            dataset_path="datasets/test.jsonl",
            accuracy=0.90,
            per_class_metrics={
                "QUALIFIED": {"precision": 0.88, "recall": 0.92, "f1": 0.90, "support": 10},
                "HELD": {"precision": 0.75, "recall": 0.70, "f1": 0.72, "support": 5},
                "REJECTED": {"precision": 0.95, "recall": 0.98, "f1": 0.96, "support": 8},
            },
            confusion_matrix={
                "QUALIFIED": {"QUALIFIED": 9, "HELD": 1, "REJECTED": 0},
                "HELD": {"QUALIFIED": 1, "HELD": 4, "REJECTED": 0},
                "REJECTED": {"QUALIFIED": 0, "HELD": 0, "REJECTED": 8},
            },
            latency_ms=5000,
            token_usage={"input_tokens": 1500, "output_tokens": 800},
        )

        assert row_id > 0

    @pytest.mark.asyncio
    async def test_save_with_errors(self, store):
        """Should save evaluation with error list."""
        row_id = await store.save_thesis_evaluation(
            run_id="kw_test789",
            evaluator_type="keyword",
            dataset_path="datasets/test.jsonl",
            accuracy=0.80,
            per_class_metrics={},
            confusion_matrix={},
            errors=["Sample 5: Parse error", "Sample 12: Missing input"],
        )

        assert row_id > 0


# =============================================================================
# TESTS: GET THESIS EVALUATIONS
# =============================================================================

class TestGetThesisEvaluations:
    """Tests for get_thesis_evaluations method."""

    @pytest.mark.asyncio
    async def test_get_empty_returns_empty_list(self, store):
        """Should return empty list when no evaluations."""
        results = await store.get_thesis_evaluations()
        assert results == []

    @pytest.mark.asyncio
    async def test_get_after_save(self, store):
        """Should retrieve saved evaluation."""
        await store.save_thesis_evaluation(
            run_id="kw_get_test",
            evaluator_type="keyword",
            dataset_path="datasets/test.jsonl",
            accuracy=0.85,
            per_class_metrics={"QUALIFIED": {"precision": 0.8}},
            confusion_matrix={},
        )

        results = await store.get_thesis_evaluations()

        assert len(results) == 1
        assert results[0]["run_id"] == "kw_get_test"
        assert results[0]["evaluator_type"] == "keyword"
        assert results[0]["accuracy"] == 0.85

    @pytest.mark.asyncio
    async def test_filter_by_evaluator_type(self, store):
        """Should filter by evaluator type."""
        # Save keyword evaluation
        await store.save_thesis_evaluation(
            run_id="kw_filter1",
            evaluator_type="keyword",
            dataset_path="datasets/test.jsonl",
            accuracy=0.80,
            per_class_metrics={},
            confusion_matrix={},
        )

        # Save LLM evaluation
        await store.save_thesis_evaluation(
            run_id="llm_filter1",
            evaluator_type="llm",
            dataset_path="datasets/test.jsonl",
            accuracy=0.90,
            per_class_metrics={},
            confusion_matrix={},
        )

        # Filter keyword only
        keyword_results = await store.get_thesis_evaluations(evaluator_type="keyword")
        assert len(keyword_results) == 1
        assert keyword_results[0]["evaluator_type"] == "keyword"

        # Filter LLM only
        llm_results = await store.get_thesis_evaluations(evaluator_type="llm")
        assert len(llm_results) == 1
        assert llm_results[0]["evaluator_type"] == "llm"

        # Get all
        all_results = await store.get_thesis_evaluations()
        assert len(all_results) == 2

    @pytest.mark.asyncio
    async def test_limit_results(self, store):
        """Should limit number of results."""
        # Save 5 evaluations
        for i in range(5):
            await store.save_thesis_evaluation(
                run_id=f"kw_limit_{i}",
                evaluator_type="keyword",
                dataset_path="datasets/test.jsonl",
                accuracy=0.80 + i * 0.02,
                per_class_metrics={},
                confusion_matrix={},
            )

        # Get only 3
        results = await store.get_thesis_evaluations(limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_returns_most_recent_first(self, store):
        """Should return most recent evaluations first."""
        # Save in order
        await store.save_thesis_evaluation(
            run_id="kw_old",
            evaluator_type="keyword",
            dataset_path="datasets/test.jsonl",
            accuracy=0.80,
            per_class_metrics={},
            confusion_matrix={},
        )

        await store.save_thesis_evaluation(
            run_id="kw_new",
            evaluator_type="keyword",
            dataset_path="datasets/test.jsonl",
            accuracy=0.85,
            per_class_metrics={},
            confusion_matrix={},
        )

        results = await store.get_thesis_evaluations()

        # Most recent (kw_new) should be first
        assert results[0]["run_id"] == "kw_new"
        assert results[1]["run_id"] == "kw_old"

    @pytest.mark.asyncio
    async def test_includes_per_class_metrics(self, store):
        """Should include per-class metrics in results."""
        await store.save_thesis_evaluation(
            run_id="kw_metrics_test",
            evaluator_type="keyword",
            dataset_path="datasets/test.jsonl",
            accuracy=0.85,
            per_class_metrics={
                "QUALIFIED": {"precision": 0.8, "recall": 0.9, "f1": 0.85, "support": 10},
            },
            confusion_matrix={},
        )

        results = await store.get_thesis_evaluations()

        assert "per_class_metrics" in results[0]
        assert "QUALIFIED" in results[0]["per_class_metrics"]
        assert results[0]["per_class_metrics"]["QUALIFIED"]["precision"] == 0.8

    @pytest.mark.asyncio
    async def test_includes_confusion_matrix(self, store):
        """Should include confusion matrix in results."""
        await store.save_thesis_evaluation(
            run_id="kw_confusion_test",
            evaluator_type="keyword",
            dataset_path="datasets/test.jsonl",
            accuracy=0.85,
            per_class_metrics={},
            confusion_matrix={
                "QUALIFIED": {"QUALIFIED": 9, "HELD": 1, "REJECTED": 0},
            },
        )

        results = await store.get_thesis_evaluations()

        assert "confusion_matrix" in results[0]
        assert results[0]["confusion_matrix"]["QUALIFIED"]["QUALIFIED"] == 9


# =============================================================================
# TESTS: GET THESIS BASELINE
# =============================================================================

class TestGetThesisBaseline:
    """Tests for get_thesis_baseline method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self, store):
        """Should return None when no evaluations exist."""
        baseline = await store.get_thesis_baseline("keyword")
        assert baseline is None

    @pytest.mark.asyncio
    async def test_returns_most_recent(self, store):
        """Should return most recent evaluation as baseline."""
        await store.save_thesis_evaluation(
            run_id="kw_baseline_old",
            evaluator_type="keyword",
            dataset_path="datasets/test.jsonl",
            accuracy=0.80,
            per_class_metrics={},
            confusion_matrix={},
        )

        await store.save_thesis_evaluation(
            run_id="kw_baseline_new",
            evaluator_type="keyword",
            dataset_path="datasets/test.jsonl",
            accuracy=0.85,
            per_class_metrics={},
            confusion_matrix={},
        )

        baseline = await store.get_thesis_baseline("keyword")

        assert baseline is not None
        assert baseline["run_id"] == "kw_baseline_new"
        assert baseline["accuracy"] == 0.85

    @pytest.mark.asyncio
    async def test_filters_by_evaluator_type(self, store):
        """Should only consider evaluations of requested type."""
        # Save keyword evaluation
        await store.save_thesis_evaluation(
            run_id="kw_type_test",
            evaluator_type="keyword",
            dataset_path="datasets/test.jsonl",
            accuracy=0.80,
            per_class_metrics={},
            confusion_matrix={},
        )

        # Save LLM evaluation
        await store.save_thesis_evaluation(
            run_id="llm_type_test",
            evaluator_type="llm",
            dataset_path="datasets/test.jsonl",
            accuracy=0.90,
            per_class_metrics={},
            confusion_matrix={},
        )

        # Get keyword baseline
        kw_baseline = await store.get_thesis_baseline("keyword")
        assert kw_baseline["run_id"] == "kw_type_test"
        assert kw_baseline["accuracy"] == 0.80

        # Get LLM baseline
        llm_baseline = await store.get_thesis_baseline("llm")
        assert llm_baseline["run_id"] == "llm_type_test"
        assert llm_baseline["accuracy"] == 0.90

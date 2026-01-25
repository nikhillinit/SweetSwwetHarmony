"""
Tests for evaluation runner.

Sprint 6: Evaluation & Calibration.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from utils.evaluation_runner import (
    EvaluationRunner,
    ExtractionMetrics,
    SimilarityMetrics,
    InvestorMatchMetrics,
    EvaluationResult,
)


# =============================================================================
# METRICS TESTS
# =============================================================================

class TestExtractionMetrics:
    """Tests for ExtractionMetrics."""

    def test_precision_calculation(self):
        """Precision is calculated correctly."""
        metrics = ExtractionMetrics(
            total_samples=10,
            exact_matches=7,
            incorrect=3,
            abstentions=0,
        )
        # precision = 7 / (7 + 3) = 0.7
        assert metrics.precision == pytest.approx(0.7)

    def test_recall_calculation(self):
        """Recall is calculated correctly."""
        metrics = ExtractionMetrics(
            total_samples=10,
            exact_matches=6,
            incorrect=2,
            abstentions=2,
        )
        # recall = 6 / (6 + 2) = 0.75
        assert metrics.recall == pytest.approx(0.75)

    def test_f1_calculation(self):
        """F1 is calculated correctly."""
        metrics = ExtractionMetrics(
            total_samples=10,
            exact_matches=8,
            incorrect=2,
            abstentions=0,
        )
        # precision = 0.8, recall = 1.0
        # f1 = 2 * 0.8 * 1.0 / (0.8 + 1.0) = 0.889
        assert metrics.f1 == pytest.approx(0.889, rel=0.01)

    def test_abstention_rate(self):
        """Abstention rate is calculated correctly."""
        metrics = ExtractionMetrics(
            total_samples=20,
            abstentions=5,
        )
        # abstention_rate = 5/20 * 100 = 25%
        assert metrics.abstention_rate == pytest.approx(25.0)

    def test_zero_division_handling(self):
        """Handles zero samples gracefully."""
        metrics = ExtractionMetrics()
        assert metrics.precision == 0.0
        assert metrics.recall == 0.0
        assert metrics.f1 == 0.0
        assert metrics.abstention_rate == 0.0

    def test_to_dict(self):
        """to_dict returns all metrics."""
        metrics = ExtractionMetrics(total_samples=10, exact_matches=5)
        d = metrics.to_dict()
        assert "precision" in d
        assert "recall" in d
        assert "f1" in d
        assert "abstention_rate" in d


class TestSimilarityMetrics:
    """Tests for SimilarityMetrics."""

    def test_top_k_recall(self):
        """Top-k recall is calculated correctly."""
        metrics = SimilarityMetrics(
            total_queries=100,
            top_1_hits=30,
            top_5_hits=60,
            top_10_hits=80,
        )
        assert metrics.top_1_recall == pytest.approx(30.0)
        assert metrics.top_5_recall == pytest.approx(60.0)
        assert metrics.top_10_recall == pytest.approx(80.0)

    def test_zero_queries(self):
        """Handles zero queries gracefully."""
        metrics = SimilarityMetrics()
        assert metrics.top_1_recall == 0.0
        assert metrics.top_5_recall == 0.0
        assert metrics.top_10_recall == 0.0

    def test_to_dict(self):
        """to_dict returns all metrics."""
        metrics = SimilarityMetrics(total_queries=10)
        d = metrics.to_dict()
        assert "top_1_recall" in d
        assert "top_5_recall" in d
        assert "top_10_recall" in d
        assert "mean_reciprocal_rank" in d


class TestInvestorMatchMetrics:
    """Tests for InvestorMatchMetrics."""

    def test_precision_at_5(self):
        """Precision@5 is calculated correctly."""
        metrics = InvestorMatchMetrics(
            total_queries=10,
            relevant_in_top_5=30,  # 3 per query avg
            partial_in_top_5=10,   # 1 per query avg
            irrelevant_in_top_5=10,  # 1 per query avg
        )
        # (30 + 0.5 * 10) / 50 = 35 / 50 = 0.7
        assert metrics.precision_at_5 == pytest.approx(0.7)

    def test_zero_results(self):
        """Handles zero results gracefully."""
        metrics = InvestorMatchMetrics()
        assert metrics.precision_at_5 == 0.0

    def test_to_dict(self):
        """to_dict returns all metrics."""
        metrics = InvestorMatchMetrics(total_queries=10)
        d = metrics.to_dict()
        assert "precision_at_5" in d
        assert "mean_precision_at_5" in d


class TestEvaluationResult:
    """Tests for EvaluationResult."""

    def test_to_dict_with_extraction(self):
        """to_dict includes extraction metrics."""
        result = EvaluationResult(
            run_id="test_run",
            run_type="extraction",
            gold_set_version="v1",
            model_version="v1",
            extraction_metrics=ExtractionMetrics(total_samples=10, exact_matches=5),
        )
        d = result.to_dict()
        assert "extraction" in d
        assert d["extraction"]["total_samples"] == 10

    def test_to_dict_with_similarity(self):
        """to_dict includes similarity metrics."""
        result = EvaluationResult(
            run_id="test_run",
            run_type="similarity",
            gold_set_version="v1",
            model_version="v1",
            similarity_metrics=SimilarityMetrics(total_queries=20),
        )
        d = result.to_dict()
        assert "similarity" in d
        assert d["similarity"]["total_queries"] == 20


# =============================================================================
# EVALUATION RUNNER TESTS
# =============================================================================

class TestEvaluationRunner:
    """Tests for EvaluationRunner class."""

    @pytest.fixture
    async def store(self):
        """Create in-memory store."""
        from storage.signal_store import SignalStore
        store = SignalStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.fixture
    async def gold_set(self, store):
        """Create gold set manager."""
        from utils.gold_set_manager import GoldSetManager
        return GoldSetManager(store)

    @pytest.fixture
    async def runner(self, store, gold_set):
        """Create evaluation runner."""
        return EvaluationRunner(store, gold_set)

    @pytest.mark.asyncio
    async def test_extraction_evaluation_empty(self, runner):
        """Handles empty gold set gracefully."""
        result = await runner.run_extraction_evaluation("v1")

        assert result.run_type == "extraction"
        assert result.extraction_metrics.total_samples == 0
        assert result.extraction_metrics.f1 == 0.0

    @pytest.mark.asyncio
    async def test_extraction_evaluation_with_data(self, store, gold_set, runner):
        """Computes extraction metrics correctly."""
        # Add gold set company
        await gold_set.add_company("domain:test.com", "Test Co", "core_sector")
        company = await gold_set.get_company("domain:test.com")

        # Add gold label
        await gold_set.add_label(
            company_id=company.id,
            predicate="sector",
            label_type="exact",
            annotator="alice",
            gold_value="fintech",
        )

        # Ensure predicate exists
        await store._db.execute(
            """
            INSERT OR IGNORE INTO predicates (name, display_name, data_type, description)
            VALUES ('sector', 'Sector', 'text', 'Business sector')
            """
        )

        # Add matching claim
        await store._db.execute(
            """
            INSERT INTO claims (entity_key, predicate, value, confidence, status, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            ("domain:test.com", "sector", "fintech", 0.9, "active"),
        )
        await store._db.commit()

        result = await runner.run_extraction_evaluation("v1", predicates=["sector"])

        assert result.extraction_metrics.total_samples == 1
        assert result.extraction_metrics.exact_matches == 1
        assert result.extraction_metrics.f1 == 1.0

    @pytest.mark.asyncio
    async def test_extraction_evaluation_abstention(self, store, gold_set, runner):
        """Counts abstentions correctly."""
        # Add gold set company without corresponding claim
        await gold_set.add_company("domain:test.com", "Test Co", "core_sector")
        company = await gold_set.get_company("domain:test.com")

        await gold_set.add_label(
            company_id=company.id,
            predicate="sector",
            label_type="exact",
            annotator="alice",
            gold_value="fintech",
        )

        result = await runner.run_extraction_evaluation("v1", predicates=["sector"])

        assert result.extraction_metrics.total_samples == 1
        assert result.extraction_metrics.abstentions == 1
        assert result.extraction_metrics.abstention_rate == 100.0

    @pytest.mark.asyncio
    async def test_similarity_evaluation_empty(self, runner):
        """Handles empty gold set gracefully."""
        result = await runner.run_similarity_evaluation("v1")

        assert result.run_type == "similarity"
        assert result.similarity_metrics.total_queries == 0

    @pytest.mark.asyncio
    async def test_investor_match_evaluation_empty(self, runner):
        """Handles empty gold set gracefully."""
        result = await runner.run_investor_match_evaluation("v1")

        assert result.run_type == "investor_match"
        assert result.investor_match_metrics.total_queries == 0

    @pytest.mark.asyncio
    async def test_investor_match_evaluation_with_data(self, store, gold_set, runner):
        """Computes investor match metrics correctly."""
        # Create investor
        await store.save_investor(
            investor_id="investor:test_vc",
            name="Test VC",
            source="curated_json",
        )

        # Add gold set company
        await gold_set.add_company("domain:test.com", "Test Co", "core_sector")
        company = await gold_set.get_company("domain:test.com")

        # Add investor label
        await gold_set.add_investor_label(
            company_id=company.id,
            investor_id="investor:test_vc",
            relevance="relevant",
            annotator="alice",
        )

        # Add investor match
        await store.save_investor_match(
            company_key="domain:test.com",
            investor_id="investor:test_vc",
            match_score=0.8,
            explanation=["Strong sector fit"],
            rank=1,
        )

        result = await runner.run_investor_match_evaluation("v1")

        assert result.investor_match_metrics.total_queries == 1
        assert result.investor_match_metrics.relevant_in_top_5 == 1

    @pytest.mark.asyncio
    async def test_full_evaluation(self, runner):
        """Run full evaluation returns all types."""
        results = await runner.run_full_evaluation("v1")

        assert "extraction" in results
        assert "similarity" in results
        assert "investor_match" in results

    @pytest.mark.asyncio
    async def test_evaluation_saves_to_db(self, store, gold_set, runner):
        """Evaluation results are saved to database."""
        result = await runner.run_extraction_evaluation("v1")

        # Check it was saved
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM evaluation_runs WHERE run_id = ?",
            (result.run_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == 1


class TestEvaluationRunnerHelpers:
    """Tests for helper methods."""

    @pytest.fixture
    async def store(self):
        """Create in-memory store."""
        from storage.signal_store import SignalStore
        store = SignalStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.fixture
    async def gold_set(self, store):
        """Create gold set manager."""
        from utils.gold_set_manager import GoldSetManager
        return GoldSetManager(store)

    @pytest.fixture
    async def runner(self, store, gold_set):
        """Create evaluation runner."""
        return EvaluationRunner(store, gold_set)

    @pytest.mark.asyncio
    async def test_get_extracted_claim(self, store, runner):
        """_get_extracted_claim returns claim value."""
        # Ensure predicate exists
        await store._db.execute(
            """
            INSERT OR IGNORE INTO predicates (name, display_name, data_type, description)
            VALUES ('sector', 'Sector', 'text', 'Business sector')
            """
        )

        # Add claim
        await store._db.execute(
            """
            INSERT INTO claims (entity_key, predicate, value, confidence, status, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            ("domain:test.com", "sector", "fintech", 0.9, "active"),
        )
        await store._db.commit()

        value = await runner._get_extracted_claim("domain:test.com", "sector")
        assert value == "fintech"

    @pytest.mark.asyncio
    async def test_get_extracted_claim_not_found(self, runner):
        """_get_extracted_claim returns None for missing claim."""
        value = await runner._get_extracted_claim("domain:nonexistent.com", "sector")
        assert value is None

    @pytest.mark.asyncio
    async def test_get_investor_matches(self, store, runner):
        """_get_investor_matches returns investor IDs."""
        # Create investor and match
        await store.save_investor(
            investor_id="investor:a",
            name="Investor A",
            source="test",
        )
        await store.save_investor_match(
            company_key="domain:test.com",
            investor_id="investor:a",
            match_score=0.9,
            explanation=["Test"],
            rank=1,
        )

        matches = await runner._get_investor_matches("domain:test.com", top_k=5)
        assert "investor:a" in matches

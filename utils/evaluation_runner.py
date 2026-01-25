"""
Evaluation Runner

Runs evaluation against gold set and computes metrics:
- Extraction metrics (F1, precision, recall, abstention rate)
- Similarity metrics (top-k recall)
- Investor match metrics

Sprint 6: Evaluation & Calibration.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore
    from utils.gold_set_manager import GoldSetManager

logger = logging.getLogger(__name__)


# =============================================================================
# METRICS DATA CLASSES
# =============================================================================

@dataclass
class ExtractionMetrics:
    """Metrics for extraction evaluation."""
    total_samples: int = 0
    exact_matches: int = 0
    partial_matches: int = 0
    incorrect: int = 0
    abstentions: int = 0

    @property
    def precision(self) -> float:
        """Precision: exact / (exact + incorrect)."""
        denominator = self.exact_matches + self.incorrect
        return self.exact_matches / denominator if denominator > 0 else 0.0

    @property
    def recall(self) -> float:
        """Recall: exact / (exact + abstentions)."""
        denominator = self.exact_matches + self.abstentions
        return self.exact_matches / denominator if denominator > 0 else 0.0

    @property
    def f1(self) -> float:
        """F1 score."""
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @property
    def abstention_rate(self) -> float:
        """Abstention rate as percentage."""
        return (self.abstentions / self.total_samples * 100) if self.total_samples > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_samples": self.total_samples,
            "exact_matches": self.exact_matches,
            "partial_matches": self.partial_matches,
            "incorrect": self.incorrect,
            "abstentions": self.abstentions,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "abstention_rate": round(self.abstention_rate, 2),
        }


@dataclass
class SimilarityMetrics:
    """Metrics for similarity evaluation."""
    total_queries: int = 0
    top_1_hits: int = 0
    top_5_hits: int = 0
    top_10_hits: int = 0
    mean_reciprocal_rank: float = 0.0

    @property
    def top_1_recall(self) -> float:
        """Top-1 recall percentage."""
        return (self.top_1_hits / self.total_queries * 100) if self.total_queries > 0 else 0.0

    @property
    def top_5_recall(self) -> float:
        """Top-5 recall percentage."""
        return (self.top_5_hits / self.total_queries * 100) if self.total_queries > 0 else 0.0

    @property
    def top_10_recall(self) -> float:
        """Top-10 recall percentage."""
        return (self.top_10_hits / self.total_queries * 100) if self.total_queries > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_queries": self.total_queries,
            "top_1_hits": self.top_1_hits,
            "top_5_hits": self.top_5_hits,
            "top_10_hits": self.top_10_hits,
            "top_1_recall": round(self.top_1_recall, 2),
            "top_5_recall": round(self.top_5_recall, 2),
            "top_10_recall": round(self.top_10_recall, 2),
            "mean_reciprocal_rank": round(self.mean_reciprocal_rank, 4),
        }


@dataclass
class InvestorMatchMetrics:
    """Metrics for investor match evaluation."""
    total_queries: int = 0
    relevant_in_top_5: int = 0
    partial_in_top_5: int = 0
    irrelevant_in_top_5: int = 0
    mean_precision_at_5: float = 0.0

    @property
    def precision_at_5(self) -> float:
        """Precision at 5."""
        total = self.relevant_in_top_5 + self.partial_in_top_5 + self.irrelevant_in_top_5
        if total == 0:
            return 0.0
        # Count relevant as 1, partial as 0.5
        score = self.relevant_in_top_5 + 0.5 * self.partial_in_top_5
        return score / total

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_queries": self.total_queries,
            "relevant_in_top_5": self.relevant_in_top_5,
            "partial_in_top_5": self.partial_in_top_5,
            "irrelevant_in_top_5": self.irrelevant_in_top_5,
            "precision_at_5": round(self.precision_at_5, 4),
            "mean_precision_at_5": round(self.mean_precision_at_5, 4),
        }


@dataclass
class EvaluationResult:
    """Complete evaluation result."""
    run_id: str
    run_type: str
    gold_set_version: str
    model_version: str
    embedding_version: Optional[str] = None
    extraction_metrics: Optional[ExtractionMetrics] = None
    similarity_metrics: Optional[SimilarityMetrics] = None
    investor_match_metrics: Optional[InvestorMatchMetrics] = None
    config: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        metrics = {}
        if self.extraction_metrics:
            metrics["extraction"] = self.extraction_metrics.to_dict()
        if self.similarity_metrics:
            metrics["similarity"] = self.similarity_metrics.to_dict()
        if self.investor_match_metrics:
            metrics["investor_match"] = self.investor_match_metrics.to_dict()
        return metrics


# =============================================================================
# EVALUATION RUNNER
# =============================================================================

class EvaluationRunner:
    """
    Runs evaluation against gold set.

    Evaluation types:
    - extraction: Compare extracted claims against gold labels
    - similarity: Check if similar companies appear in top-k
    - investor_match: Check if relevant investors appear in top-k
    """

    def __init__(
        self,
        store: "SignalStore",
        gold_set_manager: "GoldSetManager",
        model_version: str = "v1",
        embedding_version: str = "text-embedding-004",
    ):
        """
        Initialize evaluation runner.

        Args:
            store: SignalStore for database access
            gold_set_manager: GoldSetManager for gold set access
            model_version: Model version being evaluated
            embedding_version: Embedding model version
        """
        self._store = store
        self._gold_set = gold_set_manager
        self._model_version = model_version
        self._embedding_version = embedding_version

    async def run_extraction_evaluation(
        self,
        gold_set_version: str,
        predicates: Optional[List[str]] = None,
    ) -> EvaluationResult:
        """
        Run extraction evaluation.

        Compares extracted claims against gold labels for each predicate.

        Args:
            gold_set_version: Version of gold set to use
            predicates: Specific predicates to evaluate (default: all)

        Returns:
            EvaluationResult with extraction metrics
        """
        run_id = f"extraction_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        if predicates is None:
            predicates = self._gold_set.VALID_PREDICATES

        metrics = ExtractionMetrics()

        # Get all gold set companies
        companies = await self._gold_set.list_companies(limit=10000)

        for company in companies:
            # Get gold labels for this company
            labels = await self._gold_set.get_labels(company.id)

            for label in labels:
                if label.predicate not in predicates:
                    continue

                metrics.total_samples += 1

                # Get extracted claim for this company/predicate
                extracted = await self._get_extracted_claim(
                    company.canonical_key, label.predicate
                )

                if extracted is None:
                    # System abstained
                    metrics.abstentions += 1
                elif label.gold_value and extracted.lower() == label.gold_value.lower():
                    # Exact match
                    metrics.exact_matches += 1
                elif label.gold_value and label.gold_value.lower() in extracted.lower():
                    # Partial match
                    metrics.partial_matches += 1
                else:
                    # Incorrect
                    metrics.incorrect += 1

        result = EvaluationResult(
            run_id=run_id,
            run_type="extraction",
            gold_set_version=gold_set_version,
            model_version=self._model_version,
            extraction_metrics=metrics,
            config={"predicates": predicates},
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Save to database
        await self._store.save_evaluation_run(
            run_id=run_id,
            run_type="extraction",
            model_version=self._model_version,
            gold_set_version=gold_set_version,
            metrics=result.to_dict(),
            config=result.config,
        )

        logger.info(
            f"Extraction evaluation complete: F1={metrics.f1:.3f}, "
            f"abstention={metrics.abstention_rate:.1f}%"
        )

        return result

    async def run_similarity_evaluation(
        self,
        gold_set_version: str,
    ) -> EvaluationResult:
        """
        Run similarity evaluation.

        For each gold set company, checks if other companies in the same
        category appear in top-k similar results.

        Args:
            gold_set_version: Version of gold set to use

        Returns:
            EvaluationResult with similarity metrics
        """
        run_id = f"similarity_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        metrics = SimilarityMetrics()
        reciprocal_ranks = []

        # Get all gold set companies grouped by category
        companies = await self._gold_set.list_companies(limit=10000)
        by_category: Dict[str, List] = {}
        for c in companies:
            by_category.setdefault(c.category, []).append(c)

        # For each company, query similar and check if same-category appears
        for company in companies:
            same_category = [
                c for c in by_category.get(company.category, [])
                if c.canonical_key != company.canonical_key
            ]

            if not same_category:
                continue

            metrics.total_queries += 1

            # Get similar companies (would call similarity_engine in production)
            similar = await self._get_similar_companies(company.canonical_key, top_k=10)

            # Check hits
            same_category_keys = {c.canonical_key for c in same_category}
            found_rank = None

            for rank, sim_key in enumerate(similar, 1):
                if sim_key in same_category_keys:
                    if found_rank is None:
                        found_rank = rank
                    if rank <= 1:
                        metrics.top_1_hits += 1
                    if rank <= 5:
                        metrics.top_5_hits += 1
                    if rank <= 10:
                        metrics.top_10_hits += 1
                    break  # Only count first hit

            if found_rank:
                reciprocal_ranks.append(1.0 / found_rank)
            else:
                reciprocal_ranks.append(0.0)

        if reciprocal_ranks:
            metrics.mean_reciprocal_rank = sum(reciprocal_ranks) / len(reciprocal_ranks)

        result = EvaluationResult(
            run_id=run_id,
            run_type="similarity",
            gold_set_version=gold_set_version,
            model_version=self._model_version,
            embedding_version=self._embedding_version,
            similarity_metrics=metrics,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Save to database
        await self._store.save_evaluation_run(
            run_id=run_id,
            run_type="similarity",
            model_version=self._model_version,
            embedding_version=self._embedding_version,
            gold_set_version=gold_set_version,
            metrics=result.to_dict(),
        )

        logger.info(
            f"Similarity evaluation complete: top10_recall={metrics.top_10_recall:.1f}%, "
            f"MRR={metrics.mean_reciprocal_rank:.3f}"
        )

        return result

    async def run_investor_match_evaluation(
        self,
        gold_set_version: str,
    ) -> EvaluationResult:
        """
        Run investor match evaluation.

        For each gold set company with investor labels, checks if
        relevant investors appear in top-5 matches.

        Args:
            gold_set_version: Version of gold set to use

        Returns:
            EvaluationResult with investor match metrics
        """
        run_id = f"investor_match_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        metrics = InvestorMatchMetrics()
        precision_scores = []

        # Get all gold set companies
        companies = await self._gold_set.list_companies(limit=10000)

        for company in companies:
            # Get investor labels
            inv_labels = await self._gold_set.get_investor_labels(company.id)
            if not inv_labels:
                continue

            metrics.total_queries += 1

            # Get investor matches (would call investor_matcher in production)
            matches = await self._get_investor_matches(company.canonical_key, top_k=5)

            # Build relevance lookup
            relevance_map = {l.investor_id: l.relevance for l in inv_labels}

            # Score matches
            query_relevant = 0
            query_partial = 0
            query_irrelevant = 0

            for investor_id in matches:
                relevance = relevance_map.get(investor_id, "unknown")
                if relevance == "relevant":
                    query_relevant += 1
                    metrics.relevant_in_top_5 += 1
                elif relevance == "partial":
                    query_partial += 1
                    metrics.partial_in_top_5 += 1
                else:
                    query_irrelevant += 1
                    metrics.irrelevant_in_top_5 += 1

            # Precision for this query
            total = query_relevant + query_partial + query_irrelevant
            if total > 0:
                p_at_5 = (query_relevant + 0.5 * query_partial) / total
                precision_scores.append(p_at_5)

        if precision_scores:
            metrics.mean_precision_at_5 = sum(precision_scores) / len(precision_scores)

        result = EvaluationResult(
            run_id=run_id,
            run_type="investor_match",
            gold_set_version=gold_set_version,
            model_version=self._model_version,
            investor_match_metrics=metrics,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Save to database
        await self._store.save_evaluation_run(
            run_id=run_id,
            run_type="investor_match",
            model_version=self._model_version,
            gold_set_version=gold_set_version,
            metrics=result.to_dict(),
        )

        logger.info(
            f"Investor match evaluation complete: P@5={metrics.precision_at_5:.3f}, "
            f"mean_P@5={metrics.mean_precision_at_5:.3f}"
        )

        return result

    async def run_full_evaluation(
        self,
        gold_set_version: str,
    ) -> Dict[str, EvaluationResult]:
        """
        Run all evaluation types.

        Args:
            gold_set_version: Version of gold set to use

        Returns:
            Dictionary of run_type -> EvaluationResult
        """
        results = {}

        results["extraction"] = await self.run_extraction_evaluation(gold_set_version)
        results["similarity"] = await self.run_similarity_evaluation(gold_set_version)
        results["investor_match"] = await self.run_investor_match_evaluation(gold_set_version)

        return results

    # =========================================================================
    # HELPER METHODS (stubs for integration)
    # =========================================================================

    async def _get_extracted_claim(
        self,
        canonical_key: str,
        predicate: str,
    ) -> Optional[str]:
        """
        Get extracted claim value for a company/predicate.

        In production, this queries the claims table.
        """
        if not self._store._db:
            return None

        cursor = await self._store._db.execute(
            """
            SELECT value FROM claims
            WHERE entity_key = ? AND predicate = ? AND status = 'active'
            ORDER BY confidence DESC
            LIMIT 1
            """,
            (canonical_key, predicate),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def _get_similar_companies(
        self,
        canonical_key: str,
        top_k: int = 10,
    ) -> List[str]:
        """
        Get similar companies.

        In production, this calls similarity_engine.
        Returns list of canonical keys.
        """
        # Stub: return empty list
        # Would integrate with similarity_engine in production
        return []

    async def _get_investor_matches(
        self,
        canonical_key: str,
        top_k: int = 5,
    ) -> List[str]:
        """
        Get investor matches.

        In production, this calls investor_matcher or reads from cache.
        Returns list of investor IDs.
        """
        if not self._store._db:
            return []

        cursor = await self._store._db.execute(
            """
            SELECT investor_id FROM investor_matches
            WHERE company_key = ?
            ORDER BY match_score DESC
            LIMIT ?
            """,
            (canonical_key, top_k),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

"""
Two-Stage Thesis Filter Pipeline

Stage 1: Hard disqualifiers (FREE, fast)
Stage 2: LLM classification (~$0.002 per signal)

Routing:
- AUTO_REJECT: Hard disqualifier matched
- LLM_REJECT: LLM score < 0.5
- LLM_REVIEW: LLM score >= 0.5, needs human review
- LLM_AUTO_APPROVE: LLM score >= 0.85, high confidence
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from telemetry.thesis_tracing import create_thesis_tracer
from .hard_disqualifiers import HardDisqualifiers, DisqualifyResult
from .llm_classifier import LLMClassifier, ThesisClassification

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

REVIEW_THRESHOLD = 0.50
AUTO_APPROVE_THRESHOLD = 0.85


class FilterResultType(Enum):
    """Filter outcome types."""
    AUTO_REJECT = "auto_reject"       # Hard disqualifier matched
    LLM_REJECT = "llm_reject"         # LLM score < 0.5
    LLM_REVIEW = "llm_review"         # LLM score >= 0.5, needs review
    LLM_AUTO_APPROVE = "llm_auto"     # LLM score >= 0.85, high confidence


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class FilterResult:
    """Complete filter result with all metadata."""
    result_type: FilterResultType
    passed: bool  # True if should continue to Notion
    stage: str  # 'hard_disqualifier' or 'llm_classifier'

    # Hard disqualifier result (if Stage 1)
    disqualify_result: Optional[DisqualifyResult] = None

    # LLM classification result (if Stage 2)
    classification: Optional[ThesisClassification] = None

    # Summary fields for convenience
    reason: Optional[str] = None
    score: Optional[float] = None
    category: Optional[str] = None

    @property
    def filter_result_str(self) -> str:
        """Get filter result as string for database."""
        return self.result_type.value


# =============================================================================
# PIPELINE
# =============================================================================

class ThesisFilterPipeline:
    """
    Two-stage thesis filter pipeline.

    Stage 1: Hard disqualifiers (free, fast)
    - Keyword matching for B2B, crypto, services, job posts
    - Cost: $0

    Stage 2: LLM classification
    - GPT-4o-mini thesis fit scoring
    - Cost: ~$0.002 per signal

    Usage:
        pipeline = ThesisFilterPipeline()
        result = await pipeline.filter({
            "title": "Show HN: My meal delivery startup",
            "url": "https://example.com",
            "source_api": "hn",
            "source_context": "We're launching..."
        })

        if result.passed:
            # Push to Notion for review
            pass
    """

    def __init__(
        self,
        review_threshold: float = REVIEW_THRESHOLD,
        auto_approve_threshold: float = AUTO_APPROVE_THRESHOLD,
        skip_llm: bool = False,
        tracer: Any = None,
    ):
        """
        Initialize filter pipeline.

        Args:
            review_threshold: Minimum score to pass to review
            auto_approve_threshold: Score for auto-approval
            skip_llm: If True, skip LLM stage (for testing)
        """
        self.review_threshold = review_threshold
        self.auto_approve_threshold = auto_approve_threshold
        self.skip_llm = skip_llm

        self.hard_disqualifiers = HardDisqualifiers()
        self._llm_classifier: Optional[LLMClassifier] = None
        self._tracer = tracer or create_thesis_tracer()

    @property
    def llm_classifier(self) -> LLMClassifier:
        """Lazy-load LLM classifier."""
        if self._llm_classifier is None:
            self._llm_classifier = LLMClassifier(tracer=self._tracer)
        return self._llm_classifier

    def _finish_route_span(
        self,
        route_span: Any,
        *,
        disqualify_result: Optional[DisqualifyResult],
        llm_stage_ran: bool,
        result: Optional[FilterResult] = None,
        classification: Optional[ThesisClassification] = None,
        error_kind: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Finalize pipeline tracing for both success and exception paths."""
        if error_kind:
            self._tracer.record_error(
                route_span,
                error_kind=error_kind,
                message=str(error_message or ""),
            )
        self._tracer.finish(
            route_span,
            stage1_passed=(disqualify_result.passed if disqualify_result else None),
            stage1_category=(disqualify_result.category if disqualify_result else None),
            stage1_reason=(disqualify_result.reason if disqualify_result else None),
            llm_stage_ran=llm_stage_ran,
            result_type=(result.result_type.value if result else None),
            passed=(result.passed if result else None),
            score=(result.score if result else None),
            category=(result.category if result else None),
            classification_status=(
                getattr(classification, "classification_status", None)
                if classification
                else None
            ),
            llm_model=(getattr(classification, "model", None) if classification else None),
            llm_prompt_version=(
                getattr(classification, "prompt_version", None)
                if classification
                else None
            ),
        )

    async def filter(
        self,
        signal_data: Dict[str, Any],
    ) -> FilterResult:
        """
        Run signal through two-stage filter.

        Args:
            signal_data: Dict with title, url, source_api, source_context

        Returns:
            FilterResult with outcome and metadata
        """
        title = signal_data.get("title", "")
        source_context = signal_data.get("source_context", "")
        url = signal_data.get("url")
        route_span = self._tracer.start_span(
            "thesis.pipeline.route",
            component="thesis_filter_pipeline",
            source_api=signal_data.get("source_api", "unknown"),
            signal_has_url=bool(url),
            skip_llm=self.skip_llm,
        )
        disqualify_result: Optional[DisqualifyResult] = None

        try:
            # =============================================================
            # STAGE 1: Hard Disqualifiers (FREE)
            # =============================================================
            disqualify_result = self.hard_disqualifiers.check(
                title=title,
                description=source_context,
                url=url,
            )

            if not disqualify_result.passed:
                logger.debug(f"Hard disqualified: {disqualify_result.reason}")
                result = FilterResult(
                    result_type=FilterResultType.AUTO_REJECT,
                    passed=False,
                    stage="hard_disqualifier",
                    disqualify_result=disqualify_result,
                    reason=disqualify_result.reason,
                    score=0.0,
                    category="excluded",
                )
                self._finish_route_span(
                    route_span,
                    disqualify_result=disqualify_result,
                    llm_stage_ran=False,
                    result=result,
                )
                return result

            # =============================================================
            # STAGE 2: LLM Classification (~$0.002)
            # =============================================================
            if self.skip_llm:
                # Skip LLM for testing - pass everything that cleared Stage 1
                result = FilterResult(
                    result_type=FilterResultType.LLM_REVIEW,
                    passed=True,
                    stage="llm_classifier_skipped",
                    reason="LLM skipped for testing",
                    score=0.5,
                    category="unknown",
                )
                self._finish_route_span(
                    route_span,
                    disqualify_result=disqualify_result,
                    llm_stage_ran=False,
                    result=result,
                )
                return result

            classification = await self.llm_classifier.classify(signal_data)

            # Route based on score and confidence
            score = classification.thesis_fit_score
            confidence = classification.confidence

            if score >= self.auto_approve_threshold and confidence == "high":
                result_type = FilterResultType.LLM_AUTO_APPROVE
                passed = True
            elif score >= self.review_threshold:
                result_type = FilterResultType.LLM_REVIEW
                passed = True
            else:
                result_type = FilterResultType.LLM_REJECT
                passed = False

            logger.debug(
                f"LLM classified: score={score:.2f}, confidence={confidence}, "
                f"result={result_type.value}"
            )

            result = FilterResult(
                result_type=result_type,
                passed=passed,
                stage="llm_classifier",
                classification=classification,
                reason=classification.rationale,
                score=score,
                category=classification.category,
            )
            self._finish_route_span(
                route_span,
                disqualify_result=disqualify_result,
                llm_stage_ran=True,
                result=result,
                classification=classification,
            )
            return result
        except Exception as exc:
            llm_stage_ran = bool(disqualify_result and disqualify_result.passed and not self.skip_llm)
            error_kind = "llm" if llm_stage_ran else "stage1"
            self._finish_route_span(
                route_span,
                disqualify_result=disqualify_result,
                llm_stage_ran=llm_stage_ran,
                error_kind=error_kind,
                error_message=str(exc),
            )
            raise

    async def filter_batch(
        self,
        signals: list[Dict[str, Any]],
        max_concurrent: int = 5,
    ) -> list[FilterResult]:
        """
        Filter multiple signals with concurrency control.

        Args:
            signals: List of signal dicts
            max_concurrent: Max concurrent LLM calls

        Returns:
            List of FilterResults in same order as input
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)

        async def filter_with_semaphore(signal: Dict[str, Any]) -> FilterResult:
            async with semaphore:
                return await self.filter(signal)

        return await asyncio.gather(
            *[filter_with_semaphore(s) for s in signals]
        )

    def estimate_cost(self, total_signals: int, hard_reject_rate: float = 0.3) -> float:
        """
        Estimate cost for filtering N signals.

        Args:
            total_signals: Total signals to filter
            hard_reject_rate: Expected % rejected by Stage 1 (free)

        Returns:
            Estimated cost in USD
        """
        # Only signals passing Stage 1 go to LLM
        llm_signals = int(total_signals * (1 - hard_reject_rate))
        return self.llm_classifier.estimate_cost(llm_signals)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

_default_pipeline: Optional[ThesisFilterPipeline] = None


def get_pipeline() -> ThesisFilterPipeline:
    """Get default pipeline instance."""
    global _default_pipeline
    if _default_pipeline is None:
        _default_pipeline = ThesisFilterPipeline()
    return _default_pipeline


async def filter_signal(signal_data: Dict[str, Any]) -> FilterResult:
    """
    Convenience function to filter a signal.

    Args:
        signal_data: Dict with title, url, source_api, source_context

    Returns:
        FilterResult
    """
    return await get_pipeline().filter(signal_data)

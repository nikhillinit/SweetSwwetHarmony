"""
Threshold Configuration - Unified source of truth for classification thresholds.

Split into two semantic domains (per architectural review):
- MatchingThresholdConfig: ThesisMatcher score interpretation
- WorkflowThresholdConfig: Pipeline routing gates

These are deliberately separate to prevent coupling between the
matching engine (how scores are computed and interpreted) and the
business process layer (how signals are routed through the workflow).

Usage:
    from utils.threshold_config import MatchingThresholdConfig, WorkflowThresholdConfig

    # Defaults match current hardcoded values (zero behavior change)
    matching = MatchingThresholdConfig.default()
    workflow = WorkflowThresholdConfig.default()

    # From environment
    matching = MatchingThresholdConfig.from_env()
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchingThresholdConfig:
    """Thresholds for ThesisMatcher score interpretation.

    Controls how keyword matcher scores are interpreted for:
    - is_fit determination
    - routing decisions (QUALIFIED/HELD/REJECTED)
    - confidence levels

    These thresholds are internal to the matching engine.
    Changes here affect what "thesis fit" means, not how
    signals flow through the workflow.
    """
    # ThesisFit.is_fit threshold (score >= this = fit)
    is_fit_threshold: float = 0.4

    # Routing thresholds in _generate_trace()
    qualified_threshold: float = 0.3   # score >= this = QUALIFIED
    held_threshold: float = 0.1        # score >= this = HELD, below = REJECTED

    # Confidence level thresholds in _build_fit()
    high_confidence: float = 0.7
    medium_confidence: float = 0.4     # below this = LOW

    # Thesis assignment threshold (below this = UNKNOWN)
    thesis_assignment_threshold: float = 0.1

    @classmethod
    def default(cls) -> "MatchingThresholdConfig":
        """Default config matching current hardcoded values."""
        return cls()

    @classmethod
    def from_env(cls) -> "MatchingThresholdConfig":
        """Create from environment variables with defaults.

        Env vars (all optional):
            MATCHING_IS_FIT_THRESHOLD
            MATCHING_QUALIFIED_THRESHOLD
            MATCHING_HELD_THRESHOLD
        """
        return cls(
            is_fit_threshold=_float_env("MATCHING_IS_FIT_THRESHOLD", 0.4),
            qualified_threshold=_float_env("MATCHING_QUALIFIED_THRESHOLD", 0.3),
            held_threshold=_float_env("MATCHING_HELD_THRESHOLD", 0.1),
            high_confidence=_float_env("MATCHING_HIGH_CONFIDENCE", 0.7),
            medium_confidence=_float_env("MATCHING_MEDIUM_CONFIDENCE", 0.4),
            thesis_assignment_threshold=_float_env("MATCHING_THESIS_THRESHOLD", 0.1),
        )


@dataclass(frozen=True)
class WorkflowThresholdConfig:
    """Thresholds for pipeline workflow routing.

    Controls how signals flow through the discovery pipeline:
    - ThesisFilter: keyword/LLM combination gating
    - ThesisFilterPipeline: LLM-only routing (review/auto-approve)
    - Pipeline confidence adjustments

    These thresholds are in the business process layer.
    Changes here affect signal routing, not score interpretation.
    """
    # ThesisFilter (utils/thesis_filter.py) thresholds
    hold_threshold: float = 0.3             # Below this = HELD
    skip_llm_if_keyword_below: float = 0.2  # Skip LLM for obvious non-fit
    keyword_high_threshold: float = 0.7     # Keyword score for positive boost
    keyword_low_threshold: float = 0.4      # Keyword score for negative penalty
    high_boost: float = 0.08               # Confidence boost
    low_penalty: float = -0.08             # Confidence penalty
    negative_keyword_penalty: float = -0.12 # Extra penalty for negatives

    # ThesisFilterPipeline (consumer/thesis_filter/pipeline.py) thresholds
    llm_review_threshold: float = 0.50      # LLM score to pass to review
    llm_auto_approve_threshold: float = 0.85  # LLM score for auto-approval

    @classmethod
    def default(cls) -> "WorkflowThresholdConfig":
        """Default config matching current hardcoded values."""
        return cls()

    @classmethod
    def from_env(cls) -> "WorkflowThresholdConfig":
        """Create from environment variables with defaults.

        Env vars (all optional):
            WORKFLOW_HOLD_THRESHOLD
            WORKFLOW_SKIP_LLM_THRESHOLD
            WORKFLOW_LLM_REVIEW_THRESHOLD
            WORKFLOW_LLM_AUTO_APPROVE_THRESHOLD
        """
        return cls(
            hold_threshold=_float_env("WORKFLOW_HOLD_THRESHOLD", 0.3),
            skip_llm_if_keyword_below=_float_env("WORKFLOW_SKIP_LLM_THRESHOLD", 0.2),
            keyword_high_threshold=_float_env("WORKFLOW_KEYWORD_HIGH", 0.7),
            keyword_low_threshold=_float_env("WORKFLOW_KEYWORD_LOW", 0.4),
            llm_review_threshold=_float_env("WORKFLOW_LLM_REVIEW_THRESHOLD", 0.50),
            llm_auto_approve_threshold=_float_env("WORKFLOW_LLM_AUTO_APPROVE_THRESHOLD", 0.85),
        )


def _float_env(name: str, default: float) -> float:
    """Read a float from an environment variable with default."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value.strip())
    except ValueError:
        logger.warning(
            "Invalid float for %s: '%s'. Using default %.3f.",
            name, value, default,
        )
        return default

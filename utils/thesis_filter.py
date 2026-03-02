"""
ThesisFilter - Two-stage thesis classification for pipeline integration.

Stage 1: Fast keyword matching (free)
Stage 2: Gemini LLM semantic classification (free tier)

Routes signals to QUALIFIED, HELD, or REJECTED based on thesis fit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from utils.thesis_matcher import (
    ThesisMatcher,
    CONSUMER_SIGNAL_KEYWORDS,
    SOFT_PENALTY_KEYWORDS,
    NEGATIVE_KEYWORDS,
)
from utils.web3_detector import Web3Detector

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


class RoutingDecision(str, Enum):
    """Routing decision for signals."""
    QUALIFIED = "qualified"  # Passes gates, awaiting user push
    HELD = "held"            # Low fit, needs batch review
    REJECTED = "rejected"    # Excluded from thesis


class DecisionPathCode(str, Enum):
    """Machine-auditable path code for every routing decision.

    Phase 0 Cascade (ADR-3): Enables golden-set regression and calibration
    script parity checks. Every ThesisFilterResult includes one of these.
    """
    VETO_WEB3 = "veto_web3"
    VETO_DOMAIN_BLACKLIST = "veto_domain_blacklist"
    VETO_HARD_REJECT = "veto_hard_reject"
    HOLD_HARD_HOLD = "hold_hard_hold"
    QUALIFY_SECTOR = "qualify_sector"
    QUALIFY_CONSUMER_RESCUE = "qualify_consumer_rescue"
    HOLD_B2B_GUARD_BLOCK = "hold_b2b_guard_block"
    HOLD_DEFAULT = "hold_default"


@dataclass
class ThesisFilterConfig:
    """Configuration for thesis filter."""
    hold_threshold: float = 0.3           # Below this = HELD
    skip_llm_if_keyword_below: float = 0.2  # Skip LLM if obvious non-fit
    keyword_high_threshold: float = 0.7   # Keyword score for positive boost
    keyword_low_threshold: float = 0.4    # Keyword score for negative penalty
    high_boost: float = 0.08              # Confidence boost for high keyword fit
    low_penalty: float = -0.08            # Confidence penalty for low keyword fit
    negative_keyword_penalty: float = -0.12  # Extra penalty for negative keywords


@dataclass
class ThesisFilterResult:
    """Result of thesis filtering."""
    routing: RoutingDecision
    keyword_score: float = 0.0
    keyword_category: Optional[str] = None
    keyword_matches: List[str] = field(default_factory=list)
    negative_keywords: List[str] = field(default_factory=list)
    llm_score: Optional[float] = None
    llm_category: Optional[str] = None
    llm_rationale: Optional[str] = None
    llm_skipped: bool = False
    confidence_adjustment: float = 0.0
    rejection_reason: Optional[str] = None
    thesis_fit: Optional[float] = None  # Combined thesis fit score
    # Phase B additions (from ThesisFit)
    intent_phrases_matched: List[str] = field(default_factory=list)
    domain_match: bool = False
    domain_blacklisted: bool = False
    # Phase 0B-3: v2 shadow mode comparison data
    v2_shadow: Optional[Dict[str, Any]] = None
    # ML shadow: ML thesis model comparison data
    ml_shadow: Optional[Dict[str, Any]] = None
    # Phase 0 Cascade: decision path code and consumer signal fields
    decision_path_code: DecisionPathCode = DecisionPathCode.HOLD_DEFAULT
    decision_detail_code: Optional[str] = None  # Optional enum string, no free text
    consumer_signal_score: float = 0.0
    consumer_anchor_count: int = 0
    b2b_soft_score: float = 0.0
    consumer_keywords_matched_topk: List[Tuple[str, float]] = field(default_factory=list)
    b2b_keywords_matched_topk: List[Tuple[str, float]] = field(default_factory=list)
    # Provenance hashes
    consumer_lexicon_sha256: Optional[str] = None
    b2b_lexicon_sha256: Optional[str] = None
    negative_policy_sha256: Optional[str] = None
    matcher_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        result = {
            "routing": self.routing.value,
            "keyword_score": self.keyword_score,
            "keyword_category": self.keyword_category,
            "keyword_matches": self.keyword_matches,
            "negative_keywords": self.negative_keywords,
            "llm_score": self.llm_score,
            "llm_category": self.llm_category,
            "llm_rationale": self.llm_rationale,
            "llm_skipped": self.llm_skipped,
            "confidence_adjustment": self.confidence_adjustment,
            # Phase B additions
            "intent_phrases_matched": self.intent_phrases_matched,
            "domain_match": self.domain_match,
            "domain_blacklisted": self.domain_blacklisted,
            # Phase 0 Cascade
            "decision_path_code": self.decision_path_code.value,
            "consumer_signal_score": round(self.consumer_signal_score, 4),
            "consumer_anchor_count": self.consumer_anchor_count,
            "b2b_soft_score": round(self.b2b_soft_score, 4),
        }
        if self.decision_detail_code is not None:
            result["decision_detail_code"] = self.decision_detail_code
        if self.consumer_lexicon_sha256:
            result["consumer_lexicon_sha256"] = self.consumer_lexicon_sha256
        if self.b2b_lexicon_sha256:
            result["b2b_lexicon_sha256"] = self.b2b_lexicon_sha256
        if self.negative_policy_sha256:
            result["negative_policy_sha256"] = self.negative_policy_sha256
        if self.matcher_ms is not None:
            result["matcher_ms"] = round(self.matcher_ms, 2)
        # Phase 0B-3: Only include v2_shadow if present
        if self.v2_shadow is not None:
            result["v2_shadow"] = self.v2_shadow
        # ML shadow: Only include if present
        if self.ml_shadow is not None:
            result["ml_shadow"] = self.ml_shadow
        return result


# Alias for backward compatibility with tests
ThesisClassification = ThesisFilterResult


class ThesisFilter:
    """
    Two-stage thesis filter for discovery pipeline.

    Stage 1: Fast keyword matching using ThesisMatcher
    Stage 2: Gemini LLM semantic classification (optional)

    Usage:
        filter = ThesisFilter(ThesisFilterConfig())
        result = await filter.classify("Meal kit delivery startup")
        if result.routing == RoutingDecision.QUALIFIED:
            # Proceed to verification gate
    """

    def __init__(
        self,
        config: Optional[ThesisFilterConfig] = None,
        signal_store: Optional["SignalStore"] = None,
    ):
        """
        Initialize thesis filter.

        Args:
            config: Filter configuration (uses defaults if not provided)
            signal_store: Optional signal store for persisting classifications
        """
        self.config = config or ThesisFilterConfig()
        self.signal_store = signal_store
        self._keyword_matcher = ThesisMatcher()
        self._web3_detector = Web3Detector()
        self._llm_classifier = None  # Lazy load

        # Phase 0 Cascade: Provenance hashes (computed once at init)
        self._consumer_lexicon_sha256 = self._hash_dict(CONSUMER_SIGNAL_KEYWORDS)
        self._b2b_lexicon_sha256 = self._hash_dict(SOFT_PENALTY_KEYWORDS)
        self._negative_policy_sha256 = self._hash_dict(NEGATIVE_KEYWORDS)

    @staticmethod
    def _hash_dict(d: Any) -> str:
        """Compute SHA-256 of a dict for provenance tracking."""
        canonical = json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def _determine_path_code(
        self,
        routing: RoutingDecision,
        keyword_fit: Any,
        *,
        web3_veto: bool = False,
    ) -> DecisionPathCode:
        """Determine decision path code based on routing and keyword fit.

        Phase 0: Assigns path codes to existing routing decisions (no behavior change).
        """
        if web3_veto:
            return DecisionPathCode.VETO_WEB3
        if keyword_fit.domain_blacklisted:
            return DecisionPathCode.VETO_DOMAIN_BLACKLIST

        trace = keyword_fit.trace if hasattr(keyword_fit, "trace") else None

        # Check hard_rejects
        if trace and trace.matched_hard_rejects:
            if routing == RoutingDecision.REJECTED:
                return DecisionPathCode.VETO_HARD_REJECT

        # Check hard_holds
        if trace and trace.matched_hard_holds:
            if routing in (RoutingDecision.HELD, RoutingDecision.REJECTED):
                return DecisionPathCode.HOLD_HARD_HOLD

        # Strong sector match
        if routing == RoutingDecision.QUALIFIED:
            return DecisionPathCode.QUALIFY_SECTOR

        return DecisionPathCode.HOLD_DEFAULT

    @property
    def llm_classifier(self):
        """Lazy-load LLM classifier."""
        if self._llm_classifier is None:
            try:
                from consumer.thesis_filter.llm_classifier import LLMClassifier
                self._llm_classifier = LLMClassifier()
            except ImportError:
                logger.warning("LLM classifier not available")
        return self._llm_classifier

    async def classify(
        self,
        text: str,
        company_name: Optional[str] = None,
        domain_name: Optional[str] = None,
        skip_llm: bool = False,
    ) -> ThesisFilterResult:
        """
        Classify text through two-stage thesis filter.

        Args:
            text: Description or combined signal text
            company_name: Optional company name for context
            domain_name: Optional domain for pattern matching (Phase B)
            skip_llm: If True, only run keyword matching

        Returns:
            ThesisFilterResult with routing decision
        """
        # Pre-check: Web3 co-occurrence detector (before keyword scoring)
        web3_result = self._web3_detector.detect(text)
        if web3_result.is_crypto:
            return ThesisFilterResult(
                routing=RoutingDecision.REJECTED,
                rejection_reason=web3_result.reason,
                negative_keywords=[web3_result.matched_term],
                decision_path_code=DecisionPathCode.VETO_WEB3,
                consumer_lexicon_sha256=self._consumer_lexicon_sha256,
                b2b_lexicon_sha256=self._b2b_lexicon_sha256,
                negative_policy_sha256=self._negative_policy_sha256,
            )

        # Stage 1: Keyword matching (with Phase B domain support)
        t0 = time.monotonic()
        keyword_fit = self._keyword_matcher.score(text, company_name, domain_name=domain_name)
        matcher_ms = (time.monotonic() - t0) * 1000

        # Check if we should skip LLM (obvious non-fit or explicit skip)
        if skip_llm or keyword_fit.score < self.config.skip_llm_if_keyword_below:
            adjustment = self._calculate_adjustment(
                keyword_fit.score,
                keyword_fit.negative_keywords,
            )

            # Route based on keyword score alone
            if keyword_fit.negative_keywords:
                routing = RoutingDecision.REJECTED
            elif keyword_fit.score < self.config.hold_threshold:
                routing = RoutingDecision.HELD
            else:
                routing = RoutingDecision.QUALIFIED

            # Phase 0B-3: Extract v2_shadow from trace
            v2_shadow = None
            if keyword_fit.trace and keyword_fit.trace.v2_shadow:
                v2_shadow = keyword_fit.trace.v2_shadow

            # Extract ml_shadow from trace
            ml_shadow = None
            if keyword_fit.trace and keyword_fit.trace.ml_shadow:
                ml_shadow = keyword_fit.trace.ml_shadow

            path_code = self._determine_path_code(routing, keyword_fit)

            return ThesisFilterResult(
                routing=routing,
                keyword_score=keyword_fit.score,
                keyword_category=keyword_fit.thesis.value,
                keyword_matches=keyword_fit.matched_keywords,
                negative_keywords=keyword_fit.negative_keywords,
                llm_skipped=True,
                confidence_adjustment=adjustment,
                # Phase B additions
                intent_phrases_matched=keyword_fit.intent_phrases_matched,
                domain_match=keyword_fit.domain_match,
                domain_blacklisted=keyword_fit.domain_blacklisted,
                # Phase 0B-3: v2 shadow comparison
                v2_shadow=v2_shadow,
                # ML shadow comparison
                ml_shadow=ml_shadow,
                # Phase 0 Cascade
                decision_path_code=path_code,
                consumer_signal_score=keyword_fit.consumer_signal_score,
                consumer_anchor_count=keyword_fit.consumer_anchor_count,
                b2b_soft_score=keyword_fit.b2b_soft_score,
                consumer_lexicon_sha256=self._consumer_lexicon_sha256,
                b2b_lexicon_sha256=self._b2b_lexicon_sha256,
                negative_policy_sha256=self._negative_policy_sha256,
                matcher_ms=matcher_ms,
            )

        # Stage 2: LLM classification
        llm_result = None
        llm_classifier = self.llm_classifier
        if llm_classifier:
            try:
                signal_data = {
                    "title": company_name or "Unknown",
                    "source_context": text,
                    "source_api": "pipeline",
                }
                llm_candidate = await llm_classifier.classify(signal_data)
                if self._is_operational_llm_failure(llm_candidate):
                    logger.warning(
                        "LLM returned operational failure payload; "
                        "falling back to keyword-only routing"
                    )
                else:
                    llm_result = llm_candidate
            except Exception as e:
                logger.error(f"LLM classification failed: {e}")

        # Calculate confidence adjustment
        adjustment = self._calculate_adjustment(
            keyword_fit.score,
            keyword_fit.negative_keywords,
        )

        # Determine routing
        # Phase 9: Handle LLM failures (thesis_fit_score=None means rate limit/error)
        if llm_result and llm_result.thesis_fit_score is not None:
            # LLM succeeded - use LLM score for routing
            if llm_result.category == "excluded":
                routing = RoutingDecision.REJECTED
            elif llm_result.thesis_fit_score < self.config.hold_threshold:
                routing = RoutingDecision.HELD
            else:
                routing = RoutingDecision.QUALIFIED
        else:
            # Fallback to keyword-only routing (LLM failed or skipped)
            if keyword_fit.negative_keywords:
                routing = RoutingDecision.REJECTED
            elif keyword_fit.score < self.config.hold_threshold:
                routing = RoutingDecision.HELD
            else:
                routing = RoutingDecision.QUALIFIED

        # Phase 0B-3: Extract v2_shadow from trace
        v2_shadow = None
        if keyword_fit.trace and keyword_fit.trace.v2_shadow:
            v2_shadow = keyword_fit.trace.v2_shadow

        # Extract ml_shadow from trace
        ml_shadow = None
        if keyword_fit.trace and keyword_fit.trace.ml_shadow:
            ml_shadow = keyword_fit.trace.ml_shadow

        # Phase 9: Determine if LLM was skipped (no result or None score = skipped/failed)
        llm_skipped = not llm_result or llm_result.thesis_fit_score is None

        path_code = self._determine_path_code(routing, keyword_fit)

        return ThesisFilterResult(
            routing=routing,
            keyword_score=keyword_fit.score,
            keyword_category=keyword_fit.thesis.value,
            keyword_matches=keyword_fit.matched_keywords,
            negative_keywords=keyword_fit.negative_keywords,
            llm_score=llm_result.thesis_fit_score if llm_result else None,
            llm_category=llm_result.category if llm_result else None,
            llm_rationale=llm_result.rationale if llm_result else None,
            llm_skipped=llm_skipped,
            confidence_adjustment=adjustment,
            # Phase B additions
            intent_phrases_matched=keyword_fit.intent_phrases_matched,
            domain_match=keyword_fit.domain_match,
            domain_blacklisted=keyword_fit.domain_blacklisted,
            # Phase 0B-3: v2 shadow comparison
            v2_shadow=v2_shadow,
            # ML shadow comparison
            ml_shadow=ml_shadow,
            # Phase 0 Cascade
            decision_path_code=path_code,
            consumer_signal_score=keyword_fit.consumer_signal_score,
            consumer_anchor_count=keyword_fit.consumer_anchor_count,
            b2b_soft_score=keyword_fit.b2b_soft_score,
            consumer_lexicon_sha256=self._consumer_lexicon_sha256,
            b2b_lexicon_sha256=self._b2b_lexicon_sha256,
            negative_policy_sha256=self._negative_policy_sha256,
            matcher_ms=matcher_ms,
        )

    def _calculate_adjustment(
        self,
        keyword_score: float,
        negative_keywords: List[str],
    ) -> float:
        """
        Calculate confidence adjustment based on keyword results.

        Args:
            keyword_score: Score from keyword matcher (0.0-1.0)
            negative_keywords: List of matched negative keywords

        Returns:
            Confidence adjustment value (-0.12 to +0.08)
        """
        adjustment = 0.0

        # Keyword score adjustment
        if keyword_score >= self.config.keyword_high_threshold:
            adjustment = self.config.high_boost
        elif keyword_score < self.config.keyword_low_threshold:
            adjustment = self.config.low_penalty

        # Negative keyword penalty (replaces score adjustment if present)
        if negative_keywords:
            adjustment = self.config.negative_keyword_penalty

        return adjustment

    def _is_operational_llm_failure(self, llm_result: Any) -> bool:
        """
        Detect synthetic exclusion payloads emitted when the LLM backend fails.

        LLM operational failures currently use the same schema as normal results
        (`category="excluded"` and `thesis_fit_score=0.0`) with a failure rationale.
        These should fail open to keyword routing.
        """
        if llm_result is None:
            return False

        if getattr(llm_result, "category", None) != "excluded":
            return False

        score = getattr(llm_result, "thesis_fit_score", None)
        if score is None:
            return True

        try:
            score_value = float(score)
        except (TypeError, ValueError):
            return False

        if score_value != 0.0:
            return False

        rationale = (getattr(llm_result, "rationale", "") or "").lower()
        failure_markers = (
            "classification failed",
            "rate limit exceeded",
            "circuit breaker open",
            "gemini unavailable",
            "failed to parse response",
        )
        return any(marker in rationale for marker in failure_markers)

    async def save_classification(
        self,
        signal_id: int,
        canonical_key: str,
        classification: ThesisFilterResult,
        model: str = "gemini-1.5-flash",
        prompt_version: str = "v1",
    ) -> None:
        """
        Save thesis classification to thesis_classifications table.

        This method provides the integration point for CuratedScout to store
        thesis classifications for qualified candidates with real signal_ids.

        Args:
            signal_id: Signal ID from signals table
            canonical_key: Canonical key for the company
            classification: Classification result from classify()
            model: Model used for LLM classification
            prompt_version: Prompt version identifier

        Example:
            >>> classification = await filter.classify(text)
            >>> await filter.save_classification(
            ...     signal_id=123,
            ...     canonical_key="domain:acme.ai",
            ...     classification=classification
            ... )
        """
        if not self.signal_store:
            logger.warning("No signal_store available - skipping classification save")
            return

        now = datetime.now(timezone.utc)

        async with self.signal_store.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO thesis_classifications (
                    signal_id, canonical_key,
                    keyword_score, keyword_category, negative_keywords,
                    thesis_match, thesis_fit_score, category,
                    stage_estimate, confidence, rationale, key_signals,
                    prompt_version, model,
                    classified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    canonical_key,
                    classification.keyword_score,
                    classification.keyword_category,
                    json.dumps(classification.negative_keywords),
                    # LLM fields (may be None if skipped)
                    classification.llm_score is not None and classification.llm_score > 0.5,
                    classification.llm_score,
                    classification.llm_category,
                    None,  # stage_estimate (not in current result)
                    None,  # confidence (not in current result)
                    classification.llm_rationale,
                    json.dumps(classification.keyword_matches) if classification.keyword_matches else None,
                    prompt_version,
                    model,
                    now.isoformat(),
                )
            )
            await conn.commit()

        logger.debug(
            f"Saved thesis classification for signal_id={signal_id}, "
            f"canonical_key={canonical_key}, routing={classification.routing}"
        )

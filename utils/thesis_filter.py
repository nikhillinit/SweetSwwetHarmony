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


class Web3ReasonCode(str, Enum):
    """Typed reason code for web3/crypto detection outcome."""
    UNAMBIGUOUS_CRYPTO = "unambiguous_crypto"
    AMBIGUOUS_WITH_CONTEXT = "ambiguous_with_context"
    CLEAN = "clean"


class DomainBlacklistReasonCode(str, Enum):
    """Typed reason code for domain blacklist evaluation."""
    DOMAIN_ON_BLACKLIST = "domain_on_blacklist"
    CLEAN = "clean"


class CascadeExceptionCode(str, Enum):
    """Exception codes for cascade routing failures."""
    LIVE_ROUTE_EXCEPTION = "live_route_exception"
    SHADOW_ROUTE_EXCEPTION = "shadow_route_exception"


@dataclass(frozen=True)
class CascadeResolution:
    """Structured result from _resolve_cascade_routing().

    Replaces Tuple[RoutingDecision, DecisionPathCode] for richer observability.
    """
    decision: RoutingDecision
    path_code: DecisionPathCode
    counterfactual_path_code: Optional[DecisionPathCode] = None
    cascade_exception_code: Optional[CascadeExceptionCode] = None


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
    # Phase 2 Cascade: routing parameters (Section C.2)
    cascade_routing_enablement: str = "disabled"  # disabled / shadow / live
    consumer_rescue_threshold: float = 0.25
    consumer_anchor_min: int = 1
    consumer_dominance_margin: float = 0.10
    signal_ratio_min: float = 2.0

    @classmethod
    def from_env(cls) -> "ThesisFilterConfig":
        """Create config from environment variables.

        Reads env vars per Section C.2. Invalid values fall back to defaults
        with warning (Section C.3 fail-safe).
        """
        import os

        def _float_env(env_var: str, default: float) -> float:
            val = os.environ.get(env_var)
            if val is None:
                return default
            try:
                return float(val.strip())
            except (ValueError, TypeError):
                logger.warning(
                    "event=config_load_failed, invalid %s='%s', using default=%s",
                    env_var, val, default,
                )
                return default

        def _int_env(env_var: str, default: int) -> int:
            val = os.environ.get(env_var)
            if val is None:
                return default
            try:
                return int(val.strip())
            except (ValueError, TypeError):
                logger.warning(
                    "event=config_load_failed, invalid %s='%s', using default=%s",
                    env_var, val, default,
                )
                return default

        cascade_raw = os.environ.get(
            "CASCADE_ROUTING_ENABLEMENT", "",
        ).strip().lower()
        if cascade_raw in ("disabled", "shadow", "live"):
            cascade = cascade_raw
        elif not cascade_raw:
            cascade = "disabled"
        else:
            logger.warning(
                "event=config_load_failed, applied=cascade_disabled, "
                "reason=invalid_cascade_value, value='%s'",
                cascade_raw,
            )
            cascade = "disabled"

        return cls(
            hold_threshold=_float_env("THESIS_HOLD_THRESHOLD", 0.3),
            skip_llm_if_keyword_below=_float_env("THESIS_SKIP_LLM_BELOW", 0.2),
            cascade_routing_enablement=cascade,
            consumer_rescue_threshold=_float_env(
                "THESIS_CONSUMER_RESCUE_THRESHOLD", 0.25,
            ),
            consumer_anchor_min=_int_env("THESIS_CONSUMER_ANCHOR_MIN", 1),
            consumer_dominance_margin=_float_env(
                "THESIS_CONSUMER_DOMINANCE_MARGIN", 0.10,
            ),
            signal_ratio_min=_float_env("THESIS_SIGNAL_RATIO_MIN", 2.0),
        )


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
    llm_classification_status: Optional[str] = None
    llm_primary_end_user: Optional[str] = None
    llm_paying_customer: Optional[str] = None
    llm_sells_to_or_operates_in: Optional[str] = None
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
    # Phase 6: Observability & trace contract hardening
    web3_reason_code: Optional[Web3ReasonCode] = None
    domain_blacklist_reason_code: Optional[DomainBlacklistReasonCode] = None
    counterfactual_path_code: Optional[DecisionPathCode] = None
    cascade_exception_code: Optional[CascadeExceptionCode] = None
    cascade_config_snapshot: Optional[Dict[str, Any]] = None

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
            "llm_classification_status": self.llm_classification_status,
            "llm_primary_end_user": self.llm_primary_end_user,
            "llm_paying_customer": self.llm_paying_customer,
            "llm_sells_to_or_operates_in": self.llm_sells_to_or_operates_in,
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
        # Phase 6: Always emit web3_reason_code for analytics consistency
        result["web3_reason_code"] = self.web3_reason_code.value if self.web3_reason_code else None
        # Remaining Phase 6 fields conditional
        if self.domain_blacklist_reason_code is not None:
            result["domain_blacklist_reason_code"] = self.domain_blacklist_reason_code.value
        if self.counterfactual_path_code is not None:
            result["counterfactual_path_code"] = self.counterfactual_path_code.value
        if self.cascade_exception_code is not None:
            result["cascade_exception_code"] = self.cascade_exception_code.value
        if self.cascade_config_snapshot is not None:
            result["cascade_config_snapshot"] = self.cascade_config_snapshot
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
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

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

    def _route_keyword_only(
        self,
        fit: Any,
        *,
        cascade_enabled: bool = False,
    ) -> Tuple[RoutingDecision, DecisionPathCode]:
        """Shared routing helper for keyword-only decisions (Section C.1).

        Handles both legacy (cascade_enabled=False) and cascade
        (cascade_enabled=True) routing paths in a single function.

        Args:
            fit: ThesisFit result from keyword matcher.
            cascade_enabled: Whether cascade consumer rescue is active.

        Returns:
            (RoutingDecision, DecisionPathCode) tuple.
        """
        # Domain blacklist veto
        if fit.domain_blacklisted:
            return RoutingDecision.REJECTED, DecisionPathCode.VETO_DOMAIN_BLACKLIST

        hard_reject = (
            set(fit.trace.matched_hard_rejects) if fit.trace else set()
        )
        hard_hold = (
            set(fit.trace.matched_hard_holds) if fit.trace else set()
        )

        # Hard reject = absolute veto (precedes LLM, no rescue)
        if hard_reject:
            return RoutingDecision.REJECTED, DecisionPathCode.VETO_HARD_REJECT

        # Hard hold = never auto-qualify, route to HELD for human review
        if hard_hold:
            return RoutingDecision.HELD, DecisionPathCode.HOLD_HARD_HOLD

        # Strong sector match
        if fit.score >= self.config.hold_threshold:
            return RoutingDecision.QUALIFIED, DecisionPathCode.QUALIFY_SECTOR

        # Consumer rescue (ONLY when cascade enabled)
        if not cascade_enabled:
            # Legacy behavior
            if fit.negative_keywords:
                return RoutingDecision.REJECTED, DecisionPathCode.VETO_HARD_REJECT
            return RoutingDecision.HELD, DecisionPathCode.HOLD_DEFAULT

        # Cascade: consumer rescue attempt
        has_anchor = (
            fit.consumer_anchor_count >= self.config.consumer_anchor_min
        )
        dominance_ok = (
            (fit.consumer_signal_score - fit.b2b_soft_score)
            >= self.config.consumer_dominance_margin
            or fit.consumer_signal_score
            / max(fit.b2b_soft_score, 0.01)
            >= self.config.signal_ratio_min
        )

        if (
            fit.consumer_signal_score >= self.config.consumer_rescue_threshold
            and has_anchor
            and dominance_ok
        ):
            return (
                RoutingDecision.QUALIFIED,
                DecisionPathCode.QUALIFY_CONSUMER_RESCUE,
            )

        # Distinguishes "had consumer signal but B2B dominance blocked"
        if (
            fit.consumer_signal_score >= self.config.consumer_rescue_threshold
            and has_anchor
        ):
            return RoutingDecision.HELD, DecisionPathCode.HOLD_B2B_GUARD_BLOCK

        return RoutingDecision.HELD, DecisionPathCode.HOLD_DEFAULT

    def _resolve_cascade_routing(
        self,
        keyword_fit: Any,
    ) -> CascadeResolution:
        """Route keyword-only with cascade mode awareness.

        Shadow: compute both, log counterfactual, return legacy.
        Live: use cascade result; exception → inline legacy fallback.
        Disabled: use legacy.

        Returns CascadeResolution with decision, path_code, and optional
        counterfactual_path_code / cascade_exception_code.
        """
        cascade_mode = self.config.cascade_routing_enablement

        if cascade_mode == "live":
            try:
                decision, path_code = self._route_keyword_only(
                    keyword_fit, cascade_enabled=True,
                )
                return CascadeResolution(decision=decision, path_code=path_code)
            except Exception as e:
                logger.warning(
                    "event=cascade_exception, error=%s, applied=legacy_fallback",
                    str(e),
                )
                # Inline legacy fallback (Section C.4)
                if keyword_fit.negative_keywords:
                    decision = RoutingDecision.REJECTED
                elif keyword_fit.score < self.config.hold_threshold:
                    decision = RoutingDecision.HELD
                else:
                    decision = RoutingDecision.QUALIFIED
                return CascadeResolution(
                    decision=decision,
                    path_code=DecisionPathCode.HOLD_DEFAULT,
                    cascade_exception_code=CascadeExceptionCode.LIVE_ROUTE_EXCEPTION,
                )

        if cascade_mode == "shadow":
            legacy_routing, legacy_code = self._route_keyword_only(
                keyword_fit, cascade_enabled=False,
            )
            try:
                _cascade_routing, cascade_code = self._route_keyword_only(
                    keyword_fit, cascade_enabled=True,
                )
                counterfactual = cascade_code
                exception_code = None
            except Exception:
                _cascade_routing, cascade_code = None, None
                counterfactual = None
                exception_code = CascadeExceptionCode.SHADOW_ROUTE_EXCEPTION

            logger.info(
                "cascade_counterfactual: legacy=%s/%s, cascade=%s/%s, "
                "consumer_signal=%.4f, anchors=%d, b2b_soft=%.4f",
                legacy_routing.value,
                legacy_code.value,
                _cascade_routing.value if _cascade_routing else "error",
                cascade_code.value if cascade_code else "error",
                keyword_fit.consumer_signal_score,
                keyword_fit.consumer_anchor_count,
                keyword_fit.b2b_soft_score,
            )
            return CascadeResolution(
                decision=legacy_routing,
                path_code=legacy_code,
                counterfactual_path_code=counterfactual,
                cascade_exception_code=exception_code,
            )

        # disabled
        decision, path_code = self._route_keyword_only(
            keyword_fit, cascade_enabled=False,
        )
        return CascadeResolution(decision=decision, path_code=path_code)

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

        # Phase 6: Compute web3_reason_code
        if web3_result.is_crypto:
            _w3rc = (
                Web3ReasonCode.UNAMBIGUOUS_CRYPTO
                if web3_result.matched_term
                and web3_result.matched_term.lower() in Web3Detector.UNAMBIGUOUS_CRYPTO
                else Web3ReasonCode.AMBIGUOUS_WITH_CONTEXT
            )
        else:
            _w3rc = Web3ReasonCode.CLEAN

        # Phase 6: Build config snapshot (once per classify call)
        _snapshot: Dict[str, Any] = {
            # cascade_mode_used: effective mode for THIS filter instance.
            # Precondition: caller must pass post-gate resolved value via config.
            # ThesisFilterConfig.from_env() reads raw env (no phase gate);
            # pipeline.py constructs config with explicit kwarg (safe).
            "cascade_routing_enablement": self.config.cascade_routing_enablement,
            "cascade_mode_used": self.config.cascade_routing_enablement,
            "hold_threshold": self.config.hold_threshold,
            "skip_llm_if_keyword_below": self.config.skip_llm_if_keyword_below,
            "consumer_rescue_threshold": self.config.consumer_rescue_threshold,
            "consumer_anchor_min": self.config.consumer_anchor_min,
            "consumer_dominance_margin": self.config.consumer_dominance_margin,
            "signal_ratio_min": self.config.signal_ratio_min,
            "consumer_lexicon_sha256": self._consumer_lexicon_sha256,
            "b2b_lexicon_sha256": self._b2b_lexicon_sha256,
            "negative_policy_sha256": self._negative_policy_sha256,
        }
        if self.config.cascade_routing_enablement == "shadow":
            _snapshot["keyword_high_threshold"] = self.config.keyword_high_threshold
            _snapshot["keyword_low_threshold"] = self.config.keyword_low_threshold
            _snapshot["high_boost"] = self.config.high_boost
            _snapshot["low_penalty"] = self.config.low_penalty
            _snapshot["negative_keyword_penalty"] = self.config.negative_keyword_penalty

        if web3_result.is_crypto:
            return ThesisFilterResult(
                routing=RoutingDecision.REJECTED,
                rejection_reason=web3_result.reason,
                negative_keywords=[web3_result.matched_term],
                decision_path_code=DecisionPathCode.VETO_WEB3,
                consumer_lexicon_sha256=self._consumer_lexicon_sha256,
                b2b_lexicon_sha256=self._b2b_lexicon_sha256,
                negative_policy_sha256=self._negative_policy_sha256,
                # Phase 6: early web3 veto
                web3_reason_code=_w3rc,
                domain_blacklist_reason_code=None,  # Not evaluated (early exit)
                cascade_config_snapshot=_snapshot,
            )

        # Stage 1: Keyword matching (with Phase B domain support)
        t0 = time.monotonic()
        keyword_fit = self._keyword_matcher.score(text, company_name, domain_name=domain_name)
        matcher_ms = (time.monotonic() - t0) * 1000

        # Phase 6: Compute domain_blacklist_reason_code
        _dbl_rc = (
            DomainBlacklistReasonCode.DOMAIN_ON_BLACKLIST
            if keyword_fit.domain_blacklisted
            else DomainBlacklistReasonCode.CLEAN
        )

        # Check if we should skip LLM (obvious non-fit or explicit skip)
        if skip_llm or keyword_fit.score < self.config.skip_llm_if_keyword_below:
            adjustment = self._calculate_adjustment(
                keyword_fit.score,
                keyword_fit.negative_keywords,
            )

            # Phase 2: Cascade-aware routing via shared helper
            resolution = self._resolve_cascade_routing(keyword_fit)
            routing, path_code = resolution.decision, resolution.path_code

            # Phase 0B-3: Extract v2_shadow from trace
            v2_shadow = None
            if keyword_fit.trace and keyword_fit.trace.v2_shadow:
                v2_shadow = keyword_fit.trace.v2_shadow

            # Extract ml_shadow from trace
            ml_shadow = None
            if keyword_fit.trace and keyword_fit.trace.ml_shadow:
                ml_shadow = keyword_fit.trace.ml_shadow

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
                # Phase 6
                web3_reason_code=_w3rc,
                domain_blacklist_reason_code=_dbl_rc,
                counterfactual_path_code=resolution.counterfactual_path_code,
                cascade_exception_code=resolution.cascade_exception_code,
                cascade_config_snapshot=_snapshot,
            )

        # Stage 2: LLM classification
        llm_result = None
        llm_failure_status = None
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
                    llm_failure_status = getattr(llm_candidate, "classification_status", None)
                    if hasattr(llm_failure_status, "value"):
                        llm_failure_status = llm_failure_status.value
                    if not isinstance(llm_failure_status, str):
                        llm_failure_status = None
                    logger.warning(
                        "LLM returned operational failure payload; "
                        "falling back to keyword-only routing"
                    )
                else:
                    llm_result = llm_candidate
            except Exception as e:
                llm_failure_status = "error_api"
                logger.error(f"LLM classification failed: {e}")

        # Calculate confidence adjustment
        adjustment = self._calculate_adjustment(
            keyword_fit.score,
            keyword_fit.negative_keywords,
        )

        # Determine routing
        # Phase 9: Handle LLM failures (thesis_fit_score=None means rate limit/error)
        resolution = None
        llm_score = getattr(llm_result, "thesis_fit_score", None) if llm_result else None
        try:
            llm_score_f = float(llm_score) if llm_score is not None else None
        except (TypeError, ValueError):
            llm_score_f = None
        if llm_result and llm_score_f is not None:
            # LLM succeeded - use LLM score for routing
            if getattr(llm_result, "category", None) == "excluded":
                routing = RoutingDecision.REJECTED
            elif 0.20 <= llm_score_f < 0.30:
                # Ambiguous-distribution range: prompt instructs the LLM
                # to score employer-funded / benefit-linked products here
                routing = RoutingDecision.HELD
            elif llm_score_f < self.config.hold_threshold:
                routing = RoutingDecision.HELD
            else:
                routing = RoutingDecision.QUALIFIED
            # LLM determines routing; path_code reflects keyword-level context
            path_code = self._determine_path_code(routing, keyword_fit)
        else:
            # Fallback to cascade-aware keyword routing (LLM failed or skipped)
            resolution = self._resolve_cascade_routing(keyword_fit)
            routing, path_code = resolution.decision, resolution.path_code

        # Phase 0B-3: Extract v2_shadow from trace
        v2_shadow = None
        if keyword_fit.trace and keyword_fit.trace.v2_shadow:
            v2_shadow = keyword_fit.trace.v2_shadow

        # Extract ml_shadow from trace
        ml_shadow = None
        if keyword_fit.trace and keyword_fit.trace.ml_shadow:
            ml_shadow = keyword_fit.trace.ml_shadow

        # Phase 9: Determine if LLM was skipped (no result or None score = skipped/failed)
        llm_skipped = not llm_result or getattr(llm_result, "thesis_fit_score", None) is None

        return ThesisFilterResult(
            routing=routing,
            keyword_score=keyword_fit.score,
            keyword_category=keyword_fit.thesis.value,
            keyword_matches=keyword_fit.matched_keywords,
            negative_keywords=keyword_fit.negative_keywords,
            llm_score=getattr(llm_result, "thesis_fit_score", None) if llm_result else None,
            llm_category=getattr(llm_result, "category", None) if llm_result else None,
            llm_rationale=getattr(llm_result, "rationale", None) if llm_result else None,
            llm_classification_status=(
                getattr(llm_result, "classification_status", None) if llm_result else llm_failure_status
            ),
            llm_primary_end_user=getattr(llm_result, "primary_end_user", None) if llm_result else None,
            llm_paying_customer=getattr(llm_result, "paying_customer", None) if llm_result else None,
            llm_sells_to_or_operates_in=(
                getattr(llm_result, "sells_to_or_operates_in", None) if llm_result else None
            ),
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
            # Phase 6
            web3_reason_code=_w3rc,
            domain_blacklist_reason_code=_dbl_rc,
            counterfactual_path_code=resolution.counterfactual_path_code if resolution else None,
            cascade_exception_code=resolution.cascade_exception_code if resolution else None,
            cascade_config_snapshot=_snapshot,
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
        Detect operational failures emitted by the LLM backend.

        Prefer the explicit classification_status field emitted by the
        classifier. Keep the legacy payload-shape fallback so older mocks and
        historical persisted payloads still fail open safely.
        """
        if llm_result is None:
            return False

        classification_status = getattr(llm_result, "classification_status", None)
        if hasattr(classification_status, "value"):
            classification_status = classification_status.value
        if isinstance(classification_status, str):
            return classification_status != "success"

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

        raw_rationale = getattr(llm_result, "rationale", "") or ""
        if not isinstance(raw_rationale, str):
            return False

        rationale = raw_rationale.lower()
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

        await self.signal_store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key=canonical_key,
            keyword_score=classification.keyword_score,
            keyword_category=classification.keyword_category,
            negative_keywords=classification.negative_keywords,
            thesis_match=(
                classification.llm_score is not None and classification.llm_score > 0.5
            ),
            thesis_fit_score=classification.llm_score,
            category=classification.llm_category,
            primary_end_user=classification.llm_primary_end_user,
            paying_customer=classification.llm_paying_customer,
            sells_to_or_operates_in=classification.llm_sells_to_or_operates_in,
            stage_estimate=None,
            confidence=None,
            rationale=classification.llm_rationale,
            key_signals=classification.keyword_matches,
            prompt_version=prompt_version,
            model=model,
            classification_status=classification.llm_classification_status or "success",
        )

        logger.debug(
            f"Saved thesis classification for signal_id={signal_id}, "
            f"canonical_key={canonical_key}, routing={classification.routing}"
        )

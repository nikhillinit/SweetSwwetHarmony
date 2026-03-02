"""
Consumer Thesis Matcher - Keyword-based thesis fit scoring.

Matches companies against Press On Ventures' Consumer investment thesis:
- Consumer CPG: Food, beverage, snacks, beauty, personal care
- Consumer Health Tech: Fitness, wellness, mental health, supplements
- Travel & Hospitality: Travel booking, hospitality tech, restaurants
- Consumer Marketplaces: Consumer-facing two-sided markets

Phase B Enhancements (from founder_intel_canonical):
- Intent phrases: "join waitlist", "private beta", "pricing", etc.
- Domain regex patterns: get*, try*, join* consumer domains
- Domain blacklist: localhost, example, staging, etc.
- Additional negative keywords: boilerplate, template, tutorial, etc.

Phase 0A: v2 Policy Infrastructure (scaffolding only, no scoring changes)
- RuntimeControls: Centralized env/arg parsing for v2 enablement
- PolicyLoader: YAML policy loading with permissive/strict modes
- Zero-cost when disabled: No I/O when v2_enablement="disabled"

Usage:
    from utils.thesis_matcher import ThesisMatcher, ThesisFit

    matcher = ThesisMatcher()
    fit = matcher.score("Meal kit delivery startup")
    print(f"Thesis: {fit.thesis}, Score: {fit.score}")

    # With domain analysis
    fit = matcher.score("Health app", domain_name="getfitness.com")
    print(f"Domain match: {fit.domain_match}")

    # With v2 policy (shadow mode - logging only, no scoring changes yet)
    matcher_v2 = ThesisMatcher(v2_enablement="shadow")
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Tuple

if TYPE_CHECKING:
    from utils.policy_loader import PolicyBundle
    from utils.runtime_controls import RuntimeControls

logger = logging.getLogger(__name__)


class ConsumerThesis(str, Enum):
    """Consumer investment thesis categories."""
    CONSUMER_CPG = "consumer_cpg"
    CONSUMER_HEALTH_TECH = "consumer_health_tech"
    TRAVEL_HOSPITALITY = "travel_hospitality"
    CONSUMER_MARKETPLACE = "consumer_marketplace"
    # Phase 4a: Lexicon expansion — new consumer categories
    CONSUMER_FINTECH = "consumer_fintech"
    CONSUMER_SOCIAL = "consumer_social"
    CONSUMER_GENERAL = "consumer_general"
    UNKNOWN = "unknown"


# Keyword lists for each thesis (weighted by specificity)
CONSUMER_KEYWORDS: Dict[ConsumerThesis, Dict[str, float]] = {
    ConsumerThesis.CONSUMER_CPG: {
        # High weight - specific CPG terms
        "meal kit": 0.9,
        "meal kits": 0.9,
        "beverage brand": 0.9,
        "food brand": 0.8,
        "snack brand": 0.8,
        "skincare brand": 0.9,
        "beauty brand": 0.8,
        "personal care": 0.8,
        "household products": 0.7,
        "cpg": 0.8,
        "d2c": 0.7,
        "dtc": 0.7,
        "direct to consumer": 0.7,
        # Phase 4a: expanded CPG keywords
        "pet care": 0.7,
        "pet food": 0.7,
        "baby products": 0.7,
        "home goods": 0.6,
        "furniture": 0.5,
        # Medium weight - general terms
        "healthy": 0.4,
        "food": 0.4,
        "beverage": 0.5,
        "snack": 0.4,
        "drink": 0.4,
        "grocery": 0.5,
        "organic": 0.4,
        "vegan": 0.5,
        "plant-based": 0.5,
        "beauty": 0.4,
        "skincare": 0.5,
        "cosmetics": 0.5,
    },
    ConsumerThesis.CONSUMER_HEALTH_TECH: {
        # High weight - specific health tech terms
        "fitness app": 0.9,
        "wellness app": 0.9,
        "wellness platform": 0.8,
        "mental health app": 0.9,
        "health tracker": 0.8,
        "meditation app": 0.8,
        "sleep app": 0.8,
        "sleep tracking": 0.8,
        "nutrition app": 0.7,
        "personalized workout": 0.8,
        # Phase 4a: expanded health tech keywords
        "telehealth": 0.7,
        "telemedicine": 0.7,
        "digital health": 0.7,
        "health platform": 0.7,
        "fertility": 0.5,
        "womens health": 0.7,
        "maternal health": 0.7,
        "elder care": 0.6,
        "pharmacy": 0.5,
        # Medium weight - general terms
        "fitness": 0.5,
        "workout": 0.5,
        "wellness": 0.4,
        "meditation": 0.5,
        "sleep": 0.4,
        "supplements": 0.5,
        "vitamins": 0.4,
        "wearable": 0.5,
        "health app": 0.6,
        "mental health": 0.5,
        "therapy": 0.4,
        "guided relaxation": 0.5,
        "patient": 0.4,
        "diagnostics": 0.4,
    },
    ConsumerThesis.TRAVEL_HOSPITALITY: {
        # High weight - specific travel terms
        "travel booking": 0.9,
        "hotel tech": 0.8,
        "hospitality tech": 0.8,
        "hospitality platform": 0.8,
        "restaurant tech": 0.8,
        "travel platform": 0.7,
        "vacation rental": 0.8,
        "experience booking": 0.7,
        # Medium weight - general terms
        "travel": 0.5,
        "booking": 0.4,
        "hotel": 0.5,
        "hospitality": 0.5,
        "restaurant": 0.4,
        "vacation": 0.4,
        "experiences": 0.4,
        "tourism": 0.5,
        "lodging": 0.5,
    },
    ConsumerThesis.CONSUMER_MARKETPLACE: {
        # High weight - specific marketplace terms
        "consumer marketplace": 0.9,
        "two-sided market": 0.8,
        "peer-to-peer": 0.7,
        "p2p marketplace": 0.8,
        "c2c marketplace": 0.8,
        "buyer seller": 0.6,
        # Medium weight - general terms
        "marketplace": 0.6,
        "e-commerce": 0.5,
        "delivery": 0.4,
        "subscription": 0.4,
        "shopping": 0.4,
        "resale": 0.5,
        "secondhand": 0.5,
        "rental": 0.4,
    },
    # Phase 4a: new consumer categories
    ConsumerThesis.CONSUMER_FINTECH: {
        # High weight - specific consumer fintech terms
        "budgeting app": 0.9,
        "personal finance app": 0.9,
        "personal finance": 0.7,
        "payment app": 0.8,
        "neobank": 0.8,
        "consumer banking": 0.8,
        "digital wallet": 0.7,
        "money management": 0.7,
        # Medium weight - general fintech terms
        "fintech": 0.5,
        "payments": 0.4,
        "banking app": 0.6,
        "savings app": 0.6,
        "investing app": 0.6,
        "insurance app": 0.6,
        "lending platform": 0.5,
        "credit score": 0.5,
        "money transfer": 0.5,
    },
    ConsumerThesis.CONSUMER_SOCIAL: {
        # High weight - specific consumer social terms
        "dating app": 0.9,
        "social network": 0.8,
        "social media app": 0.9,
        "messaging app": 0.8,
        "community app": 0.8,
        "content creator platform": 0.8,
        # Medium weight - general social terms
        "dating": 0.5,
        "social media": 0.5,
        "messaging": 0.4,
        "social platform": 0.5,
        "social app": 0.6,
        "content sharing": 0.5,
        "creator platform": 0.6,
        "creator economy": 0.5,
        "live streaming": 0.5,
        "fan engagement": 0.5,
    },
    ConsumerThesis.CONSUMER_GENERAL: {
        # High weight - specific consumer general terms
        "consumer app": 0.8,
        "consumer platform": 0.8,
        "consumer brand": 0.8,
        "consumer product": 0.7,
        "lifestyle app": 0.7,
        "family app": 0.7,
        "parenting app": 0.7,
        "kids app": 0.7,
        # Medium weight - general consumer terms
        "consumer": 0.3,
        "mobile app": 0.3,
        "home services": 0.5,
        "on demand": 0.4,
        "subscription service": 0.5,
        "loyalty program": 0.5,
        "rewards": 0.4,
    },
}

# =============================================================================
# 3-TIER NEGATIVE KEYWORD MODEL (Phase 0: Cascade Instrumentation)
# =============================================================================
# ADR-1: hard_reject = absolute veto (crypto, template, late-stage)
#         hard_hold = never auto-qualifies, routes to HELD (enterprise ambiguity)
#         soft = score dampening only, no rejection
# =============================================================================

HARD_REJECT_KEYWORDS: Dict[str, float] = {
    # Crypto/Web3 — absolute veto, short-circuits before LLM
    "blockchain": 0.5,
    "crypto": 0.5,
    "web3": 0.5,
    "nft": 0.5,
    "defi": 0.5,
    "crypto token": 0.5,
    "nft token": 0.5,
    # Late-stage — out of thesis
    "series c": 0.4,
    "series d": 0.5,
    # Template/Educational — noise, not companies
    "boilerplate": 0.6,
    "template": 0.5,
    "tutorial": 0.5,
    "demo repo": 0.5,
    "homework": 0.4,
    "assignment": 0.4,
}

HARD_HOLD_KEYWORDS: Dict[str, float] = {
    # B2B/Enterprise — ambiguous, may have consumer overlap; route to HELD for review
    "enterprise": 0.5,
    "b2b": 0.5,
    "saas platform": 0.4,
    "infrastructure": 0.4,
    "logistics platform": 0.5,
    "series b": 0.3,
}

SOFT_PENALTY_KEYWORDS: Dict[str, float] = {
    # Developer tools — score dampening
    "developer tool": 0.5,
    "api platform": 0.4,
    "api management": 0.5,
    "devops": 0.5,
    "logistics": 0.3,
    "data platform": 0.4,
    "sdk": 0.4,
    "cli": 0.4,
    "library": 0.4,
    "framework": 0.4,
    "plugin": 0.4,
    "linter": 0.5,
    # Services/Agency
    "consulting": 0.4,
    "agency": 0.4,
    "services firm": 0.4,
    "aggregator": 0.2,
    # Educational (softer)
    "starter": 0.5,
    "workshop": 0.4,
    "course": 0.4,
    "example": 0.3,
    # HN FP analysis (2026-03-01): B2B/dev tool patterns
    "log aggregation": 0.5,
    "mysql": 0.5,
    "vector rendering": 0.4,
    "compression algorithm": 0.4,
    "embedded scheduler": 0.5,
    "mcp server": 0.5,
    "password system": 0.4,
    "legal research": 0.4,
    "stock movements": 0.4,
    "benchmark for llms": 0.5,
    "ship python to aws": 0.5,
    "tabular data": 0.4,
    "floor plans": 0.4,
    "skills marketplace": 0.5,
    "data-centric ai": 0.4,
    "sentiment on ai": 0.4,
    "production management tool": 0.4,
}

# Union for backward compatibility — bare 'token' removed per ADR (context-qualified only)
NEGATIVE_KEYWORDS: Dict[str, float] = {
    **HARD_REJECT_KEYWORDS,
    **HARD_HOLD_KEYWORDS,
    **SOFT_PENALTY_KEYWORDS,
}

# =============================================================================
# CONSUMER SIGNAL KEYWORDS — Tiered (A/M/N) with contribution caps
# =============================================================================
# ADR-2: Used for dominance-margin rescue in Phase 2.
# Phase 0: Computed for instrumentation/counterfactual, no routing changes.
# =============================================================================

CONSUMER_SIGNAL_KEYWORDS: Dict[str, Dict[str, float]] = {
    # A-tier (anchor): cap 0.70, weight 0.30-0.40
    "A": {
        "direct to consumer": 0.35,
        "d2c": 0.35,
        "dtc": 0.35,
        "consumer app": 0.35,
        "e-commerce": 0.30,
        "ecommerce": 0.30,
        "shopping": 0.30,
        "checkout": 0.30,
        "subscription box": 0.35,
    },
    # M-tier (medium): cap 0.40, weight 0.15-0.25
    "M": {
        "subscription": 0.20,
        "brand": 0.15,
        "retail": 0.20,
        "delivery": 0.15,
        "on-demand": 0.20,
        "membership": 0.20,
        "lifestyle": 0.15,
        "waitlist": 0.15,
    },
    # N-tier (ambient): cap 0.15, weight 0.05-0.10
    "N": {
        "app": 0.08,
        "users": 0.06,
        "customers": 0.06,
        "personalized": 0.08,
        "social": 0.07,
        "community": 0.06,
        "download": 0.05,
    },
}

# Tier contribution caps
_TIER_CAPS: Dict[str, float] = {"A": 0.70, "M": 0.40, "N": 0.15}

# Intent phrases that indicate commercial/consumer intent (Phase B)
INTENT_PHRASES: Dict[str, float] = {
    "join waitlist": 0.3,
    "request access": 0.3,
    "private beta": 0.3,
    "coming soon": 0.2,
    "book a demo": 0.2,
    "sign up": 0.2,
    "get started": 0.2,
    "pricing": 0.25,
    "subscribe": 0.2,
}

# Regex patterns for consumer-oriented domains (Phase B)
CONSUMER_DOMAIN_PATTERNS: List[str] = [
    r"^get[a-z0-9-]{3,}$",  # getmyapp.com
    r"^try[a-z0-9-]{3,}$",  # tryproduct.io
    r"^join[a-z0-9-]{3,}$",  # joincommunity.co
]

# Domain blacklist fragments for non-production domains (Phase B)
DOMAIN_BLACKLIST_FRAGMENTS: List[str] = [
    "localhost",
    "example",
    "sample",
    "test",
    "staging",
    "dev.",
    "internal",
]


@dataclass
class ThesisFitTrace:
    """Explainability trace for thesis classification debugging.

    Captures all decision points in the thesis matching process to enable
    debugging and tuning of the scoring algorithm.

    Fields:
        matched_hard_negatives: Hard negative keywords that completely disqualify
        soft_negatives: Soft negative keywords with their individual penalties
        rescue_anchors_matched: Tier-grouped positive keywords that can rescue
        rescue_blocked_by: Reason why rescue was blocked (if applicable)
        aggregator_exception_triggered: Whether aggregator override was applied
        applied_ai_path: AI-specific routing path (prosumer | role_based_b2b | None)
        final_score: Final thesis fit score after all adjustments
        routing_decision: Routing decision (QUALIFIED | HELD | REJECTED)
        explanation: Human-readable explanation of the scoring decision
        v2_shadow: Shadow mode comparison data (v1 vs v2 scoring diff) - Phase 0B-2

    Example explanation:
        "Score: 0.52. Soft negative 'platform' (-0.05) rescued by tier-1 anchor
        'patient' (+0.35). Anti-rescue not triggered. Routed to QUALIFIED."
    """
    matched_hard_negatives: List[str] = field(default_factory=list)
    # Phase 0 Cascade: 3-tier negative classification
    matched_hard_rejects: List[str] = field(default_factory=list)
    matched_hard_holds: List[str] = field(default_factory=list)
    soft_negatives: List[Tuple[str, float]] = field(default_factory=list)
    rescue_anchors_matched: Dict[str, List[str]] = field(default_factory=dict)
    rescue_blocked_by: Optional[str] = None
    aggregator_exception_triggered: bool = False
    applied_ai_path: Optional[str] = None
    final_score: float = 0.0
    routing_decision: str = "UNKNOWN"
    explanation: str = ""
    # Phase 0B-2: Shadow mode comparison data
    v2_shadow: Optional[Dict] = None
    # ML shadow: ML thesis model comparison data (disabled/shadow/live)
    ml_shadow: Optional[Dict] = None

    def to_dict(self) -> Dict:
        """Convert trace to dictionary for serialization."""
        result = {
            "matched_hard_negatives": self.matched_hard_negatives,
            "matched_hard_rejects": self.matched_hard_rejects,
            "matched_hard_holds": self.matched_hard_holds,
            "soft_negatives": [{"keyword": kw, "penalty": penalty}
                              for kw, penalty in self.soft_negatives],
            "rescue_anchors_matched": self.rescue_anchors_matched,
            "rescue_blocked_by": self.rescue_blocked_by,
            "aggregator_exception_triggered": self.aggregator_exception_triggered,
            "applied_ai_path": self.applied_ai_path,
            "final_score": round(self.final_score, 3),
            "routing_decision": self.routing_decision,
            "explanation": self.explanation,
        }
        # Phase 0B-2: Only include v2_shadow if present
        if self.v2_shadow is not None:
            result["v2_shadow"] = self.v2_shadow
        # ML shadow: Only include if present
        if self.ml_shadow is not None:
            result["ml_shadow"] = self.ml_shadow
        return result


@dataclass
class ThesisFit:
    """Result of thesis matching."""
    thesis: ConsumerThesis
    score: float  # 0.0-1.0
    matched_keywords: List[str]
    negative_keywords: List[str]
    all_scores: Dict[str, float]  # Score per thesis
    confidence: str  # HIGH, MEDIUM, LOW
    # Phase B additions
    intent_phrases_matched: List[str] = field(default_factory=list)
    domain_match: bool = False
    domain_blacklisted: bool = False
    # Gap 9: Explainability trace
    trace: Optional[ThesisFitTrace] = None
    # Phase 0 Cascade: Consumer signal scoring (instrumentation only)
    consumer_signal_score: float = 0.0
    consumer_anchor_count: int = 0
    b2b_soft_score: float = 0.0

    @property
    def is_fit(self) -> bool:
        """Returns True if score indicates good thesis fit."""
        return self.score >= 0.4

    def to_dict(self) -> Dict:
        result = {
            "thesis": self.thesis.value,
            "score": round(self.score, 3),
            "matched_keywords": self.matched_keywords,
            "negative_keywords": self.negative_keywords,
            "all_scores": {k: round(v, 3) for k, v in self.all_scores.items()},
            "confidence": self.confidence,
            "is_fit": self.is_fit,
            # Phase B additions
            "intent_phrases_matched": self.intent_phrases_matched,
            "domain_match": self.domain_match,
            "domain_blacklisted": self.domain_blacklisted,
            # Phase 0 Cascade
            "consumer_signal_score": round(self.consumer_signal_score, 4),
            "consumer_anchor_count": self.consumer_anchor_count,
            "b2b_soft_score": round(self.b2b_soft_score, 4),
        }
        # Gap 9: Include trace if available
        if self.trace:
            result["trace"] = self.trace.to_dict()
        return result


@dataclass(frozen=True)
class _CoreScore:
    """Internal: Core scoring result before penalty application.

    Captures positive thesis scoring + intent/domain signals.
    Shared between v1 and v2 paths to ensure differences only come from negatives.
    """
    normalized: str
    scores: Dict[str, float]
    all_matches: Dict[str, List[str]]
    best_thesis: ConsumerThesis
    base_score: float  # Best score BEFORE negative penalty + boosts
    matched_kws: List[str]
    intent_matches: List[str]
    domain_match: bool


@dataclass(frozen=True)
class _PenaltyResult:
    """Internal: Result of negative keyword penalty calculation.

    Separates penalty computation from score adjustment for v1/v2 comparison.
    """
    matches: List[str]
    raw_penalty: float  # sum(weights)
    applied_penalty: float  # raw_penalty * 0.5 (current behavior)


class ThesisMatcher:
    """
    Matches company descriptions against Consumer investment thesis.

    Uses keyword matching with weights to score thesis fit.
    Returns the best-matching thesis with a confidence score.

    Phase 0A v2 Policy Support:
    - Accepts v2_enablement, policy_loader_mode, v2_execution_enabled kwargs
    - Legacy enable_v2_policy kwarg mapped to v2_enablement
    - Zero-cost when disabled (no I/O, no policy loading)
    - Scoring behavior unchanged in Phase 0A (scaffolding only)
    """

    # Default model path for ML thesis model
    _DEFAULT_ML_MODEL_PATH = "models/thesis_classifier.joblib"

    # ML circuit breaker: disable after N consecutive failures
    _ML_FAILURE_THRESHOLD = 5

    # ML latency budget (ms): log warning if prediction exceeds this
    _ML_LATENCY_BUDGET_MS = 500.0

    def __init__(
        self,
        custom_keywords: Optional[Dict[ConsumerThesis, Dict[str, float]]] = None,
        *,
        enable_v2_policy: Optional[bool] = None,
        v2_enablement: Optional[str] = None,
        policy_loader_mode: Optional[str] = None,
        v2_execution_enabled: Optional[bool] = None,
        config_path: Optional[str] = None,
        ml_enablement: Optional[str] = None,
        ml_model_path: Optional[str] = None,
    ):
        """Initialize ThesisMatcher with optional v2 policy and ML model.

        Args:
            custom_keywords: Custom keyword weights to merge with defaults
            enable_v2_policy: Legacy kwarg (True → shadow, False → disabled)
            v2_enablement: "disabled", "shadow", or "live"
            policy_loader_mode: "permissive" or "strict"
            v2_execution_enabled: Whether v2 scoring is active
            config_path: Explicit path to policy directory
            ml_enablement: "disabled", "shadow", or "live" (ML thesis model)
            ml_model_path: Path to trained ML model file (joblib)

        Phase 0A: v2 infrastructure is wired but scoring behavior is unchanged.
        ML: Model loaded when ml_enablement != "disabled".
        """
        # Initialize v1 keywords (always happens)
        self.keywords = {k: dict(v) for k, v in CONSUMER_KEYWORDS.items()}
        if custom_keywords:
            for thesis, kws in custom_keywords.items():
                if thesis in self.keywords:
                    self.keywords[thesis].update(kws)
                else:
                    self.keywords[thesis] = kws

        # Phase 0A: Wire v2 controls
        self._controls: Optional["RuntimeControls"] = None
        self._policy_bundle: Optional["PolicyBundle"] = None
        self.config: Dict = {}  # Shallow copy of policies (Bug #5 mitigation)
        # Phase 0B-1: Typed policy object (populated when v2 enabled, None when disabled)
        self._negative_keyword_policy = None  # NegativeKeywordPolicy or None
        # Phase 0B-3: Policy hash for tracking which policy version was used
        self._policy_hash: Optional[str] = None

        # ML model state (initialized BEFORE v2 early return - Review 1 bug fix)
        self._ml_model = None  # MLThesisModel or None
        self._ml_failure_count: int = 0

        # Step 1: Resolve RuntimeControls (validate-before-I/O)
        # Import here to avoid circular imports and allow zero-cost when disabled
        from utils.runtime_controls import RuntimeControls

        self._controls = RuntimeControls.from_env(
            v2_enablement=v2_enablement,
            policy_loader_mode=policy_loader_mode,
            v2_execution_enabled=v2_execution_enabled,
            enable_v2_policy=enable_v2_policy,
            ml_enablement=ml_enablement,
            ml_model_path=ml_model_path,
        )

        # Step 1b: Initialize ML model (BEFORE v2 early return)
        # ML and v2 are independent features — v2 disabled should not block ML
        self._init_ml_model()

        # Step 2: Zero-cost when disabled - no I/O, no policy loading
        if self._controls.v2_enablement == "disabled":
            if config_path is not None:
                logger.warning(
                    "config_path='%s' supplied but v2_enablement='disabled'. "
                    "Policy will not be loaded.",
                    config_path,
                )
            # Leave _policy_bundle as None, config as empty dict
            return

        # Step 3: Load policies (only if v2 is enabled)
        from utils.policy_loader import (
            DEFAULT_V2_SPECS,
            load_policy_bundle,
            resolve_policy_dir,
        )

        policy_dir = resolve_policy_dir(explicit=config_path)
        self._policy_bundle = load_policy_bundle(
            base_dir=policy_dir,
            specs=DEFAULT_V2_SPECS,
            loader_mode=self._controls.policy_loader_mode,
        )

        # Bug #5 mitigation: Shallow copy of policies dict
        # Top-level mutation of self.config won't affect bundle.policies
        # Nested dicts remain shared (documented limitation)
        self.config = dict(self._policy_bundle.policies)

        # Phase 0B-1: Validate schema and parse into typed object
        # PolicyLoader is schema-agnostic; ThesisMatcher validates policy-specific schema
        from utils.negative_keyword_policy import (
            NegativeKeywordPolicy,
            validate_negative_keyword_policy,
        )

        policy = self._policy_bundle.policies.get("negative_keyword_policy", {})
        result = validate_negative_keyword_policy(policy)

        if not result.valid:
            raise RuntimeError(
                f"Policy schema invalid for 'negative_keyword_policy' in "
                f"{self._policy_bundle.base_dir}: {result.errors}"
            )

        if result.warnings:
            for warning in result.warnings:
                logger.warning(warning)

        # Parse into typed object for Phase 0B-2 (not used for scoring yet)
        self._negative_keyword_policy = NegativeKeywordPolicy.from_config(policy)

        # Phase 0B-3: Compute policy hash for shadow log tracking
        self._policy_hash = self._compute_policy_hash(policy)

        logger.debug(
            "ThesisMatcher initialized with v2 policy: enablement=%s, "
            "loader_mode=%s, execution=%s, policies=%s, policy_hash=%s",
            self._controls.v2_enablement,
            self._controls.policy_loader_mode,
            self._controls.v2_execution_enabled,
            list(self.config.keys()),
            self._policy_hash[:8] if self._policy_hash else None,
        )

    def _compute_core(
        self,
        normalized: str,
        domain_name: Optional[str],
    ) -> _CoreScore:
        """Compute core scoring: positive thesis scores + intent/domain signals.

        This is shared between v1 and v2 paths so differences only come from negatives.

        Args:
            normalized: Normalized text to score
            domain_name: Optional domain for pattern matching

        Returns:
            _CoreScore with all positive scoring results
        """
        # Score each thesis (existing logic)
        scores: Dict[str, float] = {}
        all_matches: Dict[str, List[str]] = {}

        for thesis, keywords in self.keywords.items():
            score, matches = self._score_thesis(normalized, keywords)
            scores[thesis.value] = score
            all_matches[thesis.value] = matches

        # Find best thesis
        if scores:
            best_thesis_name = max(scores, key=scores.get)
            base_score = scores[best_thesis_name]
            best_thesis = ConsumerThesis(best_thesis_name)
            matched_kws = all_matches.get(best_thesis_name, [])
        else:
            best_thesis = ConsumerThesis.UNKNOWN
            base_score = 0.0
            matched_kws = []

        # Intent phrases and domain patterns
        intent_matches = self._find_intent_phrases(normalized)
        domain_match = self._check_domain_patterns(domain_name)

        return _CoreScore(
            normalized=normalized,
            scores=scores,
            all_matches=all_matches,
            best_thesis=best_thesis,
            base_score=base_score,
            matched_kws=matched_kws,
            intent_matches=intent_matches,
            domain_match=domain_match,
        )

    @staticmethod
    def _compute_consumer_signal(normalized: str) -> Tuple[float, int, List[Tuple[str, float]]]:
        """Compute consumer signal score with tiered caps and per-keyword dedupe.

        Phase 0 Cascade: Instrumentation only — does not affect routing.

        Returns:
            (consumer_signal_score, consumer_anchor_count, matched_keywords_topk)
            where matched_keywords_topk is sorted by (weight desc, keyword asc).
        """
        tier_sums: Dict[str, float] = {"A": 0.0, "M": 0.0, "N": 0.0}
        anchor_keywords: set = set()
        all_matches: List[Tuple[str, float, str]] = []  # (keyword, weight, tier)

        # Also create hyphen-normalized view for variant matching
        hyphen_norm = normalized.replace("-", " ")

        for tier_name, keywords in CONSUMER_SIGNAL_KEYWORDS.items():
            for keyword, weight in keywords.items():
                # Per-keyword dedupe: match at most once
                pattern = r"\b" + re.escape(keyword) + r"\b"
                matched = bool(re.search(pattern, normalized))
                # Try hyphen-normalized view if not matched
                if not matched and "-" in keyword:
                    pattern_hn = r"\b" + re.escape(keyword.replace("-", " ")) + r"\b"
                    matched = bool(re.search(pattern_hn, hyphen_norm))
                if not matched:
                    # Also try the hyphen-free form against hyphen_norm
                    kw_no_hyphen = keyword.replace("-", " ")
                    if kw_no_hyphen != keyword:
                        pattern_nh = r"\b" + re.escape(kw_no_hyphen) + r"\b"
                        matched = bool(re.search(pattern_nh, hyphen_norm))

                if matched:
                    tier_sums[tier_name] += weight
                    all_matches.append((keyword, weight, tier_name))
                    if tier_name == "A":
                        anchor_keywords.add(keyword)

        # Apply tier caps
        score = min(
            1.0,
            min(tier_sums["A"], _TIER_CAPS["A"])
            + min(tier_sums["M"], _TIER_CAPS["M"])
            + min(tier_sums["N"], _TIER_CAPS["N"]),
        )

        # Top-k explainability: sort by (weight desc, keyword asc), limit 10
        all_matches.sort(key=lambda x: (-x[1], x[0]))
        topk = [(kw, w) for kw, w, _ in all_matches[:10]]

        return score, len(anchor_keywords), topk

    @staticmethod
    def _compute_b2b_soft_score(normalized: str) -> Tuple[float, List[Tuple[str, float]]]:
        """Compute B2B soft score — sum of matched SOFT_PENALTY_KEYWORDS weights.

        Phase 0 Cascade: Instrumentation only — does not affect routing.

        Returns:
            (b2b_soft_score, matched_keywords_topk)
        """
        matches: List[Tuple[str, float]] = []
        for keyword, weight in SOFT_PENALTY_KEYWORDS.items():
            pattern = r"\b" + re.escape(keyword) + r"\b"
            if re.search(pattern, normalized):
                matches.append((keyword, weight))

        total = sum((w for _, w in matches), 0.0)
        # Sort by (weight desc, keyword asc) for explainability
        matches.sort(key=lambda x: (-x[1], x[0]))
        return total, matches[:10]

    @staticmethod
    def _classify_negative_tiers(
        negative_matches: List[str],
    ) -> Tuple[List[str], List[str], List[Tuple[str, float]]]:
        """Classify matched negative keywords into hard_reject/hard_hold/soft tiers.

        Returns:
            (hard_rejects, hard_holds, soft_negatives_with_weights)
        """
        hard_rejects: List[str] = []
        hard_holds: List[str] = []
        soft_negatives: List[Tuple[str, float]] = []

        for kw in negative_matches:
            if kw in HARD_REJECT_KEYWORDS:
                hard_rejects.append(kw)
            elif kw in HARD_HOLD_KEYWORDS:
                hard_holds.append(kw)
            elif kw in SOFT_PENALTY_KEYWORDS:
                soft_negatives.append((kw, SOFT_PENALTY_KEYWORDS[kw]))
            else:
                # Fallback: treat unknown negatives as soft
                soft_negatives.append((kw, NEGATIVE_KEYWORDS.get(kw, 0.2)))

        return hard_rejects, hard_holds, soft_negatives

    def score(
        self,
        text: str,
        company_name: Optional[str] = None,
        domain_name: Optional[str] = None,
    ) -> ThesisFit:
        """Score text against all Consumer thesis categories.

        Args:
            text: Company description or README content
            company_name: Optional company name for additional context
            domain_name: Optional domain for pattern matching (Phase B)

        Returns:
            ThesisFit with score, matched keywords, and domain analysis
        """
        if not text:
            empty_trace = ThesisFitTrace(
                final_score=0.0,
                routing_decision="REJECTED",
                explanation="Score: 0.00. Empty text provided. Routed to REJECTED.",
            )
            return ThesisFit(
                thesis=ConsumerThesis.UNKNOWN,
                score=0.0,
                matched_keywords=[],
                negative_keywords=[],
                all_scores={},
                confidence="LOW",
                intent_phrases_matched=[],
                domain_match=False,
                domain_blacklisted=False,
                trace=empty_trace,
            )

        normalized = self._normalize(text)
        if company_name:
            normalized += " " + self._normalize(company_name)

        # Phase B: Check domain blacklist first
        domain_blacklisted = self._check_domain_blacklist(domain_name)
        if domain_blacklisted:
            blacklist_trace = ThesisFitTrace(
                final_score=0.0,
                routing_decision="REJECTED",
                explanation=f"Score: 0.00. Domain '{domain_name}' matched blacklist "
                           f"(non-production). Routed to REJECTED.",
            )
            return ThesisFit(
                thesis=ConsumerThesis.UNKNOWN,
                score=0.0,
                matched_keywords=[],
                negative_keywords=[],
                all_scores={},
                confidence="LOW",
                intent_phrases_matched=[],
                domain_match=False,
                domain_blacklisted=True,
                trace=blacklist_trace,
            )

        # Compute core (shared between v1 and v2)
        core = self._compute_core(normalized, domain_name)

        # v1 path (always computed)
        w1 = self._negative_weights_v1()
        p1 = self._compute_penalty(core.normalized, w1)
        s1 = self._apply_adjustments(core.base_score, p1, core.intent_matches, core.domain_match)
        fit_v1 = self._build_fit(core, s1, p1, w1)

        # If v2 disabled OR execution disabled => return v1
        if (
            not self._controls
            or self._controls.v2_enablement == "disabled"
            or not self._controls.v2_execution_enabled
        ):
            return self._maybe_apply_ml(fit_v1, text, company_name, domain_name)

        # v2 path
        w2 = self._negative_weights_v2()

        # Safety: if v2 is live but weights are empty, warn and fall back to v1
        if self._controls.v2_enablement == "live" and not w2:
            logger.error(
                "v2 enabled (live) but negative_keyword_policy is empty; "
                "falling back to v1 to avoid silent penalty removal"
            )
            return self._maybe_apply_ml(fit_v1, text, company_name, domain_name)

        p2 = self._compute_penalty(core.normalized, w2)
        s2 = self._apply_adjustments(core.base_score, p2, core.intent_matches, core.domain_match)
        fit_v2 = self._build_fit(core, s2, p2, w2)

        # Shadow mode: attach diff and return v1
        if self._controls.v2_enablement == "shadow":
            self._attach_v2_shadow_diff(fit_v1, fit_v2, p1, p2)
            return self._maybe_apply_ml(fit_v1, text, company_name, domain_name)

        # Live mode: return v2
        return self._maybe_apply_ml(fit_v2, text, company_name, domain_name)

    def _normalize(self, text: str) -> str:
        # Phase 0 Cascade: NFKC normalization for fullwidth/compatibility chars
        normalized = unicodedata.normalize("NFKC", text)
        normalized = re.sub(r"[-/_]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.lower().strip()

    def _score_thesis(
        self,
        text: str,
        keywords: Dict[str, float],
    ) -> tuple[float, List[str]]:
        matches: List[str] = []
        total_weight = 0.0
        max_possible = sum(keywords.values())

        for keyword, weight in keywords.items():
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, text):
                matches.append(keyword)
                total_weight += weight

        if max_possible > 0:
            # Normalize score: matching ~15% of keyword weight gives 1.0
            # This allows single high-weight keywords (0.9) to score well
            score = min(total_weight / (max_possible * 0.15), 1.0)
        else:
            score = 0.0

        return score, matches

    def _find_negative_keywords(
        self,
        text: str,
        *,
        negative_vocab: Optional[Iterable[str]] = None,
    ) -> List[str]:
        """Find negative keywords in text.

        Args:
            text: Normalized text to search
            negative_vocab: Optional iterable of keywords to search for.
                If None, uses NEGATIVE_KEYWORDS (v1 behavior).
                Can be a dict (iterates keys), list, or any iterable.

        Returns:
            List of matched negative keywords

        Raises:
            TypeError: If negative_vocab is a single string instead of iterable
        """
        # Preserve v1 behavior exactly: iterate the dict (keys) in insertion order
        vocab = NEGATIVE_KEYWORDS if negative_vocab is None else negative_vocab

        # Guardrail: passing a single string would iterate characters
        if isinstance(vocab, str):
            raise TypeError(
                "negative_vocab must be an iterable of keywords, not a single string"
            )

        matches: List[str] = []
        for keyword in vocab:
            pattern = r"\b" + re.escape(keyword) + r"\b"
            if re.search(pattern, text):
                matches.append(keyword)
        return matches

    def _compute_penalty(
        self,
        normalized: str,
        weights: Dict[str, float],
    ) -> _PenaltyResult:
        """Compute negative keyword penalty using specified weights.

        Args:
            normalized: Normalized text to search
            weights: Dict mapping keywords to penalty weights

        Returns:
            _PenaltyResult with matches, raw penalty sum, and applied penalty
        """
        matches = self._find_negative_keywords(normalized, negative_vocab=weights)
        raw = sum(weights.get(kw, 0.2) for kw in matches)
        applied = raw * 0.5
        return _PenaltyResult(matches=matches, raw_penalty=raw, applied_penalty=applied)

    def _negative_weights_v1(self) -> Dict[str, float]:
        """Get v1 negative keyword weights (hardcoded NEGATIVE_KEYWORDS).

        Returns:
            NEGATIVE_KEYWORDS dict (not a copy - callers should not mutate)
        """
        return NEGATIVE_KEYWORDS

    def _negative_weights_v2(self) -> Dict[str, float]:
        """Get v2 negative keyword weights from YAML policy.

        Returns:
            Dict mapping keywords to weights, or empty dict if v2 not enabled
        """
        if not getattr(self, "_negative_keyword_policy", None):
            return {}
        return {
            kw: entry.weight
            for kw, entry in self._negative_keyword_policy.keywords.items()
        }

    @staticmethod
    def _compute_policy_hash(policy: Dict) -> str:
        """Compute a hash of the policy dict for tracking.

        Uses SHA-256 of canonical JSON (sorted keys, compact format)
        to ensure consistent hashing across runs.

        Args:
            policy: Policy dict to hash

        Returns:
            SHA-256 hash of canonical JSON (first 16 chars)
        """
        import hashlib
        import json

        # Canonical JSON: sorted keys, no whitespace
        canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"))
        full_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        # Return first 16 chars for brevity while maintaining uniqueness
        return full_hash[:16]

    def _apply_adjustments(
        self,
        base_score: float,
        penalty: _PenaltyResult,
        intent_matches: List[str],
        domain_match: bool,
    ) -> float:
        """Apply penalty and boosts to base score.

        Order preserved from original score():
        1. Subtract penalty (if matches)
        2. Add intent phrase boost (if matches)
        3. Add domain pattern boost (if match)

        Args:
            base_score: Score before adjustments
            penalty: Penalty result from _compute_penalty
            intent_matches: Intent phrases matched
            domain_match: Whether domain pattern matched

        Returns:
            Final score clamped to [0.0, 1.0]
        """
        score = base_score

        # Apply negative penalty
        if penalty.matches:
            score = max(0.0, score - penalty.applied_penalty)

        # Apply intent phrase boost
        if intent_matches:
            intent_boost = sum(INTENT_PHRASES.get(p, 0.1) for p in intent_matches)
            score = min(1.0, score + intent_boost)

        # Apply domain pattern boost
        if domain_match:
            score = min(1.0, score + 0.15)

        return score

    def _build_fit(
        self,
        core: _CoreScore,
        final_score: float,
        penalty: _PenaltyResult,
        negative_weights: Dict[str, float],
    ) -> ThesisFit:
        """Build ThesisFit result from core scoring and penalty.

        Args:
            core: Core scoring results
            final_score: Score after all adjustments
            penalty: Penalty calculation results
            negative_weights: Weights used for penalty (for trace)

        Returns:
            Complete ThesisFit object
        """
        # Determine confidence
        if final_score >= 0.7:
            confidence = "HIGH"
        elif final_score >= 0.4:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Phase 0 Cascade: Compute consumer signal and B2B soft scores
        consumer_score, anchor_count, consumer_topk = self._compute_consumer_signal(
            core.normalized,
        )
        b2b_soft, b2b_topk = self._compute_b2b_soft_score(core.normalized)

        # Phase 0 Cascade: Classify negatives into tiers
        hard_rejects, hard_holds, soft_negs = self._classify_negative_tiers(
            penalty.matches,
        )

        # Generate trace (now with tier classification)
        trace = self._generate_trace(
            best_score=final_score,
            matched_keywords=core.matched_kws,
            negative_matches=penalty.matches,
            intent_matches=core.intent_matches,
            domain_match=core.domain_match,
            negative_weights=negative_weights,
            hard_rejects=hard_rejects,
            hard_holds=hard_holds,
            soft_negatives_classified=soft_negs,
        )

        return ThesisFit(
            thesis=core.best_thesis if final_score > 0.1 else ConsumerThesis.UNKNOWN,
            score=final_score,
            matched_keywords=core.matched_kws,
            negative_keywords=penalty.matches,
            all_scores=core.scores,
            confidence=confidence,
            intent_phrases_matched=core.intent_matches,
            domain_match=core.domain_match,
            domain_blacklisted=False,
            trace=trace,
            consumer_signal_score=consumer_score,
            consumer_anchor_count=anchor_count,
            b2b_soft_score=b2b_soft,
        )

    def _attach_v2_shadow_diff(
        self,
        fit_v1: ThesisFit,
        fit_v2: ThesisFit,
        p1: _PenaltyResult,
        p2: _PenaltyResult,
    ) -> None:
        """Attach v2 shadow diff to fit_v1's trace for observability.

        Computes diff between v1 and v2 results and attaches to trace.
        Logs high-signal divergences (routing change, is_fit change, large delta).

        Args:
            fit_v1: The v1 result (will be modified to add v2_shadow)
            fit_v2: The v2 result (for comparison)
            p1: v1 penalty result
            p2: v2 penalty result
        """
        if not fit_v1.trace:
            return

        def route(score: float) -> str:
            """Determine routing decision from score."""
            if score >= 0.3:
                return "QUALIFIED"
            if score >= 0.1:
                return "HELD"
            return "REJECTED"

        diff = {
            "v1": {
                "score": fit_v1.score,
                "penalty_raw": p1.raw_penalty,
                "negative_keywords": p1.matches,
                "routing": route(fit_v1.score),
                "thesis": fit_v1.thesis.value,
            },
            "v2": {
                "score": fit_v2.score,
                "penalty_raw": p2.raw_penalty,
                "negative_keywords": p2.matches,
                "routing": route(fit_v2.score),
                "thesis": fit_v2.thesis.value,
            },
            "delta_score": fit_v2.score - fit_v1.score,
            "would_change_is_fit": (fit_v2.is_fit != fit_v1.is_fit),
            "would_change_routing": (route(fit_v2.score) != route(fit_v1.score)),
            "would_change_thesis": (fit_v2.thesis != fit_v1.thesis),
            # Phase 0B-3: Include policy hash for tracking
            "policy_hash": self._policy_hash,
        }

        fit_v1.trace.v2_shadow = diff

        # Log high-signal divergences only
        if (
            diff["would_change_routing"]
            or diff["would_change_is_fit"]
            or abs(diff["delta_score"]) >= 0.05
        ):
            logger.info("v2 shadow diff: %s", diff)

    # =========================================================================
    # ML THESIS MODEL INTEGRATION
    # =========================================================================

    def _init_ml_model(self) -> None:
        """Initialize ML model if ml_enablement != disabled.

        Called BEFORE v2 early return to ensure ML works independently
        of v2 policy enablement (Review 1 bug fix).

        Error isolation: ML load failure sets _ml_model to None and
        logs error. Does NOT propagate exceptions (graceful degradation).
        """
        if not self._controls or self._controls.ml_enablement == "disabled":
            return

        ml_path = (
            self._controls.ml_model_path
            or self._DEFAULT_ML_MODEL_PATH
        )

        try:
            from utils.ml_thesis_model import MLThesisModel
            model = MLThesisModel()
            model.load(ml_path)
            self._ml_model = model
            logger.info(
                "ML thesis model loaded: path=%s, model_id=%s, ml_enablement=%s",
                ml_path,
                model.model_id,
                self._controls.ml_enablement,
            )
        except FileNotFoundError:
            logger.warning(
                "ML model file not found: %s. ML thesis rescue disabled.",
                ml_path,
            )
        except Exception as e:
            logger.error(
                "ML model load failed: %s. ML thesis rescue disabled.",
                e,
            )

    def _compute_ml_score(
        self,
        text: str,
        company_name: Optional[str] = None,
        domain_name: Optional[str] = None,
    ) -> Optional[float]:
        """Compute ML probability of thesis fit.

        Uses the shared build_ml_text() to prevent training/serving skew.
        Includes latency budget monitoring (Review 3).

        Args:
            text: Raw description text
            company_name: Optional company name
            domain_name: Optional domain name

        Returns:
            Probability of positive class (0.0-1.0), or None on failure
        """
        if self._ml_model is None:
            return None

        try:
            from utils.ml_text_builder import build_ml_text
            import time

            ml_text = build_ml_text(text, company_name, domain_name)
            if not ml_text:
                return None

            start = time.monotonic()
            prob = self._ml_model.predict_proba(ml_text)
            latency_ms = (time.monotonic() - start) * 1000

            # Latency budget monitoring (Review 3)
            if latency_ms > self._ML_LATENCY_BUDGET_MS:
                logger.warning(
                    "ML prediction slow: %.1fms (budget: %.1fms)",
                    latency_ms,
                    self._ML_LATENCY_BUDGET_MS,
                )

            # Reset failure count on success
            self._ml_failure_count = 0
            return prob

        except Exception as e:
            self._ml_failure_count += 1
            logger.warning(
                "ML prediction failed (%d/%d): %s",
                self._ml_failure_count,
                self._ML_FAILURE_THRESHOLD,
                e,
            )

            # Circuit breaker: disable ML after N consecutive failures
            if self._ml_failure_count >= self._ML_FAILURE_THRESHOLD:
                logger.error(
                    "ML circuit breaker triggered: %d consecutive failures. "
                    "Disabling ML for remainder of this instance.",
                    self._ml_failure_count,
                )
                self._ml_model = None

            return None

    def _maybe_apply_ml(
        self,
        fit: ThesisFit,
        text: str,
        company_name: Optional[str] = None,
        domain_name: Optional[str] = None,
    ) -> ThesisFit:
        """Apply ML rescoring to a completed ThesisFit result.

        This is the "append-after" pattern (approved by all three reviews):
        - Called after keyword v1/v2 scoring is complete
        - Does NOT modify the v1/v2 flow
        - ML is a post-processing rescue layer

        Gating rules (widened per Review 1 and 3 consensus):
        - Do NOT rescue domain_blacklisted signals (hard rejection)
        - Do NOT rescue empty text (no data to classify)
        - Do NOT rescue if keyword score already indicates fit (>= is_fit threshold)
        - DO rescue any score below is_fit threshold (no arbitrary lower bound)

        Score semantics (per Review 1 consensus):
        - final_score = max(keyword_score, ml_prob) when rescuing
        - No arbitrary *0.8 damping
        - Both scores preserved in ml_shadow for audit

        Args:
            fit: Completed ThesisFit from keyword scoring
            text: Raw description text
            company_name: Optional company name
            domain_name: Optional domain name

        Returns:
            Original fit (shadow/disabled) or rescued fit (live mode)
        """
        if not self._controls or self._controls.ml_enablement == "disabled":
            return fit
        if self._ml_model is None:
            return fit

        # Don't compute ML for hard rejections (domain blacklist, empty text)
        if fit.domain_blacklisted:
            return fit

        ml_score = self._compute_ml_score(text, company_name, domain_name)
        if ml_score is None:
            return fit

        if self._controls.ml_enablement == "shadow":
            self._attach_ml_shadow_diff(fit, ml_score)
            return fit

        if self._controls.ml_enablement == "live":
            # Rescue: only if keyword score is below fit threshold AND ML is confident
            if fit.score < 0.4 and ml_score > 0.5:
                rescued_score = max(fit.score, ml_score)
                return self._rebuild_fit_with_ml_rescue(
                    fit, rescued_score, ml_score, "rescued"
                )
            else:
                # Still attach shadow data in live mode for non-rescued signals
                self._attach_ml_shadow_diff(fit, ml_score)
                return fit

        return fit

    def _attach_ml_shadow_diff(
        self,
        fit: ThesisFit,
        ml_score: float,
    ) -> None:
        """Attach ML shadow diff to fit's trace for observability.

        Schema mirrors v2_shadow pattern with model_id for versioning
        (Review 1 + 3 consensus: model_id like policy_hash).

        Records:
        - keyword_score and ml_score for comparison
        - would_rescue: whether live mode would change the score
        - rescued_score: what the score would be if rescued
        - model_id: which model version produced this prediction
        - gating_reason: why rescue was/wasn't triggered
        """
        if not fit.trace:
            return

        would_rescue = fit.score < 0.4 and ml_score > 0.5
        rescued_score = max(fit.score, ml_score) if would_rescue else fit.score

        # Determine gating reason for observability (Review 3)
        if fit.domain_blacklisted:
            gating_reason = "domain_blacklisted"
        elif fit.score >= 0.4:
            gating_reason = "keyword_sufficient"
        elif ml_score <= 0.5:
            gating_reason = "ml_not_confident"
        elif would_rescue:
            gating_reason = "rescued"
        else:
            gating_reason = "not_rescued"

        fit.trace.ml_shadow = {
            "keyword_score": round(fit.score, 4),
            "ml_score": round(ml_score, 4),
            "delta": round(ml_score - fit.score, 4),
            "would_rescue": would_rescue,
            "rescued_score": round(rescued_score, 4),
            "gating_reason": gating_reason,
            "model_id": self._ml_model.model_id if self._ml_model else None,
            "model_version": self._ml_model.__version__ if self._ml_model else None,
        }

        # Log significant diffs (delta >= 0.1 is noteworthy)
        if abs(ml_score - fit.score) >= 0.1:
            logger.info(
                "ML shadow diff: keyword=%.3f, ml=%.3f, delta=%.3f, "
                "would_rescue=%s, gating=%s",
                fit.score, ml_score, ml_score - fit.score,
                would_rescue, gating_reason,
            )

    def _rebuild_fit_with_ml_rescue(
        self,
        original: ThesisFit,
        rescued_score: float,
        ml_score: float,
        gating_reason: str,
    ) -> ThesisFit:
        """Create a new ThesisFit with ML-rescued score.

        Preserves all original fields except score, confidence, and trace.
        The trace is updated with ml_shadow data showing the rescue.

        Important: this creates a NEW ThesisFit rather than mutating the
        original, preserving immutability semantics.
        """
        # Recompute confidence from rescued score
        if rescued_score >= 0.7:
            confidence = "HIGH"
        elif rescued_score >= 0.4:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Preserve thesis assignment (ML doesn't change category)
        thesis = original.thesis if rescued_score > 0.1 else ConsumerThesis.UNKNOWN

        # Build new trace with rescue explanation
        trace = ThesisFitTrace(
            matched_hard_negatives=original.trace.matched_hard_negatives if original.trace else [],
            soft_negatives=original.trace.soft_negatives if original.trace else [],
            rescue_anchors_matched=original.trace.rescue_anchors_matched if original.trace else {},
            rescue_blocked_by=original.trace.rescue_blocked_by if original.trace else None,
            aggregator_exception_triggered=(
                original.trace.aggregator_exception_triggered if original.trace else False
            ),
            applied_ai_path=original.trace.applied_ai_path if original.trace else None,
            final_score=rescued_score,
            routing_decision=(
                "QUALIFIED" if rescued_score >= 0.3
                else "HELD" if rescued_score >= 0.1
                else "REJECTED"
            ),
            explanation=(
                f"Score: {rescued_score:.2f} (ML rescued from {original.score:.2f}). "
                f"ML prob: {ml_score:.3f}. "
                f"Routed to {'QUALIFIED' if rescued_score >= 0.3 else 'HELD' if rescued_score >= 0.1 else 'REJECTED'}."
            ),
            v2_shadow=original.trace.v2_shadow if original.trace else None,
        )

        # Attach ML shadow data to the new trace
        trace.ml_shadow = {
            "keyword_score": round(original.score, 4),
            "ml_score": round(ml_score, 4),
            "delta": round(ml_score - original.score, 4),
            "would_rescue": True,
            "rescued_score": round(rescued_score, 4),
            "gating_reason": gating_reason,
            "model_id": self._ml_model.model_id if self._ml_model else None,
            "model_version": self._ml_model.__version__ if self._ml_model else None,
        }

        return ThesisFit(
            thesis=thesis,
            score=rescued_score,
            matched_keywords=original.matched_keywords,
            negative_keywords=original.negative_keywords,
            all_scores=original.all_scores,
            confidence=confidence,
            intent_phrases_matched=original.intent_phrases_matched,
            domain_match=original.domain_match,
            domain_blacklisted=original.domain_blacklisted,
            trace=trace,
        )

    def _find_intent_phrases(self, text: str) -> List[str]:
        """Find intent phrases that indicate commercial/consumer intent (Phase B)."""
        matches = []
        for phrase in INTENT_PHRASES:
            pattern = r'\b' + re.escape(phrase) + r'\b'
            if re.search(pattern, text):
                matches.append(phrase)
        return matches

    def _check_domain_patterns(self, domain_name: Optional[str]) -> bool:
        """Check if domain matches consumer-oriented patterns (Phase B).

        Patterns like get*, try*, join* indicate consumer-focused apps.
        """
        if not domain_name:
            return False

        # Extract just the domain name without TLD
        domain_lower = domain_name.lower()
        # Remove protocol if present
        if "://" in domain_lower:
            domain_lower = domain_lower.split("://")[1]
        # Remove port if present
        if ":" in domain_lower:
            domain_lower = domain_lower.split(":")[0]
        # Get the first part (subdomain or main domain)
        parts = domain_lower.split(".")
        if not parts:
            return False

        # Check against patterns (typically check the main domain, not TLD)
        # For "getfitness.com", check "getfitness"
        main_domain = parts[0] if len(parts) > 1 else parts[0]

        for pattern in CONSUMER_DOMAIN_PATTERNS:
            if re.match(pattern, main_domain):
                return True
        return False

    def _check_domain_blacklist(self, domain_name: Optional[str]) -> bool:
        """Check if domain is blacklisted (non-production) (Phase B).

        Blacklist fragments: localhost, example, sample, test, staging, dev., internal
        """
        if not domain_name:
            return False

        domain_lower = domain_name.lower()
        for fragment in DOMAIN_BLACKLIST_FRAGMENTS:
            if fragment in domain_lower:
                return True
        return False

    def _generate_trace(
        self,
        best_score: float,
        matched_keywords: List[str],
        negative_matches: List[str],
        intent_matches: List[str],
        domain_match: bool,
        *,
        negative_weights: Optional[Dict[str, float]] = None,
        hard_rejects: Optional[List[str]] = None,
        hard_holds: Optional[List[str]] = None,
        soft_negatives_classified: Optional[List[Tuple[str, float]]] = None,
    ) -> ThesisFitTrace:
        """Generate explainability trace for thesis classification.

        Args:
            best_score: Final thesis fit score
            matched_keywords: Positive keywords matched
            negative_matches: Negative keywords matched
            intent_matches: Intent phrases matched
            domain_match: Whether domain pattern matched
            negative_weights: Optional dict of negative keyword weights for trace.
            hard_rejects: Phase 0 Cascade: keywords classified as hard_reject
            hard_holds: Phase 0 Cascade: keywords classified as hard_hold
            soft_negatives_classified: Phase 0 Cascade: (keyword, weight) from soft tier

        Returns:
            ThesisFitTrace with explanation
        """
        # Use provided weights or fall back to NEGATIVE_KEYWORDS
        weights = negative_weights if negative_weights is not None else NEGATIVE_KEYWORDS

        # Phase 0 Cascade: use classified soft negatives if provided,
        # otherwise fall back to old behavior
        if soft_negatives_classified is not None:
            soft_negatives = soft_negatives_classified
        else:
            soft_negatives = [
                (kw, weights.get(kw, 0.2))
                for kw in negative_matches
            ]

        # Determine routing decision based on score
        if best_score >= 0.3:
            routing_decision = "QUALIFIED"
        elif best_score >= 0.1:
            routing_decision = "HELD"
        else:
            routing_decision = "REJECTED"

        # Build human-readable explanation
        explanation_parts = [f"Score: {best_score:.2f}."]

        if matched_keywords:
            explanation_parts.append(
                f"Matched {len(matched_keywords)} positive keyword(s): "
                f"{', '.join(matched_keywords[:3])}."
            )

        if soft_negatives:
            total_penalty = sum(penalty for _, penalty in soft_negatives)
            explanation_parts.append(
                f"Soft negatives ({', '.join(kw for kw, _ in soft_negatives[:3])}) "
                f"applied penalty: -{total_penalty * 0.5:.2f}."
            )

        if intent_matches:
            explanation_parts.append(
                f"Intent phrases boosted score: {', '.join(intent_matches)}."
            )

        if domain_match:
            explanation_parts.append("Consumer domain pattern matched (+0.15).")

        explanation_parts.append(f"Routed to {routing_decision}.")

        return ThesisFitTrace(
            matched_hard_negatives=list(hard_rejects or []) + list(hard_holds or []),
            matched_hard_rejects=list(hard_rejects or []),
            matched_hard_holds=list(hard_holds or []),
            soft_negatives=soft_negatives,
            rescue_anchors_matched={},
            rescue_blocked_by=None,
            aggregator_exception_triggered=False,
            applied_ai_path=None,
            final_score=best_score,
            routing_decision=routing_decision,
            explanation=" ".join(explanation_parts),
        )

    def score_signals(self, signals: List[Dict]) -> ThesisFit:
        """Score a list of signals to determine thesis fit."""
        texts = []
        company_name = None

        for signal in signals:
            raw = signal.get("raw_data", {}) if isinstance(signal, dict) else {}
            for field in ["description", "short_description", "about", "bio"]:
                if field in raw and raw[field]:
                    texts.append(str(raw[field]))
            if "company_name" in raw and not company_name:
                company_name = raw["company_name"]

        combined_text = " ".join(texts)
        return self.score(combined_text, company_name=company_name)


# Module-level singleton to avoid reinstantiating ThesisMatcher (and reloading
# the ML model) on every call to score_thesis_fit() (Review 1 performance fix).
_default_matcher: Optional[ThesisMatcher] = None


def score_thesis_fit(text: str, company_name: Optional[str] = None) -> ThesisFit:
    """Convenience function to score thesis fit.

    Uses a module-level singleton to avoid reinstantiating ThesisMatcher
    (and reloading the ML model) on every call.
    """
    global _default_matcher
    if _default_matcher is None:
        _default_matcher = ThesisMatcher()
    return _default_matcher.score(text, company_name)


def is_thesis_fit(text: str, min_score: float = 0.4) -> bool:
    """Quick check if text matches investment thesis."""
    fit = score_thesis_fit(text)
    return fit.score >= min_score


# =============================================================================
# CLI
# =============================================================================

def main():
    """CLI for testing thesis matcher."""
    test_cases = [
        "We make healthy meal kits delivered to your door",
        "A fitness app for tracking your workouts and wellness",
        "Travel booking platform for unique hotel experiences",
        "Consumer marketplace connecting buyers and sellers",
        "Enterprise B2B SaaS platform for developers",
        "Premium skincare brand with d2c subscription model",
        "Blockchain crypto trading platform",
        "Mental health app for meditation and therapy",
        "Restaurant tech platform for hospitality businesses",
        "P2P marketplace for secondhand fashion resale",
    ]

    matcher = ThesisMatcher()

    print("=" * 70)
    print("CONSUMER THESIS MATCHER TEST")
    print("=" * 70)

    for text in test_cases:
        fit = matcher.score(text)
        marker = "[FIT]" if fit.is_fit else "[---]"
        print(f"\n{marker} {text[:55]}...")
        print(f"   Thesis: {fit.thesis.value}")
        print(f"   Score: {fit.score:.2f} ({fit.confidence})")
        print(f"   Matched: {', '.join(fit.matched_keywords[:5])}")
        if fit.negative_keywords:
            print(f"   Negative: {', '.join(fit.negative_keywords)}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()

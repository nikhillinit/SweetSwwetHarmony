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

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

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
        "sleep tracking": 0.8,  # Added: "sleep tracking app" matches this
        "nutrition app": 0.7,
        "personalized workout": 0.8,  # Added for fitness app specificity
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
        "guided relaxation": 0.5,  # Added for meditation/sleep apps
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
}

# Negative signals - exclusions from thesis
NEGATIVE_KEYWORDS: Dict[str, float] = {
    # B2B/Enterprise
    "enterprise": 0.5,
    "b2b": 0.5,
    "saas platform": 0.4,
    "developer tool": 0.5,
    "api platform": 0.4,
    "api management": 0.5,
    "devops": 0.5,
    "infrastructure": 0.4,
    "logistics platform": 0.5,
    "logistics": 0.3,
    "data platform": 0.4,
    "sdk": 0.4,
    # Crypto/Web3
    "blockchain": 0.5,
    "crypto": 0.5,
    "web3": 0.5,
    "nft": 0.5,
    "defi": 0.5,
    "token": 0.3,
    # Other exclusions
    "consulting": 0.4,
    "agency": 0.4,
    "services firm": 0.4,
    "series b": 0.3,
    "series c": 0.4,
    "series d": 0.5,
    "aggregator": 0.2,
    # Phase B: Template/Educational content (from founder_intel_canonical)
    "boilerplate": 0.6,
    "starter": 0.5,
    "template": 0.5,
    "tutorial": 0.5,
    "workshop": 0.4,
    "course": 0.4,
    "homework": 0.4,
    "assignment": 0.4,
    "example": 0.3,
    "demo repo": 0.5,
    # Phase B: Developer tools (from founder_intel_canonical)
    "cli": 0.4,
    "library": 0.4,
    "framework": 0.4,
    "plugin": 0.4,
    "linter": 0.5,
}

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

    def to_dict(self) -> Dict:
        """Convert trace to dictionary for serialization."""
        result = {
            "matched_hard_negatives": self.matched_hard_negatives,
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

    def __init__(
        self,
        custom_keywords: Optional[Dict[ConsumerThesis, Dict[str, float]]] = None,
        *,
        enable_v2_policy: Optional[bool] = None,
        v2_enablement: Optional[str] = None,
        policy_loader_mode: Optional[str] = None,
        v2_execution_enabled: Optional[bool] = None,
        config_path: Optional[str] = None,
    ):
        """Initialize ThesisMatcher with optional v2 policy configuration.

        Args:
            custom_keywords: Custom keyword weights to merge with defaults
            enable_v2_policy: Legacy kwarg (True → shadow, False → disabled)
            v2_enablement: "disabled", "shadow", or "live"
            policy_loader_mode: "permissive" or "strict"
            v2_execution_enabled: Whether v2 scoring is active
            config_path: Explicit path to policy directory

        Phase 0A: v2 infrastructure is wired but scoring behavior is unchanged.
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

        # Step 1: Resolve RuntimeControls (validate-before-I/O)
        # Import here to avoid circular imports and allow zero-cost when disabled
        from utils.runtime_controls import RuntimeControls

        self._controls = RuntimeControls.from_env(
            v2_enablement=v2_enablement,
            policy_loader_mode=policy_loader_mode,
            v2_execution_enabled=v2_execution_enabled,
            enable_v2_policy=enable_v2_policy,
        )

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

        logger.debug(
            "ThesisMatcher initialized with v2 policy: enablement=%s, "
            "loader_mode=%s, execution=%s, policies=%s",
            self._controls.v2_enablement,
            self._controls.policy_loader_mode,
            self._controls.v2_execution_enabled,
            list(self.config.keys()),
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

        # Score each thesis
        scores: Dict[str, float] = {}
        all_matches: Dict[str, List[str]] = {}

        for thesis, keywords in self.keywords.items():
            score, matches = self._score_thesis(normalized, keywords)
            scores[thesis.value] = score
            all_matches[thesis.value] = matches

        # Find negative keywords
        negative_matches = self._find_negative_keywords(normalized)

        # Phase B: Find intent phrases
        intent_matches = self._find_intent_phrases(normalized)

        # Phase B: Check domain patterns
        domain_match = self._check_domain_patterns(domain_name)

        # Find best thesis
        if scores:
            best_thesis_name = max(scores, key=scores.get)
            best_score = scores[best_thesis_name]
            best_thesis = ConsumerThesis(best_thesis_name)
            matched_kws = all_matches.get(best_thesis_name, [])
        else:
            best_thesis = ConsumerThesis.UNKNOWN
            best_score = 0.0
            matched_kws = []

        # Apply negative penalty
        if negative_matches:
            penalty = sum(NEGATIVE_KEYWORDS.get(kw, 0.2) for kw in negative_matches)
            best_score = max(0.0, best_score - penalty * 0.5)

        # Phase B: Apply intent phrase boost
        if intent_matches:
            intent_boost = sum(INTENT_PHRASES.get(phrase, 0.1) for phrase in intent_matches)
            best_score = min(1.0, best_score + intent_boost)

        # Phase B: Apply domain pattern boost
        if domain_match:
            best_score = min(1.0, best_score + 0.15)

        # Determine confidence
        if best_score >= 0.7:
            confidence = "HIGH"
        elif best_score >= 0.4:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Generate explainability trace
        trace = self._generate_trace(
            best_score=best_score,
            matched_keywords=matched_kws,
            negative_matches=negative_matches,
            intent_matches=intent_matches,
            domain_match=domain_match,
        )

        return ThesisFit(
            thesis=best_thesis if best_score > 0.1 else ConsumerThesis.UNKNOWN,
            score=best_score,
            matched_keywords=matched_kws,
            negative_keywords=negative_matches,
            all_scores=scores,
            confidence=confidence,
            intent_phrases_matched=intent_matches,
            domain_match=domain_match,
            domain_blacklisted=False,
            trace=trace,
        )

    def _normalize(self, text: str) -> str:
        return text.lower().strip()

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

    def _find_negative_keywords(self, text: str) -> List[str]:
        matches = []
        for keyword in NEGATIVE_KEYWORDS:
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, text):
                matches.append(keyword)
        return matches

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
    ) -> ThesisFitTrace:
        """Generate explainability trace for thesis classification.

        This is a stub implementation that captures current state.
        Full scoring logic (rescue anchors, anti-rescue, aggregator exceptions)
        will be implemented in Task #13.

        Args:
            best_score: Final thesis fit score
            matched_keywords: Positive keywords matched
            negative_matches: Negative keywords matched
            intent_matches: Intent phrases matched
            domain_match: Whether domain pattern matched

        Returns:
            ThesisFitTrace with explanation
        """
        # Convert negative matches to soft negatives with penalties
        soft_negatives = [
            (kw, NEGATIVE_KEYWORDS.get(kw, 0.2))
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

        # Note: Full rescue logic will be added in Task #13
        return ThesisFitTrace(
            matched_hard_negatives=[],  # Stub: no hard negatives yet
            soft_negatives=soft_negatives,
            rescue_anchors_matched={},  # Stub: rescue logic in Task #13
            rescue_blocked_by=None,  # Stub: anti-rescue in Task #13
            aggregator_exception_triggered=False,  # Stub: aggregator in Task #13
            applied_ai_path=None,  # Stub: AI path detection in Task #13
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


def score_thesis_fit(text: str, company_name: Optional[str] = None) -> ThesisFit:
    """Convenience function to score thesis fit."""
    matcher = ThesisMatcher()
    return matcher.score(text, company_name)


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

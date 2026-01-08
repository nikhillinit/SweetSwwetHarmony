"""
Verification Gate for Discovery Engine - V2

CORRECTED to route to actual Notion statuses:
- AUTO_PUSH → "Source" (not "Lead")
- NEEDS_REVIEW → "Tracking" (not "Needs Research")

Implements Glass.AI principles:
- Cross-source verification before accepting claims
- Full provenance tracking
- Uncertain claims flagged for human review

Implements MiroThinker principles:
- Verification loops (targeted, not exhaustive)
- Backtracking when verification fails

Additional fixes:
- Anti-inflation: max one contribution per signal_type
- Hard kill for company_dissolved (not just "conflicting")
- Convergence boost based on distinct signal types (not raw count)

Harmonic-level enhancements:
- Founder score integration (serial founders, FAANG experience)
- Signal velocity tracking (momentum detection)
- Enhanced confidence scoring
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
from collections import defaultdict
import hashlib
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS & CONSTANTS
# =============================================================================

class VerificationStatus(str, Enum):
    """How well-verified is this entity?"""
    UNVERIFIED = "unverified"
    SINGLE_SOURCE = "single_source"
    MULTI_SOURCE = "multi_source"
    CONFLICTING = "conflicting"
    VERIFICATION_FAILED = "failed"


class PushDecision(str, Enum):
    """What to do with this prospect"""
    AUTO_PUSH = "auto_push"       # High confidence → Notion "Source"
    NEEDS_REVIEW = "needs_review" # Medium confidence → Notion "Tracking"
    HOLD = "hold"                 # Low confidence → don't push yet
    REJECT = "reject"             # Negative signals or hard kill


# Signal weights (from Evertrace methodology)
SIGNAL_WEIGHTS = {
    "incorporation": 0.25,
    "github_spike": 0.20,
    "domain_registration": 0.15,
    "patent_filing": 0.15,
    "product_hunt_launch": 0.10,
    "social_announcement": 0.10,
    "cofounder_search": 0.05,
    "research_paper": 0.05,
    "funding_event": 0.20,
    "hiring_signal": 0.30,  # High weight - active hiring is strong signal
    "github_activity": 0.18,  # Medium-high weight - founder activity
}

# Decay half-lives (days)
HALF_LIVES = {
    "incorporation": 365,
    "github_spike": 14,
    "domain_registration": 90,
    "patent_filing": 180,
    "product_hunt_launch": 30,
    "social_announcement": 30,
    "cofounder_search": 60,
    "research_paper": 180,
    "funding_event": 180,
    "hiring_signal": 45,  # Slower decay - hiring signals stay relevant longer
    "github_activity": 30,  # Medium decay - activity signals fade moderately
}

# Negative signals and their multipliers
NEGATIVE_MULTIPLIERS = {
    "job_at_big_co_recent": 0.6,      # 0-6 months
    "job_at_big_co_medium": 0.3,      # 6-12 months
    "job_at_big_co_old": 0.1,         # 12+ months
    "domain_dead": 0.1,
    "github_inactive_90d": 0.3,
    "company_dissolved": 0.0,          # HARD KILL - score becomes 0
}

# Hard kill signals (reject immediately, don't even route to review)
HARD_KILL_SIGNALS = {"company_dissolved"}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Signal:
    """A detected signal with provenance"""
    id: str
    signal_type: str
    confidence: float
    
    # Provenance (Glass.AI)
    source_api: str
    source_url: Optional[str] = None
    source_response_hash: Optional[str] = None
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Verification
    verified_by_sources: List[str] = field(default_factory=list)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED

    raw_data: Dict[str, Any] = field(default_factory=dict)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def age_days(self) -> int:
        return (datetime.now(timezone.utc) - self.detected_at).days


@dataclass
class VerificationResult:
    """Result of verification gate"""
    decision: PushDecision
    verification_status: VerificationStatus
    confidence_score: float
    confidence_breakdown: Dict[str, Any]
    reason: str
    
    # For Notion routing - these are the ACTUAL status strings
    suggested_status: str  # "Source", "Tracking", or "" (don't push)
    
    # Audit trail
    signals_used: List[str]
    sources_checked: List[str]
    verification_details: List[Dict[str, Any]]


@dataclass
class ConfidenceBreakdown:
    """Auditable confidence calculation (Glass.AI: show your work)"""
    overall: float
    base_score: float
    multi_source_boost: float
    convergence_boost: float
    signals_contributing: int  # Distinct signal TYPES, not raw count
    sources_checked: int
    sources: List[str]
    signal_details: List[Dict[str, Any]]
    calculation_method: str = "glass_ai_v2"
    calculated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Harmonic-level enhancements
    founder_score: float = 0.0  # Aggregate founder score (0-1)
    founder_boost: float = 0.0  # Boost applied from founder score
    velocity_boost: float = 0.0  # Boost from signal velocity/momentum
    momentum_score: float = 0.0  # Raw momentum score (0-1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall": round(self.overall, 3),
            "base_score": round(self.base_score, 3),
            "multi_source_boost": self.multi_source_boost,
            "convergence_boost": round(self.convergence_boost, 2),
            "founder_score": round(self.founder_score, 3),
            "founder_boost": round(self.founder_boost, 3),
            "velocity_boost": round(self.velocity_boost, 3),
            "momentum_score": round(self.momentum_score, 3),
            "signals_contributing": self.signals_contributing,
            "sources_checked": self.sources_checked,
            "sources": self.sources,
            "signal_details": self.signal_details,
            "calculation_method": self.calculation_method,
            "calculated_at": self.calculated_at.isoformat()
        }


# =============================================================================
# VERIFICATION GATE
# =============================================================================

class VerificationGate:
    """
    Decides whether a prospect should be pushed to Notion and with what status.

    CORRECTED routing:
    - AUTO_PUSH → "Source" (your Notion's entry-point status)
    - NEEDS_REVIEW → "Tracking" (your Notion's watch-list status)

    Harmonic-level enhancements:
    - Founder score integration for team quality signals
    - Velocity tracking for momentum detection
    """

    # Thresholds
    HIGH_CONFIDENCE_THRESHOLD = 0.7
    MEDIUM_CONFIDENCE_THRESHOLD = 0.4
    MIN_SOURCES_FOR_AUTO_PUSH = 2

    # Founder score thresholds
    FOUNDER_HIGH_SCORE = 0.7  # Serial founder, FAANG, etc.
    FOUNDER_MEDIUM_SCORE = 0.4  # Some experience

    # Boost weights
    FOUNDER_BOOST_WEIGHT = 0.15  # Max boost from founder score
    VELOCITY_BOOST_WEIGHT = 0.20  # Max boost from velocity

    def __init__(
        self,
        strict_mode: bool = False,
        auto_push_status: str = "Source",
        needs_review_status: str = "Tracking",
        use_founder_scoring: bool = True,
        use_velocity_scoring: bool = True,
    ):
        """
        Args:
            strict_mode: If True, require 2+ sources for any push.
            auto_push_status: Notion status for high-confidence deals.
            needs_review_status: Notion status for medium-confidence deals.
            use_founder_scoring: Enable founder score integration.
            use_velocity_scoring: Enable velocity/momentum scoring.
        """
        self.strict_mode = strict_mode
        self.auto_push_status = auto_push_status
        self.needs_review_status = needs_review_status
        self.use_founder_scoring = use_founder_scoring
        self.use_velocity_scoring = use_velocity_scoring
    
    def evaluate(
        self,
        signals: List[Signal],
        founder_score: float = 0.0,
        velocity_boost: float = 0.0,
        momentum_score: float = 0.0,
    ) -> VerificationResult:
        """
        Main entry point: evaluate signals and decide on push action.

        Args:
            signals: List of Signal objects to evaluate.
            founder_score: Aggregate founder score (0-1) from FounderStore.
            velocity_boost: Confidence boost from signal velocity (0-0.35).
            momentum_score: Raw momentum score (0-1) for tracking.
        """
        if not signals:
            return VerificationResult(
                decision=PushDecision.REJECT,
                verification_status=VerificationStatus.UNVERIFIED,
                confidence_score=0.0,
                confidence_breakdown={},
                reason="No signals provided",
                suggested_status="",
                signals_used=[],
                sources_checked=[],
                verification_details=[]
            )
        
        # Check for hard kill signals FIRST
        hard_kill = [s for s in signals if s.signal_type in HARD_KILL_SIGNALS]
        if hard_kill:
            return VerificationResult(
                decision=PushDecision.REJECT,
                verification_status=VerificationStatus.UNVERIFIED,
                confidence_score=0.0,
                confidence_breakdown={"hard_kill": True, "kill_signal": hard_kill[0].signal_type},
                reason=f"Hard kill signal: {hard_kill[0].signal_type}",
                suggested_status="",
                signals_used=[s.id for s in hard_kill],
                sources_checked=[],
                verification_details=[{"signal": s.id, "type": s.signal_type, "effect": "hard_kill"} for s in hard_kill]
            )
        
        # Calculate confidence with full breakdown
        breakdown = self._calculate_confidence(
            signals,
            founder_score=founder_score,
            velocity_boost=velocity_boost,
            momentum_score=momentum_score,
        )

        # Determine verification status
        verification_status = self._assess_verification_status(signals)

        # Make push decision
        decision, reason, suggested_status = self._make_decision(
            breakdown, verification_status, signals
        )
        
        # Build verification details for audit
        verification_details = [
            {
                "signal_id": s.id,
                "signal_type": s.signal_type,
                "source": s.source_api,
                "verified_by": s.verified_by_sources,
                "status": s.verification_status.value
            }
            for s in signals
        ]
        
        return VerificationResult(
            decision=decision,
            verification_status=verification_status,
            confidence_score=breakdown.overall,
            confidence_breakdown=breakdown.to_dict(),
            reason=reason,
            suggested_status=suggested_status,
            signals_used=[s.id for s in signals],
            sources_checked=breakdown.sources,
            verification_details=verification_details
        )
    
    def _calculate_confidence(
        self,
        signals: List[Signal],
        founder_score: float = 0.0,
        velocity_boost: float = 0.0,
        momentum_score: float = 0.0,
    ) -> ConfidenceBreakdown:
        """
        Calculate confidence score with anti-inflation protection.

        Key fix: Only count the BEST signal per signal_type to prevent
        gaming the score with repeated signals of the same type.

        Harmonic enhancements:
        - Founder score boost (up to 0.15)
        - Velocity boost (up to 0.20)
        """
        # Group signals by source
        by_source = defaultdict(list)
        for s in signals:
            by_source[s.source_api].append(s)
        
        sources_checked = len(by_source)
        
        # Separate positive and negative signals
        positive_signals = [s for s in signals if s.signal_type not in NEGATIVE_MULTIPLIERS]
        negative_signals = [s for s in signals if s.signal_type in NEGATIVE_MULTIPLIERS]
        
        # ANTI-INFLATION: Keep only the strongest contribution per signal_type
        best_by_type: Dict[str, Dict[str, Any]] = {}
        
        for s in positive_signals:
            weight = SIGNAL_WEIGHTS.get(s.signal_type, 0.05)
            half_life = HALF_LIVES.get(s.signal_type, 90)
            decay_factor = 0.5 ** (s.age_days / half_life)
            contribution = weight * decay_factor * s.confidence
            
            detail = {
                "id": s.id,
                "type": s.signal_type,
                "source": s.source_api,
                "weight": weight,
                "decay_factor": round(decay_factor, 3),
                "confidence": float(s.confidence),
                "contribution": round(contribution, 4),
                "age_days": s.age_days
            }
            
            # Only keep if this is the best for this signal type
            prev = best_by_type.get(s.signal_type)
            if prev is None or contribution > prev["contribution"]:
                best_by_type[s.signal_type] = detail
        
        # Base score = sum of best contributions (one per type)
        signal_details = list(best_by_type.values())
        base_score = sum(d["contribution"] for d in signal_details)
        
        # Apply negative signal multipliers
        for s in negative_signals:
            multiplier = NEGATIVE_MULTIPLIERS.get(s.signal_type, 1.0)
            base_score *= multiplier
            
            signal_details.append({
                "id": s.id,
                "type": s.signal_type,
                "source": s.source_api,
                "multiplier": multiplier,
                "effect": "penalty"
            })
        
        # Multi-source boost (Glass.AI principle)
        if sources_checked >= 3:
            multi_source_boost = 1.3
        elif sources_checked == 2:
            multi_source_boost = 1.15
        else:
            multi_source_boost = 1.0
        
        # Convergence boost based on DISTINCT signal types (not raw count)
        distinct_types = len(best_by_type)
        if distinct_types >= 3:
            convergence_boost = 1.5
        elif distinct_types == 2:
            convergence_boost = 1.2
        else:
            convergence_boost = 1.0

        # Calculate intermediate score
        intermediate_score = base_score * multi_source_boost * convergence_boost

        # Founder score boost (Harmonic enhancement)
        founder_boost = 0.0
        if self.use_founder_scoring and founder_score > 0:
            # Scale founder score to boost: max 0.15 for perfect founder
            founder_boost = min(founder_score * self.FOUNDER_BOOST_WEIGHT, self.FOUNDER_BOOST_WEIGHT)

            # Add founder signal detail
            signal_details.append({
                "type": "founder_score",
                "source": "founder_store",
                "founder_score": round(founder_score, 3),
                "contribution": round(founder_boost, 4),
                "effect": "boost"
            })

        # Velocity boost (Harmonic enhancement)
        velocity_boost_applied = 0.0
        if self.use_velocity_scoring and velocity_boost > 0:
            # Velocity boost is already pre-calculated (0-0.35 range)
            # Scale it to our weight limit
            velocity_boost_applied = min(velocity_boost, self.VELOCITY_BOOST_WEIGHT)

            # Add velocity signal detail
            signal_details.append({
                "type": "velocity_momentum",
                "source": "signal_velocity",
                "momentum_score": round(momentum_score, 3),
                "contribution": round(velocity_boost_applied, 4),
                "effect": "boost"
            })

        # Final score with all boosts
        final_score = min(intermediate_score + founder_boost + velocity_boost_applied, 1.0)

        return ConfidenceBreakdown(
            overall=final_score,
            base_score=base_score,
            multi_source_boost=multi_source_boost,
            convergence_boost=convergence_boost,
            founder_score=founder_score,
            founder_boost=founder_boost,
            velocity_boost=velocity_boost_applied,
            momentum_score=momentum_score,
            signals_contributing=distinct_types,
            sources_checked=sources_checked,
            sources=list(by_source.keys()),
            signal_details=signal_details
        )
    
    def _assess_verification_status(self, signals: List[Signal]) -> VerificationStatus:
        """Determine overall verification status from signals."""
        sources = set(s.source_api for s in signals)
        
        # Check for conflicts (positive + strong negative)
        signal_types = [s.signal_type for s in signals]
        has_positive = any(t not in NEGATIVE_MULTIPLIERS for t in signal_types)
        has_strong_negative = any(
            t in ["company_dissolved", "domain_dead"] 
            for t in signal_types
        )
        
        if has_positive and has_strong_negative:
            return VerificationStatus.CONFLICTING
        
        if len(sources) >= 2:
            return VerificationStatus.MULTI_SOURCE
        elif len(sources) == 1:
            return VerificationStatus.SINGLE_SOURCE
        else:
            return VerificationStatus.UNVERIFIED
    
    def _make_decision(
        self,
        breakdown: ConfidenceBreakdown,
        verification_status: VerificationStatus,
        signals: List[Signal]
    ) -> tuple[PushDecision, str, str]:
        """
        Make push decision based on confidence and verification.
        
        Returns: (decision, reason, suggested_notion_status)
        """
        score = breakdown.overall
        sources = breakdown.sources_checked
        
        # Conflicting signals = needs human review
        if verification_status == VerificationStatus.CONFLICTING:
            return (
                PushDecision.NEEDS_REVIEW,
                "Conflicting signals detected - requires human review",
                self.needs_review_status
            )
        
        # High confidence + multi-source = auto-push
        if (score >= self.HIGH_CONFIDENCE_THRESHOLD and 
            verification_status == VerificationStatus.MULTI_SOURCE):
            return (
                PushDecision.AUTO_PUSH,
                f"High confidence ({score:.2f}) with {sources} sources",
                self.auto_push_status
            )
        
        # High confidence + single source (non-strict mode)
        if (score >= self.HIGH_CONFIDENCE_THRESHOLD and 
            verification_status == VerificationStatus.SINGLE_SOURCE and
            not self.strict_mode):
            return (
                PushDecision.AUTO_PUSH,
                f"High confidence ({score:.2f}) from single authoritative source",
                self.auto_push_status
            )
        
        # Medium confidence or single source in strict mode = needs review
        if score >= self.MEDIUM_CONFIDENCE_THRESHOLD:
            return (
                PushDecision.NEEDS_REVIEW,
                f"Medium confidence ({score:.2f}) - requires verification",
                self.needs_review_status
            )
        
        # Low confidence = hold (don't push yet)
        if score > 0:
            return (
                PushDecision.HOLD,
                f"Low confidence ({score:.2f}) - waiting for more signals",
                ""
            )
        
        # No positive signals
        return (
            PushDecision.REJECT,
            "Insufficient evidence",
            ""
        )


# =============================================================================
# VERIFICATION LOOPS (MiroThinker-inspired)
# =============================================================================

class VerificationLoop:
    """
    Targeted verification loops for specific signal types.
    
    Unlike MiroThinker's 600-call exhaustive approach, we do targeted
    verification against rate-limited APIs.
    """
    
    def __init__(self, github_client=None, companies_house_client=None, whois_client=None):
        self.github = github_client
        self.companies_house = companies_house_client
        self.whois = whois_client
    
    async def verify_founder_signal(self, signal: Signal, founder_name: str) -> Signal:
        """
        Attempt to verify a founder signal with a second source.
        """
        signal_type = signal.signal_type
        verified_sources = list(signal.verified_by_sources)
        
        if signal_type == "github_spike" and self.companies_house:
            try:
                is_director = await self.companies_house.is_person_director(founder_name)
                if is_director:
                    verified_sources.append("companies_house")
            except Exception as e:
                logger.warning(f"Companies House verification failed: {e}")
        
        elif signal_type == "incorporation" and self.github:
            try:
                github_handle = signal.raw_data.get("github_handle")
                if github_handle:
                    has_activity = await self.github.has_recent_activity(github_handle)
                    if has_activity:
                        verified_sources.append("github")
            except Exception as e:
                logger.warning(f"GitHub verification failed: {e}")
        
        elif signal_type == "domain_registration" and self.whois:
            try:
                domain = signal.raw_data.get("domain")
                if domain:
                    is_active = await self.whois.is_domain_active(domain)
                    if is_active:
                        verified_sources.append("dns")
            except Exception as e:
                logger.warning(f"WHOIS verification failed: {e}")
        
        # Update signal
        signal.verified_by_sources = verified_sources
        if len(set(verified_sources)) >= 2:
            signal.verification_status = VerificationStatus.MULTI_SOURCE
        elif len(verified_sources) == 1:
            signal.verification_status = VerificationStatus.SINGLE_SOURCE
        
        return signal


# =============================================================================
# PROVENANCE UTILITIES (Glass.AI)
# =============================================================================

def hash_response(response_data: Any) -> str:
    """Create SHA256 hash of API response for audit trail"""
    import json
    serialized = json.dumps(response_data, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def create_provenance_record(
    company_id: str,
    event_type: str,
    event_data: Dict[str, Any],
    source_documents: List[str]
) -> Dict[str, Any]:
    """Create a provenance audit record"""
    return {
        "company_id": company_id,
        "event_type": event_type,
        "event_data": event_data,
        "source_documents": source_documents,
        "created_at": datetime.now(timezone.utc).isoformat()
    }


# =============================================================================
# USAGE EXAMPLE
# =============================================================================

def example_usage():
    """Example demonstrating the corrected verification gate"""
    
    # Create some signals
    signals = [
        Signal(
            id="sig-001",
            signal_type="incorporation",
            confidence=0.95,
            source_api="companies_house",
            source_url="https://api.company-information.service.gov.uk/company/12345678",
            source_response_hash="abc123...",
            detected_at=datetime.now(timezone.utc) - timedelta(days=30)
        ),
        Signal(
            id="sig-002",
            signal_type="github_spike",
            confidence=0.7,
            source_api="github",
            source_url="https://api.github.com/users/founder/repos",
            detected_at=datetime.now(timezone.utc) - timedelta(days=7)
        ),
        Signal(
            id="sig-003",
            signal_type="domain_registration",
            confidence=0.8,
            source_api="whois",
            detected_at=datetime.now(timezone.utc) - timedelta(days=14)
        ),
    ]
    
    # Initialize gate with YOUR Notion statuses
    gate = VerificationGate(
        strict_mode=False,
        auto_push_status="Source",      # Your Notion's entry-point status
        needs_review_status="Tracking"  # Your Notion's watch-list status
    )
    
    result = gate.evaluate(signals)
    
    print("=" * 50)
    print("VERIFICATION GATE RESULT")
    print("=" * 50)
    print(f"Decision: {result.decision.value}")
    print(f"Confidence: {result.confidence_score:.2f}")
    print(f"Verification: {result.verification_status.value}")
    print(f"Reason: {result.reason}")
    print(f"Suggested Notion Status: '{result.suggested_status}'")
    print(f"\nSignals contributing: {result.confidence_breakdown.get('signals_contributing', 0)}")
    print(f"Sources checked: {result.confidence_breakdown.get('sources_checked', 0)}")
    
    print("\n--- Signal Details ---")
    for detail in result.confidence_breakdown.get("signal_details", []):
        if "contribution" in detail:
            print(f"  {detail['type']}: {detail['contribution']:.4f} (from {detail['source']})")
        elif "effect" in detail:
            print(f"  {detail['type']}: {detail['effect']} (multiplier: {detail.get('multiplier', 'N/A')})")


def example_hard_kill():
    """Example showing hard kill signal behavior"""
    
    signals = [
        Signal(
            id="sig-good",
            signal_type="incorporation",
            confidence=0.95,
            source_api="companies_house",
            detected_at=datetime.now(timezone.utc) - timedelta(days=30)
        ),
        Signal(
            id="sig-kill",
            signal_type="company_dissolved",
            confidence=1.0,
            source_api="companies_house",
            detected_at=datetime.now(timezone.utc) - timedelta(days=5)
        ),
    ]
    
    gate = VerificationGate()
    result = gate.evaluate(signals)
    
    print("\n" + "=" * 50)
    print("HARD KILL EXAMPLE")
    print("=" * 50)
    print(f"Decision: {result.decision.value}")
    print(f"Reason: {result.reason}")
    print(f"Suggested Status: '{result.suggested_status}' (empty = don't push)")


def example_anti_inflation():
    """Example showing anti-inflation protection"""
    
    # Multiple GitHub signals from same source - should only count once
    signals = [
        Signal(
            id="sig-gh-1",
            signal_type="github_spike",
            confidence=0.6,
            source_api="github",
            detected_at=datetime.now(timezone.utc) - timedelta(days=1)
        ),
        Signal(
            id="sig-gh-2",
            signal_type="github_spike",
            confidence=0.8,
            source_api="github",
            detected_at=datetime.now(timezone.utc) - timedelta(days=3)
        ),
        Signal(
            id="sig-gh-3",
            signal_type="github_spike",
            confidence=0.7,
            source_api="github",
            detected_at=datetime.now(timezone.utc) - timedelta(days=7)
        ),
    ]
    
    gate = VerificationGate()
    result = gate.evaluate(signals)
    
    print("\n" + "=" * 50)
    print("ANTI-INFLATION EXAMPLE")
    print("=" * 50)
    print(f"Input: 3 github_spike signals")
    print(f"Signals contributing: {result.confidence_breakdown.get('signals_contributing', 0)} (should be 1)")
    print(f"Score: {result.confidence_score:.2f}")
    print(f"Decision: {result.decision.value}")
    
    # Show which signal was kept
    for detail in result.confidence_breakdown.get("signal_details", []):
        if "contribution" in detail:
            print(f"  Kept: {detail['id']} (contribution: {detail['contribution']:.4f})")


if __name__ == "__main__":
    example_usage()
    example_hard_kill()
    example_anti_inflation()

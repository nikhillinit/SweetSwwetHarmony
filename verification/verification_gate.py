"""
Verification Gate for Discovery Engine

Implements Glass.AI principles:
- Cross-source verification before accepting claims
- Full provenance tracking
- Uncertain claims flagged for human review

Implements MiroThinker principles:
- Verification loops (not 600 calls, but targeted verification)
- Backtracking when verification fails
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
    UNVERIFIED = "unverified"           # No verification attempted
    SINGLE_SOURCE = "single_source"     # One source confirms
    MULTI_SOURCE = "multi_source"       # 2+ independent sources confirm
    CONFLICTING = "conflicting"         # Sources disagree
    VERIFICATION_FAILED = "failed"      # Verification attempted but inconclusive


class PushDecision(str, Enum):
    """What to do with this prospect"""
    AUTO_PUSH = "auto_push"             # High confidence, push to Notion as "Lead"
    NEEDS_REVIEW = "needs_review"       # Medium confidence, push to "Needs Research" queue
    HOLD = "hold"                       # Low confidence, don't push yet
    REJECT = "reject"                   # Negative signals or conflicting data


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
}

# Negative signals and their multipliers
NEGATIVE_MULTIPLIERS = {
    "job_at_big_co_recent": 0.6,      # 0-6 months
    "job_at_big_co_medium": 0.3,      # 6-12 months
    "job_at_big_co_old": 0.1,         # 12+ months
    "domain_dead": 0.1,
    "github_inactive_90d": 0.3,
    "company_dissolved": 0.0,          # Kill signal entirely
}

# Verification requirements by signal type
VERIFICATION_REQUIREMENTS = {
    "incorporation": ["companies_house", "sec_edgar", "state_sos"],
    "github_spike": ["github"],  # Self-verifying
    "domain_registration": ["whois", "dns"],
    "patent_filing": ["uspto", "espacenet"],
    "funding_event": ["crunchbase", "pitchbook", "press"],
}


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
    retrieved_at: datetime = field(default_factory=datetime.utcnow)
    
    # Verification
    verified_by_sources: List[str] = field(default_factory=list)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    
    raw_data: Dict[str, Any] = field(default_factory=dict)
    detected_at: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def age_days(self) -> int:
        return (datetime.utcnow() - self.detected_at).days


@dataclass
class VerificationResult:
    """Result of verification gate"""
    decision: PushDecision
    verification_status: VerificationStatus
    confidence_score: float
    confidence_breakdown: Dict[str, Any]
    reason: str
    
    # For Notion routing
    suggested_status: str  # "Lead", "Needs Research", or don't push
    
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
    signals_contributing: int
    sources_checked: int
    sources: List[str]
    signal_details: List[Dict[str, Any]]
    calculation_method: str = "glass_ai_v1"
    calculated_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall": round(self.overall, 3),
            "base_score": round(self.base_score, 3),
            "multi_source_boost": self.multi_source_boost,
            "convergence_boost": round(self.convergence_boost, 2),
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
    
    Glass.AI principles:
    1. Require multi-source verification for high confidence
    2. Track full provenance for every claim
    3. Flag uncertain claims for human review
    
    MiroThinker principles:
    1. Verification loops before acceptance
    2. Backtrack on conflicting signals
    """
    
    # Thresholds
    HIGH_CONFIDENCE_THRESHOLD = 0.7
    MEDIUM_CONFIDENCE_THRESHOLD = 0.4
    MIN_SOURCES_FOR_AUTO_PUSH = 2
    MIN_SIGNALS_FOR_CONSIDERATION = 1
    
    def __init__(self, strict_mode: bool = False):
        """
        Args:
            strict_mode: If True, require 2+ sources for any push.
                        If False, allow single high-confidence sources.
        """
        self.strict_mode = strict_mode
    
    def evaluate(self, signals: List[Signal]) -> VerificationResult:
        """
        Main entry point: evaluate signals and decide on push action.
        
        Returns VerificationResult with decision and full audit trail.
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
        
        # Calculate confidence with full breakdown
        breakdown = self._calculate_confidence(signals)
        
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
    
    def _calculate_confidence(self, signals: List[Signal]) -> ConfidenceBreakdown:
        """
        Calculate confidence score with full audit trail.
        
        Incorporates:
        - Signal weights
        - Time decay
        - Multi-source boost (Glass.AI)
        - Convergence boost (MiroThinker)
        - Negative signal penalties
        """
        # Group signals by source
        by_source = defaultdict(list)
        for s in signals:
            by_source[s.source_api].append(s)
        
        sources_checked = len(by_source)
        
        # Separate positive and negative signals
        positive_signals = [s for s in signals if s.signal_type not in NEGATIVE_MULTIPLIERS]
        negative_signals = [s for s in signals if s.signal_type in NEGATIVE_MULTIPLIERS]
        
        # Calculate base score from positive signals
        base_score = 0.0
        signal_details = []
        
        for s in positive_signals:
            weight = SIGNAL_WEIGHTS.get(s.signal_type, 0.05)
            half_life = HALF_LIVES.get(s.signal_type, 90)
            decay_factor = 0.5 ** (s.age_days / half_life)
            contribution = weight * decay_factor * s.confidence
            
            base_score += contribution
            
            signal_details.append({
                "id": s.id,
                "type": s.signal_type,
                "source": s.source_api,
                "weight": weight,
                "decay_factor": round(decay_factor, 3),
                "confidence": float(s.confidence),
                "contribution": round(contribution, 4),
                "age_days": s.age_days
            })
        
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
        
        # Convergence boost (MiroThinker verification principle)
        signal_count = len(positive_signals)
        if signal_count >= 3:
            convergence_boost = min(1.0 + (0.1 * (signal_count - 2)), 1.5)
        else:
            convergence_boost = 1.0
        
        # Final score
        final_score = min(base_score * multi_source_boost * convergence_boost, 1.0)
        
        return ConfidenceBreakdown(
            overall=final_score,
            base_score=base_score,
            multi_source_boost=multi_source_boost,
            convergence_boost=convergence_boost,
            signals_contributing=signal_count,
            sources_checked=sources_checked,
            sources=list(by_source.keys()),
            signal_details=signal_details
        )
    
    def _assess_verification_status(self, signals: List[Signal]) -> VerificationStatus:
        """
        Determine overall verification status from signals.
        
        Multi-source = 2+ independent API sources confirm the entity exists.
        """
        # Get unique sources
        sources = set(s.source_api for s in signals)
        
        # Check for conflicts
        signal_types = [s.signal_type for s in signals]
        has_positive = any(t not in NEGATIVE_MULTIPLIERS for t in signal_types)
        has_strong_negative = any(
            t in ["company_dissolved", "domain_dead"] 
            for t in signal_types
        )
        
        if has_positive and has_strong_negative:
            return VerificationStatus.CONFLICTING
        
        # Assess by source count
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
                "Needs Research"
            )
        
        # High confidence + multi-source = auto-push
        if (score >= self.HIGH_CONFIDENCE_THRESHOLD and 
            verification_status == VerificationStatus.MULTI_SOURCE):
            return (
                PushDecision.AUTO_PUSH,
                f"High confidence ({score:.2f}) with {sources} sources",
                "Lead"
            )
        
        # High confidence + single source (non-strict mode)
        if (score >= self.HIGH_CONFIDENCE_THRESHOLD and 
            verification_status == VerificationStatus.SINGLE_SOURCE and
            not self.strict_mode):
            return (
                PushDecision.AUTO_PUSH,
                f"High confidence ({score:.2f}) from single authoritative source",
                "Lead"
            )
        
        # Medium confidence or single source in strict mode = needs review
        if score >= self.MEDIUM_CONFIDENCE_THRESHOLD:
            return (
                PushDecision.NEEDS_REVIEW,
                f"Medium confidence ({score:.2f}) - requires verification",
                "Needs Research"
            )
        
        # Low confidence = hold
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
        
        Returns updated signal with verification status.
        """
        signal_type = signal.signal_type
        verified_sources = list(signal.verified_by_sources)
        
        if signal_type == "github_spike" and self.companies_house:
            # Cross-verify: Is this person a director at a UK company?
            try:
                is_director = await self.companies_house.is_person_director(founder_name)
                if is_director:
                    verified_sources.append("companies_house")
            except Exception as e:
                logger.warning(f"Companies House verification failed: {e}")
        
        elif signal_type == "incorporation" and self.github:
            # Cross-verify: Does this person have recent GitHub activity?
            try:
                github_handle = signal.raw_data.get("github_handle")
                if github_handle:
                    has_activity = await self.github.has_recent_activity(github_handle)
                    if has_activity:
                        verified_sources.append("github")
            except Exception as e:
                logger.warning(f"GitHub verification failed: {e}")
        
        elif signal_type == "domain_registration" and self.whois:
            # Cross-verify: Is domain actually active?
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
        "created_at": datetime.utcnow().isoformat()
    }


# =============================================================================
# USAGE EXAMPLE
# =============================================================================

def example_usage():
    """Example of using the verification gate"""
    
    # Create some signals
    signals = [
        Signal(
            id="sig-001",
            signal_type="incorporation",
            confidence=0.95,
            source_api="companies_house",
            source_url="https://api.company-information.service.gov.uk/company/12345678",
            source_response_hash="abc123...",
            detected_at=datetime.utcnow() - timedelta(days=30)
        ),
        Signal(
            id="sig-002",
            signal_type="github_spike",
            confidence=0.7,
            source_api="github",
            source_url="https://api.github.com/users/founder/repos",
            detected_at=datetime.utcnow() - timedelta(days=7)
        ),
        Signal(
            id="sig-003",
            signal_type="domain_registration",
            confidence=0.8,
            source_api="whois",
            detected_at=datetime.utcnow() - timedelta(days=14)
        ),
    ]
    
    # Run through verification gate
    gate = VerificationGate(strict_mode=False)
    result = gate.evaluate(signals)
    
    print(f"Decision: {result.decision.value}")
    print(f"Confidence: {result.confidence_score:.2f}")
    print(f"Verification: {result.verification_status.value}")
    print(f"Reason: {result.reason}")
    print(f"Suggested Notion Status: {result.suggested_status}")
    print(f"\nBreakdown: {result.confidence_breakdown}")


if __name__ == "__main__":
    example_usage()

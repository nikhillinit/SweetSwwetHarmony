"""
Hard Disqualifiers - Stage 1 of Two-Stage Thesis Filter

FREE keyword-based filters that reject obvious non-consumer signals
before expensive LLM classification.

Categories:
- B2B/Enterprise signals
- Crypto/Web3 signals
- Services/Consulting signals
- Job postings

Cost: $0 (pure string matching)
"""

from dataclasses import dataclass
from typing import Optional, Set
import re

from utils.web3_detector import Web3Detector


@dataclass
class DisqualifyResult:
    """Result of hard disqualifier check."""
    passed: bool
    reason: Optional[str] = None
    category: Optional[str] = None


# =============================================================================
# KEYWORD SETS
# =============================================================================

# B2B/Enterprise keywords - disqualify
HARD_B2B_KEYWORDS: Set[str] = {
    "api", "backend", "b2b", "crm", "devops",
    "enterprise", "erp", "infrastructure", "middleware",
    "microservice", "platform", "saas", "sdk",
}

SOFT_B2B_KEYWORDS: Set[str] = {
    # Business software
    "hris", "hrms", "invoicing", "payroll", "procurement",
    "workflow automation",

    # Technical infrastructure / devtool context
    "apm", "aws", "azure", "ci/cd", "cloud infrastructure",
    "code review", "data pipeline", "data warehouse", "developer experience",
    "devtool", "docker", "dx", "etl", "gcp", "k8s",
    "kubernetes", "logging", "monitoring", "observability",
    "terraform", "testing framework",
}

B2B_KEYWORDS: Set[str] = HARD_B2B_KEYWORDS | SOFT_B2B_KEYWORDS

# Crypto/Web3 keywords - disqualify
CRYPTO_KEYWORDS: Set[str] = {
    "blockchain", "crypto", "cryptocurrency",
    "bitcoin", "btc", "ethereum", "eth", "solana",
    "nft", "web3", "defi", "dao", "token",
    "tokenomics", "smart contract", "dapp",
    "metaverse", "play to earn", "p2e",
    "mining", "staking", "yield farming",
    "wallet", "metamask", "opensea",
}

# Services/Agency keywords - disqualify
SERVICES_KEYWORDS: Set[str] = {
    "agency", "consulting", "consultancy",
    "freelance", "freelancer", "contractor",
    "services", "professional services",
    "outsourcing", "staffing", "recruiting",
    "we build", "we create", "we design",
    "our team will", "hire us", "contact us for",
}

# Job posting keywords - disqualify
JOB_KEYWORDS: Set[str] = {
    "hiring", "we're hiring", "we are hiring",
    "join our team", "open position", "job opening",
    "apply now", "careers at", "work with us",
    "looking for", "seeking", "wanted:",
    "full-time", "part-time", "remote position",
    "senior engineer", "software engineer",
    "product manager", "designer",
}

# Consumer positive keywords - help reduce false positives
CONSUMER_POSITIVE_KEYWORDS: Set[str] = {
    # CPG
    "food", "beverage", "snack", "drink", "meal",
    "grocery", "kitchen", "recipe", "cooking",
    "organic", "vegan", "plant-based", "gluten-free",

    # Health & Wellness
    "fitness", "workout", "exercise", "wellness",
    "mental health", "meditation", "sleep",
    "skincare", "beauty", "cosmetics", "personal care",
    "supplements", "vitamins", "nutrition",

    # Travel & Hospitality
    "travel", "vacation", "booking", "hotel",
    "flight", "trip", "destination", "tourism",
    "restaurant", "cafe", "dining",

    # Consumer Apps
    "app for", "mobile app", "ios app", "android app",
    "consumer app", "lifestyle", "social",
    "dating", "entertainment", "gaming",
    "shopping", "e-commerce", "marketplace",

    # Direct-to-Consumer
    "d2c", "dtc", "direct to consumer",
    "subscription box", "membership",
}

DIRECT_CONSUMER_OVERRIDE_KEYWORDS: Set[str] = {
    "consumer", "consumers", "customer", "customers", "diners",
    "guest", "guests", "traveler", "travelers", "shopper", "shoppers",
    "patient", "patients", "family", "families", "household", "households",
    "for consumers", "for diners", "for guests", "for travelers",
}


# =============================================================================
# FILTER FUNCTIONS
# =============================================================================

def _normalize_text(text: str) -> str:
    """Normalize text for keyword matching."""
    return text.lower().strip()


def _contains_keywords(text: str, keywords: Set[str]) -> Optional[str]:
    """
    Check if text contains any keywords.
    Returns the matched keyword or None.
    """
    text_lower = _normalize_text(text)

    for keyword in keywords:
        # Word boundary match for single words
        if " " not in keyword:
            pattern = rf"\b{re.escape(keyword)}\b"
            if re.search(pattern, text_lower):
                return keyword
        # Substring match for phrases
        elif keyword in text_lower:
            return keyword

    return None


def is_b2b(text: str) -> Optional[str]:
    """
    Check if text indicates B2B/Enterprise focus.
    Returns matched keyword or None.
    """
    return _contains_keywords(text, B2B_KEYWORDS)


def is_hard_b2b(text: str) -> Optional[str]:
    """Check if text indicates a hard B2B/enterprise focus."""
    return _contains_keywords(text, HARD_B2B_KEYWORDS)


def is_soft_b2b(text: str) -> Optional[str]:
    """Check if text indicates a soft B2B/enterprise focus."""
    return _contains_keywords(text, SOFT_B2B_KEYWORDS)


def is_crypto(text: str) -> Optional[str]:
    """
    Check if text indicates Crypto/Web3 focus.
    Returns matched keyword or None.
    """
    return _contains_keywords(text, CRYPTO_KEYWORDS)


def is_services(text: str) -> Optional[str]:
    """
    Check if text indicates Services/Consulting focus.
    Returns matched keyword or None.
    """
    return _contains_keywords(text, SERVICES_KEYWORDS)


def is_job_post(text: str) -> Optional[str]:
    """
    Check if text is a job posting.
    Returns matched keyword or None.
    """
    return _contains_keywords(text, JOB_KEYWORDS)


def has_consumer_signals(text: str) -> bool:
    """
    Check if text has strong consumer signals.
    Used to reduce false positives.
    """
    return _contains_keywords(text, CONSUMER_POSITIVE_KEYWORDS) is not None


def has_direct_consumer_override(text: str) -> bool:
    """
    Check for language that explicitly identifies the end user as a consumer.

    This is intentionally narrower than CONSUMER_POSITIVE_KEYWORDS so industry
    nouns like "restaurant" do not automatically rescue hard B2B terms.
    """
    return _contains_keywords(text, DIRECT_CONSUMER_OVERRIDE_KEYWORDS) is not None


# =============================================================================
# MAIN FILTER CLASS
# =============================================================================

class HardDisqualifiers:
    """
    Hard disqualifier filter - Stage 1 of two-stage filtering.

    Performs FREE keyword-based filtering before expensive LLM calls.

    Usage:
        filter = HardDisqualifiers()
        result = filter.check("Show HN: My Enterprise SaaS Platform")
        if not result.passed:
            print(f"Disqualified: {result.reason}")
    """

    def __init__(self, allow_consumer_override: bool = True):
        """
        Initialize hard disqualifier filter.

        Args:
            allow_consumer_override: If True, strong consumer signals
                can override some disqualifiers
        """
        self.allow_consumer_override = allow_consumer_override
        self._web3_detector = Web3Detector()

    def check(
        self,
        title: str,
        description: Optional[str] = None,
        url: Optional[str] = None,
    ) -> DisqualifyResult:
        """
        Check if signal should be disqualified.

        Args:
            title: Signal title
            description: Optional description/context
            url: Optional URL

        Returns:
            DisqualifyResult with passed=True if should continue to LLM,
            passed=False if disqualified
        """
        # Combine text for analysis
        text_parts = [title]
        if description:
            text_parts.append(description)
        combined_text = " ".join(text_parts)

        # Check for consumer signals first (potential override)
        has_consumer = has_consumer_signals(combined_text) if self.allow_consumer_override else False

        # Check disqualifiers in order of severity

        # 1. Job posts - always disqualify
        if match := is_job_post(combined_text):
            return DisqualifyResult(
                passed=False,
                reason=f"Job posting detected: '{match}'",
                category="job_post"
            )

        # 2. Crypto/Web3 - always disqualify (out of thesis)
        # Uses context-aware Web3Detector to avoid false positives
        # on "access tokens", "DAO pattern", "data mining", etc.
        web3_result = self._web3_detector.detect(combined_text)
        if web3_result.is_crypto:
            return DisqualifyResult(
                passed=False,
                reason=f"Crypto/Web3 signal: '{web3_result.matched_term}' — {web3_result.reason}",
                category="crypto"
            )

        # 3. Services/Consulting - always disqualify
        if match := is_services(combined_text):
            return DisqualifyResult(
                passed=False,
                reason=f"Services/consulting business: '{match}'",
                category="services"
            )

        # 4. B2B/Enterprise - hard terms should not be silently rescued by
        # generic consumer context, while softer terms can still be overridden.
        if hard_match := is_hard_b2b(combined_text):
            if not has_consumer:
                return DisqualifyResult(
                    passed=False,
                    reason=f"B2B/Enterprise focus: '{hard_match}'",
                    category="b2b"
                )

            # Allow ambiguous hybrid consumer text to continue downstream rather
            # than rejecting it outright, but do not silently rescue clearly
            # B2B-in-disguise descriptions.
            if not has_direct_consumer_override(combined_text):
                return DisqualifyResult(
                    passed=False,
                    reason=f"B2B/Enterprise focus: '{hard_match}'",
                    category="b2b"
                )
        elif soft_match := is_soft_b2b(combined_text):
            if not has_consumer:
                return DisqualifyResult(
                    passed=False,
                    reason=f"B2B/Enterprise focus: '{soft_match}'",
                    category="b2b"
                )

        # Passed all disqualifiers
        return DisqualifyResult(passed=True)

    def check_signal(
        self,
        signal: dict,
    ) -> DisqualifyResult:
        """
        Check a signal dict for disqualification.

        Args:
            signal: Dict with 'title', optional 'source_context', optional 'url'

        Returns:
            DisqualifyResult
        """
        return self.check(
            title=signal.get("title", ""),
            description=signal.get("source_context"),
            url=signal.get("url"),
        )


# =============================================================================
# MODULE-LEVEL CONVENIENCE
# =============================================================================

# Default instance for quick checks
_default_filter = HardDisqualifiers()


def filter_signal(signal: dict) -> DisqualifyResult:
    """
    Convenience function to check a signal.

    Args:
        signal: Dict with 'title', optional 'source_context', optional 'url'

    Returns:
        DisqualifyResult
    """
    return _default_filter.check_signal(signal)

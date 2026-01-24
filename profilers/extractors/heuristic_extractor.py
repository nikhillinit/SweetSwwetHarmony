"""
Heuristic-based Profile Extractor.

Fallback extractor using regex patterns and keyword matching when LLM is unavailable.
Lower confidence than LLM but works offline and is fast.

Usage:
    extractor = ProfileHeuristicExtractor()
    result = extractor.extract(pages)
"""

from __future__ import annotations

import re
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# PATTERNS AND KEYWORDS
# =============================================================================

# Company name patterns (ordered by priority)
COMPANY_NAME_PATTERNS = [
    # <title>Company Name - Tagline</title>
    r'<title[^>]*>([^<|–\-]+?)(?:\s*[-–|]\s*|</title>)',
    # og:site_name
    r'property=["\']og:site_name["\']\s+content=["\']([^"\']+)["\']',
    # meta name="application-name"
    r'name=["\']application-name["\']\s+content=["\']([^"\']+)["\']',
]

# Pricing model indicators
PRICING_PATTERNS = {
    "free": [
        r'\bfree\s+(?:forever|plan|tier|account)\b',
        r'\b(?:no|zero)\s+(?:cost|charge|fee)\b',
        r'\b100%\s+free\b',
    ],
    "freemium": [
        r'\bfree\s+(?:to\s+start|trial)\b',
        r'\bfree\b.*\bpro\b',
        r'\bfree\b.*\bpremium\b',
        r'\btry\s+(?:for\s+)?free\b',
    ],
    "subscription": [
        r'\$/(?:mo|month)\b',
        r'\bmonthly\s+(?:subscription|billing|plan)\b',
        r'\bper\s+month\b',
        r'\bannual(?:ly)?\s+(?:subscription|billing)\b',
    ],
    "usage_based": [
        r'\bpay\s+(?:as\s+you\s+go|per\s+use)\b',
        r'\bbased\s+on\s+(?:usage|volume|transactions)\b',
        r'\bper\s+(?:transaction|request|call|unit)\b',
    ],
    "per_seat": [
        r'\bper\s+(?:user|seat|member)\b',
        r'\$/(?:user|seat)\b',
        r'\bper\s+(?:employee|license)\b',
    ],
    "contact_sales": [
        r'\bcontact\s+(?:us|sales)\b.*\bpricing\b',
        r'\brequest\s+(?:a\s+)?(?:demo|quote)\b',
        r'\bcustom\s+(?:pricing|plans?)\b',
        r'\benterprise\s+(?:pricing|plans?)\b',
    ],
}

# Business model indicators
BUSINESS_MODEL_PATTERNS = {
    "B2C_subscription": [
        r'\bsubscribe\s+(?:now|today)\b',
        r'\bmembership\b',
        r'\bmonthly\s+box\b',
        r'\bsubscription\s+(?:box|service)\b',
    ],
    "B2C_marketplace": [
        r'\bbuy\s+and\s+sell\b',
        r'\bmarketplace\b',
        r'\bconnect\s+(?:buyers?\s+(?:and|with)\s+sellers?|sellers?\s+(?:and|with)\s+buyers?)\b',
    ],
    "B2B_SaaS": [
        r'\bSaaS\b',
        r'\bsoftware\s+as\s+a\s+service\b',
        r'\bcloud\s+(?:platform|solution|software)\b',
        r'\bfor\s+(?:teams?|businesses?|enterprises?|companies)\b',
    ],
    "D2C_ecommerce": [
        r'\bshop\s+(?:now|online)\b',
        r'\badd\s+to\s+cart\b',
        r'\bfree\s+shipping\b',
        r'\bcheckout\b',
    ],
    "freemium": [
        r'\bfree\s+plan\b',
        r'\bupgrade\s+to\s+(?:pro|premium)\b',
        r'\bstart\s+for\s+free\b',
    ],
}

# Target customer indicators
CUSTOMER_PATTERNS = {
    "consumers": [
        r'\bfor\s+(?:you|your\s+family|everyone|individuals?)\b',
        r'\bpersonal\s+(?:use|account)\b',
        r'\bsave\s+(?:money|time)\b.*\byour\b',
    ],
    "small_business": [
        r'\bsmall\s+(?:business(?:es)?|teams?)\b',
        r'\bfreelancers?\b',
        r'\bstartups?\b',
        r'\bsolo\s*preneurs?\b',
    ],
    "enterprise": [
        r'\benterprise\b',
        r'\blarge\s+(?:teams?|organizations?|companies)\b',
        r'\bfortune\s+\d+\b',
    ],
    "developers": [
        r'\bdevelopers?\b',
        r'\bAPI\b',
        r'\bSDK\b',
        r'\bintegrations?\b',
    ],
    "ecommerce_brands": [
        r'\b(?:e-?commerce|online)\s+(?:brands?|stores?|businesses?)\b',
        r'\bshopify\b',
        r'\bD2C\s+brands?\b',
    ],
}

# Category keywords
CATEGORY_KEYWORDS = {
    "Consumer CPG": [
        "food", "beverage", "snack", "drink", "meal", "grocery",
        "beauty", "skincare", "cosmetics", "personal care",
        "household", "cleaning", "home goods",
    ],
    "Consumer Health Tech": [
        "fitness", "workout", "exercise", "gym",
        "wellness", "health", "mental health", "meditation",
        "nutrition", "diet", "weight loss",
        "sleep", "supplements", "vitamins",
    ],
    "Travel & Hospitality": [
        "travel", "booking", "hotel", "flight", "vacation",
        "restaurant", "dining", "food delivery",
        "hospitality", "tourism", "adventure",
    ],
    "Consumer Marketplace": [
        "marketplace", "buy and sell", "peer to peer",
        "secondhand", "resale", "rental",
    ],
    "B2B SaaS": [
        "saas", "platform", "enterprise", "teams",
        "dashboard", "analytics", "crm", "erp",
    ],
    "Developer Tools": [
        "api", "sdk", "developer", "code", "programming",
        "infrastructure", "devops", "cloud",
    ],
    "Fintech": [
        "banking", "payments", "finance", "investment",
        "crypto", "blockchain", "insurance",
    ],
}


# =============================================================================
# HEURISTIC EXTRACTOR
# =============================================================================

class ProfileHeuristicExtractor:
    """
    Heuristic-based profile extractor using patterns and keywords.

    Provides fallback extraction when LLM is unavailable.
    All extractions have lower confidence than LLM-based ones.
    """

    def __init__(self, base_confidence: float = 0.5):
        """
        Initialize extractor.

        Args:
            base_confidence: Base confidence for heuristic extractions (default: 0.5)
        """
        self.base_confidence = base_confidence

    def extract(self, pages) -> "ProfileExtractionResult":
        """
        Extract profile from fetched pages.

        Args:
            pages: List of PageFetchResult

        Returns:
            ProfileExtractionResult
        """
        from profilers.url_profiler import ProfileExtractionResult, ExtractedField

        # Combine HTML and text content
        html_content = ""
        text_content = ""

        for page in pages:
            if page.success:
                html_content += page.html_content + "\n"
                text_content += page.text_content + "\n"

        if not text_content.strip():
            return ProfileExtractionResult(
                extraction_method="heuristic_empty",
            )

        # Extract each field
        company_name = self._extract_company_name(html_content, pages)
        pricing_model = self._extract_pricing_model(text_content, pages)
        business_model = self._extract_business_model(text_content, pages)
        target_customer = self._extract_target_customer(text_content, pages)
        category_hints = self._extract_category_hints(text_content)

        # Problem solved is harder to extract heuristically
        # We'll skip it for now or use a very basic approach
        problem_solved = self._extract_problem_solved(text_content, pages)

        return ProfileExtractionResult(
            problem_solved=problem_solved,
            target_customer=target_customer,
            business_model=business_model,
            pricing_model=pricing_model,
            company_name=company_name,
            category_hints=category_hints,
            extraction_method="heuristic",
        )

    def _extract_company_name(
        self,
        html: str,
        pages,
    ) -> Optional["ExtractedField"]:
        """Extract company name from HTML."""
        from profilers.url_profiler import ExtractedField

        source_url = pages[0].url if pages else ""

        for pattern in COMPANY_NAME_PATTERNS:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                # Clean up common suffixes
                name = re.sub(r'\s*[-–|:]\s*$', '', name)
                name = name.strip()

                if name and len(name) > 1 and len(name) < 100:
                    return ExtractedField(
                        value=name,
                        short_phrase=name[:50],
                        confidence=self.base_confidence + 0.2,  # Title is reliable
                        evidence_snippet=None,
                        source_url=source_url,
                        extraction_method="heuristic",
                    )

        return None

    def _extract_pricing_model(
        self,
        text: str,
        pages,
    ) -> Optional["ExtractedField"]:
        """Extract pricing model from text."""
        from profilers.url_profiler import ExtractedField

        source_url = self._find_pricing_url(pages) or (pages[0].url if pages else "")
        text_lower = text.lower()

        best_match: Optional[Tuple[str, str, float]] = None

        for model, patterns in PRICING_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, text_lower)
                if match:
                    evidence = self._get_context_around_match(text, match)
                    confidence = self.base_confidence + 0.1

                    # Boost confidence for pricing page
                    if "/pricing" in source_url.lower():
                        confidence += 0.15

                    if best_match is None or confidence > best_match[2]:
                        best_match = (model, evidence, confidence)

        if best_match:
            return ExtractedField(
                value=best_match[0],
                short_phrase=best_match[0].replace("_", " "),
                confidence=min(best_match[2], 0.85),
                evidence_snippet=best_match[1],
                source_url=source_url,
                extraction_method="heuristic",
            )

        return None

    def _extract_business_model(
        self,
        text: str,
        pages,
    ) -> Optional["ExtractedField"]:
        """Extract business model from text."""
        from profilers.url_profiler import ExtractedField

        source_url = pages[0].url if pages else ""
        text_lower = text.lower()

        best_match: Optional[Tuple[str, str, float]] = None

        for model, patterns in BUSINESS_MODEL_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, text_lower)
                if match:
                    evidence = self._get_context_around_match(text, match)
                    confidence = self.base_confidence

                    if best_match is None or confidence > best_match[2]:
                        best_match = (model, evidence, confidence)

        if best_match:
            return ExtractedField(
                value=best_match[0],
                short_phrase=best_match[0].replace("_", " "),
                confidence=min(best_match[2], 0.75),
                evidence_snippet=best_match[1],
                source_url=source_url,
                extraction_method="heuristic",
            )

        return None

    def _extract_target_customer(
        self,
        text: str,
        pages,
    ) -> Optional["ExtractedField"]:
        """Extract target customer from text."""
        from profilers.url_profiler import ExtractedField

        source_url = pages[0].url if pages else ""
        text_lower = text.lower()

        best_match: Optional[Tuple[str, str, float]] = None

        for customer_type, patterns in CUSTOMER_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, text_lower)
                if match:
                    evidence = self._get_context_around_match(text, match)
                    confidence = self.base_confidence

                    if best_match is None or confidence > best_match[2]:
                        best_match = (customer_type.replace("_", " ").title(), evidence, confidence)

        if best_match:
            return ExtractedField(
                value=best_match[0],
                short_phrase=best_match[0],
                confidence=min(best_match[2], 0.7),
                evidence_snippet=best_match[1],
                source_url=source_url,
                extraction_method="heuristic",
            )

        return None

    def _extract_problem_solved(
        self,
        text: str,
        pages,
    ) -> Optional["ExtractedField"]:
        """
        Extract problem solved from text.

        This is harder to do heuristically, so we look for specific patterns.
        """
        from profilers.url_profiler import ExtractedField

        source_url = pages[0].url if pages else ""

        # Look for common value proposition patterns
        patterns = [
            r'we\s+help\s+(.{20,100}?)(?:\.|$)',
            r'our\s+mission\s+is\s+to\s+(.{20,100}?)(?:\.|$)',
            r'(?:helps?|enables?|allows?)\s+(?:you|teams?|businesses?)\s+(?:to\s+)?(.{20,80}?)(?:\.|$)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                if len(value) > 20:
                    return ExtractedField(
                        value=value,
                        short_phrase=value[:50],
                        confidence=self.base_confidence - 0.1,  # Lower for problem_solved
                        evidence_snippet=match.group(0),
                        source_url=source_url,
                        extraction_method="heuristic",
                    )

        return None

    def _extract_category_hints(self, text: str) -> List[str]:
        """Extract category hints based on keyword frequency."""
        text_lower = text.lower()
        categories = []

        for category, keywords in CATEGORY_KEYWORDS.items():
            matches = 0
            for keyword in keywords:
                if keyword in text_lower:
                    matches += 1

            # Require at least 2 keyword matches for a category
            if matches >= 2:
                categories.append(category)

        return categories[:3]  # Limit to top 3 categories

    def _find_pricing_url(self, pages) -> Optional[str]:
        """Find the pricing page URL if present."""
        for page in pages:
            if "/pricing" in page.url.lower():
                return page.url
        return None

    def _get_context_around_match(
        self,
        text: str,
        match: re.Match,
        context_chars: int = 100,
    ) -> str:
        """Get text context around a regex match."""
        start = max(0, match.start() - context_chars // 2)
        end = min(len(text), match.end() + context_chars // 2)
        context = text[start:end].strip()

        # Add ellipsis if truncated
        if start > 0:
            context = "..." + context
        if end < len(text):
            context = context + "..."

        return context

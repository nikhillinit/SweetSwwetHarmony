"""
Page Type Classifier for Monitoring Subsystem

Classifies URLs and page content into business-relevant categories:
- pricing: Pricing pages, plans, subscription tiers
- careers: Job listings, careers, hiring pages
- product: Product pages, features, capabilities
- terms: Terms of service, privacy policy, legal
- news: Press releases, blog posts, announcements
- landing: Homepage, about pages

This enables more intelligent severity scoring and "why now" generation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse


class PageType(str, Enum):
    """Business-relevant page categories."""
    PRICING = "pricing"
    CAREERS = "careers"
    PRODUCT = "product"
    TERMS = "terms"
    NEWS = "news"
    LANDING = "landing"
    UNKNOWN = "unknown"


@dataclass
class PageClassification:
    """Result of page type classification."""
    page_type: PageType
    confidence: float  # 0.0 to 1.0
    signals: List[str]  # What triggered this classification
    severity_boost: float  # Additional severity weight for this page type

    def to_dict(self) -> Dict:
        return {
            "page_type": self.page_type.value,
            "confidence": self.confidence,
            "signals": self.signals,
            "severity_boost": self.severity_boost,
        }


# URL path patterns for each page type
# Note: Patterns are matched against the URL path, more specific patterns first
URL_PATTERNS: Dict[PageType, List[str]] = {
    PageType.PRICING: [
        r"/pricing",
        r"/plans",
        r"/subscribe",
        r"/upgrade",
        r"/pro(?:/|$)",  # /pro or /pro/ but not /product
        r"/premium",
        r"/enterprise",
        r"/billing",
        r"/packages",
    ],
    PageType.CAREERS: [
        r"/careers",
        r"/jobs",
        r"/join",
        r"/hiring",
        r"/work-with-us",
        r"/opportunities",
        r"/team",
        r"/open-positions",
    ],
    PageType.PRODUCT: [
        r"/product",
        r"/features",
        r"/solutions",
        r"/platform",
        r"/capabilities",
        r"/how-it-works",
        r"/demo",
    ],
    PageType.TERMS: [
        r"/terms",
        r"/privacy",
        r"/legal",
        r"/tos",
        r"/policy",
        r"/gdpr",
        r"/cookies",
        r"/compliance",
    ],
    PageType.NEWS: [
        r"/blog",
        r"/news",
        r"/press",
        r"/announcements",
        r"/updates",
        r"/changelog",
        r"/release",
    ],
    PageType.LANDING: [
        r"^/$",  # Homepage
        r"/about",
        r"/company",
        r"/mission",
        r"/story",
    ],
}

# Content keywords for each page type (case-insensitive)
CONTENT_KEYWORDS: Dict[PageType, List[str]] = {
    PageType.PRICING: [
        "per month", "per year", "/mo", "/yr", "billed annually",
        "free tier", "free plan", "starter", "professional",
        "enterprise plan", "custom pricing", "contact sales",
        "pricing", "subscribe", "upgrade",
    ],
    PageType.CAREERS: [
        "we're hiring", "join our team", "open positions",
        "job opening", "career", "apply now", "full-time",
        "remote", "hybrid", "on-site", "salary range",
        "benefits", "perks", "culture",
    ],
    PageType.PRODUCT: [
        "features", "capabilities", "how it works",
        "integration", "api", "dashboard", "analytics",
        "automate", "streamline", "platform",
    ],
    PageType.TERMS: [
        "terms of service", "privacy policy", "data protection",
        "gdpr", "ccpa", "cookie policy", "legal notice",
        "user agreement", "license agreement",
    ],
    PageType.NEWS: [
        "press release", "announcement", "we're excited",
        "today we", "introducing", "launching", "new feature",
        "update:", "version", "release notes",
    ],
}

# Severity boost for each page type (changes here are more significant)
SEVERITY_BOOSTS: Dict[PageType, float] = {
    PageType.PRICING: 0.15,   # Pricing changes are highly significant
    PageType.CAREERS: 0.12,   # Hiring activity is a strong signal
    PageType.TERMS: 0.08,     # Policy changes matter
    PageType.PRODUCT: 0.05,   # Product updates are interesting
    PageType.NEWS: 0.03,      # News is informational
    PageType.LANDING: 0.02,   # Landing changes are minor signals
    PageType.UNKNOWN: 0.0,    # No boost for unknown
}

# "Why Now" templates for each page type
WHY_NOW_TEMPLATES: Dict[PageType, str] = {
    PageType.PRICING: "Pricing page changed - may indicate strategy shift, new tier, or price adjustment",
    PageType.CAREERS: "Careers page updated - hiring activity detected, possible growth phase",
    PageType.TERMS: "Legal/policy page changed - regulatory compliance or data practice updates",
    PageType.PRODUCT: "Product page updated - new features or capabilities announced",
    PageType.NEWS: "News/blog updated - company announcement or press release",
    PageType.LANDING: "Homepage changed - positioning or branding update",
    PageType.UNKNOWN: "Website content changed",
}


class PageTypeClassifier:
    """
    Classifies pages into business-relevant categories.

    Uses a combination of:
    1. URL path pattern matching (high confidence)
    2. Content keyword analysis (medium confidence)
    """

    def __init__(self):
        # Compile URL patterns for efficiency
        self._url_patterns: Dict[PageType, List[re.Pattern]] = {}
        for page_type, patterns in URL_PATTERNS.items():
            self._url_patterns[page_type] = [
                re.compile(p, re.IGNORECASE) for p in patterns
            ]

        # Compile content keywords
        self._content_patterns: Dict[PageType, List[re.Pattern]] = {}
        for page_type, keywords in CONTENT_KEYWORDS.items():
            self._content_patterns[page_type] = [
                re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords
            ]

    def classify(
        self,
        url: str,
        text_content: Optional[str] = None,
    ) -> PageClassification:
        """
        Classify a page based on URL and optionally content.

        Args:
            url: The page URL
            text_content: Optional page text content for deeper analysis

        Returns:
            PageClassification with type, confidence, and signals
        """
        signals: List[str] = []
        scores: Dict[PageType, float] = {pt: 0.0 for pt in PageType}

        # 1. URL-based classification (high weight)
        url_type, url_confidence, url_signals = self._classify_by_url(url)
        if url_type != PageType.UNKNOWN:
            scores[url_type] += url_confidence * 0.7  # 70% weight for URL
            signals.extend(url_signals)

        # 2. Content-based classification (if provided)
        if text_content:
            content_type, content_confidence, content_signals = self._classify_by_content(text_content)
            if content_type != PageType.UNKNOWN:
                scores[content_type] += content_confidence * 0.3  # 30% weight for content
                signals.extend(content_signals)

        # Find best match
        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]

        # Minimum confidence threshold
        # Content-only matches need lower threshold since they max at 0.27 (0.9 * 0.3)
        if best_score < 0.2:
            best_type = PageType.UNKNOWN
            best_score = 0.0

        return PageClassification(
            page_type=best_type,
            confidence=min(1.0, best_score),
            signals=signals,
            severity_boost=SEVERITY_BOOSTS[best_type],
        )

    def _classify_by_url(self, url: str) -> Tuple[PageType, float, List[str]]:
        """Classify based on URL path patterns."""
        try:
            parsed = urlparse(url)
            path = parsed.path.lower()
        except Exception:
            return PageType.UNKNOWN, 0.0, []

        signals = []
        for page_type, patterns in self._url_patterns.items():
            for pattern in patterns:
                if pattern.search(path):
                    signals.append(f"url_match:{pattern.pattern}")
                    return page_type, 0.9, signals  # High confidence for URL match

        # Check if it's the homepage
        if path in ("", "/", "/index", "/home"):
            return PageType.LANDING, 0.8, ["url_match:homepage"]

        return PageType.UNKNOWN, 0.0, []

    def _classify_by_content(
        self,
        text_content: str,
    ) -> Tuple[PageType, float, List[str]]:
        """Classify based on content keywords."""
        if not text_content or len(text_content) < 50:
            return PageType.UNKNOWN, 0.0, []

        # Count keyword matches for each type
        match_counts: Dict[PageType, int] = {}
        signals: List[str] = []

        for page_type, patterns in self._content_patterns.items():
            count = 0
            for pattern in patterns:
                matches = pattern.findall(text_content)
                if matches:
                    count += len(matches)
                    if count <= 3:  # Only log first few signals
                        signals.append(f"content_match:{pattern.pattern[:20]}")
            match_counts[page_type] = count

        # Find type with most matches
        if not match_counts:
            return PageType.UNKNOWN, 0.0, []

        best_type = max(match_counts, key=match_counts.get)
        best_count = match_counts[best_type]

        if best_count == 0:
            return PageType.UNKNOWN, 0.0, []

        # Convert count to confidence (diminishing returns)
        confidence = min(0.9, 0.3 + (best_count * 0.1))

        return best_type, confidence, signals

    def get_why_now(self, page_type: PageType, diff_summary: Optional[str] = None) -> str:
        """
        Generate a "why now" explanation for a change.

        Args:
            page_type: The classified page type
            diff_summary: Optional diff summary to include

        Returns:
            Human-readable explanation
        """
        base = WHY_NOW_TEMPLATES.get(page_type, WHY_NOW_TEMPLATES[PageType.UNKNOWN])

        if diff_summary:
            return f"{base}. Details: {diff_summary}"

        return base

    def get_severity_boost(self, page_type: PageType) -> float:
        """Get the severity boost for a page type."""
        return SEVERITY_BOOSTS.get(page_type, 0.0)


# Singleton instance for convenience
_classifier: Optional[PageTypeClassifier] = None


def get_classifier() -> PageTypeClassifier:
    """Get or create the singleton classifier instance."""
    global _classifier
    if _classifier is None:
        _classifier = PageTypeClassifier()
    return _classifier


def classify_page(url: str, text_content: Optional[str] = None) -> PageClassification:
    """
    Convenience function to classify a page.

    Args:
        url: The page URL
        text_content: Optional page text content

    Returns:
        PageClassification result
    """
    return get_classifier().classify(url, text_content)


# Quick test
if __name__ == "__main__":
    classifier = PageTypeClassifier()

    test_cases = [
        ("https://acme.com/pricing", None),
        ("https://acme.com/careers", None),
        ("https://acme.com/terms-of-service", None),
        ("https://acme.com/blog/announcing-v2", None),
        ("https://acme.com/", None),
        ("https://acme.com/random-page", "We're hiring! Join our team today."),
        ("https://acme.com/unknown", "Check out our new features"),
    ]

    print("Page Type Classification Tests")
    print("=" * 60)

    for url, content in test_cases:
        result = classifier.classify(url, content)
        print(f"\nURL: {url}")
        if content:
            print(f"Content hint: {content[:50]}...")
        print(f"  Type: {result.page_type.value}")
        print(f"  Confidence: {result.confidence:.2f}")
        print(f"  Severity boost: {result.severity_boost:.2f}")
        print(f"  Signals: {result.signals}")

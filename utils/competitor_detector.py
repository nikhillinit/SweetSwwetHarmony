"""
Competitor Detector - Flag signals that may compete with portfolio companies.

Checks if a signal's category and keywords overlap with existing portfolio companies.
Surfaces as warning, does not auto-reject.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CompetitorMatch:
    """Result of competitor detection."""
    portfolio_company: str
    category: str
    matched_keywords: List[str]
    confidence: float

    def to_dict(self) -> Dict:
        return {
            "portfolio_company": self.portfolio_company,
            "category": self.category,
            "matched_keywords": self.matched_keywords,
            "confidence": self.confidence,
        }


class CompetitorDetector:
    """
    Detects potential competitors to portfolio companies.

    Usage:
        detector = CompetitorDetector("config/portfolio.json")
        match = detector.check("consumer_cpg", "We make meal kits")
        if match:
            print(f"Potential competitor to {match.portfolio_company}")
    """

    def __init__(self, portfolio_path: str = "config/portfolio.json"):
        self.portfolio_path = Path(portfolio_path)
        self._portfolio: List[Dict] = []
        self._load_portfolio()

    def _load_portfolio(self) -> None:
        """Load portfolio companies from JSON file."""
        if not self.portfolio_path.exists():
            logger.warning(f"Portfolio file not found: {self.portfolio_path}")
            return

        try:
            with open(self.portfolio_path) as f:
                data = json.load(f)
                self._portfolio = data.get("companies", [])
                logger.debug(f"Loaded {len(self._portfolio)} portfolio companies")
        except Exception as e:
            logger.error(f"Failed to load portfolio: {e}")

    def check(
        self,
        category: str,
        description: str,
    ) -> Optional[CompetitorMatch]:
        """
        Check if a signal may be a competitor to portfolio companies.

        Args:
            category: The thesis category of the signal
            description: Description text to check for keyword overlap

        Returns:
            CompetitorMatch if potential competitor detected, None otherwise
        """
        if not self._portfolio:
            return None

        normalized_desc = description.lower()

        for company in self._portfolio:
            # Check category match first
            if company.get("category") != category:
                continue

            # Check for keyword overlap
            keywords = company.get("keywords", [])
            matched = []

            for keyword in keywords:
                # Allow for optional plural 's' at the end
                pattern = r'\b' + re.escape(keyword.lower()) + r's?\b'
                if re.search(pattern, normalized_desc):
                    matched.append(keyword)

            # Need at least one keyword match
            if matched:
                confidence = min(len(matched) / len(keywords), 1.0) if keywords else 0.5

                return CompetitorMatch(
                    portfolio_company=company.get("name", "Unknown"),
                    category=category,
                    matched_keywords=matched,
                    confidence=confidence,
                )

        return None

    def reload(self) -> None:
        """Reload portfolio from file."""
        self._load_portfolio()

"""
SaaSClassifier: Domain-specific classifier for B2B SaaS signals.

Classifies SaaS signals with expertise in:
- GTM motion (product-led vs sales-led)
- Vertical vs horizontal SaaS
- Target market (enterprise, mid-market, SMB)
- Technical differentiation and moat
- Investment stage alignment

Uses weighted thesis scoring based on configurable YAML rules.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from intelligence.thesis_config import load_thesis_config, ThesisConfig

logger = logging.getLogger(__name__)


@dataclass
class SaaSClassificationResult:
    """Result of SaaS company classification."""

    fit_score: int  # 1-10 scale
    category: str
    reasoning: str
    gtm_motion: str
    target_market: str
    matched_rules: List[str]
    confidence: float


class SaaSClassifier:
    """
    Domain-specific classifier for B2B SaaS vertical signals.

    Uses weighted thesis scoring to evaluate company fit against
    Press On VC's SaaS investment thesis.
    """

    # GTM motion keywords
    PLG_KEYWORDS = ["product-led", "plg", "freemium", "self-serve", "bottom-up", "free trial", "usage-based"]
    SALES_LED_KEYWORDS = ["sales-led", "enterprise sales", "outbound", "field sales", "account executive"]

    # Target market keywords
    ENTERPRISE_KEYWORDS = ["enterprise", "fortune 500", "global deployment", "f500"]
    MID_MARKET_KEYWORDS = ["mid-market", "midmarket", "growth stage"]
    SMB_KEYWORDS = ["smb", "small business", "startup-friendly", "startup"]

    # Vertical SaaS keywords
    VERTICAL_KEYWORDS = ["vertical saas", "industry-specific", "purpose-built"]
    VERTICAL_SECTORS = [
        "construction", "legal", "healthcare", "real estate", "logistics",
        "fintech", "edtech", "proptech", "legaltech", "healthtech"
    ]

    # Developer tools keywords
    DEVELOPER_KEYWORDS = ["api", "developer", "devtools", "sdk", "integration", "platform"]

    # Category mapping
    CATEGORY_KEYWORDS = {
        "vertical_saas": VERTICAL_KEYWORDS + VERTICAL_SECTORS,
        "developer_tools": DEVELOPER_KEYWORDS,
        "enterprise_saas": ENTERPRISE_KEYWORDS,
        "workflow_automation": ["workflow", "automation", "process", "orchestration"],
    }

    def __init__(self):
        """Initialize the SaaS classifier with thesis configuration."""
        self._thesis_config: Optional[ThesisConfig] = None
        logger.debug("SaaSClassifier initialized")

    @property
    def thesis_config(self) -> ThesisConfig:
        """Lazy load thesis configuration."""
        if self._thesis_config is None:
            self._thesis_config = load_thesis_config("saas")
        return self._thesis_config

    def _compute_signal_score(self, content: str, category: str) -> float:
        """
        Compute score for a specific signal category.

        Args:
            content: The text content to analyze
            category: The signal category from thesis config

        Returns:
            Score from 0.0 to 1.0 based on signal matches
        """
        if not content:
            return 0.0

        content_lower = content.lower()
        signals = self.thesis_config.positive_signals.get(category, [])

        if not signals:
            return 0.0

        matches = 0
        for signal in signals:
            # Use word boundary matching for more accurate detection
            pattern = r'\b' + re.escape(signal.lower()) + r'\b'
            if re.search(pattern, content_lower):
                matches += 1

        # Normalized score - higher base for any match, better progression
        if matches == 0:
            return 0.0
        elif matches == 1:
            return 0.6
        elif matches == 2:
            return 0.8
        else:
            return min(1.0, 0.8 + (matches - 2) * 0.05)

    def _compute_weighted_fit_score(self, content: str, signals: Dict[str, Any]) -> float:
        """
        Compute weighted fit score using thesis config.

        Args:
            content: The text content to analyze
            signals: Additional signals from the company

        Returns:
            Weighted score from 0.0 to 1.0
        """
        if not content:
            return 0.0

        weights = self.thesis_config.scoring_weights
        total_score = 0.0
        content_lower = content.lower()

        # Compute score for each weighted category
        for category, weight in weights.items():
            category_score = self._compute_signal_score(content, category)
            total_score += category_score * weight
            logger.debug(f"Category {category}: score={category_score}, weight={weight}")

        # Apply funding stage boost
        funding = signals.get("funding", "").lower()
        if funding in ["seed", "series_a", "series a", "pre-seed"]:
            total_score = min(1.0, total_score + 0.15)

        # Apply category detection boost
        detected_category = self._detect_category(content)
        if detected_category == "vertical_saas":
            total_score = min(1.0, total_score + 0.2)
        elif detected_category == "developer_tools":
            total_score = min(1.0, total_score + 0.2)

        # Apply GTM motion boost for PLG
        gtm = self._detect_gtm_motion(content)
        if gtm == "product_led":
            total_score = min(1.0, total_score + 0.1)

        # Apply negative signal penalty
        negative_count = 0
        for signal in self.thesis_config.negative_signals:
            pattern = r'\b' + re.escape(signal.lower()) + r'\b'
            if re.search(pattern, content_lower):
                negative_count += 1
                logger.debug(f"Negative signal matched: {signal}")

        # Penalty: reduce score by 15% for each negative signal, minimum 0
        if negative_count > 0:
            penalty = negative_count * 0.15
            total_score = max(0.0, total_score - penalty)

        return min(1.0, total_score)

    def _detect_gtm_motion(self, content: str) -> str:
        """
        Detect the GTM motion from content.

        Args:
            content: The text content to analyze

        Returns:
            GTM motion: "product_led", "sales_led", "mixed", or "unknown"
        """
        content_lower = content.lower()

        plg_matches = sum(1 for kw in self.PLG_KEYWORDS if kw in content_lower)
        sales_matches = sum(1 for kw in self.SALES_LED_KEYWORDS if kw in content_lower)

        if plg_matches > 0 and sales_matches > 0:
            return "mixed"
        elif plg_matches > 0:
            return "product_led"
        elif sales_matches > 0:
            return "sales_led"
        else:
            return "unknown"

    def _detect_target_market(self, content: str) -> str:
        """
        Detect the target market from content.

        Args:
            content: The text content to analyze

        Returns:
            Target market: "enterprise", "mid_market", "smb", or "unknown"
        """
        content_lower = content.lower()

        enterprise_matches = sum(1 for kw in self.ENTERPRISE_KEYWORDS if kw in content_lower)
        mid_market_matches = sum(1 for kw in self.MID_MARKET_KEYWORDS if kw in content_lower)
        smb_matches = sum(1 for kw in self.SMB_KEYWORDS if kw in content_lower)

        if enterprise_matches > mid_market_matches and enterprise_matches > smb_matches:
            return "enterprise"
        elif mid_market_matches > smb_matches:
            return "mid_market"
        elif smb_matches > 0:
            return "smb"
        else:
            return "unknown"

    def _detect_category(self, content: str) -> str:
        """
        Detect the SaaS category from content.

        Args:
            content: The text content to analyze

        Returns:
            Category string
        """
        content_lower = content.lower()

        # Check vertical SaaS first (highest priority)
        if any(kw in content_lower for kw in self.VERTICAL_KEYWORDS):
            return "vertical_saas"
        if any(sector in content_lower for sector in self.VERTICAL_SECTORS):
            return "vertical_saas"

        # Check developer tools
        dev_matches = sum(1 for kw in self.DEVELOPER_KEYWORDS if kw in content_lower)
        if dev_matches >= 2:
            return "developer_tools"

        # Check workflow automation
        if any(kw in content_lower for kw in ["workflow", "automation"]):
            return "workflow_automation"

        # Check enterprise
        if any(kw in content_lower for kw in self.ENTERPRISE_KEYWORDS):
            return "enterprise_saas"

        return "general_saas"

    def _extract_matched_rules(self, content: str) -> List[str]:
        """
        Extract matched thesis rules from content.

        Args:
            content: The text content to analyze

        Returns:
            List of matched rule names
        """
        matched = []
        content_lower = content.lower()

        for category, signals in self.thesis_config.positive_signals.items():
            for signal in signals:
                pattern = r'\b' + re.escape(signal.lower()) + r'\b'
                if re.search(pattern, content_lower):
                    if category not in matched:
                        matched.append(category)
                    break

        return matched

    def _compute_confidence(self, fit_score: float, matched_rules: List[str]) -> float:
        """
        Compute classification confidence.

        Args:
            fit_score: The computed fit score
            matched_rules: List of matched rule names

        Returns:
            Confidence from 0.0 to 1.0
        """
        # Base confidence on number of matched rules
        if len(matched_rules) == 0:
            base_confidence = 0.3
        elif len(matched_rules) <= 2:
            base_confidence = 0.5
        elif len(matched_rules) <= 4:
            base_confidence = 0.7
        else:
            base_confidence = 0.85

        # Adjust based on fit score clarity
        if fit_score >= 0.7 or fit_score <= 0.3:
            base_confidence += 0.1  # High confidence for clear signals

        return min(1.0, base_confidence)

    def classify(
        self,
        company_name: str,
        description: str,
        signals: Optional[Dict[str, Any]] = None
    ) -> SaaSClassificationResult:
        """
        Classify a company against SaaS investment thesis.

        Args:
            company_name: Name of the company
            description: Description or signal content
            signals: Optional additional signals (source, funding, etc.)

        Returns:
            SaaSClassificationResult with fit score and category
        """
        signals = signals or {}
        content = f"{company_name} {description}"

        # Compute weighted fit score
        fit_score_raw = self._compute_weighted_fit_score(content, signals)

        # Detect characteristics
        gtm_motion = self._detect_gtm_motion(content)
        target_market = self._detect_target_market(content)
        category = self._detect_category(content)

        # Extract matched rules
        matched_rules = self._extract_matched_rules(content)

        # Compute confidence
        confidence = self._compute_confidence(fit_score_raw, matched_rules)

        # Convert to 1-10 scale
        fit_score = max(1, min(10, int(fit_score_raw * 10)))

        # Generate reasoning
        reasoning = self._generate_reasoning(
            category, gtm_motion, target_market, matched_rules
        )

        logger.debug(
            f"Classification result for {company_name}: fit_score={fit_score}, "
            f"category={category}, gtm={gtm_motion}"
        )

        return SaaSClassificationResult(
            fit_score=fit_score,
            category=category,
            reasoning=reasoning,
            gtm_motion=gtm_motion,
            target_market=target_market,
            matched_rules=matched_rules,
            confidence=confidence
        )

    def _generate_reasoning(
        self,
        category: str,
        gtm_motion: str,
        target_market: str,
        matched_rules: List[str]
    ) -> str:
        """Generate reasoning text for the classification."""
        parts = []

        # Category description
        category_desc = {
            "vertical_saas": "Vertical SaaS company",
            "developer_tools": "Developer tools/API platform",
            "enterprise_saas": "Enterprise SaaS company",
            "workflow_automation": "Workflow automation platform",
            "general_saas": "SaaS company",
        }
        parts.append(category_desc.get(category, "SaaS company"))

        # GTM motion
        if gtm_motion == "product_led":
            parts.append("with product-led growth motion")
        elif gtm_motion == "sales_led":
            parts.append("with sales-led go-to-market")
        elif gtm_motion == "mixed":
            parts.append("with mixed GTM approach")

        # Target market
        if target_market != "unknown":
            parts.append(f"targeting {target_market.replace('_', ' ')} market")

        # Matched rules
        if matched_rules:
            parts.append(f"Matched thesis areas: {', '.join(matched_rules)}")

        return ". ".join(parts) + "."

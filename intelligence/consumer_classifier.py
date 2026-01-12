"""
Consumer Classifier for thesis fit scoring.

Classifies companies for consumer vertical investment thesis fit,
supporting both Premium Consumer (DTC brands) and Consumer Platforms
(marketplaces, community commerce).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ConsumerClassificationResult:
    """Result of consumer company classification."""
    fit_score: int  # 1-10
    sub_vertical: str  # premium_consumer or consumer_platforms
    category: str
    reasoning: str
    brand_positioning: str
    channel_strategy: str
    matched_rules: List[str]
    confidence: float


class ConsumerClassifier:
    """Classifies companies for consumer vertical investment thesis fit."""

    def __init__(self, thesis_path: Optional[Path] = None):
        if thesis_path is None:
            thesis_path = Path(__file__).parent.parent / "config" / "consumer_thesis_rules.yaml"

        with open(thesis_path) as f:
            self.thesis_config = yaml.safe_load(f)

        self.scoring_weights = self.thesis_config.get("scoring_weights", {})
        self.positive_signals = self.thesis_config.get("positive_signals", {})
        self.negative_signals = self.thesis_config.get("negative_signals", [])
        logger.debug(f"Loaded consumer thesis with {len(self.positive_signals)} signal categories")

    def classify(
        self,
        company_name: str,
        description: str,
        signals: Optional[Dict[str, Any]] = None
    ) -> ConsumerClassificationResult:
        """Classify a company against consumer investment thesis."""
        signals = signals or {}
        content = f"{company_name} {description}".lower()

        total_score = 0.0
        matched_rules: List[str] = []
        brand_positioning = "unknown"
        channel_strategy = "unknown"
        sub_vertical = "premium_consumer"  # default
        category = "general_consumer"

        # Brand positioning detection
        brand_keywords = self.positive_signals.get("brand_positioning", [])
        brand_weight = self.scoring_weights.get("brand_positioning", 0.25)
        brand_score = 0.0
        for kw in brand_keywords:
            if kw.lower() in content:
                brand_score = max(brand_score, 0.8)
                matched_rules.append("brand_positioning")
                if kw in ["premium", "luxury", "artisan", "craft", "boutique"]:
                    brand_positioning = "premium"
                    brand_score = 1.0
                break
        total_score += brand_score * brand_weight

        # Channel strategy detection
        channel_keywords = self.positive_signals.get("channel_strategy", [])
        channel_weight = self.scoring_weights.get("channel_strategy", 0.20)
        channel_score = 0.0
        for kw in channel_keywords:
            if kw.lower() in content:
                channel_score = max(channel_score, 0.8)
                matched_rules.append("channel_strategy")
                if kw in ["direct-to-consumer", "dtc", "d2c"]:
                    channel_strategy = "dtc"
                    channel_score = 1.0
                elif kw == "omnichannel":
                    channel_strategy = "omnichannel"
                    channel_score = 0.8
                break
        total_score += channel_score * channel_weight

        # Product category detection
        category_keywords = self.positive_signals.get("product_category", [])
        category_weight = self.scoring_weights.get("product_category", 0.20)
        category_score = 0.0
        for kw in category_keywords:
            if kw.lower() in content:
                category = f"consumer_{kw}"
                category_score = 0.9
                matched_rules.append("product_category")
                break
        total_score += category_score * category_weight

        # Platform model detection
        platform_keywords = self.positive_signals.get("platform_model", [])
        platform_weight = self.scoring_weights.get("platform_model", 0.20)
        platform_score = 0.0
        for kw in platform_keywords:
            if kw.lower() in content:
                platform_score = max(platform_score, 0.8)
                matched_rules.append("platform_model")
                if kw in ["marketplace", "two-sided", "platform"]:
                    sub_vertical = "consumer_platforms"
                    category = "marketplace"
                    platform_score = 0.9
                elif kw in ["community", "social commerce", "creator economy"]:
                    sub_vertical = "consumer_platforms"
                    category = "community_commerce"
                    platform_score = 1.0
                break
        total_score += platform_score * platform_weight

        # Traction signals
        traction_keywords = self.positive_signals.get("traction", [])
        stage_weight = self.scoring_weights.get("stage", 0.15)
        traction_score = 0.0
        for kw in traction_keywords:
            if kw.lower() in content:
                traction_score = 0.7
                matched_rules.append("traction")
                break

        # Funding stage filter
        funding = signals.get("funding", "").lower()
        if funding in ["seed", "pre-seed"]:
            traction_score = max(traction_score, 1.0)
        elif funding in ["series_a", "series a"]:
            traction_score = max(traction_score, 0.9)
        total_score += traction_score * stage_weight

        # Negative signals penalty
        for neg in self.negative_signals:
            if neg.lower() in content:
                total_score *= 0.7
                break

        # Convert to 1-10 scale
        fit_score = max(1, min(10, int(total_score * 10)))

        reasoning = f"Consumer company ({sub_vertical}) with {brand_positioning} positioning"
        if channel_strategy != "unknown":
            reasoning += f" and {channel_strategy} channel strategy"
        if matched_rules:
            reasoning += f". Matched rules: {', '.join(set(matched_rules))}"

        logger.debug(f"Classified {company_name}: score={fit_score}, sub_vertical={sub_vertical}")

        return ConsumerClassificationResult(
            fit_score=fit_score,
            sub_vertical=sub_vertical,
            category=category,
            reasoning=reasoning,
            brand_positioning=brand_positioning,
            channel_strategy=channel_strategy,
            matched_rules=list(set(matched_rules)),
            confidence=total_score
        )

"""
News Digest Generator

Generates daily summaries of news signals using Gemini LLM.
Groups signals by thesis category and provides actionable insights.

Usage:
    from utils.news_digest import NewsDigestGenerator, DigestConfig

    generator = NewsDigestGenerator()
    digest = await generator.generate(signals)

    # Output formats
    print(format_digest_markdown(digest))
    slack_payload = format_digest_slack(digest)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

# Gemini LLM (new google-genai SDK, lazy import)
try:
    from google import genai as _genai_module
    GENAI_AVAILABLE = True
except ImportError:
    _genai_module = None
    GENAI_AVAILABLE = False

from verification.verification_gate_v2 import Signal

logger = logging.getLogger(__name__)

# API key from environment
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Thesis category keywords for classification
CATEGORY_KEYWORDS = {
    "cpg": [
        "food", "beverage", "snack", "meal", "drink", "grocery",
        "beauty", "skincare", "cosmetic", "makeup", "haircare",
        "personal care", "household", "cleaning", "cpg", "d2c",
        "consumer packaged", "brand", "retail",
    ],
    "health_tech": [
        "fitness", "wellness", "health", "workout", "exercise", "gym",
        "mental health", "therapy", "meditation", "mindfulness",
        "nutrition", "diet", "weight", "sleep", "wearable",
        "telehealth", "healthcare", "medical", "supplement", "vitamin",
    ],
    "travel": [
        "travel", "hotel", "booking", "flight", "vacation", "trip",
        "hospitality", "restaurant", "dining", "tourism", "experience",
        "adventure", "destination", "lodging", "airbnb",
    ],
    "marketplace": [
        "marketplace", "platform", "two-sided", "peer-to-peer", "p2p",
        "matching", "exchange", "rental", "subscription", "membership",
        "commerce", "ecommerce", "e-commerce", "shop", "buy", "sell",
    ],
}

# Signal type importance weights for sorting
SIGNAL_TYPE_WEIGHTS = {
    "funding_announcement": 1.0,
    "product_launch": 0.8,
    "news_mention": 0.5,
    "press_release": 0.4,
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class DigestConfig:
    """Configuration for digest generation."""

    max_items_per_section: int = 5
    include_urls: bool = True
    include_confidence: bool = True
    use_llm_summary: bool = True
    llm_model: str = "gemini-1.5-flash"


@dataclass
class DigestSection:
    """A section of the digest (one thesis category)."""

    category: str
    title: str
    items: List[Dict[str, Any]]
    summary: str


@dataclass
class NewsDigest:
    """Complete news digest."""

    generated_at: datetime
    period_start: datetime
    period_end: datetime
    total_signals: int
    sections: List[DigestSection]
    summary: str


# =============================================================================
# NEWS DIGEST GENERATOR
# =============================================================================

class NewsDigestGenerator:
    """
    Generates news digests from signals.

    Groups signals by thesis category and uses Gemini LLM
    to generate summaries and insights.
    """

    def __init__(self, config: Optional[DigestConfig] = None):
        """
        Initialize the digest generator.

        Args:
            config: Optional configuration (defaults to DigestConfig())
        """
        self.config = config or DigestConfig()
        self._client = None

        # Check availability at init time for early warnings
        if not GENAI_AVAILABLE:
            logger.warning("google-genai not installed, LLM summaries disabled")
        elif not GOOGLE_API_KEY:
            logger.warning("GOOGLE_API_KEY not set, LLM summaries disabled")

    @property
    def _model_available(self) -> bool:
        """Check if LLM model is available."""
        return GENAI_AVAILABLE and bool(GOOGLE_API_KEY)

    @property
    def client(self):
        """Lazy-load Gemini client (matches llm_classifier.py pattern)."""
        if self._client is None:
            if not GENAI_AVAILABLE:
                return None
            api_key = GOOGLE_API_KEY
            if not api_key:
                return None
            self._client = _genai_module.Client(api_key=api_key)
        return self._client

    async def generate(
        self,
        signals: List[Signal],
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> NewsDigest:
        """
        Generate a news digest from signals.

        Args:
            signals: List of news signals to digest
            period_start: Start of reporting period (default: 24h ago)
            period_end: End of reporting period (default: now)

        Returns:
            NewsDigest with categorized sections and summaries
        """
        now = datetime.now(timezone.utc)
        period_start = period_start or (now - timedelta(days=1))
        period_end = period_end or now

        # Filter to news signals only
        news_signals = [
            s for s in signals
            if s.signal_type in ["news_mention", "funding_announcement", "product_launch", "press_release"]
        ]

        if not news_signals:
            return NewsDigest(
                generated_at=now,
                period_start=period_start,
                period_end=period_end,
                total_signals=0,
                sections=[],
                summary="No news signals in this period.",
            )

        # Group and sort
        grouped = self._group_by_category(news_signals)
        sections = []

        for category, cat_signals in grouped.items():
            sorted_signals = self._sort_by_importance(cat_signals)
            limited_signals = sorted_signals[:self.config.max_items_per_section]

            # Convert to items
            items = [self._signal_to_item(s) for s in limited_signals]

            # Generate section summary
            if self.config.use_llm_summary and self._model_available:
                section_summary = await self._summarize_section(category, limited_signals)
            else:
                section_summary = f"{len(items)} {category.replace('_', ' ')} signals"

            sections.append(DigestSection(
                category=category,
                title=self._get_section_title(category),
                items=items,
                summary=section_summary,
            ))

        # Sort sections by importance (most items first)
        sections.sort(key=lambda s: len(s.items), reverse=True)

        # Generate overall summary
        if self.config.use_llm_summary and self._model_available:
            overall_summary = await self._summarize_with_llm(news_signals)
        else:
            overall_summary = self._fallback_summary(news_signals)

        return NewsDigest(
            generated_at=now,
            period_start=period_start,
            period_end=period_end,
            total_signals=len(news_signals),
            sections=sections,
            summary=overall_summary,
        )

    def _categorize_signal(self, signal: Signal) -> str:
        """
        Categorize a signal by thesis category.

        Args:
            signal: Signal to categorize

        Returns:
            Category string (cpg, health_tech, travel, marketplace, or other)
        """
        # Combine title and description for matching
        text = ""
        if signal.raw_data:
            text = (
                signal.raw_data.get("title", "") + " " +
                signal.raw_data.get("description", "")
            ).lower()

        # Score each category
        scores = {}
        for category, keywords in CATEGORY_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                scores[category] = score

        if scores:
            return max(scores, key=scores.get)
        return "other"

    def _group_by_category(self, signals: List[Signal]) -> Dict[str, List[Signal]]:
        """
        Group signals by thesis category.

        Args:
            signals: Signals to group

        Returns:
            Dict mapping category to list of signals
        """
        grouped: Dict[str, List[Signal]] = {}

        for signal in signals:
            category = self._categorize_signal(signal)
            if category not in grouped:
                grouped[category] = []
            grouped[category].append(signal)

        return grouped

    def _sort_by_importance(self, signals: List[Signal]) -> List[Signal]:
        """
        Sort signals by importance (confidence + signal type weight).

        Args:
            signals: Signals to sort

        Returns:
            Sorted list (most important first)
        """
        def importance_score(signal: Signal) -> float:
            type_weight = SIGNAL_TYPE_WEIGHTS.get(signal.signal_type, 0.3)
            return signal.confidence * type_weight

        return sorted(signals, key=importance_score, reverse=True)

    def _signal_to_item(self, signal: Signal) -> Dict[str, Any]:
        """Convert signal to digest item dict."""
        raw = signal.raw_data or {}
        return {
            "title": raw.get("title", "Unknown"),
            "company": raw.get("company_name", self._extract_company(raw)),
            "source": raw.get("source", "Unknown"),
            "url": raw.get("url", ""),
            "signal_type": signal.signal_type,
            "confidence": signal.confidence,
            "detected_at": signal.detected_at.isoformat() if signal.detected_at else None,
        }

    def _extract_company(self, raw_data: Dict) -> str:
        """Extract company name from raw data."""
        # Try various fields
        for field in ["company_name", "company", "name", "organization"]:
            if field in raw_data and raw_data[field]:
                return raw_data[field]

        # Extract from title (first capitalized word)
        title = raw_data.get("title", "")
        words = title.split()
        for word in words:
            if word and word[0].isupper() and word.isalpha():
                return word

        return "Unknown"

    def _get_section_title(self, category: str) -> str:
        """Get human-readable section title."""
        titles = {
            "cpg": "Consumer CPG & Beauty",
            "health_tech": "Health & Wellness Tech",
            "travel": "Travel & Hospitality",
            "marketplace": "Consumer Marketplaces",
            "other": "Other Consumer",
        }
        return titles.get(category, category.replace("_", " ").title())

    async def _summarize_section(self, category: str, signals: List[Signal]) -> str:
        """Generate summary for a section using LLM."""
        if not self.client:
            return f"{len(signals)} signals in {category}"

        titles = [s.raw_data.get("title", "") for s in signals if s.raw_data]
        titles_text = "\n".join(f"- {t}" for t in titles[:5])

        prompt = f"""Summarize these {category.replace('_', ' ')} news headlines in 1-2 sentences.
Focus on key themes and trends for a VC investor.

Headlines:
{titles_text}

Summary:"""

        try:
            response = self.client.models.generate_content(
                model=self.config.llm_model,
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e:
            logger.warning(f"LLM section summary failed: {e}")
            return f"{len(signals)} {category.replace('_', ' ')} signals"

    async def _summarize_with_llm(self, signals: List[Signal]) -> str:
        """
        Generate overall summary using Gemini LLM.

        Args:
            signals: All news signals

        Returns:
            Summary string
        """
        if not self.client:
            return self._fallback_summary(signals)

        # Build context
        headlines = []
        for s in signals[:10]:  # Limit context
            if s.raw_data:
                title = s.raw_data.get("title", "")
                source = s.raw_data.get("source", "")
                headlines.append(f"- [{s.signal_type}] {title} ({source})")

        headlines_text = "\n".join(headlines)

        prompt = f"""You are a VC analyst at Press On Ventures, focused on consumer companies
(CPG, health tech, travel, marketplaces). Summarize today's news in 2-3 sentences.

Highlight:
1. Most significant funding or launch
2. Key trend or pattern across news
3. Any actionable insight for deal sourcing

Today's headlines:
{headlines_text}

Summary:"""

        try:
            response = self.client.models.generate_content(
                model=self.config.llm_model,
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e:
            logger.warning(f"LLM summary failed: {e}")
            return self._fallback_summary(signals)

    def _fallback_summary(self, signals: List[Signal]) -> str:
        """Generate fallback summary without LLM."""
        if not signals:
            return "No news signals in this period."

        # Count by type
        type_counts = {}
        for s in signals:
            type_counts[s.signal_type] = type_counts.get(s.signal_type, 0) + 1

        parts = []
        if type_counts.get("funding_announcement", 0) > 0:
            parts.append(f"{type_counts['funding_announcement']} funding announcements")
        if type_counts.get("product_launch", 0) > 0:
            parts.append(f"{type_counts['product_launch']} product launches")
        if type_counts.get("news_mention", 0) > 0:
            parts.append(f"{type_counts['news_mention']} news mentions")

        return f"Today's digest: {', '.join(parts)}." if parts else f"{len(signals)} news signals."


# =============================================================================
# OUTPUT FORMATTERS
# =============================================================================

def format_digest_markdown(
    digest: NewsDigest,
    include_urls: bool = True,
    include_confidence: bool = False,
) -> str:
    """
    Format digest as markdown.

    Args:
        digest: NewsDigest to format
        include_urls: Include article URLs
        include_confidence: Include confidence scores

    Returns:
        Markdown string
    """
    lines = []

    # Header
    lines.append(f"# News Digest - {digest.generated_at.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append(f"*{digest.total_signals} signals from {digest.period_start.strftime('%m/%d')} to {digest.period_end.strftime('%m/%d')}*")
    lines.append("")

    # Overall summary
    lines.append("## Summary")
    lines.append("")
    lines.append(digest.summary)
    lines.append("")

    # Sections
    for section in digest.sections:
        lines.append(f"## {section.title}")
        lines.append("")
        lines.append(f"*{section.summary}*")
        lines.append("")

        for item in section.items:
            title = item.get("title", "Unknown")
            company = item.get("company", "")
            source = item.get("source", "")
            url = item.get("url", "")
            confidence = item.get("confidence", 0)

            # Format item
            if include_urls and url:
                line = f"- **[{title}]({url})**"
            else:
                line = f"- **{title}**"

            details = []
            if company:
                details.append(company)
            if source:
                details.append(source)
            if include_confidence:
                details.append(f"{confidence:.0%}")

            if details:
                line += f" ({', '.join(details)})"

            lines.append(line)

        lines.append("")

    return "\n".join(lines)


def format_digest_slack(digest: NewsDigest) -> Dict[str, Any]:
    """
    Format digest as Slack message payload.

    Args:
        digest: NewsDigest to format

    Returns:
        Slack message dict with blocks
    """
    blocks = []

    # Header
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"News Digest - {digest.generated_at.strftime('%Y-%m-%d')}",
        }
    })

    # Summary
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*{digest.total_signals} signals*\n\n{digest.summary}",
        }
    })

    blocks.append({"type": "divider"})

    # Sections
    for section in digest.sections:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{section.title}*\n_{section.summary}_",
            }
        })

        # Items as bullet list
        items_text = "\n".join(
            f"• <{item.get('url', '#')}|{item.get('title', 'Unknown')[:50]}>"
            for item in section.items[:3]
        )

        if items_text:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": items_text,
                }
            })

    return {"blocks": blocks}

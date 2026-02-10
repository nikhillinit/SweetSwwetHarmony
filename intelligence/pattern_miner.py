"""Pattern Miner v1 — Heuristic template extraction from TP signals.

Extracts query templates by analyzing True Positive labels:
1. Query signal_quality_metrics JOIN signals WHERE human_label='TP'
2. Group by source_api → collector distribution
3. Extract top keywords from descriptions (stop-word filtered)
4. Group by thesis category → category concentration
5. Produce QueryTemplate objects for query_generator.py

Bootstrap mode: When TP count < 20, accepts ManualSeed list.
"""

from __future__ import annotations

import re
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

# Stop words for keyword extraction
STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "that", "this", "was", "are",
    "be", "has", "have", "had", "not", "no", "we", "our", "they", "their",
    "its", "as", "if", "so", "up", "out", "about", "into", "over", "just",
    "do", "did", "get", "got", "can", "will", "new", "one", "two", "all",
    "also", "than", "more", "been", "would", "could", "should", "may",
    "some", "these", "those", "each", "which", "when", "what", "who", "how",
    "inc", "llc", "ltd", "co", "company", "startup", "com", "www", "http",
    "https", "based", "using", "like", "first", "next",
})

# Thesis-relevant categories
THESIS_CATEGORIES = {
    "cpg": ["food", "beverage", "beauty", "skincare", "cosmetics", "snack", "drink"],
    "health_tech": ["health", "fitness", "wellness", "mental", "therapy", "nutrition", "supplement"],
    "travel": ["travel", "hospitality", "hotel", "booking", "tourism", "restaurant"],
    "marketplace": ["marketplace", "platform", "ecommerce", "shopping", "retail", "consumer"],
}

# Minimum TP count before bootstrap mode kicks in
MIN_TP_FOR_TEMPLATES = 20


@dataclass
class QueryTemplate:
    """Template for generating search queries."""
    collector: str
    keywords: List[str]
    categories: List[str]
    priority: int = 1  # 1=high, 2=medium, 3=low
    template_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "collector": self.collector,
            "keywords": self.keywords,
            "categories": self.categories,
            "priority": self.priority,
            "template_version": self.template_version,
        }


@dataclass
class ManualSeed:
    """Manual seed for bootstrap mode."""
    company_name: str
    domain: Optional[str] = None
    category: Optional[str] = None
    reason: Optional[str] = None


def extract_keywords(text: str, top_n: int = 10) -> List[str]:
    """Extract top keywords from text, filtering stop words."""
    if not text:
        return []

    # Tokenize: split on non-alphanumeric, lowercase
    words = re.findall(r"[a-z]+", text.lower())
    # Filter stop words and short words
    words = [w for w in words if w not in STOP_WORDS and len(w) >= 3]
    # Count and return top N
    counter = Counter(words)
    return [word for word, _ in counter.most_common(top_n)]


def categorize_keywords(keywords: List[str]) -> List[str]:
    """Map keywords to thesis categories."""
    matched = set()
    for category, category_words in THESIS_CATEGORIES.items():
        for kw in keywords:
            if kw in category_words:
                matched.add(category)
    return sorted(matched) if matched else ["general"]


def templates_from_seeds(seeds: List[ManualSeed]) -> List[QueryTemplate]:
    """Generate templates from manual seeds (bootstrap mode).

    Groups seeds by category and extracts keywords from company names.
    """
    if not seeds:
        return []

    # Group by category
    by_category: Dict[str, List[ManualSeed]] = {}
    for seed in seeds:
        cat = seed.category or "general"
        by_category.setdefault(cat, []).append(seed)

    templates = []
    for category, cat_seeds in by_category.items():
        # Extract keywords from company names and reasons
        all_text = " ".join(
            filter(None, [s.company_name for s in cat_seeds] + [s.reason for s in cat_seeds])
        )
        keywords = extract_keywords(all_text, top_n=8)

        # Default to thesis keywords for the category
        if not keywords:
            keywords = THESIS_CATEGORIES.get(category, ["consumer"])

        templates.append(QueryTemplate(
            collector="github",
            keywords=keywords,
            categories=[category],
            priority=2,
            template_version=1,
        ))

    return templates


async def mine_patterns(
    store: "SignalStore",
    manual_seeds: Optional[List[ManualSeed]] = None,
    top_keywords: int = 10,
) -> List[QueryTemplate]:
    """Mine patterns from TP labels to produce query templates.

    Falls back to bootstrap mode if TP count < MIN_TP_FOR_TEMPLATES.
    """
    db = store._db

    # Count TPs
    cursor = await db.execute(
        "SELECT COUNT(*) FROM signal_quality_metrics WHERE human_label = 'TP'"
    )
    tp_count = (await cursor.fetchone())[0]

    if tp_count < MIN_TP_FOR_TEMPLATES:
        logger.info("TP count %d < %d, using bootstrap mode", tp_count, MIN_TP_FOR_TEMPLATES)
        if manual_seeds:
            return templates_from_seeds(manual_seeds)
        return []

    # Fetch TP signals with metadata
    cursor = await db.execute(
        """SELECT s.source_api, s.company_name, s.raw_data, s.canonical_key
           FROM signal_quality_metrics sqm
           JOIN signals s ON sqm.signal_id = s.id
           WHERE sqm.human_label = 'TP'"""
    )
    rows = await cursor.fetchall()

    # Group by collector
    by_collector: Dict[str, List[dict]] = {}
    for row in rows:
        source_api = row[0]
        by_collector.setdefault(source_api, []).append({
            "company_name": row[1],
            "raw_data": row[2],
            "canonical_key": row[3],
        })

    templates = []
    for collector, signals in by_collector.items():
        # Extract keywords from all company names + raw_data descriptions
        all_text_parts = []
        for sig in signals:
            if sig["company_name"]:
                all_text_parts.append(sig["company_name"])
            try:
                rd = sig["raw_data"]
                if isinstance(rd, str):
                    import json
                    rd = json.loads(rd)
                if isinstance(rd, dict):
                    desc = rd.get("description", "")
                    if desc:
                        all_text_parts.append(desc)
            except (ValueError, TypeError):
                pass

        all_text = " ".join(all_text_parts)
        keywords = extract_keywords(all_text, top_n=top_keywords)
        categories = categorize_keywords(keywords)

        if keywords:
            templates.append(QueryTemplate(
                collector=collector,
                keywords=keywords,
                categories=categories,
                priority=1,
                template_version=1,
            ))

    # Sort by priority
    templates.sort(key=lambda t: t.priority)
    return templates

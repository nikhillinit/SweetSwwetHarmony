"""Query Generator — per-collector query formatters with negative keyword protection.

Transforms QueryTemplates into executable HunterQuery objects with:
- Per-collector format (GitHub search, Algolia, GNews)
- Negative keyword exclusion
- Protected vocabulary enforcement
- Dedup via inputs_hash
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from intelligence.pattern_miner import QueryTemplate

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

# Protected vocabulary: thesis-defining words that can NEVER be negative keywords
PROTECTED_VOCABULARY = frozenset({
    "health", "fitness", "food", "beauty", "travel", "marketplace",
    "wellness", "nutrition", "beverage", "skincare", "hospitality",
    "consumer", "cpg", "supplement", "restaurant", "booking",
    "shopping", "retail", "ecommerce",
})

# Supported collectors for query generation
SUPPORTED_COLLECTORS = {"github", "hacker_news", "news_api"}


@dataclass
class HunterQuery:
    """Executable query for a hunter run."""
    collector: str
    query_text: str
    query_type: str = "pattern"
    source_pattern: Optional[str] = None
    inputs_hash: str = ""
    timeout_seconds: int = 30
    estimated_cost: float = 1.0
    negative_keywords: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.inputs_hash:
            self.inputs_hash = compute_inputs_hash(
                self.collector, self.query_text, ""
            )


def compute_inputs_hash(collector: str, query_text: str, date_range: str) -> str:
    """SHA256[:16] hash of query inputs for dedup."""
    seed = f"{collector}:{query_text}:{date_range}"
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def filter_negative_keywords(
    negative_keywords: List[Dict[str, Any]],
    collector: Optional[str] = None,
    category: Optional[str] = None,
) -> Set[str]:
    """Filter negative keywords by scope, excluding protected vocabulary."""
    result = set()
    for nk in negative_keywords:
        kw = nk["keyword"].lower()
        # Never exclude protected vocabulary
        if kw in PROTECTED_VOCABULARY:
            logger.debug("Skipping protected keyword: %s", kw)
            continue
        # Skip review_required keywords
        if nk.get("review_required"):
            continue
        # Scope filtering
        nk_collector = nk.get("collector")
        nk_category = nk.get("category")
        if nk_collector and nk_collector != collector:
            continue
        if nk_category and nk_category != category:
            continue
        result.add(kw)
    return result


def format_github_query(
    keywords: List[str],
    neg_keywords: Set[str],
    category: Optional[str] = None,
    days_back: int = 30,
) -> str:
    """Format GitHub search query string."""
    parts = []
    # Positive keywords
    parts.append(" ".join(keywords[:5]))  # Limit to 5 keywords
    # Negative keywords
    for nk in sorted(neg_keywords)[:10]:  # Limit negative keywords
        parts.append(f"-{nk}")
    # Topic filter from category
    if category and category != "general":
        topic_map = {
            "cpg": "food",
            "health_tech": "health",
            "travel": "travel",
            "marketplace": "marketplace",
        }
        topic = topic_map.get(category)
        if topic:
            parts.append(f"topic:{topic}")
    # Recency filter
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    parts.append(f"stars:>10 created:>{cutoff}")
    return " ".join(parts)


def format_hacker_news_query(
    keywords: List[str],
    neg_keywords: Set[str],
) -> str:
    """Format Hacker News Algolia search query.

    HN Algolia doesn't support negation in query, so negatives are post-filter.
    """
    query = " ".join(keywords[:5])
    return f"search?query={query}&tags=show_hn"


def format_news_api_query(
    keywords: List[str],
    neg_keywords: Set[str],
) -> str:
    """Format GNews search query.

    GNews has limited query syntax; negatives are post-filter.
    """
    query = " ".join(keywords[:5])
    return f"search?q={query}"


def generate_queries(
    templates: List[QueryTemplate],
    negative_keywords: List[Dict[str, Any]],
    existing_hashes: Optional[Set[str]] = None,
    days_back: int = 30,
) -> List[HunterQuery]:
    """Generate executable queries from templates with negative keyword protection.

    Args:
        templates: QueryTemplates from pattern_miner
        negative_keywords: Active negative keywords from store
        existing_hashes: Set of inputs_hash values to skip (dedup)
        days_back: Days of history to search

    Returns:
        List of HunterQuery objects ready for execution.
    """
    if existing_hashes is None:
        existing_hashes = set()
    seen_hashes = set(existing_hashes)

    queries = []

    for template in templates:
        collector = template.collector
        if collector not in SUPPORTED_COLLECTORS:
            logger.warning("Unsupported collector: %s", collector)
            continue

        # Get filtered negative keywords for this scope
        category = template.categories[0] if template.categories else None
        neg_kws = filter_negative_keywords(
            negative_keywords, collector=collector, category=category,
        )

        # Format query per collector
        if collector == "github":
            query_text = format_github_query(
                template.keywords, neg_kws, category=category, days_back=days_back,
            )
        elif collector == "hacker_news":
            query_text = format_hacker_news_query(template.keywords, neg_kws)
        elif collector == "news_api":
            query_text = format_news_api_query(template.keywords, neg_kws)
        else:
            continue

        # Compute inputs hash for dedup
        inputs_hash = compute_inputs_hash(collector, query_text, "")
        if inputs_hash in seen_hashes:
            logger.debug("Skipping duplicate query: %s", inputs_hash)
            continue
        seen_hashes.add(inputs_hash)

        # query_type must be one of: 'pattern', 'bootstrap', 'manual'
        # Bootstrap templates (priority=2) get 'bootstrap'; mined templates get 'pattern'
        if template.priority == 2:
            query_type = "bootstrap"
        else:
            query_type = "pattern"

        queries.append(HunterQuery(
            collector=collector,
            query_text=query_text,
            query_type=query_type,
            source_pattern=",".join(template.keywords[:3]),
            inputs_hash=inputs_hash,
            negative_keywords=sorted(neg_kws),
        ))

    return queries

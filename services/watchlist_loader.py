"""
Watchlist loader for Notion-configured watchlists.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from connectors.notion_transport import NotionTransport

logger = logging.getLogger(__name__)


@dataclass
class Watchlist:
    """Watchlist definition."""
    name: str
    include_keywords: List[str]
    exclude_keywords: List[str]
    min_score: Optional[float]
    status: str

    def matches(self, text: str, score: float) -> bool:
        if self.min_score is not None and score < self.min_score:
            return False
        if self.include_keywords:
            if not any(keyword in text for keyword in self.include_keywords):
                return False
        if self.exclude_keywords:
            if any(keyword in text for keyword in self.exclude_keywords):
                return False
        return True


class WatchlistLoader:
    """Load active watchlists from Notion with TTL caching."""

    PROP_NAME = "Name"
    PROP_STATUS = "Status"
    PROP_INCLUDE = "Include Keywords"
    PROP_EXCLUDE = "Exclude Keywords"
    PROP_MIN_SCORE = "Min Score"

    def __init__(
        self,
        database_id: Optional[str],
        transport: Optional[NotionTransport],
        cache_ttl_seconds: int = 600,
    ) -> None:
        self.database_id = database_id
        self.transport = transport
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: Optional[Tuple[List[Watchlist], float]] = None

    async def get_watchlists(self, force_refresh: bool = False) -> List[Watchlist]:
        if self._cache and not force_refresh:
            watchlists, fetched_at = self._cache
            if (time.time() - fetched_at) < self.cache_ttl_seconds:
                return watchlists

        if not self.transport or not self.database_id:
            return []

        try:
            response = await self.transport.post(
                f"/databases/{self.database_id}/query",
                json={
                    "filter": {
                        "property": self.PROP_STATUS,
                        "select": {"equals": "Active"},
                    }
                },
            )
        except Exception as exc:
            logger.warning(f"Failed to load watchlists: {exc}")
            return []

        results = response.get("results", [])
        watchlists: List[Watchlist] = []
        for page in results:
            props = page.get("properties", {})
            name = self._extract_title(props.get(self.PROP_NAME, {}))
            if not name:
                continue
            include_keywords = self._extract_keywords(props.get(self.PROP_INCLUDE, {}))
            exclude_keywords = self._extract_keywords(props.get(self.PROP_EXCLUDE, {}))
            min_score = self._extract_number(props.get(self.PROP_MIN_SCORE, {}))
            status = self._extract_select(props.get(self.PROP_STATUS, {})) or "Active"
            watchlists.append(
                Watchlist(
                    name=name,
                    include_keywords=include_keywords,
                    exclude_keywords=exclude_keywords,
                    min_score=min_score,
                    status=status,
                )
            )

        self._cache = (watchlists, time.time())
        return watchlists

    @staticmethod
    def _extract_title(prop: Dict[str, Any]) -> str:
        title = prop.get("title", [])
        if title:
            return title[0].get("plain_text", "") or title[0].get("text", {}).get("content", "")
        return ""

    @staticmethod
    def _extract_select(prop: Dict[str, Any]) -> Optional[str]:
        select = prop.get("select")
        if select:
            return select.get("name")
        return None

    @staticmethod
    def _extract_number(prop: Dict[str, Any]) -> Optional[float]:
        number = prop.get("number")
        return float(number) if number is not None else None

    @staticmethod
    def _extract_keywords(prop: Dict[str, Any]) -> List[str]:
        prop_type = prop.get("type")
        keywords: List[str] = []
        if prop_type == "multi_select":
            for option in prop.get("multi_select", []) or []:
                name = option.get("name")
                if name:
                    keywords.append(name.strip().lower())
        elif prop_type == "rich_text":
            text = "".join(
                item.get("plain_text", "") or item.get("text", {}).get("content", "")
                for item in prop.get("rich_text", [])
            )
            keywords.extend(_split_keywords(text))
        elif prop_type == "title":
            text = "".join(
                item.get("plain_text", "") or item.get("text", {}).get("content", "")
                for item in prop.get("title", [])
            )
            keywords.extend(_split_keywords(text))
        return [kw for kw in keywords if kw]


def _split_keywords(text: str) -> List[str]:
    if not text:
        return []
    raw_parts = []
    for chunk in text.replace(";", ",").split(","):
        raw_parts.extend(chunk.splitlines())
    return [part.strip().lower() for part in raw_parts if part.strip()]

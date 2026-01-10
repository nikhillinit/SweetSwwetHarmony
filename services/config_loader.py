"""
Notion config loader for Config Releases.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from connectors.notion_transport import NotionTransport

logger = logging.getLogger(__name__)


@dataclass
class ActiveConfig:
    """Resolved active configuration from Notion (or fallback)."""
    config_type: str
    human_version: str
    content_text: str
    content_hash: str
    notion_page_id: Optional[str]
    fetched_at: datetime


class ConfigLoader:
    """
    Load Active configs from a Notion Config Releases database with TTL caching.
    """

    PROP_CONFIG_TYPE = "Config Type"
    PROP_STATUS = "Status"
    PROP_HUMAN_VERSION = "Human Version"
    PROP_CONTENT = "Content"

    def __init__(
        self,
        database_id: Optional[str],
        transport: Optional[NotionTransport] = None,
        cache_ttl_seconds: int = 600,
        snapshot_store: Optional[Any] = None,
    ) -> None:
        self.database_id = database_id
        self.transport = transport
        self.cache_ttl_seconds = cache_ttl_seconds
        self._snapshot_store = snapshot_store
        self._cache: Dict[str, Tuple[ActiveConfig, float]] = {}

    def clear_cache(self) -> None:
        """Clear cached configs (forces reload on next request)."""
        self._cache.clear()

    async def get_active_config(
        self,
        config_type: str,
        fallback_text: Optional[str] = None,
        fallback_version: Optional[str] = None,
        force_refresh: bool = False,
    ) -> ActiveConfig:
        """Get the active config for a type, with TTL caching."""
        cached = self._cache.get(config_type)
        if cached and not force_refresh:
            config, fetched_at = cached
            if (time.time() - fetched_at) < self.cache_ttl_seconds:
                return config

        config = await self._fetch_active_config(
            config_type=config_type,
            fallback_text=fallback_text,
            fallback_version=fallback_version,
        )
        self._cache[config_type] = (config, time.time())
        return config

    async def _fetch_active_config(
        self,
        config_type: str,
        fallback_text: Optional[str],
        fallback_version: Optional[str],
    ) -> ActiveConfig:
        if not self.transport or not self.database_id:
            if fallback_text is None:
                raise RuntimeError("ConfigLoader missing Notion transport or database_id")
            fallback = self._build_fallback(config_type, fallback_text, fallback_version)
            await self._store_snapshot(fallback)
            return fallback

        try:
            response = await self.transport.post(
                f"/databases/{self.database_id}/query",
                json={
                    "filter": {
                        "and": [
                            {
                                "property": self.PROP_CONFIG_TYPE,
                                "select": {"equals": config_type},
                            },
                            {
                                "property": self.PROP_STATUS,
                                "select": {"equals": "Active"},
                            },
                        ]
                    }
                },
            )
        except Exception as exc:
            if fallback_text is None:
                raise
            logger.warning(f"Config fetch failed for {config_type}; using fallback: {exc}")
            fallback = self._build_fallback(config_type, fallback_text, fallback_version)
            await self._store_snapshot(fallback)
            return fallback

        results = response.get("results", [])
        if not results:
            if fallback_text is None:
                raise RuntimeError(f"No Active config found for '{config_type}'")
            fallback = self._build_fallback(config_type, fallback_text, fallback_version)
            await self._store_snapshot(fallback)
            return fallback

        if len(results) > 1:
            raise RuntimeError(f"Multiple Active configs found for '{config_type}'")

        page = results[0]
        props = page.get("properties", {})

        human_version = self._extract_text(props.get(self.PROP_HUMAN_VERSION, {}))
        if not human_version:
            human_version = fallback_version or "unknown"
            logger.warning(
                f"Active config for {config_type} missing Human Version; using '{human_version}'"
            )

        content_text = self._extract_text(props.get(self.PROP_CONTENT, {}))
        if not content_text:
            raise RuntimeError(f"Active config for '{config_type}' missing Content")

        fetched_at = datetime.now(timezone.utc)
        content_hash = self._hash_content(content_text)
        notion_page_id = page.get("id")

        active = ActiveConfig(
            config_type=config_type,
            human_version=human_version,
            content_text=content_text,
            content_hash=content_hash,
            notion_page_id=notion_page_id,
            fetched_at=fetched_at,
        )

        await self._store_snapshot(active)
        return active

    def _build_fallback(
        self,
        config_type: str,
        content_text: str,
        fallback_version: Optional[str],
    ) -> ActiveConfig:
        fetched_at = datetime.now(timezone.utc)
        human_version = fallback_version or f"{config_type}_fallback"
        content_hash = self._hash_content(content_text)
        active = ActiveConfig(
            config_type=config_type,
            human_version=human_version,
            content_text=content_text,
            content_hash=content_hash,
            notion_page_id=None,
            fetched_at=fetched_at,
        )
        return active

    async def _store_snapshot(self, config: ActiveConfig) -> None:
        if not self._snapshot_store:
            return
        save_fn = getattr(self._snapshot_store, "save_config_snapshot", None)
        if not callable(save_fn):
            return
        try:
            await save_fn(
                config_type=config.config_type,
                human_version=config.human_version,
                notion_page_id=config.notion_page_id,
                content_hash=config.content_hash,
                content_text=config.content_text,
                fetched_at=config.fetched_at,
            )
        except Exception as exc:
            logger.warning(f"Failed to store config snapshot: {exc}")

    @staticmethod
    def _hash_content(content_text: str) -> str:
        return hashlib.sha256(content_text.encode("utf-8")).hexdigest()

    @staticmethod
    def _extract_text(prop: Dict[str, Any]) -> str:
        if "rich_text" in prop:
            parts = []
            for item in prop.get("rich_text", []):
                if "plain_text" in item:
                    parts.append(item["plain_text"])
                else:
                    parts.append(item.get("text", {}).get("content", ""))
            return "".join(parts).strip()
        if "title" in prop:
            parts = []
            for item in prop.get("title", []):
                if "plain_text" in item:
                    parts.append(item["plain_text"])
                else:
                    parts.append(item.get("text", {}).get("content", ""))
            return "".join(parts).strip()
        return ""

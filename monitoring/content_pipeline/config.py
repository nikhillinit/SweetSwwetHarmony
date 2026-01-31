"""
Configuration Classes for Content Pipeline

Typed configuration for extraction and transport settings.
Supports JSON serialization/deserialization with sensible defaults.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FallbackConfig:
    """
    Configuration for selector fallback behavior.

    Controls how SelectorExtractor handles selector failures
    and provides quality signals for extracted content.

    Attributes:
        fallback_on_empty: If True, try next selector when current returns empty content
        min_chars: Minimum characters required; if content shorter, try next selector
        always_include_body: If True, add 'body' as ultimate fallback selector
    """
    fallback_on_empty: bool = True
    min_chars: int = 0
    always_include_body: bool = True


@dataclass
class ExtractorConfig:
    """
    Configuration for content extraction.

    Attributes:
        preset: Name of the extraction preset (e.g., "default", "article", "spa")
        selectors: Optional CSS selectors for custom extraction
        fallback_on_empty: If True, fall back to full-page extraction when selectors return empty
        prefer_structured: If True, prefer structured data (JSON-LD) as primary when available
    """
    preset: str = "default"
    selectors: Optional[List[str]] = None
    fallback_on_empty: bool = True
    prefer_structured: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: Dict[str, Any] = {"preset": self.preset}
        if self.selectors is not None:
            result["selectors"] = self.selectors
        if not self.fallback_on_empty:
            result["fallback_on_empty"] = self.fallback_on_empty
        if self.prefer_structured:
            result["prefer_structured"] = self.prefer_structured
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtractorConfig:
        """Create from dictionary."""
        return cls(
            preset=data.get("preset", "default"),
            selectors=data.get("selectors"),
            fallback_on_empty=data.get("fallback_on_empty", True),
            prefer_structured=data.get("prefer_structured", False),
        )


@dataclass
class TransportConfig:
    """
    Configuration for HTTP transport with fallback strategies.

    Attributes:
        initial: Initial transport to use (e.g., "httpx", "playwright", "curl")
        on_403: Transport to use on 403 Forbidden response
        on_429: Transport to use on 429 Too Many Requests response
        on_timeout: Transport to use on timeout
        on_blocked: Transport to use when blocked pattern detected (third tier)
        max_html_bytes: Maximum size for HTML content (default: 5MB)
        max_json_bytes: Maximum size for JSON content (default: 2MB)
        user_agent_profile: Browser profile for curl_cffi (e.g., "chrome", "firefox", "safari")
        playwright_wait_selector: CSS selector to wait for in Playwright (auto-detect if None)
        playwright_timeout_ms: Timeout for Playwright navigation in milliseconds (default: 30000)
    """
    initial: str = "httpx"
    on_403: Optional[str] = None
    on_429: Optional[str] = None
    on_timeout: Optional[str] = None
    on_blocked: Optional[str] = None
    max_html_bytes: int = 5_242_880  # 5MB
    max_json_bytes: int = 2_097_152  # 2MB
    user_agent_profile: Optional[str] = None
    playwright_wait_selector: Optional[str] = None
    playwright_timeout_ms: int = 30000  # 30 seconds

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: Dict[str, Any] = {"initial": self.initial}
        if self.on_403 is not None:
            result["on_403"] = self.on_403
        if self.on_429 is not None:
            result["on_429"] = self.on_429
        if self.on_timeout is not None:
            result["on_timeout"] = self.on_timeout
        if self.on_blocked is not None:
            result["on_blocked"] = self.on_blocked
        result["max_html_bytes"] = self.max_html_bytes
        result["max_json_bytes"] = self.max_json_bytes
        if self.user_agent_profile is not None:
            result["user_agent_profile"] = self.user_agent_profile
        if self.playwright_wait_selector is not None:
            result["playwright_wait_selector"] = self.playwright_wait_selector
        if self.playwright_timeout_ms != 30000:
            result["playwright_timeout_ms"] = self.playwright_timeout_ms
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TransportConfig:
        """Create from dictionary."""
        return cls(
            initial=data.get("initial", "httpx"),
            on_403=data.get("on_403"),
            on_429=data.get("on_429"),
            on_timeout=data.get("on_timeout"),
            on_blocked=data.get("on_blocked"),
            max_html_bytes=data.get("max_html_bytes", 5_242_880),
            max_json_bytes=data.get("max_json_bytes", 2_097_152),
            user_agent_profile=data.get("user_agent_profile"),
            playwright_wait_selector=data.get("playwright_wait_selector"),
            playwright_timeout_ms=data.get("playwright_timeout_ms", 30000),
        )


@dataclass
class WatchConfig:
    """
    Complete configuration for a watch's content pipeline.

    Combines preset name, extractor settings, and transport settings.

    Attributes:
        preset: Name of the preset this config is based on
        extractor: Content extraction configuration
        transport: HTTP transport configuration
        metadata: Optional metadata (e.g., auto_selected, page_type, confidence)
    """
    preset: str = "default"
    extractor: ExtractorConfig = field(default_factory=ExtractorConfig)
    transport: TransportConfig = field(default_factory=TransportConfig)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "preset": self.preset,
            "extractor": self.extractor.to_dict(),
            "transport": self.transport.to_dict(),
        }
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WatchConfig:
        """Create from dictionary."""
        extractor_data = data.get("extractor", {})
        transport_data = data.get("transport", {})
        metadata = data.get("metadata", {})

        return cls(
            preset=data.get("preset", "default"),
            extractor=ExtractorConfig.from_dict(extractor_data),
            transport=TransportConfig.from_dict(transport_data),
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    @classmethod
    def from_json(cls, json_str: str) -> WatchConfig:
        """
        Parse from JSON string with fallback on invalid JSON.

        Args:
            json_str: JSON string to parse

        Returns:
            WatchConfig instance (defaults if JSON is invalid)
        """
        if not json_str:
            return cls()

        try:
            data = json.loads(json_str)
            if not isinstance(data, dict):
                logger.warning(
                    "WatchConfig JSON is not a dict (got %s), using defaults",
                    type(data).__name__,
                )
                return cls()
            return cls.from_dict(data)
        except json.JSONDecodeError as e:
            logger.warning("Invalid WatchConfig JSON: %s, using defaults", str(e))
            return cls()


class ConfigParser:
    """
    Static utility for parsing watch configuration JSON.

    Usage:
        config = ConfigParser.parse(watch.config_json)
    """

    @staticmethod
    def parse(json_str: Optional[str]) -> WatchConfig:
        """
        Parse watch config_json into a WatchConfig.

        Args:
            json_str: JSON string from watch.config_json (may be None)

        Returns:
            WatchConfig instance (defaults if input is None or invalid)
        """
        if json_str is None:
            return WatchConfig()
        return WatchConfig.from_json(json_str)

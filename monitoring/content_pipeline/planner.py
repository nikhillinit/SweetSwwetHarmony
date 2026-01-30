"""
Pipeline Planner for Content Pipeline

Uses PageTypeClassifier to auto-select extraction presets when a watch
has no explicit configuration.

Implements Opportunity O1: Wire PageTypeClassifier to preset auto-selection.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from monitoring.page_type_classifier import PageType, PageTypeClassifier, get_classifier
from monitoring.content_pipeline.config import (
    ExtractorConfig,
    TransportConfig,
    WatchConfig,
)
from monitoring.content_pipeline.presets import PresetRegistry, get_preset

logger = logging.getLogger(__name__)


# Mapping from page type to preset name
PAGE_TYPE_PRESETS: Dict[PageType, str] = {
    PageType.PRICING: "pricing_table_v1",
    PageType.CAREERS: "default",  # No special preset yet
    PageType.PRODUCT: "default",
    PageType.TERMS: "default",
    PageType.NEWS: "blog_post_v1",
    PageType.LANDING: "default",
    PageType.UNKNOWN: "default",
}


class PipelinePlanner:
    """
    Plans extraction strategy for a watch based on URL and page type.

    Uses PageTypeClassifier to auto-select presets when watch has no explicit config.

    Usage:
        planner = PipelinePlanner()
        config = planner.plan("https://acme.com/pricing")
        # config.preset == "pricing_table_v1"
        # config.metadata["auto_selected"] == True

        # With explicit config (overrides auto-selection):
        config = planner.plan("https://acme.com/pricing", config_json='{"preset": "spa"}')
        # config.preset == "spa"
        # config.metadata["auto_selected"] == False
    """

    def __init__(
        self,
        classifier: Optional[PageTypeClassifier] = None,
        preset_registry: Optional[PresetRegistry] = None,
    ):
        """
        Initialize the pipeline planner.

        Args:
            classifier: Optional PageTypeClassifier instance (uses singleton if not provided)
            preset_registry: Optional PresetRegistry instance (uses default if not provided)
        """
        self._classifier = classifier or get_classifier()
        self._preset_registry = preset_registry

    def plan(
        self,
        url: str,
        config_json: Optional[str] = None,
    ) -> WatchConfig:
        """
        Determine the extraction configuration for a URL.

        If config_json provided and valid, use it.
        Otherwise, classify page type and select appropriate preset.

        Args:
            url: The URL to plan extraction for
            config_json: Optional explicit configuration JSON

        Returns:
            WatchConfig with extraction settings and metadata
        """
        # Try explicit config first
        if config_json and config_json.strip():
            explicit_config = self._try_parse_explicit_config(config_json)
            if explicit_config is not None:
                # Mark as not auto-selected
                explicit_config.metadata["auto_selected"] = False
                return explicit_config
            # Invalid JSON - fall through to auto-selection

        # Auto-select based on page type classification
        return self._auto_select_config(url)

    def _try_parse_explicit_config(self, config_json: str) -> Optional[WatchConfig]:
        """
        Try to parse explicit config JSON.

        Args:
            config_json: JSON string to parse

        Returns:
            WatchConfig if valid JSON, None if invalid
        """
        try:
            data = json.loads(config_json)
            if not isinstance(data, dict):
                logger.warning(
                    "Config JSON is not a dict (got %s), falling back to auto-selection",
                    type(data).__name__,
                )
                return None
            return WatchConfig.from_dict(data)
        except json.JSONDecodeError as e:
            logger.warning(
                "Invalid config JSON: %s, falling back to auto-selection",
                str(e),
            )
            return None

    def _auto_select_config(self, url: str) -> WatchConfig:
        """
        Auto-select configuration based on URL classification.

        Args:
            url: The URL to classify

        Returns:
            WatchConfig with auto-selected preset and metadata
        """
        # Classify the URL
        classification = self._classifier.classify(url)

        # Map page type to preset name
        preset_name = PAGE_TYPE_PRESETS.get(classification.page_type, "default")

        # Load preset configuration
        preset_data = self._get_preset(preset_name)

        # Build WatchConfig from preset
        config = self._build_config_from_preset(preset_name, preset_data)

        # Add auto-selection metadata
        config.metadata = {
            "auto_selected": True,
            "page_type": classification.page_type.value,
            "preset_name": preset_name,
            "confidence": classification.confidence,
        }

        return config

    def _get_preset(self, preset_name: str) -> Dict[str, Any]:
        """
        Get preset data by name.

        Args:
            preset_name: Name of the preset

        Returns:
            Preset configuration dict
        """
        if self._preset_registry is not None:
            return self._preset_registry.get(preset_name)
        return get_preset(preset_name)

    def _build_config_from_preset(
        self,
        preset_name: str,
        preset_data: Dict[str, Any],
    ) -> WatchConfig:
        """
        Build a WatchConfig from preset data.

        Args:
            preset_name: Name of the preset
            preset_data: Preset configuration dict

        Returns:
            WatchConfig instance
        """
        extractor_data = preset_data.get("extractor", {})
        transport_data = preset_data.get("transport", {})

        return WatchConfig(
            preset=preset_name,
            extractor=ExtractorConfig.from_dict(extractor_data),
            transport=TransportConfig.from_dict(transport_data),
            metadata={},  # Will be populated by caller
        )

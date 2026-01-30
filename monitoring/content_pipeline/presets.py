"""
Preset Registry for Content Pipeline

Loads and manages extraction presets from config/watch_presets.yaml.
Provides sensible defaults when the config file is missing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default presets path
DEFAULT_PRESETS_PATH = Path(__file__).parent.parent.parent / "config" / "watch_presets.yaml"


class PresetRegistry:
    """
    Registry for loading and accessing watch presets.

    Loads presets from a YAML file and provides fallback defaults.

    Usage:
        registry = PresetRegistry()
        registry.load()
        preset = registry.get("default")
    """

    def __init__(self, presets_path: Optional[Path] = None):
        """
        Initialize the registry.

        Args:
            presets_path: Path to watch_presets.yaml (uses default if not provided)
        """
        self.presets_path = presets_path or DEFAULT_PRESETS_PATH
        self._presets: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def load(self) -> Dict[str, Dict[str, Any]]:
        """
        Load presets from YAML file.

        Returns:
            Dictionary of preset name -> preset configuration

        Note:
            Returns default presets if file is missing or invalid.
        """
        if not self.presets_path.exists():
            logger.info(
                "Presets file not found at %s, using defaults",
                self.presets_path,
            )
            self._presets = self._default_presets()
            self._loaded = True
            return self._presets

        try:
            import yaml

            with open(self.presets_path, "r") as f:
                data = yaml.safe_load(f)

            if not isinstance(data, dict):
                logger.warning(
                    "Presets file %s does not contain a dict, using defaults",
                    self.presets_path,
                )
                self._presets = self._default_presets()
            else:
                # Merge with defaults to ensure required presets exist
                self._presets = {**self._default_presets(), **data.get("presets", data)}

            self._loaded = True
            logger.info(
                "Loaded %d presets from %s",
                len(self._presets),
                self.presets_path,
            )
            return self._presets

        except ImportError:
            logger.error("PyYAML not installed, using default presets")
            self._presets = self._default_presets()
            self._loaded = True
            return self._presets
        except Exception as e:
            logger.error(
                "Failed to load presets from %s: %s, using defaults",
                self.presets_path,
                str(e),
            )
            self._presets = self._default_presets()
            self._loaded = True
            return self._presets

    def get(self, preset_name: str) -> Dict[str, Any]:
        """
        Get a preset by name.

        Args:
            preset_name: Name of the preset to retrieve

        Returns:
            Preset configuration dict (returns "default" preset if not found)
        """
        if not self._loaded:
            self.load()

        if preset_name in self._presets:
            return self._presets[preset_name]

        logger.warning(
            "Preset '%s' not found, falling back to 'default'",
            preset_name,
        )
        return self._presets.get("default", self._default_presets()["default"])

    @staticmethod
    def _default_presets() -> Dict[str, Dict[str, Any]]:
        """
        Return default presets when config file is unavailable.

        Returns:
            Dictionary of default preset configurations
        """
        return {
            "default": {
                "description": "Standard extraction for most websites",
                "extractor": {
                    "preset": "default",
                    "selectors": None,
                    "fallback_on_empty": True,
                },
                "content_limits": {
                    "max_html_bytes": 5242880,  # 5 MB
                    "max_json_bytes": 2097152,  # 2 MB
                },
                "transport": {
                    "initial": "httpx",
                    "on_403": None,
                    "on_429": None,
                    "on_timeout": None,
                },
            },
            "spa": {
                "description": "Single-page application with JavaScript rendering",
                "extractor": {
                    "preset": "spa",
                    "selectors": None,
                    "fallback_on_empty": True,
                },
                "content_limits": {
                    "max_html_bytes": 5242880,  # 5 MB
                    "max_json_bytes": 2097152,  # 2 MB
                },
                "transport": {
                    "initial": "playwright",
                    "on_403": None,
                    "on_429": None,
                    "on_timeout": None,
                },
            },
            "article": {
                "description": "News article or blog post extraction",
                "extractor": {
                    "preset": "article",
                    "selectors": ["article", "main", ".post-content", ".article-body"],
                    "fallback_on_empty": True,
                },
                "content_limits": {
                    "max_html_bytes": 5242880,  # 5 MB
                    "max_json_bytes": 2097152,  # 2 MB
                },
                "transport": {
                    "initial": "httpx",
                    "on_403": "playwright",
                    "on_429": None,
                    "on_timeout": None,
                },
            },
            "pricing": {
                "description": "Pricing page extraction",
                "extractor": {
                    "preset": "pricing",
                    "selectors": [".pricing", "#pricing", "[data-testid='pricing']"],
                    "fallback_on_empty": True,
                },
                "content_limits": {
                    "max_html_bytes": 5242880,  # 5 MB
                    "max_json_bytes": 2097152,  # 2 MB
                },
                "transport": {
                    "initial": "httpx",
                    "on_403": "playwright",
                    "on_429": None,
                    "on_timeout": None,
                },
            },
        }


# Global registry instance
_registry: Optional[PresetRegistry] = None


def _get_registry() -> PresetRegistry:
    """Get or create the global registry instance."""
    global _registry
    if _registry is None:
        _registry = PresetRegistry()
    return _registry


def get_preset(preset_name: str) -> Dict[str, Any]:
    """
    Get a preset by name (convenience function).

    Args:
        preset_name: Name of the preset to retrieve

    Returns:
        Preset configuration dict
    """
    return _get_registry().get(preset_name)


def load_presets(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """
    Load presets from a file (convenience function).

    Args:
        path: Optional path to presets file

    Returns:
        Dictionary of all presets
    """
    global _registry
    _registry = PresetRegistry(path)
    return _registry.load()

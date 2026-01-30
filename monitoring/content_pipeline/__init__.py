"""
Content Pipeline for Monitoring Subsystem

Configuration, presets, and models for content extraction and transport.

Components:
- WatchConfig: Per-watch configuration combining extractor and transport settings
- ExtractorConfig: Content extraction settings (presets, selectors)
- TransportConfig: HTTP transport settings (fallback strategies)
- ConfigParser: JSON parsing for watch config_json
- PresetRegistry: Loads and manages watch_presets.yaml
- FetchArtifact: Result of HTTP fetch before extraction
- PipelineResult: Final output from content pipeline
- ExtractedContent: Single extracted content representation
- RepresentationType: Types of content representations
- HttpxTransport: HTTP transport with conditional request support
- SelectorExtractor: CSS/XPath-based HTML content extraction (parsel)
"""

from monitoring.content_pipeline.config import (
    ExtractorConfig,
    FallbackConfig,
    TransportConfig,
    WatchConfig,
    ConfigParser,
)
from monitoring.content_pipeline.presets import (
    PresetRegistry,
    get_preset,
    load_presets,
)
from monitoring.content_pipeline.models import (
    FetchArtifact,
    PipelineResult,
    ExtractedContent,
    RepresentationType,
)
from monitoring.content_pipeline.transport_httpx import HttpxTransport
from monitoring.content_pipeline.extract_html import SelectorExtractor

__all__ = [
    # Config classes
    "ExtractorConfig",
    "FallbackConfig",
    "TransportConfig",
    "WatchConfig",
    "ConfigParser",
    # Preset management
    "PresetRegistry",
    "get_preset",
    "load_presets",
    # Pipeline models
    "FetchArtifact",
    "PipelineResult",
    "ExtractedContent",
    "RepresentationType",
    # Transport
    "HttpxTransport",
    # Extraction
    "SelectorExtractor",
]

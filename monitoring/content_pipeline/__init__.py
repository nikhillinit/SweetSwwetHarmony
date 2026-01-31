"""
Content Pipeline for Monitoring Subsystem

Configuration, presets, and models for content extraction and transport.

Components:
- WatchConfig: Per-watch configuration combining extractor and transport settings
- ExtractorConfig: Content extraction settings (presets, selectors)
- TransportConfig: HTTP transport settings (fallback strategies, content size limits)
- ConfigParser: JSON parsing for watch config_json
- PresetRegistry: Loads and manages watch_presets.yaml
- FetchArtifact: Result of HTTP fetch before extraction
- PipelineResult: Final output from content pipeline
- ExtractedContent: Single extracted content representation
- RepresentationType: Types of content representations
- HttpxTransport: HTTP transport with conditional request support and streaming size limits
- SelectorExtractor: CSS/XPath-based HTML content extraction (parsel)
- ContentSizeExceededError: Raised when content exceeds configured size limits
"""

from monitoring.content_pipeline.config import (
    ExtractorConfig,
    FallbackConfig,
    TransportConfig,
    WatchConfig,
    ConfigParser,
)
from monitoring.content_pipeline.exceptions import (
    ContentSizeExceededError,
)
from monitoring.content_pipeline.presets import (
    PresetRegistry,
    get_preset,
    load_presets,
)
from monitoring.content_pipeline.planner import (
    PipelinePlanner,
    PAGE_TYPE_PRESETS,
)
from monitoring.content_pipeline.models import (
    FetchArtifact,
    PipelineResult,
    ExtractedContent,
    RepresentationType,
)
from monitoring.content_pipeline.transport_httpx import HttpxTransport
from monitoring.content_pipeline.extract_html import SelectorExtractor
from monitoring.content_pipeline.orchestrator import ContentPipeline

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
    # Pipeline planner
    "PipelinePlanner",
    "PAGE_TYPE_PRESETS",
    # Pipeline models
    "FetchArtifact",
    "PipelineResult",
    "ExtractedContent",
    "RepresentationType",
    # Transport
    "HttpxTransport",
    # Extraction
    "SelectorExtractor",
    # Orchestrator
    "ContentPipeline",
    # Exceptions
    "ContentSizeExceededError",
]

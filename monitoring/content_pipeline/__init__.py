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
- InscriptisExtractor: HTML-to-text with table/grid layout preservation (inscriptis)
- StructuredDataExtractor: JSON-LD/microdata/OpenGraph/RDFa extraction (extruct)
- NormalizationMode: Enum for whitespace normalization strategies
- normalize_layout_preserving: Normalize whitespace while preserving layout
- normalize_aggressive: Collapse all whitespace to single spaces
- ContentSizeExceededError: Raised when content exceeds configured size limits
- ATSSignatureDetector: Detects ATS embeds (Greenhouse, Lever, Ashby, Workable)
- ATSDiscoveryResult: Result of ATS detection with provider, board_id, and API URL
- ATSProvider: Enum of supported ATS providers
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
from monitoring.content_pipeline.extract_inscriptis import InscriptisExtractor
from monitoring.content_pipeline.extract_structured import StructuredDataExtractor
from monitoring.content_pipeline.normalize import (
    NormalizationMode,
    normalize_layout_preserving,
    normalize_aggressive,
)
from monitoring.content_pipeline.orchestrator import ContentPipeline
from monitoring.content_pipeline.ats_discovery import (
    ATSSignatureDetector,
    ATSDiscoveryResult,
    ATSProvider,
    detect_ats,
    get_detector,
)

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
    "InscriptisExtractor",
    "StructuredDataExtractor",
    # Normalization
    "NormalizationMode",
    "normalize_layout_preserving",
    "normalize_aggressive",
    # Orchestrator
    "ContentPipeline",
    # Exceptions
    "ContentSizeExceededError",
    # ATS Discovery
    "ATSSignatureDetector",
    "ATSDiscoveryResult",
    "ATSProvider",
    "detect_ats",
    "get_detector",
]

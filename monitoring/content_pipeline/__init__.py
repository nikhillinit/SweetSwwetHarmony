"""
Content Pipeline for Monitoring Subsystem

Configuration, presets, and models for content extraction and transport.

Components:
- WatchConfig: Per-watch configuration combining extractor and transport settings
- ExtractorConfig: Content extraction settings (presets, selectors)
- TransportConfig: HTTP transport settings (fallback strategies)
- ConfigParser: JSON parsing for watch config_json
- PresetRegistry: Loads and manages watch_presets.yaml
"""

from monitoring.content_pipeline.config import (
    ExtractorConfig,
    TransportConfig,
    WatchConfig,
    ConfigParser,
)
from monitoring.content_pipeline.presets import (
    PresetRegistry,
    get_preset,
    load_presets,
)

# TODO (Task 0.5): Uncomment when FetchArtifact/PipelineResult are implemented
# from monitoring.content_pipeline.models import (
#     FetchArtifact,
#     PipelineResult,
# )

__all__ = [
    # Config classes
    "ExtractorConfig",
    "TransportConfig",
    "WatchConfig",
    "ConfigParser",
    # Preset management
    "PresetRegistry",
    "get_preset",
    "load_presets",
    # TODO (Task 0.5): Add when implemented
    # "FetchArtifact",
    # "PipelineResult",
]

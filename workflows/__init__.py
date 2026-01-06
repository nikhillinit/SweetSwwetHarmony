"""
Workflows for Discovery Engine Pipeline

This package contains high-level workflow orchestration:
- pipeline.py: Main pipeline orchestrator
- notion_pusher.py: Notion CRM integration
- suppression_sync.py: Suppression cache sync

Usage:
    # Pipeline orchestration
    from workflows.pipeline import DiscoveryPipeline
    pipeline = DiscoveryPipeline()
    await pipeline.initialize()
    result = await pipeline.run_full_pipeline(["github"])

    # Suppression sync
    from workflows.suppression_sync import SuppressionSync
    sync = SuppressionSync(notion_connector, signal_store)
    stats = await sync.sync()
"""

# Lazy imports to avoid circular dependencies
__all__ = [
    "DiscoveryPipeline",
    "PipelineConfig",
    "PipelineMode",
    "PipelineStats",
    "pipeline_context",
    "SuppressionSync",
    "SyncStats",
    "run_scheduled_sync",
]


def __getattr__(name):
    """Lazy import to avoid circular dependencies."""
    if name == "DiscoveryPipeline":
        from workflows.pipeline import DiscoveryPipeline
        return DiscoveryPipeline
    elif name == "PipelineConfig":
        from workflows.pipeline import PipelineConfig
        return PipelineConfig
    elif name == "PipelineMode":
        from workflows.pipeline import PipelineMode
        return PipelineMode
    elif name == "PipelineStats":
        from workflows.pipeline import PipelineStats
        return PipelineStats
    elif name == "pipeline_context":
        from workflows.pipeline import pipeline_context
        return pipeline_context
    elif name == "SuppressionSync":
        from workflows.suppression_sync import SuppressionSync
        return SuppressionSync
    elif name == "SyncStats":
        from workflows.suppression_sync import SyncStats
        return SyncStats
    elif name == "run_scheduled_sync":
        from workflows.suppression_sync import run_scheduled_sync
        return run_scheduled_sync
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

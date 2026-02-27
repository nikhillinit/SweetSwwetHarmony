"""
Pipeline visualization components.

Provides three-tier visualization system:
- Tier 1: Terminal progress bars (real-time)
- Tier 2: HTML reports (post-execution)
- Tier 3: Dashboard trends (historical)
"""

try:
    from .terminal_progress import PipelineProgress
except ImportError:
    PipelineProgress = None

__all__ = ["PipelineProgress"]

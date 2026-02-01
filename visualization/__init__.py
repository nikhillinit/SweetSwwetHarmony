"""
Pipeline visualization components.

Provides three-tier visualization system:
- Tier 1: Terminal progress bars (real-time)
- Tier 2: HTML reports (post-execution)
- Tier 3: Dashboard trends (historical)
"""

from .terminal_progress import PipelineProgress

__all__ = ["PipelineProgress"]

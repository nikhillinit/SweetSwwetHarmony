"""Migration v30: Add identity_stats column to pipeline_runs.

Stores per-run identity/thin-file counters as JSON:
  {sweep_promoted, sweep_evaluated, sweep_pages, sweep_error}
"""

V30_PIPELINE_IDENTITY_STATS_DDL = """
ALTER TABLE pipeline_runs ADD COLUMN identity_stats TEXT;
"""

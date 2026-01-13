# Collector Metrics Design

**Date:** 2026-01-13
**Goal:** Add per-collector timing and API metrics for performance debugging

## Problem

Pipeline runs show aggregate stats but no per-collector breakdown. When performance degrades, there's no visibility into which collector is slow or experiencing API issues.

## Solution

### Data Model

New `collector_metrics` table:

```sql
CREATE TABLE collector_metrics (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,           -- Links to pipeline_runs
    collector_name TEXT NOT NULL,   -- e.g., "github", "sec_edgar"

    -- Timing
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_seconds REAL,

    -- Results
    signals_found INTEGER DEFAULT 0,
    status TEXT,                    -- "success", "failed", "skipped"

    -- API metrics
    api_calls INTEGER DEFAULT 0,
    rate_limit_hits INTEGER DEFAULT 0,
    retries INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    error_messages TEXT             -- JSON array
);

CREATE INDEX idx_collector_metrics_run_id ON collector_metrics(run_id);
CREATE INDEX idx_collector_metrics_collector ON collector_metrics(collector_name);
```

### Metrics Capture

```python
@dataclass
class CollectorMetrics:
    collector_name: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    signals_found: int = 0
    status: str = "pending"

    # API metrics
    api_calls: int = 0
    rate_limit_hits: int = 0
    retries: int = 0
    errors: int = 0
    error_messages: List[str] = field(default_factory=list)
```

**Capture points:**
1. Start timer before each collector runs
2. Stop timer when collector completes
3. Pull API metrics from existing RateLimiter/RetryStrategy
4. Save to collector_metrics table with pipeline run_id

### CLI Command

`python run_pipeline.py metrics`

```
DISCOVERY ENGINE - PIPELINE METRICS
======================================================================

Last 5 runs:

Run: 2026-01-13 14:32:01 (45.2s total)
  github          12.3s   ✓   42 signals   |  API: 15 calls, 0 retries
  sec_edgar       28.1s   ✓   18 signals   |  API: 8 calls, 2 retries
  companies_house  4.8s   ✓    6 signals   |  API: 3 calls, 0 retries
```

**Options:**
- `--limit N` - Show last N runs (default 5)
- `--collector NAME` - Filter to specific collector

## Files to Modify

- `storage/signal_store.py` - Add table schema and save/query methods
- `workflows/pipeline.py` - Add timing capture and metrics collection
- `run_pipeline.py` - Add metrics CLI command

## Files to Create

- `tests/test_collector_metrics.py` - TDD tests

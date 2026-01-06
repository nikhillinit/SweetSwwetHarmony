# Discovery Engine Workflows

Scheduled and on-demand jobs for maintaining Discovery Engine data.

## Available Workflows

### Suppression Sync

Syncs Notion CRM entries to the local SQLite suppression cache to prevent duplicate pushes.

**Features:**
- Fetches all active prospects from Notion (all statuses including Passed/Lost)
- Extracts canonical keys from Notion properties
- Builds canonical keys from Website if missing
- Handles missing canonical keys gracefully
- Bulk updates suppression_cache table via SignalStore
- Cleans expired cache entries
- Comprehensive stats and logging

**Usage:**

```bash
# Run once (standalone)
python -m workflows.suppression_sync

# Dry run (show what would be synced)
python -m workflows.suppression_sync --dry-run

# Run on interval (every 15 minutes)
python -m workflows.suppression_sync --interval 900

# Custom database path
python -m workflows.suppression_sync --db-path /path/to/signals.db

# Custom TTL (14 days instead of default 7)
python -m workflows.suppression_sync --ttl-days 14

# Verbose logging
python -m workflows.suppression_sync --verbose
```

**Environment Variables:**

```bash
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=xxx
```

**Programmatic Usage:**

```python
from connectors.notion_connector_v2 import NotionConnector
from storage.signal_store import SignalStore
from workflows.suppression_sync import SuppressionSync

# Initialize connectors
notion = NotionConnector(api_key="...", database_id="...")
store = SignalStore(db_path="signals.db")
await store.initialize()

# Run sync
sync = SuppressionSync(notion, store, ttl_days=7)
stats = await sync.sync(dry_run=False)

# Check results
print(f"Synced {stats.entries_synced} entries")
print(f"Cleaned {stats.entries_expired_cleaned} expired entries")
```

**Scheduled Execution:**

```python
from workflows.suppression_sync import run_scheduled_sync

# Run every 15 minutes
await run_scheduled_sync(
    interval_seconds=900,
    notion_connector=notion,
    signal_store=store,
    ttl_days=7,
)
```

**Stats Output:**

```json
{
  "started_at": "2026-01-06T12:00:00Z",
  "completed_at": "2026-01-06T12:00:15Z",
  "duration_seconds": 15.2,
  "notion_pages_fetched": 150,
  "notion_errors": 0,
  "entries_processed": 145,
  "entries_with_canonical_key": 142,
  "entries_without_canonical_key": 3,
  "entries_with_strong_key": 138,
  "entries_with_weak_key": 4,
  "entries_synced": 145,
  "entries_expired_cleaned": 12,
  "errors_count": 0,
  "errors": []
}
```

## Integration with MCP Server

The suppression sync job is integrated into the Discovery Engine MCP server:

```bash
# Trigger sync via MCP
/mcp__discovery-engine__sync-suppression-cache
```

This runs the full SuppressionSync workflow and returns comprehensive stats.

## Adding New Workflows

To add a new workflow:

1. Create a new file in `workflows/` (e.g., `workflows/my_workflow.py`)
2. Implement your workflow class with async methods
3. Add CLI entry point with argparse
4. Export from `workflows/__init__.py`
5. Document in this README

**Template:**

```python
"""
My Workflow Job for Discovery Engine

Description of what this workflow does.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MyWorkflowStats:
    """Statistics from workflow run."""
    items_processed: int = 0
    errors: list[str] = field(default_factory=list)


class MyWorkflow:
    """
    My workflow implementation.
    """

    def __init__(self, connector: SomeConnector):
        self.connector = connector

    async def run(self, dry_run: bool = False) -> MyWorkflowStats:
        """Run the workflow."""
        stats = MyWorkflowStats()

        # Workflow logic here

        return stats


async def main():
    """CLI entry point."""
    # Parse args, initialize, run workflow
    pass


if __name__ == "__main__":
    asyncio.run(main())
```

## Testing

```bash
# Test suppression sync
python test_suppression_sync.py
```

## Monitoring

All workflows emit structured logs that can be consumed by monitoring tools:

- Start/completion timestamps
- Duration metrics
- Success/error counts
- Detailed error messages

Recommended monitoring setup:
- CloudWatch/DataDog for log aggregation
- Alerts on error_count > threshold
- Dashboards for sync duration trends

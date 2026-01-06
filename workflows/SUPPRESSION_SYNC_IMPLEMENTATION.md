# Suppression Sync Implementation Summary

## Overview

Created a complete suppression cache sync job that syncs Notion CRM entries to the local SQLite cache to prevent duplicate prospect pushes.

## Files Created

### Core Implementation

1. **`workflows/suppression_sync.py`** (main implementation)
   - `SuppressionSync` class - handles sync logic
   - `SyncStats` dataclass - comprehensive statistics
   - `run_scheduled_sync()` - scheduled execution helper
   - CLI entry point with argparse

2. **`workflows/__init__.py`** (updated)
   - Added lazy imports to avoid circular dependencies
   - Exports SuppressionSync, SyncStats, run_scheduled_sync

3. **`discovery_engine/mcp_server.py`** (updated)
   - Added SignalStore initialization
   - Updated `_handle_sync_suppression_cache()` to use SuppressionSync
   - Returns comprehensive stats instead of just entry count

### Documentation

4. **`workflows/README.md`** (updated)
   - Added suppression sync section
   - Usage examples
   - Integration guide

5. **`workflows/SUPPRESSION_SYNC_GUIDE.md`** (comprehensive guide)
   - Detailed usage instructions
   - Configuration options
   - Troubleshooting guide
   - Architecture diagram

### Examples and Tests

6. **`workflows/example_suppression_sync.py`**
   - One-time sync example
   - Dry run example
   - Scheduled sync example
   - Suppression check example
   - Full integration example

7. **`test_suppression_sync.py`**
   - Test script for validation
   - Runs dry-run and live sync
   - Verifies stats output

## Features Implemented

### 1. Notion Integration

- ✅ Fetches all active prospects from Notion (all statuses)
- ✅ Uses efficient batch queries (100 pages per request)
- ✅ Respects Notion rate limits (3 req/sec)
- ✅ Handles pagination for large databases

### 2. Canonical Key Handling

- ✅ Extracts canonical keys from Notion properties
- ✅ Builds keys from Website if missing
- ✅ Graceful fallback to name_loc keys
- ✅ Tracks strong vs. weak key quality

### 3. Cache Management

- ✅ Bulk upserts to suppression_cache table
- ✅ Configurable TTL (default: 7 days)
- ✅ Automatic cleanup of expired entries
- ✅ Transaction safety via SignalStore

### 4. Statistics and Monitoring

- ✅ Comprehensive SyncStats dataclass
- ✅ Timing metrics (duration, timestamps)
- ✅ Processing metrics (pages fetched, entries processed)
- ✅ Quality metrics (strong/weak keys)
- ✅ Error tracking and reporting
- ✅ Human-readable summary logs
- ✅ JSON output for APIs

### 5. Execution Modes

- ✅ One-time sync (standalone)
- ✅ Dry-run mode (preview only)
- ✅ Scheduled sync (interval-based)
- ✅ MCP server integration
- ✅ Programmatic API

### 6. Error Handling

- ✅ Graceful handling of missing canonical keys
- ✅ Per-page error logging (doesn't stop entire sync)
- ✅ Comprehensive error messages
- ✅ Retry logic via NotionConnector

## Usage Examples

### CLI

```bash
# Run once
python -m workflows.suppression_sync

# Dry run
python -m workflows.suppression_sync --dry-run

# Run every 15 minutes
python -m workflows.suppression_sync --interval 900

# Custom TTL
python -m workflows.suppression_sync --ttl-days 14

# Verbose logging
python -m workflows.suppression_sync --verbose
```

### Programmatic

```python
from workflows.suppression_sync import SuppressionSync

sync = SuppressionSync(notion_connector, signal_store, ttl_days=7)
stats = await sync.sync(dry_run=False)

print(f"Synced {stats.entries_synced} entries")
print(f"Duration: {stats.duration_seconds:.2f}s")
```

### MCP Server

```bash
/mcp__discovery-engine__sync-suppression-cache
```

Returns comprehensive JSON stats.

## Integration Points

### SignalStore

Uses existing `SignalStore` methods:
- `update_suppression_cache(entries)` - bulk upsert
- `clean_expired_cache()` - cleanup
- `check_suppression(canonical_key)` - lookup

### NotionConnector

Uses existing `NotionConnector` methods:
- `_query_by_statuses(client, statuses)` - batch fetch
- `_extract_text()`, `_extract_title()`, `_extract_select()` - property extraction

### Canonical Keys

Uses `utils/canonical_keys.py`:
- `normalize_domain()` - domain normalization
- `is_strong_key()` - key quality check
- `_slug()` - name slugification

## Testing

### Manual Testing

```bash
# Test imports
python -c "from workflows.suppression_sync import SuppressionSync; print('OK')"

# Test dry run
python -m workflows.suppression_sync --dry-run

# Test live sync (with test DB)
python test_suppression_sync.py
```

### Expected Output

```
================================================================================
SUPPRESSION CACHE SYNC SUMMARY
================================================================================
Started:  2026-01-06T12:00:00Z
Completed: 2026-01-06T12:00:15Z
Duration: 15.23s

Notion Fetch:
  Pages fetched: 150

Processing:
  Entries processed: 145
  With canonical key: 142
  Without canonical key: 3
  Strong keys: 138
  Weak keys: 4

Cache Update:
  Entries synced: 145
  Expired entries cleaned: 12
================================================================================
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Suppression Sync Job                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Fetch from Notion                                       │
│     └─> Query all statuses in single OR filter             │
│     └─> Batch pagination (100 pages/request)               │
│                                                             │
│  2. Process Pages                                           │
│     └─> Extract canonical_key, page_id, status, name       │
│     └─> Build canonical key from Website if missing        │
│     └─> Fallback to name_loc if no other key               │
│                                                             │
│  3. Build SuppressionEntry Objects                          │
│     └─> canonical_key, notion_page_id, status              │
│     └─> cached_at, expires_at (TTL)                        │
│     └─> metadata (website, source)                         │
│                                                             │
│  4. Bulk Update Cache                                       │
│     └─> SignalStore.update_suppression_cache(entries)      │
│     └─> UPSERT with ON CONFLICT DO UPDATE                  │
│                                                             │
│  5. Clean Expired Entries                                   │
│     └─> SignalStore.clean_expired_cache()                  │
│     └─> DELETE WHERE expires_at <= NOW()                   │
│                                                             │
│  6. Return Stats                                            │
│     └─> SyncStats with comprehensive metrics               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Performance

### Benchmarks (expected)

- **Small DB (50 pages):** < 5 seconds
- **Medium DB (200 pages):** < 15 seconds
- **Large DB (500 pages):** < 30 seconds
- **Very Large DB (1000+ pages):** < 60 seconds

### Optimization Opportunities

1. **Parallel processing** - process pages in parallel batches
2. **Schema caching** - cache Notion schema to reduce API calls
3. **Incremental sync** - only fetch pages updated since last sync
4. **Compression** - compress metadata JSON in cache

## Deployment

### Systemd Service (Linux)

```ini
[Unit]
Description=Discovery Engine Suppression Sync
After=network.target

[Service]
Type=simple
User=discovery
WorkingDirectory=/opt/discovery-engine
ExecStart=/usr/bin/python3 -m workflows.suppression_sync --interval 900
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
```

### Cron (Alternative)

```cron
# Sync every 15 minutes
*/15 * * * * cd /opt/discovery-engine && python -m workflows.suppression_sync
```

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "-m", "workflows.suppression_sync", "--interval", "900"]
```

## Monitoring

### Metrics to Track

- `sync_duration_seconds` - how long sync takes
- `entries_synced` - number of entries updated
- `entries_expired_cleaned` - churn rate
- `entries_without_canonical_key` - data quality
- `errors_count` - reliability
- `notion_pages_fetched` - pipeline volume

### Alerting

- Alert if `errors_count > 5`
- Alert if `sync_duration_seconds > 60`
- Alert if sync hasn't run in > 30 minutes (scheduled mode)
- Alert if `entries_without_canonical_key / entries_processed > 0.1` (10%)

## Future Enhancements

1. **Incremental sync** - only fetch updated pages
2. **Webhook integration** - real-time updates from Notion
3. **Multi-database support** - sync from multiple Notion DBs
4. **Backup/restore** - export/import cache snapshots
5. **Conflict resolution** - handle concurrent updates
6. **Metrics export** - Prometheus/StatsD integration

## Related Files

- `storage/signal_store.py` - SQLite cache implementation
- `connectors/notion_connector_v2.py` - Notion API client
- `utils/canonical_keys.py` - Key generation logic
- `discovery_engine/mcp_server.py` - MCP integration
- `workflows/pipeline.py` - Main discovery pipeline

## Maintenance

### Regular Tasks

- Monitor sync logs for errors
- Review entries without canonical keys
- Update TTL based on pipeline velocity
- Add Canonical Key properties to new Notion pages
- Archive old Passed/Lost deals if DB grows too large

### Troubleshooting

See `workflows/SUPPRESSION_SYNC_GUIDE.md` for detailed troubleshooting.

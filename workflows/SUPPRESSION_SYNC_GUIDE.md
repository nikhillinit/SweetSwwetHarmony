# Suppression Cache Sync Guide

## Overview

The Suppression Cache Sync job keeps your local SQLite cache in sync with Notion CRM to prevent duplicate prospect pushes.

**Key Benefits:**
- Fast local lookups (no Notion API calls during discovery)
- Works offline (uses cached data)
- Automatic expiration and cleanup
- Handles missing canonical keys gracefully
- Comprehensive stats and monitoring

## Quick Start

### Prerequisites

1. Set environment variables:
```bash
export NOTION_API_KEY=secret_xxx
export NOTION_DATABASE_ID=xxx
```

2. Ensure Notion database has these properties:
   - Company Name (title)
   - Status (select)
   - Canonical Key (rich_text) - optional but recommended
   - Website (url) - used as fallback if Canonical Key missing

### Run Once

```bash
# Sync now
python -m workflows.suppression_sync

# Dry run (preview only)
python -m workflows.suppression_sync --dry-run
```

### Run on Schedule

```bash
# Sync every 15 minutes
python -m workflows.suppression_sync --interval 900

# Run as background service
nohup python -m workflows.suppression_sync --interval 900 > suppression_sync.log 2>&1 &
```

### Via MCP Server

```bash
# Trigger from Claude Desktop or API
/mcp__discovery-engine__sync-suppression-cache
```

## How It Works

### 1. Fetch from Notion

The sync job queries Notion for ALL prospects in these statuses:
- Source
- Initial Meeting / Call
- Dilligence (note the typo - matches Notion)
- Tracking
- Committed
- Funded
- Passed
- Lost

This ensures we suppress duplicates across the entire pipeline, including deals we've passed on.

### 2. Extract Canonical Keys

For each page, the sync job:

**Priority 1: Use existing Canonical Key property**
```
Canonical Key = "domain:acme.ai"
```

**Priority 2: Build from Website**
```
Website = "https://www.acme.ai/product"
→ Canonical Key = "domain:acme.ai"
```

**Priority 3: Build weak key from Company Name**
```
Company Name = "Acme Inc"
→ Canonical Key = "name_loc:acme-inc"
```

### 3. Update Cache

Bulk upserts entries to `suppression_cache` table:

```sql
INSERT INTO suppression_cache (
    canonical_key, notion_page_id, status, company_name,
    cached_at, expires_at, metadata
) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(canonical_key) DO UPDATE SET ...
```

### 4. Clean Expired Entries

Removes entries older than TTL (default: 7 days):

```sql
DELETE FROM suppression_cache WHERE expires_at <= NOW()
```

## Configuration

### TTL (Time To Live)

How long to cache entries before re-syncing:

```bash
# 14-day TTL (instead of default 7 days)
python -m workflows.suppression_sync --ttl-days 14
```

**Recommendation:** Use 7 days for active pipeline, 14+ days for stable/slow-moving pipeline.

### Database Path

```bash
# Use custom database
python -m workflows.suppression_sync --db-path /var/data/signals.db
```

### Sync Interval

```bash
# Sync every hour
python -m workflows.suppression_sync --interval 3600

# Sync every 5 minutes (aggressive)
python -m workflows.suppression_sync --interval 300
```

**Recommendation:** 15 minutes (900s) for active discovery, 1 hour for maintenance mode.

## Output and Monitoring

### Console Output

```
================================================================================
SUPPRESSION CACHE SYNC SUMMARY
================================================================================
Started:  2026-01-06T12:00:00Z
Completed: 2026-01-06T12:00:15Z
Duration: 15.23s

Notion Fetch:
  Pages fetched: 150
  Errors: 0

Processing:
  Entries processed: 145
  With canonical key: 142
  Without canonical key: 3
  Strong keys (domain/CH/CB): 138
  Weak keys (name_loc/github): 4

Cache Update:
  Entries synced: 145
  Expired entries cleaned: 12
================================================================================
```

### JSON Stats (via MCP)

```json
{
  "started_at": "2026-01-06T12:00:00Z",
  "completed_at": "2026-01-06T12:00:15Z",
  "duration_seconds": 15.23,
  "notion_pages_fetched": 150,
  "entries_processed": 145,
  "entries_with_canonical_key": 142,
  "entries_without_canonical_key": 3,
  "entries_with_strong_key": 138,
  "entries_with_weak_key": 4,
  "entries_synced": 145,
  "entries_expired_cleaned": 12,
  "errors_count": 0
}
```

### Logs

```bash
# Enable verbose logging
python -m workflows.suppression_sync --verbose

# Watch logs
tail -f suppression_sync.log
```

## Error Handling

### Missing Canonical Keys

**Problem:** Some Notion pages don't have canonical keys.

**Solution:** Sync job builds them from Website or Company Name.

**Action:** Review warnings and add Canonical Key properties to Notion.

### Notion Rate Limits

**Problem:** Too many requests to Notion API.

**Solution:** Sync uses batch queries (100 pages/request) and respects rate limits.

**Action:** If hitting limits, increase sync interval.

### Database Lock Errors

**Problem:** SQLite database locked during sync.

**Solution:** Sync uses transactions and proper connection pooling.

**Action:** Ensure only one sync job running at a time.

## Integration with Discovery Pipeline

The suppression cache is used by the verification gate:

```python
from storage.signal_store import SignalStore

store = SignalStore("signals.db")
await store.initialize()

# Check if company is suppressed
suppressed = await store.check_suppression("domain:acme.ai")

if suppressed:
    print(f"Already in Notion: {suppressed.status}")
else:
    print("New prospect - proceed with verification")
```

## Troubleshooting

### Sync Not Finding Pages

**Check:**
1. NOTION_DATABASE_ID is correct
2. Notion integration has access to database
3. Status values match exactly (including "Dilligence" typo)

### High Memory Usage

**Cause:** Large Notion database (1000+ pages)

**Solution:**
1. Reduce sync frequency
2. Increase pagination (edit `_query_by_statuses`)
3. Add status filtering (exclude old "Passed" deals)

### Stale Cache

**Symptoms:** Duplicates getting through despite sync

**Solution:**
1. Force refresh: `python -m workflows.suppression_sync --dry-run`
2. Check TTL settings
3. Verify sync is running on schedule

### Slow Sync Performance

**Benchmark:** Should complete in < 30s for 500 pages

**If slower:**
1. Check network latency to Notion
2. Reduce logging verbosity
3. Consider caching Notion schema

## Best Practices

1. **Run on Schedule:** Set up cron/systemd to run every 15 minutes
2. **Monitor Errors:** Alert on `errors_count > 0`
3. **Track Metrics:** Log `entries_without_canonical_key` trend
4. **Set Appropriate TTL:** Balance freshness vs. API usage
5. **Add Canonical Keys:** Proactively add to new Notion pages

## Architecture

```
┌─────────────────┐
│   Notion CRM    │
│  (All Statuses) │
└────────┬────────┘
         │
         │ Fetch pages
         │ (batched, rate-limited)
         ▼
┌─────────────────┐
│ SuppressionSync │
│   (Process)     │
└────────┬────────┘
         │
         │ Extract/build canonical keys
         │ Build SuppressionEntry objects
         ▼
┌─────────────────┐
│  SignalStore    │
│ (SQLite Cache)  │
└────────┬────────┘
         │
         │ Bulk upsert + cleanup
         ▼
┌─────────────────┐
│suppression_cache│
│     (Table)     │
└─────────────────┘
```

## See Also

- `storage/signal_store.py` - Cache storage implementation
- `connectors/notion_connector_v2.py` - Notion API integration
- `utils/canonical_keys.py` - Canonical key building logic
- `workflows/README.md` - Workflows overview

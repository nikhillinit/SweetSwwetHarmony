# Signal Storage Layer

Production-ready SQLite storage for the Discovery Engine with async support, migrations, and connection pooling.

## Overview

The storage layer provides persistent storage for signals with:

- **Deduplication** via canonical keys
- **Processing state tracking** (pending/pushed/rejected)
- **Notion suppression cache** to avoid duplicate pushes
- **Schema migrations** with rollback support
- **Async/await** via aiosqlite
- **Connection pooling** and transaction support
- **JSON serialization** for complex data

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      SignalStore                             │
├─────────────────────────────────────────────────────────────┤
│  signals              │ Raw signals from collectors         │
│  signal_processing    │ Processing state & Notion links     │
│  suppression_cache    │ Local copy of Notion DB (TTL)       │
│  schema_migrations    │ Track applied migrations            │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### Basic Usage

```python
from storage import signal_store

async with signal_store("signals.db") as store:
    # Save a signal
    signal_id = await store.save_signal(
        signal_type="github_spike",
        source_api="github",
        canonical_key="domain:acme.ai",
        company_name="Acme Inc",
        confidence=0.85,
        raw_data={
            "repo": "acme/awesome-ml",
            "stars": 1500,
            "recent_stars": 200,
        }
    )

    # Check for duplicates
    if await store.is_duplicate("domain:acme.ai"):
        print("Already seen this company")

    # Get pending signals
    pending = await store.get_pending_signals()

    # Mark as pushed to Notion
    await store.mark_pushed(signal_id, "notion-page-abc123")
```

### Integration with Collectors

```python
from collectors.github import GitHubCollector
from storage import signal_store
from utils.canonical_keys import build_canonical_key

collector = GitHubCollector()

async with signal_store() as store:
    # Run collector
    results = await collector.collect()

    for result in results:
        # Build canonical key
        canonical_key = build_canonical_key(
            domain_or_website=result.get("website"),
            github_org=result.get("github_org")
        )

        # Check suppression
        if await store.check_suppression(canonical_key):
            print(f"Skipping {canonical_key} - already in Notion")
            continue

        # Save signal
        await store.save_signal(
            signal_type="github_spike",
            source_api="github",
            canonical_key=canonical_key,
            company_name=result["company_name"],
            confidence=result["confidence"],
            raw_data=result
        )
```

### Integration with Verification Gate

```python
from storage import signal_store
from verification.verification_gate_v2 import VerificationGate, Signal

async with signal_store() as store:
    # Get signals for a company
    stored_signals = await store.get_signals_for_company("domain:acme.ai")

    # Convert to verification gate format
    signals = [
        Signal(
            id=str(s.id),
            signal_type=s.signal_type,
            confidence=s.confidence,
            source_api=s.source_api,
            raw_data=s.raw_data,
            detected_at=s.detected_at
        )
        for s in stored_signals
    ]

    # Evaluate
    gate = VerificationGate()
    result = gate.evaluate(signals)

    if result.decision == "auto_push":
        # Push to Notion and mark as pushed
        notion_page_id = await push_to_notion(...)
        for s in stored_signals:
            await store.mark_pushed(s.id, notion_page_id)
```

## Database Schema

### signals

Raw signals from collectors.

| Column          | Type     | Description                              |
|-----------------|----------|------------------------------------------|
| id              | INTEGER  | Primary key                              |
| signal_type     | TEXT     | github_spike, incorporation, etc.        |
| source_api      | TEXT     | github, companies_house, etc.            |
| canonical_key   | TEXT     | domain:acme.ai, companies_house:12345    |
| company_name    | TEXT     | Human-readable company name              |
| confidence      | REAL     | 0.0 to 1.0                               |
| raw_data        | TEXT     | JSON blob with full signal data          |
| detected_at     | TEXT     | When signal was detected (ISO 8601)      |
| created_at      | TEXT     | When signal was saved (ISO 8601)         |

**Unique constraint:** `(canonical_key, signal_type, source_api, detected_at)`

### signal_processing

Processing state for each signal.

| Column          | Type     | Description                              |
|-----------------|----------|------------------------------------------|
| id              | INTEGER  | Primary key                              |
| signal_id       | INTEGER  | FK to signals.id                         |
| status          | TEXT     | pending, pushed, rejected                |
| notion_page_id  | TEXT     | Notion page ID if pushed                 |
| processed_at    | TEXT     | When processed (ISO 8601)                |
| error_message   | TEXT     | Error if rejected                        |
| metadata        | TEXT     | JSON for extra context                   |
| created_at      | TEXT     | Created timestamp                        |
| updated_at      | TEXT     | Updated timestamp                        |

### suppression_cache

Local cache of what's in Notion to avoid duplicates.

| Column          | Type     | Description                              |
|-----------------|----------|------------------------------------------|
| id              | INTEGER  | Primary key                              |
| canonical_key   | TEXT     | Unique - domain:acme.ai                  |
| notion_page_id  | TEXT     | Notion page ID                           |
| status          | TEXT     | Source, Tracking, Passed, etc.           |
| company_name    | TEXT     | Company name                             |
| cached_at       | TEXT     | When cached (ISO 8601)                   |
| expires_at      | TEXT     | When to re-check (ISO 8601)              |
| metadata        | TEXT     | JSON for extra Notion fields             |

### schema_migrations

Track applied migrations.

| Column          | Type     | Description                              |
|-----------------|----------|------------------------------------------|
| version         | INTEGER  | Primary key - migration version          |
| applied_at      | TEXT     | When applied (ISO 8601)                  |
| description     | TEXT     | Description of migration                 |

## API Reference

### SignalStore

#### Constructor

```python
store = SignalStore(
    db_path="signals.db",      # Path to SQLite database
    suppression_ttl_days=7     # How long to cache Notion entries
)
```

#### Lifecycle

```python
await store.initialize()  # Connect and apply migrations
await store.close()       # Close connection
```

Or use context manager:

```python
async with signal_store("signals.db") as store:
    ...
```

#### Signal Operations

```python
# Save a signal
signal_id = await store.save_signal(
    signal_type="github_spike",
    source_api="github",
    canonical_key="domain:acme.ai",
    confidence=0.85,
    raw_data={...},
    company_name="Acme Inc",        # Optional
    detected_at=datetime.now()      # Optional, defaults to now
)

# Get a signal by ID
signal = await store.get_signal(signal_id)

# Get pending signals
pending = await store.get_pending_signals(
    limit=100,                      # Optional
    signal_type="github_spike"      # Optional filter
)

# Get all signals for a company
signals = await store.get_signals_for_company("domain:acme.ai")

# Check if canonical key exists
is_dup = await store.is_duplicate("domain:acme.ai")
```

#### Processing State

```python
# Mark as pushed to Notion
await store.mark_pushed(
    signal_id,
    notion_page_id="abc-123",
    metadata={"status": "Source"}   # Optional
)

# Mark as rejected
await store.mark_rejected(
    signal_id,
    reason="Low confidence score",
    metadata={"confidence": 0.15}   # Optional
)

# Get processing statistics
stats = await store.get_processing_stats()
# Returns: {"pending": 10, "pushed": 5, "rejected": 2}
```

#### Suppression Cache

```python
from storage import SuppressionEntry

# Update cache from Notion sync
entries = [
    SuppressionEntry(
        canonical_key="domain:acme.ai",
        notion_page_id="notion-123",
        status="Source",
        company_name="Acme Inc"
    )
]
await store.update_suppression_cache(entries)

# Check if suppressed
entry = await store.check_suppression("domain:acme.ai")
if entry:
    print(f"Already in Notion: {entry.notion_page_id}")

# Clean expired entries
cleaned = await store.clean_expired_cache()
```

#### Statistics

```python
stats = await store.get_stats()
# Returns:
# {
#     "total_signals": 150,
#     "signals_by_type": {"github_spike": 100, "incorporation": 50},
#     "processing_status": {"pending": 10, "pushed": 130, "rejected": 10},
#     "active_suppression_entries": 50,
#     "database_path": "/path/to/signals.db"
# }
```

## Migration Tools

The `migrations.py` script provides utilities for schema management:

```bash
# List applied migrations
python storage/migrations.py list signals.db

# Export database to JSON (for backup)
python storage/migrations.py export signals.db backup.json

# Import from JSON
python storage/migrations.py import backup.json new_signals.db

# Validate schema
python storage/migrations.py validate signals.db

# Get database info
python storage/migrations.py info signals.db
```

## Testing

Run the test suite:

```bash
python storage/test_signal_store.py
```

Tests cover:
- Database initialization
- Signal CRUD operations
- Duplicate detection
- Processing state management
- Suppression cache
- Transaction rollback
- Statistics

## Production Deployment

### Performance

- **Indexes:** All frequently queried columns have indexes
- **Connection pooling:** Managed by aiosqlite
- **Transactions:** Use `async with store.transaction()` for multi-step operations
- **Batch operations:** Use `update_suppression_cache()` for bulk updates

### Backup Strategy

```bash
# Daily backup
0 2 * * * cd /app && python storage/migrations.py export signals.db backups/signals_$(date +\%Y\%m\%d).json

# Weekly cleanup of old backups
0 3 * * 0 find /app/backups -name "signals_*.json" -mtime +30 -delete
```

### Monitoring

```python
# Get stats for monitoring
stats = await store.get_stats()

# Alert if too many pending
if stats["processing_status"].get("pending", 0) > 1000:
    send_alert("High pending signal count")

# Alert if suppression cache is stale
if stats["active_suppression_entries"] < 10:
    send_alert("Suppression cache may be stale")
```

## Common Patterns

### Collector Integration

```python
async def run_collector_with_storage(collector_name: str):
    """Run a collector and save signals to storage."""
    async with signal_store() as store:
        collector = get_collector(collector_name)

        for result in await collector.collect():
            canonical_key = result["canonical_key"]

            # Check suppression first
            if await store.check_suppression(canonical_key):
                logger.info(f"Skipping {canonical_key} - in suppression cache")
                continue

            # Check duplicates
            if await store.is_duplicate(canonical_key):
                logger.info(f"Skipping {canonical_key} - already have signals")
                continue

            # Save signal
            await store.save_signal(
                signal_type=result["signal_type"],
                source_api=collector_name,
                canonical_key=canonical_key,
                company_name=result.get("company_name"),
                confidence=result["confidence"],
                raw_data=result
            )
```

### Batch Processing

```python
async def process_pending_batch(batch_size: int = 50):
    """Process pending signals in batches."""
    async with signal_store() as store:
        gate = VerificationGate()

        pending = await store.get_pending_signals(limit=batch_size)

        for signal in pending:
            # Get all signals for this company
            company_signals = await store.get_signals_for_company(
                signal.canonical_key
            )

            # Convert to verification format
            verification_signals = [
                Signal(
                    id=str(s.id),
                    signal_type=s.signal_type,
                    confidence=s.confidence,
                    source_api=s.source_api,
                    raw_data=s.raw_data,
                    detected_at=s.detected_at
                )
                for s in company_signals
            ]

            # Evaluate
            result = gate.evaluate(verification_signals)

            if result.decision == "auto_push":
                # Push to Notion
                notion_page_id = await push_to_notion(...)

                # Mark all signals as pushed
                for s in company_signals:
                    await store.mark_pushed(s.id, notion_page_id)

            elif result.decision == "reject":
                # Mark as rejected
                for s in company_signals:
                    await store.mark_rejected(s.id, result.reason)
```

## Troubleshooting

### Database locked

SQLite only allows one writer at a time. Use transactions properly:

```python
# Good: Uses transaction
async with store.transaction() as conn:
    await conn.execute(...)
    await conn.execute(...)

# Bad: Multiple writes without transaction
await store._db.execute(...)  # Don't do this
await store._db.execute(...)
```

### Schema drift

Validate schema regularly:

```bash
python storage/migrations.py validate signals.db
```

### Performance issues

- Check indexes: `EXPLAIN QUERY PLAN SELECT ...`
- Vacuum database: `VACUUM;`
- Analyze tables: `ANALYZE;`

## Future Enhancements

- [ ] PostgreSQL support for multi-writer scenarios
- [ ] Partitioning by date for large datasets
- [ ] Compression for old signals
- [ ] Full-text search on raw_data
- [ ] Automated archival of old signals

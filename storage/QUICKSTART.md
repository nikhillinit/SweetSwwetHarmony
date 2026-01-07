# Signal Storage Quick Start

Get started with the Discovery Engine signal storage layer in 5 minutes.

## Installation

```bash
# Install dependencies (if not already installed)
pip install aiosqlite

# Test the installation
python storage/manual_test_signal_store.py
```

## Basic Example

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
        raw_data={"repo": "acme/ml", "stars": 1500}
    )

    print(f"Saved signal: {signal_id}")
```

## Collector Integration Pattern

```python
from storage import signal_store
from utils.canonical_keys import build_canonical_key

async def run_collector(collector):
    """Run a collector and save signals."""

    async with signal_store() as store:
        results = await collector.collect()

        for result in results:
            # Build canonical key
            canonical_key = build_canonical_key(
                domain_or_website=result.get("website"),
                github_org=result.get("github_org")
            )

            # Check suppression (skip if already in Notion)
            if await store.check_suppression(canonical_key):
                continue

            # Check duplicates (skip if we've seen this signal)
            if await store.is_duplicate(canonical_key):
                continue

            # Save signal
            await store.save_signal(
                signal_type=result["signal_type"],
                source_api="github",
                canonical_key=canonical_key,
                company_name=result["company_name"],
                confidence=result["confidence"],
                raw_data=result
            )
```

## Processing Pattern

```python
from storage import signal_store
from verification.verification_gate_v2 import VerificationGate

async def process_pending():
    """Process pending signals."""

    async with signal_store() as store:
        gate = VerificationGate()

        # Get pending signals
        pending = await store.get_pending_signals(limit=50)

        for signal in pending:
            # Get all signals for this company
            company_signals = await store.get_signals_for_company(
                signal.canonical_key
            )

            # Evaluate with verification gate
            result = gate.evaluate([...])  # Convert to Signal objects

            if result.decision == "auto_push":
                # Push to Notion
                notion_page_id = await push_to_notion(...)

                # Mark as pushed
                await store.mark_pushed(signal.id, notion_page_id)

            elif result.decision == "reject":
                # Mark as rejected
                await store.mark_rejected(signal.id, result.reason)
```

## Common Operations

### Check if company exists

```python
async with signal_store() as store:
    if await store.is_duplicate("domain:acme.ai"):
        print("Already have signals for this company")
```

### Get all signals for a company

```python
async with signal_store() as store:
    signals = await store.get_signals_for_company("domain:acme.ai")
    for sig in signals:
        print(f"{sig.signal_type}: {sig.confidence:.2f}")
```

### Update suppression cache from Notion

```python
from storage import signal_store, SuppressionEntry

async with signal_store() as store:
    # Get entries from Notion
    notion_entries = await fetch_from_notion()

    # Convert to SuppressionEntry objects
    entries = [
        SuppressionEntry(
            canonical_key=entry["canonical_key"],
            notion_page_id=entry["id"],
            status=entry["status"],
            company_name=entry["company_name"]
        )
        for entry in notion_entries
    ]

    # Update cache
    await store.update_suppression_cache(entries)
```

### Get statistics

```python
async with signal_store() as store:
    stats = await store.get_stats()
    print(f"Total signals: {stats['total_signals']}")
    print(f"Pending: {stats['processing_status']['pending']}")
```

## Management Tools

### List migrations

```bash
python storage/migrations.py list signals.db
```

### Export/backup database

```bash
python storage/migrations.py export signals.db backup.json
```

### Import from backup

```bash
python storage/migrations.py import backup.json signals_new.db
```

### Validate schema

```bash
python storage/migrations.py validate signals.db
```

### Get database info

```bash
python storage/migrations.py info signals.db
```

## Examples

Run the full integration example:

```bash
python storage/integration_example.py
```

This will:
1. Create a test database
2. Simulate collector runs
3. Process pending signals
4. Show statistics and queries

## Next Steps

- Read the [full README](README.md) for detailed API documentation
- Check [integration_example.py](integration_example.py) for complete workflow
- Review [manual_test_signal_store.py](manual_test_signal_store.py) for usage examples

## Database Location

By default, the database is created in the current directory:
- `signals.db` - production database
- `test_*.db` - test databases

To use a different location:

```python
async with signal_store("/path/to/signals.db") as store:
    ...
```

## Troubleshooting

**Database locked error:**
- Only one writer at a time
- Use transactions for multi-step operations
- Close connections properly

**Schema validation fails:**
```bash
python storage/migrations.py validate signals.db
```

**Check database size:**
```bash
python storage/migrations.py info signals.db
```

**Performance issues:**
- Indexes are automatically created
- Use `LIMIT` on large queries
- Consider archiving old signals

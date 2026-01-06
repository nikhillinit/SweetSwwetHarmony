# NotionPusher - Quick Start Guide

## Installation

No additional dependencies required beyond the main project requirements.

## Basic Usage (3 Steps)

### 1. Ensure Environment Variables are Set

```bash
export NOTION_API_KEY="secret_xxx"
export NOTION_DATABASE_ID="your-database-id"
```

### 2. Add Signals to Store

```python
from storage.signal_store import SignalStore

store = SignalStore("signals.db")
await store.initialize()

# Add a signal
await store.save_signal(
    signal_type="github_spike",
    source_api="github",
    canonical_key="domain:acme.ai",
    company_name="Acme Inc",
    confidence=0.8,
    raw_data={"repo": "acme/ml", "stars": 500}
)

await store.close()
```

### 3. Push to Notion

```bash
# Command line
python workflows/notion_pusher.py --db signals.db

# Or in Python
from workflows.notion_pusher import run_batch_push

result = await run_batch_push(db_path="signals.db")
print(result.summary())
```

## Common Use Cases

### Preview Before Pushing (Dry Run)

```bash
python workflows/notion_pusher.py --db signals.db --dry-run
```

### Process Limited Batch

```bash
python workflows/notion_pusher.py --db signals.db --limit 50
```

### Custom Configuration

```python
from workflows.notion_pusher import NotionPusher
from storage.signal_store import SignalStore
from connectors.notion_connector_v2 import create_connector_from_env
from verification.verification_gate_v2 import VerificationGate

store = SignalStore("signals.db")
await store.initialize()

notion = create_connector_from_env()

# Strict mode: require 2+ sources for auto-push
gate = VerificationGate(strict_mode=True)

pusher = NotionPusher(
    signal_store=store,
    notion_connector=notion,
    verification_gate=gate
)

result = await pusher.process_batch()
await store.close()
```

## Decision Flow

```
Signal Confidence → Decision → Notion Status
────────────────────────────────────────────
≥ 0.7 + multi-source → AUTO_PUSH → "Source"
0.4 - 0.7 → NEEDS_REVIEW → "Tracking"
< 0.4 → HOLD → (not pushed, stays pending)
Hard kill signal → REJECT → (not pushed, marked rejected)
```

## Testing

### Run Unit Tests

```bash
pytest workflows/test_notion_pusher.py -v
```

### Run Integration Tests

```bash
python workflows/integration_test_pusher.py
```

## Troubleshooting

### No Signals Being Pushed

1. **Check confidence scores:**
   ```python
   pending = await store.get_pending_signals()
   for s in pending:
       print(f"{s.company_name}: {s.confidence}")
   ```

2. **Run dry-run to see decisions:**
   ```bash
   python workflows/notion_pusher.py --db signals.db --dry-run --verbose
   ```

### Notion API Errors

1. **Validate schema:**
   ```python
   from connectors.notion_connector_v2 import create_connector_from_env

   notion = create_connector_from_env()
   validation = await notion.validate_schema()
   print(validation)
   ```

2. **Check required properties exist in Notion:**
   - Discovery ID (Text)
   - Canonical Key (Text)
   - Confidence Score (Number)
   - Signal Types (Multi-select)
   - Status (Select with "Source" and "Tracking" options)

## Examples

See detailed examples in:
- `workflows/example_push_batch.py` - 7 usage examples
- `workflows/integration_test_pusher.py` - 5 integration tests

## Next Steps

1. **Set up collectors** to generate signals:
   - `collectors/github_collector.py`
   - `collectors/companies_house_collector.py`
   - `collectors/domain_collector.py`

2. **Run periodic batch pushes**:
   ```python
   # Every hour
   while True:
       await run_batch_push(limit=100)
       await asyncio.sleep(3600)
   ```

3. **Monitor results**:
   ```python
   stats = await store.get_processing_stats()
   print(stats)
   # {'pending': 50, 'pushed': 100, 'rejected': 10}
   ```

## Key Files

| File | Purpose |
|------|---------|
| `workflows/notion_pusher.py` | Main implementation |
| `workflows/test_notion_pusher.py` | Unit tests |
| `workflows/integration_test_pusher.py` | Integration tests |
| `workflows/example_push_batch.py` | Usage examples |
| `workflows/NOTION_PUSHER_README.md` | Full documentation |

## Support

For detailed documentation, see `workflows/NOTION_PUSHER_README.md`.

For architecture details, see `CLAUDE.md` in the project root.

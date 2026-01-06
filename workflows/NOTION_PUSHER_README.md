# NotionPusher - Batch Signal Processor

## Overview

The **NotionPusher** is a batch processor that processes pending signals from the SignalStore and pushes qualified prospects to Notion. It implements the complete signal aggregation, verification, and routing pipeline.

## Features

### 1. Multi-Source Signal Aggregation
- Groups signals by `canonical_key`
- Aggregates signals from multiple sources for same company
- Merges raw data from all signals
- Tracks signal types and source APIs
- Identifies earliest and latest detection times

### 2. Confidence-Based Routing

Following the rules from CLAUDE.md:

| Confidence | Decision | Notion Status | Description |
|------------|----------|---------------|-------------|
| ≥ 0.7 + multi-source | AUTO_PUSH | "Source" | High confidence, ready for review |
| 0.4 - 0.7 | NEEDS_REVIEW | "Tracking" | Medium confidence, needs verification |
| < 0.4 | HOLD | (not pushed) | Low confidence, wait for more signals |
| Hard kill signal | REJECT | (not pushed) | Company dissolved or other blocker |

### 3. Error Handling & Resilience
- Exponential backoff retry for Notion API failures
- Single failure doesn't stop entire batch
- Comprehensive error logging and audit trail
- Dry-run mode for testing

### 4. Rate Limiting
- Respects Notion API rate limits (3 req/sec)
- Configurable delay between requests
- Built-in retry logic with backoff

## Usage

### Basic Usage

```python
from workflows.notion_pusher import run_batch_push

# Process all pending signals
result = await run_batch_push(
    db_path="signals.db",
    limit=None,  # Process all
    dry_run=False
)

print(result.summary())
```

### Command Line

```bash
# Process all pending signals
python workflows/notion_pusher.py --db signals.db

# Process limited batch
python workflows/notion_pusher.py --db signals.db --limit 50

# Dry run (preview without pushing)
python workflows/notion_pusher.py --db signals.db --dry-run

# Verbose logging
python workflows/notion_pusher.py --db signals.db --verbose
```

### Custom Configuration

```python
from workflows.notion_pusher import NotionPusher
from storage.signal_store import SignalStore
from connectors.notion_connector_v2 import create_connector_from_env
from verification.verification_gate_v2 import VerificationGate

# Initialize components
store = SignalStore("signals.db")
await store.initialize()

notion = create_connector_from_env()

# Custom verification gate (strict mode requires 2+ sources)
gate = VerificationGate(
    strict_mode=True,
    auto_push_status="Source",
    needs_review_status="Tracking"
)

pusher = NotionPusher(
    signal_store=store,
    notion_connector=notion,
    verification_gate=gate,
    dry_run=False
)

# Process batch
result = await pusher.process_batch(limit=100)

await store.close()
```

### Process Single Prospect

```python
pusher = NotionPusher(...)

result = await pusher.process_single_prospect("domain:acme.ai")

print(f"Company: {result.company_name}")
print(f"Decision: {result.decision}")
print(f"Confidence: {result.confidence:.2%}")
print(f"Pushed: {result.pushed}")
```

## Architecture

### Data Flow

```
SignalStore (pending signals)
    ↓
Group by canonical_key
    ↓
AggregatedProspect (multi-source)
    ↓
VerificationGate (confidence scoring)
    ↓
PushDecision (AUTO_PUSH / NEEDS_REVIEW / HOLD / REJECT)
    ↓
NotionConnector (upsert_prospect)
    ↓
Update SignalStore (mark pushed/rejected)
```

### Key Components

#### 1. AggregatedProspect
Represents signals grouped by canonical key:
- `canonical_key`: Unique identifier (e.g., "domain:acme.ai")
- `signals`: List of StoredSignal objects
- `signal_types`: Unique signal types detected
- `sources`: Unique source APIs
- `aggregated_data`: Merged raw data from all signals
- `is_multi_source`: True if signals from 2+ sources

#### 2. PushResult
Result of processing a single prospect:
- `decision`: PushDecision enum
- `confidence`: Confidence score (0.0-1.0)
- `pushed`: Boolean - was it pushed to Notion?
- `notion_page_id`: Notion page ID (if pushed)
- `notion_status`: Notion status (if pushed)
- `error`: Error message (if failed)

#### 3. BatchResult
Result of processing entire batch:
- `total_processed`: Number of prospects processed
- `pushed`: Number pushed to Notion
- `rejected`: Number rejected
- `held`: Number held (low confidence)
- `errors`: Number of errors
- `results`: List of PushResult objects
- `duration_seconds`: Time taken

## Signal Aggregation Logic

### Multi-Source Signals

When multiple signals exist for the same `canonical_key`:

```python
# Example: Two signals for same company
Signal 1: github_spike from GitHub (confidence: 0.7)
Signal 2: incorporation from Companies House (confidence: 0.9)

# Aggregated:
- canonical_key: "domain:acme.ai"
- signal_count: 2
- sources: ["github", "companies_house"]
- is_multi_source: True
- signal_types: ["github_spike", "incorporation"]

# Raw data merged (latest wins on conflicts):
{
    "repo": "acme/ml",
    "stars": 100,
    "company_number": "12345678",
    "website": "acme.ai"
}
```

### Confidence Boost

Multi-source signals get confidence boost from VerificationGate:
- 2 sources: 1.15x multiplier
- 3+ sources: 1.3x multiplier
- Multiple distinct signal types: additional 1.2-1.5x boost

## Error Handling

### Retry Logic

Notion API calls retry with exponential backoff:

```python
MAX_RETRIES = 3
RETRY_DELAY_BASE = 2.0

# Retry delays: 2s, 4s, 8s
```

### Partial Batch Failures

Single prospect failure doesn't stop entire batch:

```python
result = await pusher.process_batch()

# Result shows both successes and failures
print(f"Pushed: {result.pushed}")
print(f"Errors: {result.errors}")

for error in result.error_messages:
    print(f"  - {error}")
```

### Dry Run Mode

Test without actually pushing:

```python
pusher = NotionPusher(..., dry_run=True)
result = await pusher.process_batch()

# Shows what WOULD be pushed, but doesn't actually:
# - Push to Notion
# - Update SignalStore
```

## Signal Status Updates

### Pushed Signals

When successfully pushed to Notion:

```python
await store.mark_pushed(
    signal_id=signal.id,
    notion_page_id="notion-abc-123",
    metadata={
        "confidence": 0.85,
        "status": "Source",
        "decision": "auto_push",
        "verification_status": "multi_source"
    }
)
```

### Rejected Signals

When rejected (hard kill or insufficient evidence):

```python
await store.mark_rejected(
    signal_id=signal.id,
    reason="Hard kill signal: company_dissolved",
    metadata={"hard_kill": True}
)
```

### Held Signals

Low confidence signals remain `pending` in the store, waiting for more signals to arrive.

## Prospect Payload Generation

### ProspectPayload Fields

```python
ProspectPayload(
    # Required
    discovery_id="disc-domain:acme.ai",
    company_name="Acme Inc",
    canonical_key="domain:acme.ai",
    stage=InvestmentStage.PRE_SEED,
    status="Source",  # Or "Tracking"

    # Identity
    website="acme.ai",

    # Discovery fields
    confidence_score=0.85,
    signal_types=["github_spike", "incorporation"],
    why_now="Detected via github_spike, incorporation from 2 source(s). "
            "Confidence: 85%. Latest signal: 2026-01-06.",

    # Enrichment
    short_description="AI testing platform",
    founder_name="Jane Doe",
    location="San Francisco",
    target_raise="$2M Seed"
)
```

### Why Now Generation

Auto-generated summary of discovery context:

```python
def _generate_why_now(prospect, verification_result):
    return (
        f"Detected via {signal_types} from {sources} source(s). "
        f"Confidence: {confidence:.0%}. "
        f"Latest signal: {latest_date}."
    )
```

## Monitoring & Logging

### Log Levels

```python
# INFO: High-level progress
logger.info(f"Processing: {company_name} ({canonical_key})")
logger.info(f"  Signals: {signal_count} from {sources} sources")
logger.info(f"  Verification: {decision} (confidence: {score:.2f})")

# DEBUG: Detailed processing
logger.debug(f"Converted {len(signals)} signals to verification format")

# WARNING: Retries
logger.warning(f"Push failed (attempt 1/3): {error}. Retrying...")

# ERROR: Failures
logger.error(f"Error processing {canonical_key}: {error}")
```

### Batch Summary

```python
result = await pusher.process_batch()

print(result.summary())
# Output:
# Batch Results:
#   Processed: 15
#   Pushed to Notion: 10
#   Rejected: 2
#   Held (low confidence): 3
#   Errors: 0
#   Duration: 12.5s
```

## Testing

### Run Tests

```bash
pytest workflows/test_notion_pusher.py -v
```

### Test Coverage

- Signal aggregation by canonical key
- Multi-source aggregation
- High/medium/low confidence routing
- Hard kill signal rejection
- Error handling and retry
- Partial batch failures
- Dry run mode
- Prospect payload generation
- Batch limits

## Integration Examples

### Continuous Monitoring

```python
async def continuous_push():
    """Run batch pusher on schedule"""
    while True:
        result = await run_batch_push(
            db_path="signals.db",
            limit=50,
            dry_run=False
        )

        if result.total_processed > 0:
            logger.info(result.summary())

        await asyncio.sleep(60)  # Check every minute
```

### Workflow Orchestration

```python
from collectors.github_collector import GitHubCollector
from workflows.notion_pusher import NotionPusher

async def full_pipeline():
    """Collect signals, then push to Notion"""

    # 1. Collect signals
    collector = GitHubCollector(...)
    await collector.collect()

    # 2. Push to Notion
    result = await run_batch_push()

    return result
```

## Configuration

### Environment Variables

```bash
# Required for NotionConnector
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=xxx

# Optional
DATABASE_URL=postgresql://...  # For advanced features
```

### Thresholds

```python
class NotionPusher:
    HIGH_CONFIDENCE_THRESHOLD = 0.7   # AUTO_PUSH
    MEDIUM_CONFIDENCE_THRESHOLD = 0.4 # NEEDS_REVIEW

    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 2.0  # seconds
```

## Troubleshooting

### No Signals Being Pushed

**Check signal confidence:**
```python
pending = await store.get_pending_signals()
for signal in pending:
    print(f"{signal.canonical_key}: {signal.confidence}")
```

**Run in dry-run mode:**
```python
result = await run_batch_push(dry_run=True)
for r in result.results:
    print(f"{r.company_name}: {r.decision} ({r.confidence:.2%})")
```

### Notion API Errors

**Check schema validation:**
```python
validation = await notion.validate_schema()
if not validation.valid:
    print(validation)
```

**Check rate limiting:**
- Default: 0.35s delay between requests (< 3 req/sec)
- Increase if hitting rate limits: `NotionConnector(rate_limit_delay=0.5)`

### Signals Stuck in Pending

**Low confidence:**
- Signals with confidence < 0.4 are held (not rejected)
- Wait for more signals to arrive, or lower threshold

**Check processing status:**
```python
stats = await store.get_processing_stats()
print(stats)
# {'pending': 10, 'pushed': 5, 'rejected': 2}
```

## Related Files

- `storage/signal_store.py` - Signal persistence
- `connectors/notion_connector_v2.py` - Notion API integration
- `verification/verification_gate_v2.py` - Confidence scoring
- `utils/canonical_keys.py` - Deduplication keys
- `workflows/suppression_sync.py` - Sync Notion → local suppression cache

## Future Enhancements

- [ ] Bulk update optimization (batch Notion API calls)
- [ ] Signal prioritization (process high-confidence first)
- [ ] Scheduled batch processing (cron-like)
- [ ] Webhook triggers for real-time pushing
- [ ] A/B testing different confidence thresholds
- [ ] Machine learning for confidence calibration

---
name: collector-framework
description: Universal workflow for building and running signal collectors in the
  Discovery Engine. Use when creating a new collector, running an existing collector,
  debugging collector failures, or understanding the collector architecture. Covers
  the 5-step workflow (Initialize, Fetch, Enrich, Convert, Persist) that all collectors
  follow.
allowed-tools:
- Bash
- Read
- Grep
license: MIT
metadata:
  version: 1.0.0
  category: mcp-enhancement
  author: Press On Ventures
---

# Collector Framework

Universal workflow pattern for building and running signal collectors.

## When to Use This Skill

Use this skill when you want to:
- **Run an existing collector** via conversation (instead of CLI)
- **Create a new collector** (collector #11, #12, etc.)
- **Debug collector failures** (rate limits, API errors)
- **Understand collector architecture** (how signals are collected and stored)

**Trigger phrases:**
- "Run the SEC EDGAR collector"
- "Create a new collector for [source]"
- "Debug the GitHub collector"
- "Explain the collector workflow"

## Quick Start

**Run existing collector:**
```
User: "Run the SEC EDGAR collector"
→ Guides you through execution with dry-run preview
```

**Create new collector:**
```
User: "Create a collector for Product Hunt"
→ Uses template_reference.md to build skeleton
```

## Universal 5-Step Workflow

All collectors follow this pattern:

### Step 1: INITIALIZE
Set up API client, authentication, rate limiting, and retry strategy.

**Code Pattern:**
```python
class MyCollector(BaseCollector):
    def __init__(self, store=None, api_key=None, lookback_days=30):
        super().__init__(store=store, collector_name="my_collector")
        self.api_key = api_key or os.environ.get("MY_API_KEY")
        self.lookback_days = lookback_days
        self._client = None  # httpx.AsyncClient
```

**BaseCollector provides:**
- `rate_limiter` - Automatic rate limiting via `get_rate_limiter(api_name)`
- `retry_config` - Exponential backoff configuration
- `timeout_config` - Operation-specific timeouts

### Step 2: FETCH RAW DATA
Make rate-limited HTTP requests with pagination and error handling.

**Code Pattern:**
```python
async def _fetch_raw_data(self) -> List[Dict]:
    # Rate-limited request
    await self.rate_limiter.acquire()

    # HTTP GET with retry
    response = await self._http_get(url, headers={...}, params={...})

    # Parse response (JSON, XML, or feed)
    data = response.json()

    # Handle pagination
    while len(items) < self.max_items and has_more:
        page += 1
        items.extend(await self._fetch_page(page))

    return items
```

**BaseCollector helpers:**
- `_http_get()` - HTTP GET with retry + rate limit
- `_fetch_with_retry()` - Generic retry wrapper
- `with_retry()` - Decorator for exponential backoff

### Step 3: ENRICH & PARSE
Extract structured data, normalize fields, classify by industry/sector.

**Code Pattern:**
```python
def _enrich_item(self, raw_item: Dict) -> EnrichedData:
    # Extract fields
    company_name = raw_item.get("name")
    website = raw_item.get("url")

    # Normalize dates
    event_date = parse_iso_date(raw_item.get("created_at"))

    # Classify industry (SIC codes, topics, etc.)
    industry_group = classify_industry(raw_item.get("sic_code"))

    # Build canonical keys
    canonical_keys = build_canonical_key_candidates(
        domain_or_website=website,
        fallback_company_name=company_name
    )

    return EnrichedData(...)
```

**Utilities:**
- `build_canonical_key_candidates()` - Multi-candidate deduplication
- `create_provenance()` - Glass.AI audit trail
- `hash_response()` - Change detection hash

### Step 4: CONVERT TO SIGNALS
Transform enriched data to `Signal` objects with confidence scores.

**Code Pattern:**
```python
def to_signal(self, enriched: EnrichedData) -> Signal:
    # Calculate confidence
    confidence = self._calculate_confidence(enriched)

    # Create provenance
    provenance = create_provenance(
        source_url=enriched.url,
        response_data=enriched.raw_data,
        endpoint="/api/path",
        query_params={...}
    )

    return Signal(
        id=f"{self.SOURCE_TYPE}_{enriched.unique_id}",
        signal_type="funding_event",  # or incorporation, github_spike, etc.
        confidence=confidence,
        source_api=self.collector_name,
        source_url=enriched.url,
        source_response_hash=hash_response(enriched.raw_data),
        detected_at=enriched.event_date,
        retrieved_at=datetime.now(timezone.utc),
        verification_status=VerificationStatus.SINGLE_SOURCE,
        verified_by_sources=[self.collector_name],
        raw_data={
            **enriched.__dict__,
            "canonical_key": enriched.canonical_keys[0],
            "canonical_key_candidates": enriched.canonical_keys,
            **provenance,
        }
    )
```

**Confidence Formula Pattern:**
```python
def _calculate_confidence(self, data) -> float:
    base = 0.7  # Base confidence by signal type

    # Boosts
    if is_target_sector(data): base += 0.15
    if has_strong_signal(data): base += 0.1
    if data_complete(data): base += 0.05

    # Penalties
    if age_days > 60: base -= 0.05
    if missing_metadata(data): base -= 0.1

    return min(1.0, max(0.0, base))
```

### Step 5: PERSIST & DEDUPLICATE
Check suppression cache, deduplicate via canonical keys, save to database.

**Code Pattern:**
```python
async def _collect_signals(self) -> List[Signal]:
    # Steps 2-4: Fetch, enrich, convert
    signals = []
    for raw_item in await self._fetch_raw_data():
        enriched = self._enrich_item(raw_item)
        signal = enriched.to_signal()
        signals.append(signal)

    # Step 5: Deduplication (handled by BaseCollector.run())
    return signals  # BaseCollector._save_signals() will dedupe
```

**BaseCollector handles:**
- Extract `canonical_key` from `raw_data`
- Check run-level dedup (`_processed_canonical_keys` set)
- Check database dedup (`store.is_duplicate()`)
- Check suppression cache (`store.check_suppression()`)
- Save to database (`store.save_signal()`)
- Update stats (`signals_new`, `signals_suppressed`)

---

## Integration with MCP

The skill integrates with the internal MCP server:

```python
# MCP prompt: run-collector
mcp__discovery-engine__run-collector(
    collector="sec_edgar",
    dry_run=true
)
```

**Returns:**
```python
CollectorResult(
    collector="sec_edgar",
    status="SUCCESS",  # or PARTIAL_SUCCESS, ERROR, SKIPPED
    signals_found=18,
    signals_new=15,
    signals_suppressed=3,
    error_message=None
)
```

---

## Collector References

For collector-specific details (API endpoints, SIC codes, confidence formulas):

- **[SEC EDGAR](references/sec_edgar_reference.md)** - Form D filings, SIC classification
- **[GitHub](references/github_reference.md)** - Trending repos, spike detection
- **[Companies House](references/companies_house_reference.md)** - UK incorporations, SIC 2007

---

## Creating a New Collector

Use **[Template Reference](references/template_reference.md)** as starting point:

1. Copy template to `collectors/my_collector.py`
2. Fill in API details, authentication, endpoints
3. Implement 5-step workflow
4. Add to `ALLOWED_COLLECTORS` in `discovery_engine/mcp_server.py`
5. Test with dry-run mode

**Estimated time:** 2-4 hours for experienced developer using template

---

## Testing Collectors

### Dry-Run Mode
```bash
python run_pipeline.py collect --collectors my_collector --dry-run
```

**Validates:**
- API authentication
- Pagination logic
- Signal conversion
- Canonical key building

**Does NOT:**
- Write to database
- Call Notion API
- Update suppression cache

### Full Run
```bash
python run_pipeline.py collect --collectors my_collector
```

**Performs:**
- All dry-run validations
- Database writes
- Suppression cache checks
- Change detection (if `ENABLE_ASSET_STORE=true`)

---

## Error Handling Patterns

### Network Errors (Retryable)
```python
# Automatic retry via with_retry()
try:
    response = await self._http_get(url)
except httpx.HTTPStatusError as e:
    if e.response.status_code in [500, 502, 503, 504, 429]:
        # with_retry() handles exponential backoff
        raise  # Will be retried
    elif e.response.status_code == 404:
        # Not found, skip gracefully
        return None
    else:
        # Non-retryable error
        raise
```

### Rate Limits
```python
# Automatic handling via rate_limiter
await self.rate_limiter.acquire()  # Blocks if rate limit hit

# Manual handling for specific APIs
if response.status_code == 429:
    reset_time = response.headers.get("X-RateLimit-Reset")
    wait_seconds = int(reset_time) - time.time()
    await asyncio.sleep(wait_seconds)
```

### Graceful Degradation
```python
# Continue on individual failures
for item in items:
    try:
        signal = self._process_item(item)
        signals.append(signal)
    except Exception as e:
        self._errors.append(str(e))
        logger.warning(f"Skipping item: {e}")
        continue  # Don't fail entire batch
```

---

## Examples

- **[SEC EDGAR Session](examples/sec_edgar_session.md)** - Running SEC collector step-by-step
- **[Creating New Collector](examples/creating_new_collector.md)** - Building collector #11

---

## Success Criteria

**You'll know this skill is working when:**
- You can run any collector via conversation
- You can create a new collector in <4 hours using template
- Collector failures are easy to debug (clear error messages)
- All collectors follow the same 5-step pattern

---

## Related Files

| File | Purpose |
|------|---------|
| `collectors/base.py` | BaseCollector class with helpers |
| `storage/signal_store.py` | SignalStore for persistence |
| `verification/verification_gate_v2.py` | Signal dataclass definition |
| `utils/canonical_keys.py` | Deduplication utilities |
| `collectors/provenance.py` | Glass.AI provenance tracking |
| `collectors/retry_strategy.py` | Exponential backoff helpers |

# Collector Storage Integration

This document describes how collectors have been integrated with the SignalStore for persistent storage.

## Overview

All collectors now inherit from `BaseCollector` and can optionally save signals to a SQLite database via `SignalStore`. This provides:

1. **Persistent storage** of signals across runs
2. **Automatic deduplication** via canonical keys
3. **Suppression checking** against Notion CRM cache
4. **Accurate statistics** (signals_new vs signals_suppressed)
5. **Batch error handling** (one failed signal doesn't break the entire run)

## Architecture

```
BaseCollector (collectors/base.py)
├── Provides: SignalStore integration, deduplication, error handling
├── Abstract method: _collect_signals() → List[Signal]
└── Used by:
    ├── SECEdgarCollector (collectors/sec_edgar.py)
    ├── GitHubCollector (collectors/github.py)
    ├── CompaniesHouseCollector (collectors/companies_house.py)
    └── DomainWhoisCollector (collectors/domain_whois.py)
```

## Key Changes

### 1. Base Collector Class (`collectors/base.py`)

New abstract base class that all collectors inherit from:

**Features:**
- Optional `SignalStore` instance for persistence
- Automatic deduplication via `is_duplicate()` check
- Suppression checking via `check_suppression()`
- Batch error handling (continues on individual signal failures)
- Accurate counting of new vs suppressed signals
- Async context manager pattern support

**Abstract method:**
```python
async def _collect_signals(self) -> List[Signal]:
    """Collect signals from source - implemented by each collector"""
    pass
```

**Main entry point:**
```python
async def run(self, dry_run: bool = True) -> CollectorResult:
    """Run collector, optionally save to store"""
    pass
```

### 2. Updated Collectors

All four collectors have been updated to:

1. **Inherit from BaseCollector**
   ```python
   class SECEdgarCollector(BaseCollector):
       def __init__(self, store: Optional[SignalStore] = None, ...):
           super().__init__(store=store, collector_name="sec_edgar")
   ```

2. **Implement `_collect_signals()` method**
   - Replaces old `run()` method
   - Returns `List[Signal]` instead of `CollectorResult`
   - Base class handles storage and statistics

3. **Include canonical keys in signals**
   - All signals now include `canonical_key` and `canonical_key_candidates` in `raw_data`
   - Built using `build_canonical_key_candidates()` from `utils/canonical_keys.py`
   - Enables deduplication across runs

### 3. Signal Schema Updates

Each signal's `raw_data` now includes:

```python
{
    # ... existing fields ...
    "canonical_key": "domain:example.com",  # Primary key for deduplication
    "canonical_key_candidates": [           # Alternative keys
        "domain:example.com",
        "companies_house_12345678",
        "name_loc:example-inc-ca"
    ]
}
```

## Usage

### Basic Usage (No Persistence)

```python
from collectors.sec_edgar import SECEdgarCollector

# Run without storage (original behavior)
collector = SECEdgarCollector(
    lookback_days=30,
    max_filings=100,
)

result = await collector.run(dry_run=True)
print(f"Found {result.signals_found} signals")
```

### With SignalStore (Persistence)

```python
from collectors.sec_edgar import SECEdgarCollector
from storage.signal_store import signal_store

# Run with storage
async with signal_store("signals.db") as store:
    collector = SECEdgarCollector(
        store=store,
        lookback_days=30,
        max_filings=100,
    )

    result = await collector.run(dry_run=False)
    print(f"Saved {result.signals_new} new signals")
    print(f"Suppressed {result.signals_suppressed} duplicates")
```

### Dry Run Mode

Dry run mode checks for duplicates but doesn't save:

```python
async with signal_store("signals.db") as store:
    collector = SECEdgarCollector(store=store, ...)

    # Checks duplicates but doesn't save
    result = await collector.run(dry_run=True)
    print(f"Would save {result.signals_new} new signals")
```

## Deduplication Strategy

### 1. Within-Run Deduplication

BaseCollector tracks canonical keys processed in the current run to avoid processing the same company multiple times.

### 2. Database Deduplication

Before saving, checks `SignalStore.is_duplicate(canonical_key)`:
- Returns `True` if canonical key exists in `signals` table
- Increments `signals_suppressed` counter

### 3. Notion Suppression

Checks `SignalStore.check_suppression(canonical_key)`:
- Returns `SuppressionEntry` if company already in Notion CRM
- Uses TTL-based cache (default 7 days)
- Prevents duplicate push to Notion

## Error Handling

BaseCollector uses batch error handling:

1. **Individual signal errors** don't fail entire run
2. **Error messages** collected in `self._errors` list
3. **Status codes** reflect partial vs complete success:
   - `SUCCESS`: All signals processed
   - `PARTIAL_SUCCESS`: Some signals had errors
   - `ERROR`: Collector-level failure
   - `DRY_RUN`: Dry run mode

Example:
```python
result = await collector.run(dry_run=False)

if result.status == CollectorStatus.PARTIAL_SUCCESS:
    print(f"Warning: {result.error_message}")
    # But still got: result.signals_new signals saved
```

## Statistics

Each `CollectorResult` now provides accurate counts:

```python
result = await collector.run(dry_run=False)

print(f"Signals found: {result.signals_found}")        # Total raw signals
print(f"Signals new: {result.signals_new}")            # Successfully saved
print(f"Signals suppressed: {result.signals_suppressed}")  # Duplicates/in Notion
print(f"Dry run: {result.dry_run}")                    # Was persistence disabled?
```

**Invariant:** `signals_found >= signals_new + signals_suppressed`

(Difference represents signals that failed to save due to errors)

## Canonical Key Building

Each collector builds canonical keys using `build_canonical_key_candidates()`:

### SEC EDGAR
```python
canonical_key_candidates = build_canonical_key_candidates(
    domain_or_website=self.website or "",
    fallback_company_name=self.company_name,
    fallback_region=self.state or self.country or "",
)
```

### GitHub
```python
canonical_key_candidates = build_canonical_key_candidates(
    domain_or_website=repo.owner_website or repo.homepage or "",
    github_org=repo.org if repo.is_org_owned else "",
    github_repo=repo.repo_full_name,
    fallback_company_name=repo.owner_company or repo.org,
)
```

### Companies House
```python
canonical_key_candidates = build_canonical_key_candidates(
    domain_or_website=self.website or "",
    companies_house_number=self.company_number,
    fallback_company_name=self.company_name,
    fallback_region=self.jurisdiction or "",
)
```

### Domain WHOIS
```python
canonical_key = f"domain:{self.domain}"  # Direct domain key
```

## Testing

Run integration tests:

```bash
cd collectors
python test_collector_storage.py
```

Tests verify:
1. Signals are saved to SignalStore
2. Deduplication works across runs
3. Dry run mode doesn't persist
4. Error handling doesn't break batches
5. Statistics are accurate

## File Changes

### New Files
- `collectors/base.py` - Base collector class
- `collectors/test_collector_storage.py` - Integration tests
- `collectors/COLLECTOR_STORAGE_INTEGRATION.md` - This document

### Modified Files
- `collectors/sec_edgar.py` - Inherits from BaseCollector
- `collectors/github.py` - Inherits from BaseCollector
- `collectors/companies_house.py` - Inherits from BaseCollector
- `collectors/domain_whois.py` - Inherits from BaseCollector

### Key Method Changes

**Before:**
```python
async def run(self, dry_run: bool = True) -> CollectorResult:
    # Fetch signals
    signals = await self._fetch_signals()

    # Manual counting
    return CollectorResult(
        signals_found=len(signals),
        signals_new=len(signals),  # Guess!
        signals_suppressed=0,       # Guess!
    )
```

**After:**
```python
async def _collect_signals(self) -> List[Signal]:
    # Just fetch and return signals
    signals = await self._fetch_signals()
    return signals

# BaseCollector.run() handles:
# - Calling _collect_signals()
# - Saving to store
# - Deduplication
# - Accurate counting
```

## Benefits

1. **No code duplication** - Storage logic in one place (BaseCollector)
2. **Consistent behavior** - All collectors handle persistence the same way
3. **Accurate metrics** - Real counts of new vs duplicate signals
4. **Safer operations** - Batch error handling prevents data loss
5. **Testable** - Easy to verify persistence and deduplication
6. **Backwards compatible** - Can still run without SignalStore

## Future Enhancements

Potential improvements:

1. **Batch inserts** - Save multiple signals in one transaction
2. **Configurable deduplication** - Choose which canonical key types to check
3. **Async signal validation** - Verify signal schema before saving
4. **Metrics tracking** - Prometheus/StatsD integration
5. **Signal versioning** - Track updates to existing signals
6. **Retry logic** - Automatic retry on transient save errors

## Migration Guide

To migrate a new collector to use BaseCollector:

1. **Inherit from BaseCollector:**
   ```python
   from collectors.base import BaseCollector

   class MyCollector(BaseCollector):
       def __init__(self, store: Optional[SignalStore] = None, ...):
           super().__init__(store=store, collector_name="my_collector")
   ```

2. **Rename `run()` to `_collect_signals()`:**
   ```python
   async def _collect_signals(self) -> List[Signal]:
       # Your existing collection logic
       return signals
   ```

3. **Add canonical keys to signals:**
   ```python
   canonical_key_candidates = build_canonical_key_candidates(...)
   canonical_key = canonical_key_candidates[0] if canonical_key_candidates else fallback

   signal.raw_data["canonical_key"] = canonical_key
   signal.raw_data["canonical_key_candidates"] = canonical_key_candidates
   ```

4. **Test it:**
   ```python
   async with signal_store("test.db") as store:
       collector = MyCollector(store=store, ...)
       result = await collector.run(dry_run=False)
       assert result.signals_new > 0
   ```

## Questions?

See:
- `storage/signal_store.py` - SignalStore implementation
- `utils/canonical_keys.py` - Canonical key building
- `collectors/base.py` - BaseCollector implementation
- `collectors/test_collector_storage.py` - Integration tests

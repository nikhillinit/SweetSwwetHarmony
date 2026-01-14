# Test Coverage Design: Critical Modules

**Date:** 2026-01-14
**Status:** Ready for Implementation
**Scope:** Bug fixes + test coverage for signal_store.py, notion_connector_v2.py, migrations.py

---

## Bug Fixes (Completed)

| Bug | File | Fix |
|-----|------|-----|
| Wrong attribute `post.story_id` | `collectors/hacker_news.py:271,277` | Changed to `post.object_id` |
| Missing env vars in `from_env()` | `workflows/pipeline.py:175` | Added `USE_THESIS_FILTER`, `USE_COMPETITOR_DETECTION` |

---

## Coverage Gap Analysis

### Existing Coverage (already tested)

| Module | Existing Tests | What's Covered |
|--------|----------------|----------------|
| signal_store.py | 4 test files | FTS search, filter presets, thesis classification, collector metrics |
| notion_connector_v2.py | 5 test files | Schema repair, validation messages, webhooks, durability, docs |
| migrations.py | None | Nothing |

### Gaps to Fill

| Module | What's MISSING |
|--------|----------------|
| signal_store.py | Core CRUD, status transitions, suppression cache, outbox queue, pipeline runs |
| notion_connector_v2.py | upsert_prospect, find_by_*, create/update page, HTTP mocking |
| migrations.py | Everything |

---

## Test Strategy

### Approach Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Notion API mocking | Full mocking (aioresponses) + separate integration suite | Fast CI, real validation when needed |
| Database approach | Temp file per test | More realistic than :memory:, tests file I/O |
| Coverage target | Comprehensive + property-based (Hypothesis) | Catches edge cases humans miss |
| Integration test location | Separate `tests/integration/` directory | Clear separation, easy to skip |
| CI approach | Local only (solo dev) | No nightly runs, manual integration tests |

### Dependencies to Add

```toml
pytest-asyncio>=0.21.0
aioresponses>=0.7.4
hypothesis>=6.82.0
```

---

## Directory Structure

```
tests/
├── storage/
│   ├── conftest.py                    # NEW: Shared fixtures
│   ├── test_signal_store_crud.py      # NEW: Core CRUD (~25 tests)
│   ├── test_signal_store_status.py    # NEW: Status transitions (~15 tests)
│   ├── test_signal_store_outbox.py    # NEW: Outbox queue (~12 tests)
│   ├── test_signal_store_suppression.py # NEW: Suppression cache (~10 tests)
│   ├── test_signal_store_props.py     # NEW: Property-based (~15 tests)
│   ├── test_migrations.py             # NEW: All migration tests (~20 tests)
│   └── ...existing files...
├── connectors/
│   ├── conftest.py                    # NEW: Mock fixtures
│   ├── test_notion_upsert.py          # NEW: Core upsert (~30 tests)
│   ├── test_notion_lookups.py         # NEW: find_by_* methods (~15 tests)
│   ├── test_notion_properties.py      # NEW: Property builders (~12 tests)
│   └── ...existing files...
└── integration/
    ├── conftest.py                    # NEW: Live API fixtures
    ├── README.md                      # NEW: Setup instructions
    ├── test_notion_live.py            # NEW: Real API tests (~15 tests)
    └── ...existing files...
```

---

## Fixtures

### tests/storage/conftest.py

```python
import pytest
import tempfile
import os
from storage.signal_store import SignalStore

@pytest.fixture
async def store():
    """Fresh SignalStore with temp file DB for each test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = SignalStore(db_path=path)
    await store.initialize()
    yield store
    await store.close()
    os.unlink(path)

@pytest.fixture
async def store_with_signals(store):
    """Store pre-populated with test signals."""
    await store.save_signal(
        signal_type="funding", source_api="sec_edgar",
        canonical_key="ein:123456789", company_name="Acme Corp",
        confidence=0.75, raw_data={"amount": 500000}
    )
    await store.save_signal(
        signal_type="launch", source_api="product_hunt",
        canonical_key="domain:startup.io", company_name="Startup Inc",
        confidence=0.6, raw_data={"votes": 150}
    )
    return store
```

### tests/connectors/conftest.py

```python
import pytest
from aioresponses import aioresponses
from connectors.notion_connector_v2 import NotionConnector, ProspectPayload

@pytest.fixture
def mock_notion():
    """Mock all Notion API HTTP calls."""
    with aioresponses() as m:
        yield m

@pytest.fixture
def connector(mock_notion):
    """NotionConnector with mocked HTTP layer."""
    return NotionConnector(
        api_key="test_secret_key",
        database_id="test_db_12345",
        strict_mode=False,
    )

@pytest.fixture
def sample_prospect():
    """Valid ProspectPayload for testing."""
    return ProspectPayload(
        discovery_id="disc_abc123",
        canonical_key="domain:acme.ai",
        company_name="Acme AI",
        website="https://acme.ai",
        confidence_score=0.75,
        signal_types=["funding", "hiring"],
        why_now="Series A + 5 engineering roles",
    )
```

### tests/integration/conftest.py

```python
import pytest
import os

@pytest.fixture(scope="session")
def notion_test_credentials():
    """Load test Notion workspace credentials."""
    api_key = os.getenv("NOTION_TEST_API_KEY")
    database_id = os.getenv("NOTION_TEST_DATABASE_ID")

    if not api_key or not database_id:
        pytest.skip("NOTION_TEST_API_KEY and NOTION_TEST_DATABASE_ID required")

    return {"api_key": api_key, "database_id": database_id}

@pytest.fixture
async def live_connector(notion_test_credentials):
    """Real NotionConnector for integration tests."""
    from connectors.notion_connector_v2 import NotionConnector

    return NotionConnector(
        api_key=notion_test_credentials["api_key"],
        database_id=notion_test_credentials["database_id"],
    )

@pytest.fixture
def unique_test_id():
    """Unique identifier to isolate test data."""
    import uuid
    return f"test_{uuid.uuid4().hex[:8]}"
```

---

## Test Specifications

### test_signal_store_crud.py (~25 tests)

| Test | Purpose |
|------|---------|
| `test_save_signal_returns_id` | Basic insert returns integer ID |
| `test_save_signal_stores_all_fields` | All fields persisted correctly |
| `test_save_signal_with_minimal_data` | Only required fields |
| `test_save_signal_with_raw_data_json` | JSON serialization works |
| `test_get_signal_returns_stored_signal` | Retrieve by ID |
| `test_get_signal_not_found_returns_none` | Missing ID handling |
| `test_get_pending_signals_returns_pending_only` | Status filtering |
| `test_get_pending_signals_respects_limit` | Pagination |
| `test_get_signals_for_company_by_key` | Canonical key lookup |
| `test_get_signals_for_company_by_name` | Name-based lookup |
| `test_is_duplicate_true_for_existing_key` | Dedup detection |
| `test_is_duplicate_false_for_new_key` | New signal allowed |
| `test_save_pipeline_run_stores_stats` | Pipeline run tracking |
| `test_get_pipeline_runs_ordered_by_date` | Recent runs first |
| `test_get_pipeline_run_by_id` | Single run retrieval |
| +10 edge cases | Nulls, unicode, large data |

### test_signal_store_status.py (~15 tests)

| Test | Purpose |
|------|---------|
| `test_mark_pushed_updates_status` | pending → pushed |
| `test_mark_pushed_sets_notion_page_id` | Links to Notion |
| `test_mark_pushed_sets_timestamp` | Audit trail |
| `test_mark_rejected_with_reason` | Rejection tracking |
| `test_mark_queued_for_review` | Hold for batch |
| `test_update_signal_status_generic` | Any status transition |
| `test_get_signals_by_status_filtering` | Query by status |
| `test_get_status_counts_accurate` | Dashboard stats |
| `test_status_transition_idempotent` | Same status twice OK |
| `test_invalid_status_raises` | Validation |
| +5 edge cases | Concurrent updates, missing signals |

### test_signal_store_outbox.py (~12 tests)

| Test | Purpose |
|------|---------|
| `test_enqueue_notion_write_creates_entry` | Queue insertion |
| `test_enqueue_notion_write_with_payload` | Payload serialization |
| `test_get_pending_outbox_returns_unprocessed` | Fetch queue items |
| `test_get_pending_outbox_respects_limit` | Batch size control |
| `test_get_pending_outbox_ordered_by_created` | FIFO ordering |
| `test_mark_outbox_sent_updates_status` | Success tracking |
| `test_mark_outbox_sent_records_notion_id` | Links result |
| `test_mark_outbox_failed_increments_retry` | Retry counter |
| `test_mark_outbox_failed_records_error` | Error capture |
| `test_outbox_retry_limit_exceeded` | Max retries handling |
| `test_concurrent_outbox_processing` | No duplicate sends |
| `test_outbox_idempotency_key` | Dedup queue entries |

### test_signal_store_suppression.py (~10 tests)

| Test | Purpose |
|------|---------|
| `test_update_suppression_cache_adds_entry` | Cache population |
| `test_update_suppression_cache_with_expiry` | TTL support |
| `test_check_suppression_returns_true_for_cached` | Hit detection |
| `test_check_suppression_returns_false_for_new` | Miss detection |
| `test_check_suppression_by_canonical_key` | Key-based lookup |
| `test_check_suppression_by_domain` | Domain normalization |
| `test_clean_expired_cache_removes_old` | TTL enforcement |
| `test_clean_expired_cache_keeps_valid` | Active entries preserved |
| `test_suppression_cache_bulk_update` | Batch sync from Notion |
| `test_suppression_with_multiple_key_types` | EIN, domain, name |

### test_signal_store_props.py (~15 property-based tests)

| Property Test | Invariant Verified |
|---------------|-------------------|
| `test_save_then_get_roundtrip` | Data integrity on roundtrip |
| `test_status_counts_sum_to_total` | No signals lost in status tracking |
| `test_duplicate_check_consistent` | Dedup logic matches reality |
| `test_pending_limit_respected` | Pagination never over-fetches |
| `test_suppression_cache_invariant` | Cache consistency |
| `test_canonical_key_normalization` | Keys normalized consistently |
| `test_confidence_bounds_preserved` | 0.0-1.0 range enforced |
| `test_timestamps_monotonic` | created_at < updated_at |
| `test_outbox_fifo_ordering` | Queue order preserved |
| `test_pipeline_runs_unique_ids` | No ID collisions |
| `test_concurrent_saves_no_data_loss` | Thread safety |
| `test_transaction_rollback_clean` | Failed tx leaves no trace |
| `test_large_raw_data_handled` | JSON size limits |
| `test_unicode_company_names` | International text support |
| `test_empty_results_not_none` | Empty list vs None consistency |

### test_migrations.py (~20 tests)

| Test | Purpose |
|------|---------|
| `test_list_migrations_shows_all_versions` | All migrations visible |
| `test_list_migrations_shows_applied_status` | Applied vs pending |
| `test_list_migrations_empty_db` | Fresh DB shows all pending |
| `test_migration_version_tracking` | Version table updated |
| `test_migration_v1_to_v2_adds_columns` | Schema evolution |
| `test_migration_v2_to_v3_creates_table` | New table added |
| `test_migration_preserves_existing_data` | No data loss |
| `test_migration_runs_in_order` | Sequential execution |
| `test_migration_idempotent` | Running twice is safe |
| `test_export_data_creates_valid_json` | JSON format correct |
| `test_export_data_includes_all_tables` | Nothing missing |
| `test_export_data_handles_large_db` | 10k+ rows OK |
| `test_import_data_restores_exactly` | Roundtrip integrity |
| `test_import_data_to_empty_db` | Fresh restore works |
| `test_import_data_invalid_json_raises` | Clear error message |
| `test_validate_schema_passes_valid_db` | Healthy DB OK |
| `test_validate_schema_detects_missing_table` | Reports missing |
| `test_validate_schema_detects_missing_column` | Reports missing |
| `test_validate_schema_detects_type_mismatch` | Reports wrong type |
| `test_get_info_shows_stats` | DB info accurate |

### test_notion_upsert.py (~30 tests)

| Test | Purpose |
|------|---------|
| **Happy Path** | |
| `test_upsert_creates_new_page` | No existing → create |
| `test_upsert_updates_existing_by_discovery_id` | Match by discovery_id → update |
| `test_upsert_updates_existing_by_canonical_key` | Match by canonical_key → update |
| `test_upsert_updates_existing_by_website` | Match by website → update |
| `test_upsert_returns_page_id` | Response contains Notion page ID |
| `test_upsert_returns_operation_type` | "created" vs "updated" |
| **Retry Logic** | |
| `test_upsert_with_retry_succeeds_after_transient_failure` | 429 → retry → success |
| `test_upsert_with_retry_respects_max_attempts` | Gives up after N tries |
| `test_upsert_with_retry_exponential_backoff` | Delay increases |
| `test_upsert_rate_limit_header_respected` | Uses Retry-After |
| **Error Handling** | |
| `test_upsert_invalid_api_key_raises` | 401 → clear error |
| `test_upsert_database_not_found_raises` | 404 → clear error |
| `test_upsert_validation_error_raises` | 400 → includes details |
| `test_upsert_server_error_retries` | 500 → retry |
| `test_upsert_timeout_retries` | Network timeout → retry |
| **Deduplication** | |
| `test_upsert_prefers_discovery_id_match` | Priority: discovery_id > canonical_key > website |
| `test_upsert_multiple_matches_uses_first` | Deterministic selection |
| `test_upsert_no_match_creates_new` | All lookups miss → create |
| **Field Mapping** | |
| `test_upsert_maps_all_required_fields` | Nothing missing |
| `test_upsert_handles_optional_fields` | Nulls OK |
| `test_upsert_confidence_to_number_property` | Type conversion |
| `test_upsert_signal_types_to_multiselect` | Array → multi-select |
| `test_upsert_status_routing_high_confidence` | ≥0.7 → "Source" |
| `test_upsert_status_routing_medium_confidence` | 0.4-0.7 → "Tracking" |
| **Idempotency** | |
| `test_upsert_same_data_twice_no_change` | Idempotent updates |
| `test_upsert_idempotency_key_prevents_duplicates` | Race condition protection |
| **Edge Cases** | |
| `test_upsert_unicode_company_name` | International characters |
| `test_upsert_very_long_why_now_truncated` | Field limits |
| `test_upsert_special_chars_in_website` | URL encoding |
| `test_upsert_empty_signal_types` | Empty array OK |

### test_notion_lookups.py (~15 tests)

| Test | Purpose |
|------|---------|
| `test_find_by_discovery_id_found` | Exact match returns page |
| `test_find_by_discovery_id_not_found` | Miss returns None |
| `test_find_by_canonical_key_found` | Key lookup works |
| `test_find_by_canonical_key_normalized` | "DOMAIN:Acme.AI" matches "domain:acme.ai" |
| `test_find_by_website_found` | URL lookup works |
| `test_find_by_website_normalized` | "https://www." stripped |
| `test_find_by_website_trailing_slash` | Trailing slash ignored |
| `test_get_suppression_list_returns_all` | Full cache fetch |
| `test_get_suppression_list_caches_result` | Second call uses cache |
| `test_get_suppression_list_force_refresh` | Bypass cache |
| `test_get_suppression_list_pagination` | Handles >100 results |
| `test_get_portfolio_companies` | Funded status filter |
| `test_query_by_statuses_single` | Filter by one status |
| `test_query_by_statuses_multiple` | Filter by multiple |
| `test_query_empty_result` | No matches → empty list |

### test_notion_properties.py (~12 tests)

| Test | Purpose |
|------|---------|
| `test_build_create_properties_complete` | All fields mapped |
| `test_build_update_properties_partial` | Only changed fields |
| `test_build_taxonomy_properties` | Sector/vertical mapping |
| `test_property_type_title` | Name → title property |
| `test_property_type_url` | Website → URL property |
| `test_property_type_number` | Confidence → number |
| `test_property_type_select` | Status → select |
| `test_property_type_multiselect` | Signal types → multi-select |
| `test_property_type_rich_text` | Why now → rich text |
| `test_normalize_sector_value` | Maps variations |
| `test_extract_text_from_response` | Parse Notion response |
| `test_extract_select_from_response` | Parse select value |

### test_notion_live.py (~15 integration tests)

| Test | Purpose |
|------|---------|
| `test_live_connection_succeeds` | API key valid |
| `test_live_database_accessible` | Database exists |
| `test_live_schema_validation` | Schema matches expected |
| `test_live_create_prospect` | Create real page |
| `test_live_find_by_discovery_id` | Lookup works |
| `test_live_update_prospect` | Modify existing |
| `test_live_upsert_idempotent` | Same data twice OK |
| `test_live_get_suppression_list` | Fetch real cache |
| `test_live_suppression_list_pagination` | Handles large lists |
| `test_live_validate_schema_reports_issues` | Detects drift |
| `test_live_repair_schema_dry_run` | Preview repairs |
| `test_live_invalid_database_id` | Clear error |
| `test_live_rate_limit_handling` | Graceful backoff |
| `test_live_archive_test_pages` | Cleanup test data |
| `test_live_verify_no_test_pollution` | No leftover data |

---

## Integration Test Setup

### tests/integration/README.md

```markdown
# Integration Tests

Run these manually before pushing changes to Notion connector.

## Setup (one-time)
1. Create test Notion database (copy prod schema)
2. Create `.env.test`:
   ```
   NOTION_TEST_API_KEY=secret_xxx
   NOTION_TEST_DATABASE_ID=xxx
   ```

## Running
```bash
source .env.test && pytest tests/integration/ -v
```

That's it. No CI needed.
```

---

## Implementation Phases

### Phase 1: Core CRUD & Critical Paths (~82 tests)

| # | Task | Tests |
|---|------|-------|
| 1 | Create `tests/storage/conftest.py` | - |
| 2 | Write `test_signal_store_crud.py` | 25 |
| 3 | Write `test_signal_store_status.py` | 15 |
| 4 | Write `test_signal_store_outbox.py` | 12 |
| 5 | Write `test_migrations.py` | 20 |
| 6 | Create `tests/connectors/conftest.py` | - |
| 7 | Write `test_notion_upsert.py` | 30 |
| 8 | Run full test suite, fix regressions | - |

### Phase 2: Extended Coverage (~52 tests)

| # | Task | Tests |
|---|------|-------|
| 9 | Write `test_signal_store_suppression.py` | 10 |
| 10 | Write `test_signal_store_props.py` | 15 |
| 11 | Write `test_notion_lookups.py` | 15 |
| 12 | Write `test_notion_properties.py` | 12 |
| 13 | Run full suite, verify coverage | - |

### Phase 3: Integration Suite (~15 tests)

| # | Task | Tests |
|---|------|-------|
| 14 | Set up test Notion workspace | - |
| 15 | Create `tests/integration/conftest.py` | - |
| 16 | Create `tests/integration/README.md` | - |
| 17 | Write `test_notion_live.py` | 15 |

---

## Success Metrics

| Metric | Target |
|--------|--------|
| New test count | ≥160 |
| signal_store.py method coverage | ≥90% |
| notion_connector_v2.py method coverage | ≥85% |
| migrations.py coverage | 100% |
| All unit tests pass locally | Yes |
| Integration tests pass in test workspace | Yes |

---

## Running Tests

```bash
# Unit tests only (default)
pytest

# Include integration tests (before Notion changes)
pytest tests/integration/ -v

# With coverage report
pytest --cov=storage --cov=connectors --cov-report=term-missing
```

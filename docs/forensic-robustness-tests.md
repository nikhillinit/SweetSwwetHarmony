# Forensic Engineer Report: Robustness Test Gaps

**Date:** 2026-02-15
**Workflow:** Manual Forensic Engineer (4-phase)
**Backend:** Claude Code (Codex backend timed out on large context — signal_store.py ~6000 lines)

## Phase 0: ANALYZE

Gap analysis identified 10 critical missing test scenarios across 3 integration test files (32 existing tests). Key gaps:

1. No suppression persistence across store lifecycle
2. No malformed/non-Signal object handling tests
3. No mixed collector outcome aggregation tests
4. No merge cascade edge cases (nonexistent entities)
5. No batch commit idempotency (double-commit guard)
6. No signal validation boundary tests
7. No suppression cache idempotency verification
8. No stress integrity test (merge + batch combined)

## Phase 1: PLAN

18 new tests planned across 3 files:

| File | New Tests | Gap Coverage |
|------|-----------|-------------|
| `test_pipeline_roundtrip.py` | 6 | Suppression persistence, multi-source dedup, dry-run immutability, JSON fidelity, idempotent cache, gate crash safety |
| `test_collector_resilience.py` | 6 | None handling, dict-instead-of-Signal, empty collector, mixed outcomes, aggregate success, timeout isolation |
| `test_error_recovery.py` | 6 | Nonexistent entity merge (2 cases), double-commit guard, extreme confidence, suppression idempotency, stress integrity |

## Phase 2: EXECUTE

All 18 tests written following existing patterns:
- `pytest-asyncio` with temp SQLite DB fixture
- Monkeypatched env vars (DELIVERY_MODE, LLM_THESIS_MODE, etc.)
- Test doubles: FakeCollector, ErrorCollector, SuccessCollector, etc.
- Helper factories: `_make_signal()`, `_seed_signal()`, `_seed_company_file()`

### Key Findings During Implementation

1. **`store.db_path`** (not `store._db_path`) — public attribute on SignalStore
2. **`cascade_merge()` with nonexistent winner**: Loser's company_file gets *reassigned* to winner (not skipped). `company_file_merged = True`.
3. **`cascade_merge()` with both nonexistent**: Completes silently, `signals_reassigned=0`, `company_file_merged=False`
4. **`commit_batch()` double-commit**: Raises `BatchStateError` immediately (no mutations)
5. **`save_signal()` confidence**: No validation — stores any float value as-is
6. **`update_suppression_cache()`**: Uses `INSERT...ON CONFLICT DO UPDATE` — inherently idempotent

## Phase 3: VERIFY

```
50 passed in 11.11s (32 existing + 18 new)
```

All existing tests remain green. No regressions introduced.

## Remaining Gaps (Lower Priority)

- Circuit breaker pattern (collector auto-disable after N failures)
- Concurrent state update race conditions (requires threading tests)
- Merge rollback via drift fingerprint comparison
- Retry-After header parsing in HTTP 429 handling
- Entity_migrations audit trail verification after merge

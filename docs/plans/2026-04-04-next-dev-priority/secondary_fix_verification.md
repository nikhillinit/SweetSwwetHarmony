# Secondary Fix Verification - 2026-04-04

## ACH Regression

Original concern:
- `tests/dashboard/test_ach_matrix_view.py::TestACHGridRendering::test_grid_renders_with_dataframe`

Current result:
- no code change was required
- targeted regression results:
  - `pytest tests/dashboard/test_ach_matrix_view.py -q` -> `12 passed`
  - `pytest tests/monitoring/test_alert_escalation.py::TestComputeMTTA::test_mtta_with_acknowledged_alerts -q` -> `1 passed`

Conclusion:
- the previously reported ACH failure no longer reproduces on the current tree
- the previously suspected MTTA failure also passes

## Orphan Pending Queue Cleanup

Restored DB reality differed from the stale planning note:

- total `company_name IS NULL` rows: `367`
- pending null-name rows: `24`
- all pending null-name rows were in `news_api` / `rss_feeds`

Scoped cleanup:

1. Dry-ran `scripts/backfill_company_extraction.py` on the 24 pending IDs.
2. Structured extraction safely recovered 7 rows:
   - `43` -> `Reconcept`
   - `102` -> `DORSIA`
   - `104` -> `DORSIA`
   - `146` -> `PetBux`
   - `185` -> `Laka`
   - `315` -> `Tractor Beverage Company`
   - `476` -> `Jest`
3. Committed those 7 fill-empty-only updates.
4. Rejected the remaining 17 still-pending unresolved rows with reason:
   - `cleanup: stale unresolved signal missing company_name after structured extraction pass`

Post-cleanup verification:

```json
{
  "total_orphans": 360,
  "pending_orphans": 0,
  "recovered_named_signals": 7,
  "processing_statuses": [
    ["held", 464],
    ["pending", 15],
    ["pushed", 15],
    ["queued", 2],
    ["rejected", 115]
  ]
}
```

Conclusion:
- pending null-name queue is now clean (`0`)
- unresolved stale items were retired from the queue instead of lingering indefinitely

## Mercari Thesis-Classify-Batch Edge Case

Signal `438`:
- `source_api`: `greenhouse_jobs`
- `company_name`: `Mercari`
- `created_at`: `2026-02-26T06:33:29.255596+00:00`
- `detected_at`: `2025-12-15T17:43:09-05:00`
- existing thesis rows before fix: none

Root cause:
- `ops/quality/thesis.py::iter_signals_missing_thesis()` windowed on `detected_at`, which excluded recently ingested backfill candidates whose underlying event timestamp was older.

Fix:
- switched the selector window from `detected_at` to `created_at`
- ordered by `created_at DESC, id DESC`
- added focused regression coverage in `tests/ops/quality/test_thesis.py`

Verification:
- `pytest tests/ops/quality/test_thesis.py -q` -> `11 passed`
- direct selector check on production DB:

```json
{
  "attempted_window_90_limit_200": 1,
  "contains_438": true,
  "index_438": 0,
  "first_20": [438]
}
```

Conclusion:
- signal `438` is now in the 90-day `thesis-classify-batch` candidate set

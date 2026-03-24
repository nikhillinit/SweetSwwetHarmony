# Known Test Failures Baseline

**Commit:** obs/step4a-window-mar19-23
**Date:** 2026-03-19
**Known failures:** 0

## Policy

- Any NEW failure not listed below is a gate failure and must be fixed before merge.
- Removals from this list (failures that start passing) are welcome and should be committed.
- To regenerate: `python -m pytest tests/workflows/test_confidence_routing.py -v --tb=no -q`

## Resolved Failures

### 6 delivery-policy failures (resolved 2026-03-19)

**Root cause:** `DELIVERY_MODE=staging_only` (default) blocks Notion writes. Tests exercise
the `AUTO_PUSH` path which requires `DELIVERY_MODE=auto_publish`.

**Fix:** Added `autouse` fixture to `TestConfidenceBasedRouting` that sets
`DELIVERY_MODE=auto_publish` via `monkeypatch.setenv()`. Added sanity check test
`test_default_delivery_mode_is_staging_only` confirming default is safe outside fixtures.

| # | Test Node ID |
|---|-------------|
| 1 | `tests/workflows/test_confidence_routing.py::TestConfidenceBasedRouting::test_high_confidence_multi_source_routes_to_source` |
| 2 | `tests/workflows/test_confidence_routing.py::TestConfidenceBasedRouting::test_medium_confidence_routes_to_tracking` |
| 3 | `tests/workflows/test_confidence_routing.py::TestConfidenceBasedRouting::test_conflicting_signals_needs_review` |
| 4 | `tests/workflows/test_confidence_routing.py::TestConfidenceBasedRouting::test_routing_uses_suggested_status` |
| 5 | `tests/workflows/test_confidence_routing.py::TestConfidenceBasedRouting::test_high_confidence_single_source_strict_mode_needs_review` |
| 6 | `tests/workflows/test_confidence_routing.py::TestConfidenceBasedRouting::test_multi_source_aggregation_before_routing` |

### 1 emergency halt ordering failure (resolved 2026-03-19)

**Root cause:** Listed as intermittent audit event ordering issue, but the test
(`test_publish_queued_emergency_halt`) now passes consistently. The test correctly
queries audit_log by `entity_id` and `ORDER BY created_at DESC LIMIT 1`, which
returns the last transition regardless of insertion timing.

**Fix:** No code change needed. Removed from known failures list after verifying
it passes on 2 consecutive runs.

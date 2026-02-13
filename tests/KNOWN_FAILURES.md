# Known Test Failures Baseline

**Commit:** 856689f693463e4ae885315f50f60daf4e8efd3c
**Date:** 2026-02-13
**Collected:** 6735 tests, 0 collection errors
**Known failures:** 6

## Policy

- Any NEW failure not listed below is a gate failure and must be fixed before merge.
- Removals from this list (failures that start passing) are welcome and should be committed.
- To regenerate: `python -m pytest tests/workflows/test_confidence_routing.py -v --tb=no -q`

## Exempted Failures

All 6 failures share the same root cause: `DELIVERY_MODE=staging_only` (the default)
blocks Notion writes. These tests exercise the full push path which requires a
permissive delivery mode. They pass when `DELIVERY_MODE=batch_publish` or higher.

**Owner:** delivery policy design (intentional -- staging_only is the safe default)

| # | Test Node ID |
|---|-------------|
| 1 | `tests/workflows/test_confidence_routing.py::TestConfidenceBasedRouting::test_high_confidence_multi_source_routes_to_source` |
| 2 | `tests/workflows/test_confidence_routing.py::TestConfidenceBasedRouting::test_medium_confidence_routes_to_tracking` |
| 3 | `tests/workflows/test_confidence_routing.py::TestConfidenceBasedRouting::test_conflicting_signals_needs_review` |
| 4 | `tests/workflows/test_confidence_routing.py::TestConfidenceBasedRouting::test_routing_uses_suggested_status` |
| 5 | `tests/workflows/test_confidence_routing.py::TestConfidenceBasedRouting::test_high_confidence_single_source_strict_mode_needs_review` |
| 6 | `tests/workflows/test_confidence_routing.py::TestConfidenceBasedRouting::test_multi_source_aggregation_before_routing` |

## Error Detail (confidence routing)

```
workflows.delivery_policy.DeliveryPolicyError: Notion write blocked: intent=auto_push
is not allowed in DELIVERY_MODE=staging_only.
Set DELIVERY_MODE to a permissive mode to proceed.
```

## Additional Known Intermittent Failure

**Owner:** audit event actor ordering (Phase 1a emergency halt path)

| # | Test Node ID |
|---|-------------|
| 7 | `tests/integration/test_phase1a_identity.py::TestEmergencyHalt::test_publish_queued_emergency_halt` |

### Error Detail (emergency halt)

```
assert audit[3] == "compliance_officer"
AssertionError: assert 'system' == 'compliance_officer'
```

**Root cause:** The test queries `audit_events` for a `status_transition` event and
expects `actor_id="compliance_officer"`, but the review store records the automatic
halt transition with `actor_id="system"`. The test assumption does not account for
the two-event sequence (system halt + officer reject). Intermittent because audit
event ordering can vary by insertion timing.

# Test Spec: Learning-Loop-Only Operator Workflow

Date: 2026-04-06
Companion PRD: `.omx/plans/prd-learning-loop-only.md`
Normative diagnostic branch source: `.omx/plans/sweetharmony-executive-decision-layer.md` Gate B + Three-State Recommendation Tree

## Objective

Verify that the learning-loop-only branch becomes a real, repeatable operator loop by reusing the current quality surfaces for:
1. disagreement review
2. batch/manual labeling
3. periodic router-diagnostic rerun

The test plan must also prove that v1 stays intentionally small:
- no new persistent queue subsystem
- no threshold lowering
- no pre-review-loosening logic

## Frozen Diagnostic Contract

Parity target: `artifacts/router-diagnostic/2026-04-06/summary.json`
Threshold source: `verification/verification_gate_v2.py` `VerificationGate.HIGH_CONFIDENCE_THRESHOLD`

Required top-level fields:
- `date`
- `db_path`
- `window_days`
- `quality_stats`
- `join_coverage`
- `discrimination`
- `branch_recommendation`
- `reproduction`

Required nested fields:
- `quality_stats.labeled`
- `quality_stats.decided`
- `quality_stats.tp`
- `quality_stats.fp`
- `quality_stats.unsure`
- `quality_stats.adj`
- `quality_stats.fp_rate`
- `join_coverage.decisive_joined_rows`
- `join_coverage.tp_rows`
- `join_coverage.fp_rows`
- `join_coverage.latest_row_mismatches`
- `discrimination.auc`
- `discrimination.tp_mean`
- `discrimination.fp_mean`
- `discrimination.mean_separation`
- `discrimination.score_max`
- `discrimination.threshold_0_7.tp`
- `discrimination.threshold_0_7.fp`
- `discrimination.threshold_0_7.fn`
- `discrimination.threshold_0_7.tn`
- `branch_recommendation.name`
- `branch_recommendation.reason`
- `reproduction.quality_stats_command`
- `reproduction.notes`

Required invariants:
1. `window_days == 90`
2. `quality_stats.decided == quality_stats.tp + quality_stats.fp`
3. `join_coverage.decisive_joined_rows == join_coverage.tp_rows + join_coverage.fp_rows`
4. `join_coverage.latest_row_mismatches == 0` for a computable run
5. branch selection uses the explicit normative predicates:
   - `score_collapse_confirmed` when `discrimination.mean_separation < 0.05` or `discrimination.auc < 0.65`
   - `threshold_ceiling_only` when separation is acceptable but `discrimination.score_max < HIGH_CONFIDENCE_THRESHOLD`
   - `no_routing_problem_detected` when separation is acceptable and `discrimination.score_max >= HIGH_CONFIDENCE_THRESHOLD`
   - `diagnostic_cannot_be_computed` when computability/integrity gates fail before predicate evaluation

Fallback behavior:
- when decisive-label joins or latest-row integrity are not credible, the diagnostic must fail closed and emit `branch_recommendation.name = diagnostic_cannot_be_computed`
- no other branch may be inferred in that condition

## Verification Contract

### Workflow Contract

1. The workflow surface is discoverable from the CLI.
2. The workflow sits on top of shared read-only structured providers.
3. Direct commands continue to work outside the workflow wrapper.

### Provider Contract

1. A disagreement-candidate provider returns structured disagreement rows directly from source data.
2. An ADJ-candidate provider returns structured ADJ follow-up rows directly from source data.
3. A diagnostic-summary provider returns the frozen diagnostic contract directly and does not depend on Markdown/text parsing.
4. Provider homes are pinned to:
   - `ops/quality/thesis.py` for disagreement provider
   - `ops/quality/labels.py` for ADJ provider
   - `ops/quality/stats.py` for diagnostic-summary provider
5. The diagnostic-summary provider computes structured summary only; `ops/quality_cli.py` owns artifact writing/rendering.
6. The diagnostic-summary provider reuses `get_overall_stats()` from `ops/quality/stats.py` for the `quality_stats` block.

### Labeling Contract

1. Batch labeling uses the same manual-label helper path as direct labeling.
2. Manual labels still write append-only audit rows.
3. Manual labels still become the latest resolved label and still win over inferred labels.

### Diagnostic Contract

1. Rerun artifacts remain comparable to `artifacts/router-diagnostic/2026-04-06/summary.md`.
2. Branch logic remains the same three computable branches plus one fail-closed fallback.
3. `HIGH_CONFIDENCE_THRESHOLD=0.7` remains unchanged.
4. If the comparison path is not credible, the rerun fails closed as `diagnostic_cannot_be_computed`.
5. Threshold comparisons are sourced from `verification/verification_gate_v2.py`, not duplicated ad hoc in the workflow wrapper.

### Canonical JSON Schema Contract

`review-set` canonical JSON:

Required top-level fields:
- `schema_version`
- `generated_at`
- `db_path`
- `window_days`
- `sort_key`
- `items`

Required per-item fields:
- `signal_id`
- `queue_type`
- `canonical_key`
- `source_api`
- `detected_at`
- `priority_rank`
- `reason_code`
- `reason_summary`

Deterministic sort:
- `queue_type ASC`, `priority_rank ASC`, `detected_at DESC`, `signal_id DESC`

Failure behavior:
- unknown `queue_type`, missing required fields, or non-canonical sort order must fail validation before artifact emission
- empty result sets must still emit a valid payload with `items: []`

`apply-labels` canonical JSON:

Required top-level fields:
- `schema_version`
- `requested_by`
- `requested_at`
- `sort_key`
- `items`

Required per-item fields:
- `signal_id`
- `label`
- `created_by`
- `reason`

Deterministic sort:
- `signal_id ASC`

Failure behavior:
- reject the entire batch before writes if schema validation fails, duplicate `signal_id` values exist, labels are outside `TP|FP|UNSURE|ADJ`, or the input is not canonically sorted
- after validation, persistence continues through `label_signal_manual()` and runtime write failures must be reported explicitly per item

### Scope Contract

1. No new queue table or durable workflow-state table is added.
2. No scheduler/background daemon path is added in v1.
3. No pre-review-loosening behavior is encoded.

## Unit Tests

1. Disagreement-provider unit test:
   - returns structured disagreement candidates
   - does not depend on Markdown report parsing
   - ordering/filtering are deterministic
2. ADJ-provider unit test:
   - returns structured ADJ candidates
   - includes the fields needed for operator review
   - ordering/filtering are deterministic
3. Diagnostic-provider unit test:
   - emits the frozen contract fields
   - enforces the contract invariants on a computable fixture
   - reuses `get_overall_stats()` and the threshold source from `verification/verification_gate_v2.py`
4. Review-set assembly test:
   - disagreement candidates are included
   - ADJ revisit candidates are included when requested
   - provider outputs are composed without text parsing
   - payload matches the canonical `review-set` schema and sort order
5. Batch-label parsing test:
   - valid operator inputs parse correctly
   - invalid label values fail clearly
   - duplicate `signal_id` values fail before any write
6. Labeling helper reuse test:
   - batch path calls the same manual-label helper used by `quality label`
7. Diagnostic numerical parity test:
   - rerun output preserves the required numeric fields/invariants
   - threshold bucket counts at `0.7` are checked numerically, not just by layout
8. Fail-closed diagnostic unit test:
   - non-computable fixture emits `diagnostic_cannot_be_computed`
   - no other branch is produced
9. Guardrail test:
   - no code path changes `HIGH_CONFIDENCE_THRESHOLD` as part of the workflow

## Integration Tests

1. SQLite-backed batch-label test:
   - apply a bounded batch
   - verify one audit row per label in `quality_feedback`
   - verify the latest resolved label in `signal_quality_metrics`
2. CLI help/argument test:
   - workflow surface appears in help text
   - existing `quality label`, `quality adj-review`, `quality thesis-disagreement-report`, and `quality thesis-refresh-latest` remain callable
3. Review-set artifact test:
   - run the workflow on fixture data
   - verify the output artifact is reproducible and auditable
   - verify JSON is canonical source and any Markdown view is derived
4. Diagnostic-rerun parity test:
   - generate a rerun artifact on fixture/scratch data
   - verify the artifact includes the frozen contract fields
   - verify the required numerical invariants and integrity counts
   - verify the provider does not write files directly; CLI wrapper owns artifact creation
5. Non-computable diagnostic integration test:
   - break computability conditions on fixture/scratch data
   - verify the written artifact records `diagnostic_cannot_be_computed`
   - verify no alternate branch is selected

## E2E / Operator Tests

1. Scratch operator cycle:
   - generate review set
   - apply a bounded batch of labels
   - rerun the diagnostic
   - verify a new dated artifact is written
2. Repeatability test:
   - run a second cycle with no new inputs
   - verify deterministic behavior or an explicit no-op message
3. Failure-closed test:
   - simulate missing/non-comparable diagnostic join conditions
   - verify rerun exits without silently selecting a branch
   - verify the resulting artifact records `diagnostic_cannot_be_computed`

## Observability Checks

1. Workflow output includes enough context for an operator to see:
   - how many review candidates were materialized
   - how many labels were applied
   - where the latest rerun artifact was written
2. Diagnostic artifacts include dated output paths under `artifacts/router-diagnostic/`.
3. Runbook text explicitly states:
   - current active branch is learning-loop-only
   - threshold stays at `0.7`
   - reruns must remain comparable to the `2026-04-06` basis
   - docs landing spot is `docs/runbooks/learning-loop-only.md`

## Exit Gates

1. Shared read-only providers exist for disagreement, ADJ, and diagnostic-summary data.
2. One small operator workflow surface exists and is discoverable.
3. Existing direct commands still work.
4. Batch labeling and single labeling share the same persistence semantics.
5. Review-set artifacts are derived from existing disagreement and ADJ surfaces.
6. Router-diagnostic reruns produce dated comparable artifacts with frozen-field and numerical parity.
7. Non-computable runs fail closed as `diagnostic_cannot_be_computed`.
8. The branch logic and threshold remain unchanged.
9. No new queue table, scheduler, or loosening path exists in v1.
10. `review-set` and `apply-labels` are each validated against one canonical JSON schema with deterministic sorting.
11. Provider-first architecture is preserved: structured providers compute data, CLI wrapper writes artifacts.

## Not-Tested / Deferred

1. Persistent queue infrastructure.
2. Scheduler automation for the operator loop.
3. Any branch behavior that lowers downstream thresholds.
4. Broader SweetSweetHarmony expansion beyond the learning-loop-only lane.

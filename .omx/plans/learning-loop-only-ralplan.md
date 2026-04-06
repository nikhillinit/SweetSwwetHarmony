# Learning-Loop-Only: RALPLAN Short Draft

Date: 2026-04-06
Status: initial consensus draft

## Final Draft Position

The learning-loop-only branch should not introduce a new persisted queue/orchestration subsystem first.
The diagnostic branch predicates are not implicit: they are normative from `.omx/plans/sweetharmony-executive-decision-layer.md` Gate B / Three-State Recommendation Tree and must be preserved verbatim in the PRD and test spec for this lane.

Approved draft shape:
1. first extract shared read-only structured providers for disagreement candidates, ADJ candidates, and diagnostic-summary computation
2. then add one thin operator workflow surface on top of `quality`
3. route batch labeling through the existing manual-label helper
4. freeze router-diagnostic parity to the `2026-04-06` artifact contract, including explicit `diagnostic_cannot_be_computed` fail-closed behavior
5. land the operator docs in `docs/runbooks/learning-loop-only.md`
6. keep the lane small and explicitly separate from any threshold or loosening changes

## Why This Shape

Current repo evidence already provides most of the operator loop primitives:
- `ops/quality_cli.py` already exposes `label`, `thesis-disagreement-report`, `adj-review`, and `thesis-refresh-latest`
- `ops/quality/labels.py` already gives append-only manual feedback plus resolved-label upsert with manual precedence
- `ops/quality/thesis.py` already provides disagreement reporting and thesis refresh helpers

What is missing is composition, not a new state model.
The composition should be provider-first, not report-first.

Pinned implementation homes and reuse points:
- disagreement provider home: `ops/quality/thesis.py`, adjacent to `generate_disagreement_report()` and `batch_refresh_latest_missing_provenance()`
- ADJ provider home: `ops/quality/labels.py`, moving the current `quality adj-review` read-query out of `ops/quality_cli.py` into a reusable read-only provider beside manual-label semantics
- diagnostic-summary provider home: `ops/quality/stats.py`, reusing `get_overall_stats()` for the `quality_stats` block and keeping summary computation near existing quality metrics
- CLI wrapper / artifact-writing home: `ops/quality_cli.py`; provider returns structured summary only, wrapper writes `summary.json` / `summary.md`
- threshold source of truth: `verification/verification_gate_v2.py` `VerificationGate.HIGH_CONFIDENCE_THRESHOLD = 0.7`; this lane must read or mirror that value, not redefine it elsewhere

## RALPLAN-DR Summary

### Principles

1. Reuse current quality storage and CLI surfaces first.
2. Extract shared structured providers before adding the operator front door.
3. Prefer a thin operator front door over new infrastructure.
4. Preserve diagnostic comparability with `artifacts/router-diagnostic/2026-04-06/`.
5. Keep learning-loop work separate from any loosening decision.
6. Fail closed when a rerun is not credibly comparable.

### Decision Drivers

1. smallest path to a real operator loop
2. lowest risk of state-model duplication
3. strongest preservation of auditability and branch comparability

### Viable Options

#### Option A: Thin operator workflow surface

Recommended.

Pros:
- provider-first design avoids coupling to Markdown/text outputs
- concrete loop
- reuses existing modules directly
- no new durable state

Cons:
- still adds a small new CLI surface

#### Option B: Runbook-only composition of current commands

Pros:
- smallest code diff

Cons:
- weak repeatability
- no single execution-ready workflow surface

#### Option C: Persisted queue/orchestration layer

Pros:
- richer future control

Cons:
- over-scoped for current evidence
- conflicts with the small-surface requirement

### Decision

Choose **Option A**.

Reason:
- It gives the branch an actual operator loop without paying for new infrastructure that the current evidence does not justify.
- The provider layer makes the CLI and tests depend on structured data contracts instead of report text.

## Active Artifacts

- `.omx/plans/prd-learning-loop-only.md`
- `.omx/plans/test-spec-learning-loop-only.md`

## Key Constraints

1. Do not lower `HIGH_CONFIDENCE_THRESHOLD=0.7`.
2. Do not plan pre-review loosening in this lane.
3. Reuse current `quality` CLI and modules where possible.
4. Keep v1 free of new queue tables and scheduler automation.
5. Use the `2026-04-06` router-diagnostic artifact as the comparison basis for later reruns.
6. Freeze parity to the current JSON contract and require explicit `diagnostic_cannot_be_computed` fallback behavior.
7. Preselect docs landing at `docs/runbooks/learning-loop-only.md`.
8. Pin provider homes and keep artifact writing outside provider logic.

## Execution Handoff

Recommended:
- `$ralph` for a single-owner implementation and verification loop because the work is cohesive and bounded

Alternative:
- `$team` if you want workflow, labeling semantics, diagnostic parity, and verification split into separate lanes

## Verification Focus

1. review-set output is derived from existing disagreement and ADJ surfaces
2. providers are structured and read-only, with no Markdown parsing dependency
3. batch labeling uses the same persistence semantics as `quality label`
4. router-diagnostic reruns stay comparable to the current dated artifact basis
5. non-computable runs emit `diagnostic_cannot_be_computed`
6. threshold and branch logic remain unchanged
7. no new persisted queue infrastructure appears in v1
8. `review-set` and `apply-labels` use explicit canonical JSON contracts with deterministic sort order and fail-fast validation

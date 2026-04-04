# Test Spec: temporal-puzzling-dove execution sync

## Scope

Verify that the fallback execution of `temporal-puzzling-dove` is complete by proving:
- the canonical repo plan at `C:\dev\Harmonic\docs\plans\2026-04-03-thesis-classifier-delta-spec.md` remains the source-of-truth
- internal OMX planning artifacts are aligned to that document
- current repo evidence still supports the plan's "already landed" and "reuse-first" claims

This is a document-state verification task. Product-code changes are not expected unless a real mismatch is discovered.

## Test Objectives

1. Prove the internal OMX PRD no longer describes already-landed code as missing implementation.
2. Prove the internal OMX test spec no longer assumes active rollout of already-landed work.
3. Prove the existing eval-gate infrastructure is represented as current, runnable infrastructure.
4. Prove the quality-loop section remains reuse-first.
5. Prove the fallback execution finished without needing product-code changes.

## Test Matrix

### A. Document-state alignment
- **Target files:**
  - `C:\dev\Harmonic\docs\plans\2026-04-03-thesis-classifier-delta-spec.md`
  - `C:\Users\nikhi\.claude\plans\temporal-puzzling-dove.md`
  - `.omx/plans/prd-temporal-puzzling-dove.md`
  - `.omx/plans/test-spec-temporal-puzzling-dove.md`
- **Required checks:**
  1. Internal PRD references the repo plan as canonical.
  2. The external copy clearly identifies itself as a mirror/snapshot.
  3. Internal PRD does not reopen `classification_status`, `max_tokens=800`, minimum decomposition fields, hard/soft B2B split, or the CLAUDE wording as pending implementation.
  4. Internal test spec describes this execution as document-state alignment and verification, not product-code rollout.

### B. Eval-gate infrastructure
- **Target files:**
  - `.omx/specs/thesis-llm-eval-gate.json`
  - `tests/fixtures/thesis_llm_golden_set.jsonl`
  - `tests/utils/test_thesis_llm_golden_set.py`
  - `tests/utils/test_thesis_llm_accuracy.py`
  - `tests/utils/test_thesis_eval_gate.py`
  - `tests/scripts/test_run_thesis_llm_eval_gate.py`
- **Required evidence:**
  1. The gate artifact exists and is treated as current infrastructure.
  2. The focused eval-gate test slice passes.
  3. Future candidate fields remain deferred unless a fresh gate artifact authorizes them.
- **Evidence command:**
  - `pytest tests/utils/test_thesis_llm_golden_set.py tests/utils/test_thesis_llm_accuracy.py tests/utils/test_thesis_eval_gate.py tests/scripts/test_run_thesis_llm_eval_gate.py -q`

### C. Quality-loop reuse
- **Target files:**
  - `ops/scheduler.py`
  - `ops/quality/patterns.py`
  - `scripts/build_exemplar_library.py`
  - `.omx/sandbox/step4-quality-loop-reuse/summary.md`
- **Required evidence:**
  1. Existing quality modes remain the named reuse path.
  2. No new scheduler architecture is required for the current slice.
  3. Scheduler-quality tests still pass.
- **Evidence command:**
  - `pytest tests/ops/test_scheduler_quality.py -q`

### D. No unnecessary product-code rollout
- **Target scope:**
  - product source files under `consumer/`, `storage/`, `utils/`, `workflows/`, `ops/`
- **Required check:**
  1. This fallback execution does not introduce product-code edits unless a mismatch is discovered.
  2. Completion can be justified by planning-artifact alignment plus verification evidence alone.

## Regression Strategy

1. Re-read the canonical repo plan.
2. Re-read the external mirror.
3. Re-read the aligned internal PRD/test-spec.
4. Run the focused eval-gate tests.
5. Run the scheduler-quality tests.
6. Spot-check that no new source-code work was introduced for this fallback execution.

## Acceptance Gate for Completion

Execution is complete only when all of the following are true:
1. The repo plan remains the canonical delta spec.
2. The external copy is marked as a mirror/snapshot.
3. Internal OMX planning artifacts are aligned to the canonical repo plan.
4. The eval-gate test slice passes.
5. The scheduler-quality test slice passes.
6. No product-code changes were needed to satisfy this fallback execution.

## Known Risks

- Future automation may still prefer stale artifacts if internal docs are not aligned.
- Future `v1.6` candidates may be mistaken for approved work if the gate boundary is not kept explicit.
- Owner/cadence still needs manual follow-through because `nikhi` is the sole owner.
- The scheduler-quality slice currently carries one existing pydantic deprecation warning; this is not a task blocker but should not be misread as a new regression.

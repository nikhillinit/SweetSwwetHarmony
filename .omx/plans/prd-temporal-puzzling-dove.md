# PRD: temporal-puzzling-dove execution sync

## Requirements Summary

Execute the approved `temporal-puzzling-dove` plan by bringing OMX planning artifacts into alignment with the rewritten external source-of-truth document at `C:\Users\nikhi\.claude\plans\temporal-puzzling-dove.md`.

This is a document-state execution task, not a product-code rollout. The external plan has already been rewritten as a repo-aligned delta spec. The remaining work is to ensure internal OMX artifacts no longer misstate already-landed code as missing and that completion is backed by current repo evidence.

## Brownfield Grounding

- `C:\Users\nikhi\.claude\plans\temporal-puzzling-dove.md` is now the canonical execution brief and already uses the delta-spec structure:
  - `Already Landed`
  - `Remaining Delta for v1.6`
  - `Acceptance Gates`
  - `Crosswalk From Original Phases`
  - `Deferred Items`
- `consumer/thesis_filter/llm_classifier.py` already contains:
  - `CLASSIFIER_PROMPT_VERSION = "v1.5.0-b2b-decomposition-minimal"`
  - `ClassificationStatus`
  - `max_tokens = 800`
  - the minimum structured fields
- `consumer/thesis_filter/hard_disqualifiers.py` already contains the hard/soft B2B split and the narrower direct-consumer rescue model.
- `storage/signal_store.py`, `utils/thesis_filter.py`, and `workflows/pipeline.py` already persist and consume the landed status/decomposition fields.
- `CLAUDE.md` already reflects the "tools sold to consumer industries" exclusion wording.
- `tests/fixtures/thesis_llm_golden_set.jsonl`, `tests/utils/test_thesis_llm_golden_set.py`, `tests/utils/test_thesis_llm_accuracy.py`, `utils/thesis_evaluator.py`, `utils/thesis_eval_gate.py`, and `scripts/run_thesis_llm_eval_gate.py` already implement the eval-gate scaffold.
- `.omx/specs/thesis-llm-eval-gate.json` already records a live gate artifact with `decision = "go"` and threshold `0.90`.
- `ops/scheduler.py` already exposes `quality-sync`, `quality-classify`, and `quality-patterns`.

## RALPLAN-DR Summary

### Principles
1. Keep planning artifacts truthful to the current repo state.
2. Do not reopen already-landed code changes as pending execution work.
3. Treat the external plan as canonical and align internal OMX artifacts to it.
4. Preserve evidence that future `v1.6` work is deferred behind a fresh eval gate.
5. Close the task with verification evidence, not assumption.

### Decision Drivers
1. The old internal PRD/test-spec still describe shipped work as missing.
2. The external plan has already been rewritten and should now drive execution state.
3. Future automation should not pick up stale OMX artifacts and re-implement already-landed behavior.

### Viable Options

#### Option A — Align internal OMX artifacts to the rewritten external plan
**Pros:** prevents future execution drift; keeps planning state coherent; minimal scope.
**Cons:** requires touching internal OMX docs after the main external rewrite.

#### Option B — Leave stale internal artifacts in place and treat the external plan as implicitly authoritative
**Pros:** fewer file edits now.
**Cons:** future `$ralph` / `$team` follow-ups can still pick up stale requirements and generate redundant code work.

### Recommendation
Choose **Option A**.

## Acceptance Criteria

1. `.omx/plans/prd-temporal-puzzling-dove.md` no longer describes `classification_status`, `max_tokens=800`, minimum decomposition fields, hard/soft B2B split, or the CLAUDE exclusion update as missing implementation work.
2. `.omx/plans/test-spec-temporal-puzzling-dove.md` no longer treats product-code rollout as the active execution target for this task.
3. Internal OMX artifacts explicitly recognize the external plan at `C:\Users\nikhi\.claude\plans\temporal-puzzling-dove.md` as the canonical delta spec.
4. Internal OMX artifacts preserve the distinction between:
   - already landed
   - existing gate/reuse infrastructure
   - future `v1.6` candidates that require a fresh gate
5. Verification evidence confirms the existing eval-gate and quality-loop test slices still pass.
6. No product source files are modified as part of this fallback execution unless a real mismatch is discovered.

## Implementation Steps

### Step 1 — Replace stale OMX planning state
- Rewrite `.omx/plans/prd-temporal-puzzling-dove.md` so it reflects the already-completed external delta-spec rewrite instead of the pre-rewrite implementation assumptions.
- Rewrite `.omx/plans/test-spec-temporal-puzzling-dove.md` so it verifies document-state alignment and repo-grounded evidence rather than treating already-landed code as open work.

### Step 2 — Preserve the real future boundary
- Keep `v1.6` candidate fields (`monetization_model`, `deal_size_indicator`, `customer_acquisition_channel`) explicitly deferred behind a fresh gate.
- Keep Step 4 framed as reuse-first through `quality-sync`, `quality-classify`, and `quality-patterns`.
- Keep the existing eval-gate artifact represented as current infrastructure, not hypothetical scaffolding.

### Step 3 — Verify closure with current evidence
- Run the focused eval-gate test slice:
  - `pytest tests/utils/test_thesis_llm_golden_set.py tests/utils/test_thesis_llm_accuracy.py tests/utils/test_thesis_eval_gate.py tests/scripts/test_run_thesis_llm_eval_gate.py -q`
- Run the scheduler-quality slice:
  - `pytest tests/ops/test_scheduler_quality.py -q`
- Re-read the updated internal docs and confirm they match the canonical external plan.

## Risks and Mitigations

| Risk | Why it matters | Mitigation |
|---|---|---|
| Internal OMX docs drift behind the external plan | Future execution could reopen shipped work | Rewrite PRD/test-spec to make the external delta spec canonical |
| Future `v1.6` candidates look pre-approved | Could trigger speculative schema expansion | Keep them explicitly deferred behind a fresh gate |
| Quality-loop work gets reopened as greenfield | Could cause duplicate orchestration work | Keep Step 4 framed as reuse of `quality-*` modes |
| Task is declared done without evidence | Could leave hidden doc drift | Re-run focused test slices and re-read updated artifacts |

## Verification Steps

1. Confirm `.omx/plans/prd-temporal-puzzling-dove.md` now describes this task as planning-artifact alignment, not product-code rollout.
2. Confirm `.omx/plans/test-spec-temporal-puzzling-dove.md` now verifies document-state alignment and current infrastructure reuse.
3. Confirm both internal artifacts point back to `C:\Users\nikhi\.claude\plans\temporal-puzzling-dove.md` as the canonical delta spec.
4. Confirm the focused eval-gate test slice passes.
5. Confirm the scheduler-quality test slice passes.
6. Confirm no product source files were changed for this fallback execution.

## ADR

### Decision
Treat the rewritten external plan as canonical and align internal OMX planning artifacts to it.

### Drivers
- The previous internal PRD/test-spec are stale.
- The external plan rewrite is already complete.
- Future execution safety depends on internal and external planning state agreeing.

### Alternatives Considered
- Leave stale internal artifacts untouched.
- Reopen product-code execution against already-landed behavior.

### Why Chosen
This is the smallest change set that closes the task honestly and prevents future redundant implementation work.

### Consequences
- Internal planning artifacts become consistent with the external source-of-truth.
- The task can close without unnecessary product-code edits.
- Future `v1.6` work remains gated instead of being implied.

### Follow-ups
1. If a future prompt/schema candidate is proposed, produce a fresh eval-gate artifact before implementation.
2. If future automation uses OMX PRD/test-spec artifacts, it can now do so without reopening already-landed work.

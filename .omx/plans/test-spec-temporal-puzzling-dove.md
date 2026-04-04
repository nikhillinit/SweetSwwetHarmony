# Test Spec: temporal-puzzling-dove

## Scope
Verification for the eval-gated rewrite of the thesis-classifier plan. This spec assumes downstream execution follows the PRD in `prd-temporal-puzzling-dove.md`.

## Test Objectives
1. Prove operational failures are distinguishable from genuine exclusions.
2. Prove the hard-disqualifier loophole is narrowed without breaking obvious consumer cases.
3. Prove the LLM-targeted eval gate exists before broad prompt/schema expansion is accepted.
4. Prove any schema changes remain additive and all save call sites are aligned.
5. Prove existing quality-loop infrastructure is reused rather than duplicated.

## Test Matrix

### A. Operational truthfulness
- **Target files:**
  - `consumer/thesis_filter/llm_classifier.py`
  - `utils/thesis_filter.py`
  - `storage/signal_store.py`
  - `workflows/pipeline.py`
- **Required tests:**
  1. Unit test: each classifier synthetic failure path emits the correct `classification_status`.
  2. Unit/integration test: `_is_operational_llm_failure` replacement still fails open to keyword routing for operational errors.
  3. Storage test: persisted thesis classification records include `classification_status` with backward-compatible defaults.
  4. Pipeline test: all three `save_thesis_classification(...)` call sites populate the updated persistence contract.
- **Evidence commands (expected downstream):**
  - `pytest tests/utils -k thesis`
  - targeted pipeline/storage tests covering thesis persistence

### B. Hard-disqualifier loophole tightening
- **Target file:** `consumer/thesis_filter/hard_disqualifiers.py`
- **Required tests:**
  1. “Enterprise API platform for restaurants” → reject.
  2. “Restaurant reservation app for diners” → still passes disqualifier stage.
  3. One ambiguous consumer/business hospitality case routes onward instead of being silently overruled.
  4. Existing hard-disqualifier suite stays green.
- **Evidence commands:**
  - targeted pytest selection for hard-disqualifier tests

### C. Eval-gate creation
- **Target files:**
  - `tests/fixtures/` (new LLM-focused fixture)
  - `tests/utils/` or `tests/integration/`
  - `utils/thesis_evaluator.py`
- **Required tests/evidence:**
  1. New LLM-focused golden-set fixture loads successfully.
  2. Comparison runner can produce keyword vs LLM metrics on that fixture.
  3. Plan-level acceptance threshold is encoded (for example >=90% on the LLM-focused set, if execution adopts that threshold).
  4. v1.4 baseline and revised-prompt comparison are both reproducible.
  5. Step 2 emits a concrete go/no-go artifact that states which prompt/schema additions are authorized, narrowed, or blocked.
- **Evidence commands:**
  - pytest target for the new golden-set/evaluation tests
  - any project-standard evaluation CLI/script invocation added during execution

### D. Prompt/schema gating
- **Target files:**
  - `consumer/thesis_filter/llm_classifier.py`
  - `storage/signal_store.py`
  - `CLAUDE.md`
- **Required tests/evidence:**
  1. No broad schema expansion lands without a passing Step C evidence artifact.
  2. Any added fields have parse defaults and additive migrations only.
  3. Any newly persisted field demonstrates routing/reporting value or is explicitly deferred from persistence.
  4. `CLAUDE.md` wording changes only when behavior contract changes, not merely because prompt text changed.
- **Evidence review:**
  - diff inspection against PRD preserve/defer/cut intent
  - migration tests / storage smoke test

### E. Quality-loop reuse
- **Target files:**
  - `ops/scheduler.py`
  - `ops/quality/patterns.py`
  - `scripts/build_exemplar_library.py`
- **Required tests/evidence:**
  1. Existing quality modes remain the primary orchestration path if touched.
  2. No redundant scheduler framework or duplicate quality loop is introduced.
  3. Existing tests around quality scheduling/patterns remain green when related code is touched.
- **Evidence commands:**
  - `pytest tests/ops/test_scheduler_quality.py`
  - targeted tests for exemplar/pattern scripts if modified


## Architect Review
- Verdict: APPROVE
- Key synthesis carried into this spec: execution must produce a concrete Step 2 go/no-go artifact before any broad Step 3 prompt/schema rollout, and newly persisted fields must prove routing/reporting value.
## Regression Strategy
- Run focused tests for touched thesis/filter/storage/pipeline modules first.
- Run all newly added LLM-eval tests second.
- If migrations change, run storage migration tests.
- Before completion, run an expanded regression slice covering touched modules plus quality scheduler tests when affected.

## Acceptance Gate for Execution Completion
Execution is only complete when all of the following are true:
1. Immediate truthfulness/routing fixes are implemented and tested.
2. The LLM-targeted eval gate exists and is runnable.
3. Any prompt/schema expansion merged into the branch is explicitly backed by eval evidence.
4. Migrations are additive and save call sites are aligned.
5. Final verifier review says the output still matches the PRD’s preserve/defer/cut decisions.

## Known Planning Risks to Watch During Execution
- Conflating “richer prompt output” with “proven useful routing signal.”
- Breaking fail-open behavior while cleaning up operational-failure handling.
- Over-tightening the hard-disqualifier logic and rejecting legitimate consumer hospitality cases.
- Adding DB columns that are never validated or consumed.
- Duplicating existing quality-loop infrastructure instead of reusing it.





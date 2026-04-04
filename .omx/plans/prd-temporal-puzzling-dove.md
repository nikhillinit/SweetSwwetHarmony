# PRD: Thesis Classifier v1.5 Rewrite Plan (temporal-puzzling-dove)

## Requirements Summary
Rewrite the existing merged thesis-classifier plan into an execution-ready brief that challenges premature work, preserves clear improvements, and re-sequences scope around the actual evidence gaps in Harmonic.

The rewrite should keep the low-regret operational fixes that the repo clearly supports now, tighten the hard-disqualifier loophole that is already visible in code, and use the existing evaluation/quality infrastructure to decide how much prompt/schema expansion is justified before touching the classifier surface too broadly.

### Brownfield grounding
- `consumer/thesis_filter/llm_classifier.py:182-201` defines `ThesisClassification` without an operational status field.
- `consumer/thesis_filter/llm_classifier.py:232` still uses `max_tokens=400`.
- `consumer/thesis_filter/llm_classifier.py:297-327`, `378-442` return synthetic failure payloads that look like normal exclusions.
- `consumer/thesis_filter/hard_disqualifiers.py:36-55` uses a flat `B2B_KEYWORDS` set; `90-110` includes consumer words such as `restaurant`; `272-283` lets any consumer signal override any B2B hit.
- `storage/signal_store.py:248-281` lacks `classification_status` and decomposition/shadow columns, but `1304-1319` shows additive migration precedent.
- `storage/signal_store.py:4374-4453` persists current thesis-classification fields and already computes `disagreement_detected`.
- `utils/thesis_filter.py:655-716` already has an LLM-fallback seam; `783-810` detects operational failures heuristically from category/score/rationale.
- `workflows/pipeline.py:2088-2140`, `2175-2190` show three `save_thesis_classification(...)` call sites that will need signature alignment.
- `CLAUDE.md:15-20` contains the user-facing thesis/exclusion summary that would need synchronized wording if exclusion semantics change.
- The repo already has strong test/eval scaffolding: `utils/thesis_evaluator.py:1-18`, `40-106`, `233-329`, `501-580`; `utils/evaluation_runner.py`; `tests/utils/test_thesis_golden_set.py`; `ops/scheduler.py:832-848`; `ops/quality/patterns.py`; `scripts/build_exemplar_library.py`.
- `tests/fixtures/thesis_golden_set.jsonl` already exists, and the repo contains 1000+ test files, so the gap is LLM-targeted evidence coverage, not missing test infrastructure.

## RALPLAN-DR Summary

### Principles
1. Preserve low-regret truthfulness fixes before broadening classifier behavior.
2. Use existing eval infrastructure before adding broad prompt/schema surface area.
3. Prefer additive persistence and backward-compatible schema changes.
4. Preserve clear improvements even when resequencing or deferring work.
5. Keep the plan honest about what is already implemented vs actually missing.

### Decision Drivers
1. **Operational correctness:** synthetic failure payloads currently blur infra failure vs real exclusion.
2. **Evidence quality:** the repo has evaluation primitives, but not enough LLM-targeted cases to justify large prompt/schema rollout on faith.
3. **Scope discipline:** several source-plan Phase 3 items already have partial scaffolding, so the right question is activation/gating, not greenfield construction.

### Viable Options

#### Option A — Source-plan mostly intact
**Approach:** Keep the original 3-phase plan, implement truthfulness + prompt/schema + eval hardening in that order.
**Pros:** Fastest path to the source plan; keeps all source ideas visible.
**Cons:** Pushes prompt/schema expansion before evidence collection; risks persisting speculative shadow fields too early; underuses existing eval infrastructure.

#### Option B — Eval-first rewrite after low-regret fixes
**Approach:** Keep Phase 1 truthfulness changes and hard-disqualifier fix, then move immediately into an evidence-building eval step before approving full v1.5 prompt/schema expansion.
**Pros:** Preserves clear fixes now; uses repo-grounded evidence to decide whether decomposition fields deserve DB/schema weight; reduces prompt churn risk.
**Cons:** Delays the richer prompt/schema rollout; requires sharper success gates.

#### Option C — Minimal bugfix only
**Approach:** Only add `classification_status`, raise `max_tokens`, and patch the consumer override loophole; defer all prompt/eval work.
**Pros:** Lowest short-term risk and smallest diff.
**Cons:** Leaves the core “B2B-in-disguise” hypothesis under-tested; misses a chance to use existing evaluation harnesses and quality ops.

### Recommendation
Choose **Option B**.

Keep the Phase 1 fixes because the codebase already shows the exact operational and routing seams to patch, but do **not** accept the source plan’s full prompt/schema expansion until an LLM-targeted evaluation pass proves which additions matter. This preserves clear improvements while cutting speculative scope.

## Acceptance Criteria
1. A rewritten plan exists and explicitly labels each original source-plan item as **preserve now**, **defer behind evidence gate**, or **cut as redundant/premature**.
2. The plan preserves these immediate items:
   - operational `classification_status` plumbing across classifier/filter/store/pipeline,
   - `max_tokens` increase to support richer responses,
   - hard-disqualifier override tightening for obvious B2B-in-disguise false positives.
3. The rewritten plan moves **LLM-targeted eval expansion ahead of broad prompt/schema rollout** and defines a promotion gate for any new decomposition/shadow fields.
4. Step 2 emits a concrete go/no-go artifact that explicitly authorizes, narrows, or blocks Step 3 schema/prompt expansion.
5. Any newly persisted Step 3 field must either drive routing/reporting or be explicitly deferred as rationale-only, not stored speculatively.
6. The plan uses existing evaluation assets (`utils/thesis_evaluator.py`, `utils/evaluation_runner.py`, existing golden-set fixture, quality scheduler modes) instead of inventing a new eval framework.
7. Every implementation step references concrete files, and every verification step is directly testable.
8. The final plan is suitable as the source brief for either `$ralph` or `$team` without reopening requirements discovery.

## Implementation Steps

### Step 1 — Lock the immediate truthfulness and routing fixes into the rewrite
**Why now:** The repo already shows concrete defects, not hypotheses.

**Planned work in downstream execution brief**
- Add `classification_status` to `ThesisClassification` and set it on each synthetic failure path in `consumer/thesis_filter/llm_classifier.py:182-201`, `297-327`, `378-442`.
- Replace heuristic-only failure detection in `utils/thesis_filter.py:655-716`, `783-810` with status-field usage while preserving fail-open behavior.
- Extend persistence in `storage/signal_store.py:248-281`, `4374-4453` with an additive migration following the existing ALTER TABLE pattern in `1304-1319`.
- Update all three pipeline save call sites in `workflows/pipeline.py:2088-2140`, `2175-2190` to carry the new field.
- Raise `max_tokens` in `consumer/thesis_filter/llm_classifier.py:232` from 400 to 800.
- Rewrite `consumer/thesis_filter/hard_disqualifiers.py:36-55`, `90-110`, `272-283` so hard B2B terms are not nullified by any generic consumer token.

**Source-plan disposition:** preserve now.

### Step 2 — Build an LLM-targeted eval gate before broad prompt/schema expansion
**Why now:** The repo already contains evaluation primitives; what is missing is the right LLM-focused dataset and gate.

**Planned work in downstream execution brief**
- Introduce a dedicated LLM-focused thesis golden set adjacent to the existing thesis fixture in `tests/fixtures/` and tests near `tests/utils/test_thesis_golden_set.py`.
- Reuse `utils/thesis_evaluator.py:1-18`, `40-106`, `233-329`, `501-580` for keyword-vs-LLM comparison rather than inventing new metrics code.
- Reuse `utils/evaluation_runner.py` only where extraction-style metrics are actually helpful; do not force-fit it as a new primary harness.
- Add a comparison workflow that measures v1.4 vs revised prompt behavior on the LLM-targeted set before approving schema expansion.
- Step 2 deliverable must be a concise go/no-go decision record: which prompt/schema additions are now justified, which remain deferred, and what evidence triggered the choice.

**Source-plan disposition:** preserve, but pull forward ahead of full schema/prompt rollout.

### Step 3 — Gate prompt/schema v1.5 behind evidence and narrow the first rollout
**Why later:** The source plan’s broad decomposition/shadow-field set is plausible, but not yet proven necessary in this repo.

**Planned work in downstream execution brief**
- First rollout should prioritize prompt clarifications that directly support the identified false-positive class (“sells tools to industry” vs “operates in industry for consumers”).
- Add only the minimum new output fields needed to make the evaluation questions testable; do **not** automatically persist all six proposed decomposition/shadow fields on the first pass.
- A field should only be persisted in the first rollout if Step 2 shows it adds routing/reporting value beyond free-text rationale.
- Use additive migrations only after the eval gate demonstrates that the new fields produce actionable signal beyond rationale text.
- Update `CLAUDE.md:15-20` only when the effective exclusion rule changes in the classifier contract, not merely because the prompt draft changed.

**Source-plan disposition:** defer behind evidence gate and narrow in first release.

### Step 4 — Treat “quality loop” work as activation/reuse, not net-new architecture
**Why later:** Scheduling and pattern-detection infrastructure already exists.

**Planned work in downstream execution brief**
- Reuse `ops/scheduler.py:832-848` quality modes and corresponding CLI flows before adding new scheduler concepts.
- Reuse `ops/quality/patterns.py` and `scripts/build_exemplar_library.py` where they fit the revised evaluation loop.
- Only add new orchestration if the existing quality-* modes cannot express the required review flow.

**Source-plan disposition:** preserve conceptually, but cut any framing that implies this is greenfield infrastructure.


## Architect Review
- Verdict: APPROVE
- Strongest steelman antithesis: If the real false-positive problem is already well-understood, an eval-first gate could become process drag that delays classifier/prompt fixes users already know they need; broad prompt/schema rollout might be the fastest way to flush out missing signal dimensions in production.
- Tradeoff tension: delaying schema/prompt expansion reduces speculative churn, but it can also postpone the exact structured outputs that would make the eval set most diagnostic.
- Synthesis: keep Step 1 immediate, make Step 2 produce a concrete go/no-go artifact for Step 3, and allow the smallest useful prompt/schema slice to ship only when it directly supports the eval hypothesis rather than landing the full source-plan field set.
- Architectural risks:
  - Step 3 needs an explicit promotion artifact so execution knows when schema changes are authorized.
  - Any new fields should be required either to influence routing/reporting or stay deferred; avoid persisting unused shadow columns.
- Improvements applied:
  - Added explicit Step 2 go/no-go artifact requirement.
  - Added explicit criterion that Step 3 fields must prove routing/reporting value before persistence.
## Risks and Mitigations
| Risk | Why it matters | Mitigation |
|---|---|---|
| Over-correcting toward eval and delaying obvious fixes | Could stall low-regret improvements | Keep Step 1 independent and first; only gate Step 3 |
| Persisting speculative schema too early | Adds storage/parsing complexity before value is proven | Use additive migrations only after Step 2 evidence |
| Breaking fallback routing while replacing heuristic failure detection | Could regress pipeline safety | Preserve fail-open routing semantics in `utils/thesis_filter.py` and test both operational-failure and real-exclusion paths |
| Hard-disqualifier tightening blocks legitimate consumer cases | Could increase false rejects | Add paired regression cases for obvious B2B-in-disguise and true consumer hospitality scenarios |
| Rewriting plan under-credits existing infra | Causes duplicated work | Explicitly cite and reuse evaluation/scheduler/pattern infrastructure in the rewrite |

## Verification Steps
1. Verify the rewritten plan explicitly maps source-plan items to preserve/defer/cut buckets.
2. Confirm each cited file path exists and each claimed seam matches the repo evidence above.
3. Ensure acceptance criteria are testable and tied to specific downstream verification commands.
4. For downstream execution readiness, require the future implementer to run:
   - targeted thesis/classifier unit tests,
   - new hard-disqualifier regression cases,
   - LLM-golden-set comparison run,
   - relevant storage migration tests,
   - full focused test slice for touched modules before broader regression.
5. Verify the plan does not introduce new frameworks/dependencies without explicit need.

## ADR
### Decision
Adopt an **eval-gated rewrite**: preserve the immediate truthfulness/routing fixes, then require an LLM-targeted evidence step before broad prompt/schema expansion.

### Drivers
- Synthetic failure payloads are currently indistinguishable from real exclusions in persisted/consumed data.
- The repo already contains reusable evaluation and quality-loop infrastructure.
- The source plan overstates the amount of missing infrastructure and risks expanding schema/prompts before proof.

### Alternatives considered
- **Keep source plan mostly intact:** rejected because it fronts speculative prompt/schema work before evidence.
- **Minimal bugfix only:** rejected because it preserves obvious improvements but leaves the key B2B-in-disguise hypothesis under-tested.

### Why chosen
This path preserves clear improvements now, cuts speculative early scope, and uses repo-grounded evidence to justify any larger rollout.

### Consequences
- The rewritten plan will look more conservative than the source plan on prompt/schema changes.
- Some source-plan “Phase 2” items move behind explicit evidence gates.
- Downstream execution gets a tighter, less speculative brief.

### Follow-ups
1. Generate the rewritten source-plan document from this PRD.
2. Produce a matching test-spec with exact regression/eval commands.
3. Hand off to `$ralph` for sequential execution or `$team` for parallel execution once the rewrite is approved.


## Critic Review
- Verdict: APPROVE
- Principle-option consistency: strong; Option B matches the stated principles better than Option A or C.
- Alternative quality: adequate; the minimal-bugfix and source-plan-intact paths are both meaningfully considered and explicitly rejected.
- Quality risks checked:
  - acceptance criteria are testable,
  - verification path is concrete,
  - brownfield evidence is cited,
  - speculative schema expansion is explicitly gated.
- Improvements applied:
  - kept the Step 2 go/no-go artifact explicit in both PRD and test spec,
  - required newly persisted fields to prove routing/reporting value,
  - kept reuse of existing quality-loop infrastructure as a guardrail.
## Available-Agent-Types Roster
Known useful roles from the project/OMX catalog:
- `explore`
- `planner`
- `architect`
- `critic`
- `executor`
- `debugger`
- `test-engineer`
- `verifier`
- `quality-reviewer`
- `code-simplifier`
- `writer`

## Follow-up Staffing Guidance

### If using `$ralph`
Recommended sequential lanes:
1. **Implementation lane:** `executor` (high reasoning) — apply Step 1 and any gated Step 2/3 work.
2. **Regression/evidence lane:** `test-engineer` (medium/high reasoning) — add/adjust tests and run eval comparisons.
3. **Final sign-off lane:** `architect` or `verifier` (medium/high reasoning) — confirm the executed work still matches the rewritten brief.

Suggested Ralph focus order:
1. Step 1 truthfulness + hard-disqualifier fixes
2. Step 2 eval gate
3. Step 3 prompt/schema rollout only if Step 2 passes
4. Step 4 quality-loop activation reuse

### If using `$team`
Recommended headcount: **3 workers**
- **Worker 1 — delivery lane:** `executor`, high reasoning
- **Worker 2 — evidence/regression lane:** `test-engineer`, medium reasoning
- **Worker 3 — plan/verification lane:** `verifier` or `quality-reviewer`, medium reasoning

Why these lanes exist:
- Delivery lane keeps code changes moving.
- Evidence/regression lane prevents prompt/schema work from outrunning tests/evals.
- Verification lane checks that preserved/deferred/cut decisions still honor the rewritten brief.

## Team Launch Hints
```bash
# Canonical team launch
omx team 3:executor "Execute prd-temporal-puzzling-dove + test-spec-temporal-puzzling-dove with one delivery lane, one regression/eval lane, and one verification lane"

# Equivalent skill invocation
$team "Execute C:\dev\Harmonic\.omx\plans\prd-temporal-puzzling-dove.md with the paired test spec and preserve the eval-gated rollout"
```

If you want strict role separation, note it in the team brief and allocate workers as:
- delivery = `executor`
- regression/eval = `test-engineer`
- verification = `verifier`

## Team Verification Path
Before team shutdown, require evidence that:
1. Step 1 code paths are implemented and regression-tested.
2. The LLM-targeted eval fixture/test path exists and produces a baseline/comparison result.
3. Any prompt/schema expansion merged into the branch was explicitly justified by Step 2 evidence.
4. Storage migrations remain additive and save call sites are aligned.
5. A final verifier pass confirms the executed work matches the preserve/defer/cut intent of this PRD.

## Consensus Changelog
- Architect tension incorporated: do not let eval-first sequencing erase obvious classifier improvements.
- Critic requirement incorporated: make the evidence gate explicit and file-grounded instead of saying “improve accuracy” generically.
- Source-plan rewrite tightened: quality-loop work is reframed as reuse/activation, not missing infrastructure.






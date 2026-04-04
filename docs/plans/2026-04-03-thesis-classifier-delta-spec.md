# Plan: Thesis Classifier Delta Spec

Canonical copy. The planning-mode mirror lives at `C:\Users\nikhi\.claude\plans\temporal-puzzling-dove.md`.
If the two copies diverge, this repo copy wins.

Validated against current Harmonic repo state, sandbox outputs, and the repo's babysitter-aware execution surfaces on April 3, 2026.

## Context

This document replaces the earlier merged `v1.5` implementation plan as the source-of-truth execution brief.

The earlier plan drifted from the repo in two ways:
- several items it still treated as pending are already implemented
- the sandbox work already validated a narrowed rollout and a reuse-first Step 4 path

The operating principle remains:

**fix the classifier honestly, preserve low-regret improvements, and only widen prompt/schema scope when the evidence gate explicitly authorizes it.**

## Execution Surface Boundaries

This plan uses two different kinds of evidence and they should stay clearly separated:

### Repo-local truth

These are facts that should be verified against the checked-out codebase in `C:\dev\Harmonic`:
- classifier behavior
- stored fields and migrations
- filter and pipeline persistence
- tests, scripts, and scheduler/CLI surfaces

### Babysitter-managed execution

This environment also has real babysitter/A5C infrastructure:
- [`.a5c`](C:/dev/Harmonic/.a5c)
- [`.claude/skills`](C:/dev/Harmonic/.claude/skills)
- repo-local skills such as [quality-stats](C:/dev/Harmonic/.claude/skills/quality-stats) and [thesis-classify](C:/dev/Harmonic/.claude/skills/thesis-classify)

That means babysitter/process/skill framing is valid here. The caution is narrower:
- treat repo-local code and tests as the system of record for landed behavior
- treat babysitter process ids, skill wiring, and agent selection as execution surfaces that must match the active babysitter registry or checked-in process files
- if a process entrypoint is referenced explicitly, confirm that the referenced path/export is the active one before relying on it

## Ownership and Review Cadence

- Owner: `nikhi` (sole owner)
- Canonical plan location: `C:\dev\Harmonic\docs\plans\2026-04-03-thesis-classifier-delta-spec.md`
- Mirror/snapshot location: `C:\Users\nikhi\.claude\plans\temporal-puzzling-dove.md`

Re-run the eval gate or review deferred items when any of the following is true:
- a materially new prompt/schema candidate is proposed
- the threshold policy changes
- the golden-set dataset composition changes materially
- the underlying model/version changes materially
- labeled-signal count exceeds 200
- June 1, 2026 arrives

Whichever comes first should trigger a review.

## Already Landed

These are baseline capabilities now, not future work.

### Operational truthfulness and persistence
- `classification_status` already exists in `consumer/thesis_filter/llm_classifier.py`
- `classification_status` is already persisted in `storage/signal_store.py`
- `utils/thesis_filter.py` already consumes the explicit status field for operational-failure handling while preserving fail-open behavior
- `workflows/pipeline.py` already persists the landed thesis-classification fields across rejected, held, and qualified paths

### Baseline classifier capacity
- `max_tokens=800` is already the live default in `consumer/thesis_filter/llm_classifier.py`

### Minimum B2B-in-disguise structured fields
- the current minimum structured rollout is already landed:
  - `primary_end_user`
  - `paying_customer`
  - `sells_to_or_operates_in`
- those fields are already parsed, defaulted, stored, and persisted through the pipeline
- today they are best understood as reporting/analysis surfaces rather than direct routing controls; routing still hinges on the LLM category/score path

### Hard-disqualifier tightening
- `consumer/thesis_filter/hard_disqualifiers.py` already uses a hard/soft B2B split
- hard B2B terms are **not** rescued by generic consumer-industry nouns alone
- ambiguous rescue requires the narrower direct-consumer override concept, not broad consumer context

### User-facing thesis wording
- `CLAUDE.md` already includes the exclusion wording for tools sold to consumer industries

## Sandbox-Validated Conclusions

These are not just repo facts; they were explicitly validated in the sandbox work and should be preserved as conclusions.

### Eval-gate scaffold and artifact already exist
- `tests/fixtures/thesis_llm_golden_set.jsonl` already exists
- `tests/utils/test_thesis_llm_golden_set.py`, `tests/utils/test_thesis_llm_accuracy.py`, `utils/thesis_evaluator.py`, `utils/thesis_eval_gate.py`, and `scripts/run_thesis_llm_eval_gate.py` already exist
- `.omx/specs/thesis-llm-eval-gate.json` already records a live gate artifact with:
  - `decision = "go"`
  - `threshold = 0.90`
  - `keyword_accuracy = 0.40`
  - `llm_accuracy = 0.90`
  - `authorized_changes` limited to minimum B2B-in-disguise prompt guidance and minimum structured fields

### Important clarification about the gate
- that artifact is historical evidence for the already-landed minimum rollout
- the normal automated test slice validates fixture/harness structure and blocked-error behavior
- the normal automated test slice does **not** continuously rerun a live LLM gate by default

### Step 4 is reuse-first, not greenfield
- `ops/scheduler.py` already supports:
  - `quality-sync`
  - `quality-classify`
  - `quality-patterns`
- `ops/cli.py` already exposes operator-facing schedule creation and schedule-run surfaces for those modes
- `ops/quality/patterns.py` and `scripts/build_exemplar_library.py` already exist for the quality-loop reuse path
- existing sandbox validation concluded that no new Step 4 implementation code is required for the current slice

## Remaining Delta for v1.6

This is the real future scope beyond the current `v1.5.0-b2b-decomposition-minimal` baseline.

### 1. Future prompt/schema expansion must be re-gated

Do **not** describe the next rollout as `v1.5`. The next forward-looking prompt/spec change should be a distinct version family, such as `v1.6`.

Future candidates may include:
- `monetization_model`
- `deal_size_indicator`
- `customer_acquisition_channel`

But these are **not** pre-authorized next steps. Treat them as future candidates only.

They should only move forward when a fresh eval-gate pass explicitly authorizes them.

### 2. Broader field persistence is conditional, not assumed

If a future `v1.6` prompt expands beyond the current minimum fields:
- additive migrations only
- parse defaults required
- pipeline/save-call alignment required
- each new persisted field must improve routing or reporting evidence
- if a field is rationale-only, keep it out of persistence until it proves value

### 3. Eval follow-up is about rerunning and extending the gate, not inventing it

The remaining evaluation work is:
- rerun the gate when a materially new prompt candidate exists
- keep threshold-based authorization explicit
- treat execution errors as operational blocks, not model regressions
- use the existing artifact contract to decide which changes are authorized, narrowed, deferred, or blocked
- do not assume the live gate is automatically rerun by the normal test suite; fresh live runs remain operator-invoked unless a real automation surface is later implemented

### 4. Step 4 remains reuse-first

The current quality-loop story should stay reuse-first:
- reuse `quality-sync`, `quality-classify`, and `quality-patterns`
- reuse `ops/cli.py` schedule creation/run surfaces
- reuse `ops/quality/patterns.py`
- reuse `scripts/build_exemplar_library.py`

Only add a new scheduler mode later if the existing quality surfaces cannot express the needed workflow cleanly.

## Acceptance Gates

### Gate A: Repo-truthfulness gate

Before approving this spec or any follow-on execution brief:
- do not list already-landed items as pending
- do not describe the current minimum-field rollout as hypothetical
- do not imply Step 4 architecture is missing when reuse already exists

### Gate B: Future prompt/schema authorization gate

Any broader future rollout beyond the current minimum baseline requires a fresh gate artifact that shows:
- live LLM evaluation ran successfully
- `blocked_reasons` is empty
- `llm_accuracy >= 0.90`
- accuracy delta is not negative against the keyword baseline
- `authorized_changes` explicitly names the proposed additions
- changes outside the authorized list stay deferred

### Gate C: Persistence gate for new fields

Any new field added in the future must satisfy all of:
- additive migration only
- parsing with safe defaults
- persistence contract updated consistently
- routing/reporting value demonstrated
- no speculative shadow persistence just because a field sounds useful

### Gate D: Quality-loop reuse gate

Any new review-loop or active-learning orchestration must first prove that:
- `quality-sync`
- `quality-classify`
- `quality-patterns`

cannot already express the required behavior well enough.

If that proof does not exist, reuse the current scheduler path.

## Crosswalk From Original Phases

### Original Phase 1
- `1A classification_status` -> already landed
- `1B max_tokens` -> already landed
- `1C hard-disqualifier narrowing` -> already landed; prior wording is superseded by the real direct-consumer override model

### Original Phase 2
- `2A minimum structured decomposition` -> already landed for the minimum three-field set
- `2A broader candidate fields` -> moved to future `v1.6` candidates behind a fresh gate
- `2B B2B-in-disguise guidance` -> minimum guidance already validated; broader expansions remain gate-controlled
- `2C richer CoT / prompt refinements` -> future tuning only if justified by a new gate
- `2D CLAUDE.md update` -> already landed

### Original Phase 3
- `3A LLM-specific golden set` -> scaffold already landed
- `3B baseline vs revised prompt comparison` -> gate pattern already landed; reruns remain future evidence work
- `3C shadow-field validation` -> deferred until broader fields actually exist and data volume justifies validation
- `3D active learning orchestration` -> reuse path already exists; do not present as missing greenfield infrastructure

## Critical Repo and Evidence Surfaces

Current baseline truth comes from:
- `consumer/thesis_filter/llm_classifier.py`
- `consumer/thesis_filter/hard_disqualifiers.py`
- `storage/signal_store.py`
- `utils/thesis_filter.py`
- `workflows/pipeline.py`
- `CLAUDE.md`
- `tests/fixtures/thesis_llm_golden_set.jsonl`
- `tests/utils/test_thesis_llm_golden_set.py`
- `tests/utils/test_thesis_llm_accuracy.py`
- `utils/thesis_evaluator.py`
- `utils/thesis_eval_gate.py`
- `scripts/run_thesis_llm_eval_gate.py`
- `ops/scheduler.py`
- `ops/cli.py`
- `.omx/specs/thesis-llm-eval-gate.json`
- `.omx/sandbox/step2-eval-gate/sandbox-results.md`
- `.omx/sandbox/step3-narrowed-prompt-schema/sandbox-plan.md`
- `.omx/sandbox/step4-quality-loop-reuse/summary.md`
- `.a5c`
- `.claude/skills`

## Babysitter-Aware Eval-Gate Rerun Protocol

### Purpose

Run a fresh eval gate only when a materially new prompt/schema candidate exists beyond the current `v1.5.0-b2b-decomposition-minimal` baseline. The rerun decides whether any currently deferred candidates such as `monetization_model`, `deal_size_indicator`, or `customer_acquisition_channel` are newly authorized for implementation.

This is **not** a prerequisite for the already-landed minimum rollout. It is a future operator workflow for evaluating additional scope.

### Rerun Triggers

Invoke a fresh gate when one or more of the following is true:
- a materially new prompt candidate is proposed
- the threshold policy changes
- the golden-set dataset composition changes materially
- the underlying model/version changes materially
- you want to promote a currently deferred field into implementation scope

### Preconditions

- `GOOGLE_API_KEY` or `GEMINI_API_KEY` is available and valid
- `tests/fixtures/thesis_llm_golden_set.jsonl` still represents the failure modes you want to measure
- `scripts/run_thesis_llm_eval_gate.py --help` runs successfully
- you have a concrete list of candidate changes you want the gate to evaluate

### Execution

1. Run the existing gate entrypoint:
   - `python scripts/run_thesis_llm_eval_gate.py --dataset tests/fixtures/thesis_llm_golden_set.jsonl --output .omx/specs/thesis-llm-eval-gate.json`
2. Read the resulting artifact and capture:
   - `decision`
   - `threshold`
   - `keyword_accuracy`
   - `llm_accuracy`
   - `accuracy_delta`
   - `authorized_changes`
   - `narrowed_changes`
   - `blocked_reasons`

### Babysitter execution note

If a babysitter process is used to run this workflow:
- keep the babysitter/process framing
- but ensure the active `processId` and `entry` pair resolve in the current babysitter registry or checked-in process files
- do not assume a repo-local path is authoritative unless it actually exists in the repo checkout

### Skill and agent surfaces

If the babysitter process references skills or agents, prefer actual available surfaces in this environment, for example:
- `$quality-stats`
- `$thesis-classify`
- repo-local skills under `.claude/skills`
- repo-local agents under `.claude/agents`

Avoid generic placeholders unless they are known defaults in the active babysitter runtime.

### Interpretation

- If `decision = "no_go"` or `blocked_reasons` is non-empty, broader candidate fields remain deferred
- If `decision = "go"`, only the changes explicitly listed in `authorized_changes` advance
- changes not explicitly authorized stay deferred even if they seem directionally related

### Evidence Handling

- If you rerun the gate, archive the result to a timestamped artifact path before changing the plan
- record whether the new artifact supersedes or merely confirms `.omx/specs/thesis-llm-eval-gate.json`
- update `Deferred Items` only when the rerun artifact explicitly authorizes a previously deferred item

### Guardrails

- do not imply undocumented automation just because babysitter exists
- do not promote broader candidate fields just because the current historical artifact was `go`; a future rerun must authorize them explicitly
- keep repo-local truth and babysitter-managed execution surfaces conceptually separate in the write-up

## Deferred Items

| Item | Status | Revisit When |
|---|---|---|
| `monetization_model` persistence | Deferred | After a fresh gate explicitly authorizes it |
| `deal_size_indicator` persistence | Deferred | After a fresh gate and enough labeled data support it |
| `customer_acquisition_channel` persistence | Deferred | After a fresh gate and enough labeled data support it |
| Two-call extraction/classification split | Deferred | Only if single-call parse quality degrades materially |
| Multi-model disagreement routing | Deferred | After more labeled eval evidence exists |
| New scheduler mode for review loop | Deferred | Only if current `quality-*` modes prove insufficient |
| Automated prompt-refresh active learning | Deferred | After the current reuse path is exhausted and evidence supports more orchestration |

## Verification

### Document verification
1. Re-check that every item in `Already Landed` is supported by the current repo, not inferred from old planning text.
2. Confirm no future section still uses plain `v1.5` naming for new prompt/spec work.
3. Confirm the hard-disqualifier prose matches the current direct-consumer override logic and does not duplicate `has_consumer`.
4. Confirm the crosswalk preserves historical traceability without reintroducing stale pending-work claims.

### Eval verification
1. Confirm the eval section references the real existing fixture, harness, and gate artifact.
2. Confirm the document treats the gate as existing infrastructure, not greenfield work.
3. Confirm broader future candidate fields remain deferred unless a new gate artifact explicitly authorizes them.
4. Confirm any future rerun path uses the existing script/tooling surface directly.
5. Confirm the babysitter note does not claim a repo-local process entrypoint unless that exact entrypoint exists.

### Quality-loop verification
1. Confirm Step 4 is described as reuse-first.
2. Confirm `ops/cli.py` is included alongside `ops/scheduler.py` in the reuse path.
3. Confirm no new scheduler architecture is implied without an explicit insufficiency claim against existing `quality-*` modes.

## Rollback and Scope Guardrails

- Reverting this document is text-only; there is no code rollback implied by this rewrite.
- Do not reopen already-landed items as pending work in later plan revisions unless the repo materially changes.
- Do not describe broader prompt/schema candidates as approved follow-up work unless a new gate artifact says so.
- Do not add a new Step 4 architecture to the spec without first proving the current quality-loop reuse path is insufficient.

## Decision Record

### Decision
Rewrite the plan as a repo-aligned delta spec instead of patching the old phase list in place.

### Why
- the old merged spec drifted from the current repo
- several "future" items are already shipped
- the sandbox pass already narrowed the authorized rollout
- the quality-loop reuse path is already validated
- the plan needed a babysitter-aware explanation of how future gate reruns should be invoked

### Consequences
- future execution agents can separate baseline from true delta cleanly
- `v1.6` scope stays narrower and more evidence-driven
- the document preserves historical context without preserving stale instructions
- future reruns of the gate are described as a real workflow with explicit triggers and execution-surface boundaries

# PRD: Thesis Classifier Golden-Set Expansion Follow-Up

Date: 2026-04-05
Mode: deliberate consensus plan
Requirements source:
- user request to revise the golden-set expansion follow-up plan using architect+critic feedback
Context snapshot:
- `.omx/context/thesis-classifier-golden-set-expansion-followup-20260405T223034Z.md`
Parent plan:
- `.omx/plans/thesis-classifier-golden-set-expansion-followup.md`

## Problem Statement

The current thesis golden set is benchmark-saturated, benchmark identity is not owned anywhere explicit, and `scripts/thesis_diagnostic_runner.py --compare-against` can make candidate-vs-baseline claims using shared sample ids even when the compared artifacts may come from different benchmark versions.

The follow-up needs to:
- add harder ambiguous coverage
- separate row-level ambiguity rationale from benchmark-level identity ownership
- make diagnostics and eval-gate outputs provenance-aware
- move threshold governance into a distinct re-baseline artifact instead of overloading `.omx/specs/thesis-llm-eval-gate.json`

## Goals

1. Expand the benchmark by `24` ambiguous/edge-case samples.
2. Keep `metadata.label_rationale` inline only where ambiguity actually exists.
3. Make `tests/fixtures/thesis_llm_golden_set.manifest.json` the single owner of benchmark identity/version/fingerprint/changelog.
4. Make `--compare-against` fail closed on benchmark mismatch.
5. Make threshold review explicit and measurable.
6. Keep `.omx/specs/thesis-llm-eval-gate.json` limited to prompt/schema go-no-go plus benchmark provenance echo.

## Non-Goals

1. Prompt redesign or prompt promotion.
2. Requiring benchmark identity on every fixture row.
3. Letting eval-gate artifacts own benchmark governance.
4. Adding new dependencies or a broad benchmarking framework.

## Evidence Snapshot

1. The current fixture has `40` samples with only `20` clear controls and `20` ambiguous cases spread thinly across six scenarios.
2. Structural coverage is currently light in `tests/utils/test_thesis_llm_golden_set.py`.
3. `scripts/thesis_diagnostic_runner.py` currently writes per-sample JSONL records and a summary JSON, but comparison is shared-id based only.
4. `utils/thesis_eval_gate.py` currently produces prompt/schema decision artifacts without benchmark provenance fields.
5. `.omx/specs/thesis-llm-eval-gate.json` already records a current `decision="go"` at `threshold=0.9`.

## Fixed Scope Contract

### Benchmark Governance

- Add `tests/fixtures/thesis_llm_golden_set.manifest.json`.
- The manifest is the single owner of:
  - `benchmark_id`
  - `benchmark_version`
  - `dataset_path`
  - `dataset_fingerprint`
  - `sample_count`
  - `scenario_counts`
  - `ambiguous_scenarios`
  - `changelog`
- `dataset_fingerprint` is the lowercase SHA-256 hex digest of the UTF-8 bytes of the canonical JSONL serialization of `tests/fixtures/thesis_llm_golden_set.jsonl`.
- Canonical JSONL serialization means:
  - parse each non-empty fixture line as JSON
  - recursively sort object keys
  - preserve row order exactly as committed
  - serialize each normalized row as compact JSON with no extra whitespace
  - join rows with `\n`
  - append one trailing `\n`
- Tests and validation must recompute that fingerprint from `tests/fixtures/thesis_llm_golden_set.jsonl` and fail on manifest drift.

### Fixture Contract

- Benchmark identity is not repeated on each fixture row.
- `metadata.label_rationale` is required only for scenarios listed in manifest `ambiguous_scenarios`.

### Generated Artifact Contract

Diagnostic JSONL rows must echo:
- `benchmark_id`
- `benchmark_version`
- `benchmark_fingerprint`
- `benchmark_manifest_path`

Diagnostic summary JSON must echo:
- `benchmark_id`
- `benchmark_version`
- `benchmark_fingerprint`
- `benchmark_manifest_path`
- `benchmark_sample_count`

Eval-gate JSON must echo:
- `benchmark_id`
- `benchmark_version`
- `benchmark_fingerprint`
- `benchmark_manifest_path`

### Governance Artifact Contract

- Re-baseline recommendation artifact: `.omx/specs/thesis-llm-benchmark-rebaseline.json`
- Eval-gate artifact: `.omx/specs/thesis-llm-eval-gate.json`

The first owns threshold recommendation. The second does not.

## RALPLAN-DR Summary

### Principles

1. Put row-local facts on rows and benchmark-global facts in one benchmark-global artifact.
2. Fail closed when benchmark provenance is missing or mismatched.
3. Make threshold changes evidence-backed and benchmark-version-scoped.
4. Preserve the current eval-gate artifact's narrow job.

### Decision Drivers

1. The benchmark needs more ambiguous support before threshold interpretation is credible.
2. Shared sample ids are not a valid comparison contract across benchmark versions.
3. The repo already has a stable place for prompt/schema go-no-go output and should not overload it.

### Viable Options

#### Option A: Inline everything in fixture rows

Rejected because benchmark identity becomes duplicated and harder to review.

#### Option B: Hybrid manifest + inline rationale

Chosen because it fits the ownership split and the current repo surfaces.

#### Option C: Generated artifacts own benchmark identity

Rejected because it versions runs, not the benchmark.

### Decision

Adopt the hybrid model:
- inline `metadata.label_rationale` for ambiguous rows
- manifest-owned benchmark identity
- provenance-aware generated artifacts
- benchmark-mismatch comparison blocking
- separate re-baseline governance artifact

## Implementation Plan

### Step 1: Add the benchmark manifest and tighten fixture rules

Files:
- `tests/fixtures/thesis_llm_golden_set.jsonl`
- `tests/fixtures/thesis_llm_golden_set.manifest.json`
- `tests/utils/test_thesis_llm_golden_set.py`

Requirements:
- manifest is the sole benchmark identity owner
- fixture rows keep `metadata.label_rationale` only where required
- tests enforce manifest/fixture consistency
- tests enforce manifest/fixture fingerprint parity using the canonical JSONL rule

Acceptance criteria:
- manifest `sample_count` matches dataset row count
- manifest `scenario_counts` matches dataset contents
- manifest `dataset_fingerprint` matches the recomputed canonical fixture fingerprint
- every ambiguous row has `metadata.label_rationale`
- clear-control rows are not forced to carry rationale

### Step 2: Expand the ambiguous taxonomy by 24 rows

Files:
- `tests/fixtures/thesis_llm_golden_set.jsonl`
- `tests/fixtures/thesis_llm_golden_set.manifest.json`

Target distribution:
- `b2b_in_disguise`: `+6`
- `ad_supported`: `+4`
- `employer_sponsored`: `+4`
- `two_sided_marketplace`: `+4`
- `gig_economy`: `+3`
- `creator_tools`: `+3`

Acceptance criteria:
- dataset total becomes `64`
- clear-control totals remain `clear_consumer=10`, `clear_b2b=10`
- every new ambiguous row includes rationale

### Step 3: Make diagnostic artifacts benchmark-aware

Files:
- `scripts/thesis_diagnostic_runner.py`
- `tests/scripts/test_thesis_diagnostic_runner.py`

Requirements:
- add benchmark provenance echo fields to JSONL and summary
- validate internal artifact consistency before comparing

`--compare-against` acceptance criteria:
- block if baseline or candidate artifact lacks benchmark provenance
- block if `benchmark_id`, `benchmark_version`, or `benchmark_fingerprint` differ
- emit `comparison.status="blocked_benchmark_mismatch"` and exit non-zero
- do not emit improved/regressed sample claims on mismatch

### Step 4: Make eval-gate output provenance-aware only

Files:
- `utils/thesis_eval_gate.py`
- `scripts/run_thesis_llm_eval_gate.py`
- `tests/scripts/test_run_thesis_llm_eval_gate.py`

Acceptance criteria:
- eval-gate JSON echoes benchmark provenance fields
- eval-gate JSON does not own threshold-recommendation logic
- no benchmark changelog or threshold-governance fields are moved into eval-gate

### Step 5: Produce a re-baseline recommendation artifact

Files:
- `.omx/specs/thesis-llm-benchmark-rebaseline.json`
- tests for whichever helper emits it

Artifact contents:
- benchmark identity echo
- pre/post sample counts
- scenario counts
- overall LLM accuracy
- ambiguous-slice accuracy
- per-scenario metrics and support
- recommendation:
  - `keep_0_90`
  - `raise_threshold`
  - `lower_threshold`
- justification

Threshold acceptance rule:
- keep `0.90` if overall `>= 0.90`, ambiguous slice `>= 0.85`, and each ambiguous scenario with support `>= 6` scores `>= 0.75`
- lower only if overall is `0.85-0.89`, ambiguous slice `>= 0.80`, each ambiguous scenario with support `>= 6` scores `>= 0.67`, and misses are concentrated in new ambiguous rows
- raise only if overall `>= 0.97`, ambiguous slice `>= 0.95`, and each ambiguous scenario with support `>= 6` scores `>= 0.90`

## Acceptance Criteria

1. Benchmark identity is singly owned by `tests/fixtures/thesis_llm_golden_set.manifest.json`.
2. Ambiguous-row rationale remains inline and is not moved to a separate governance artifact.
3. `dataset_fingerprint` is explicitly defined as the lowercase SHA-256 digest of the canonical JSONL serialization of `tests/fixtures/thesis_llm_golden_set.jsonl`.
4. Tests and validation recompute the fixture fingerprint from `tests/fixtures/thesis_llm_golden_set.jsonl` and fail on manifest drift.
5. The expanded benchmark reaches `64` rows with the exact six-scenario `24`-row distribution.
6. Diagnostic JSONL and summary JSON include the exact benchmark provenance echo fields defined above.
7. `--compare-against` blocks on benchmark mismatch or missing benchmark provenance.
8. Eval-gate JSON echoes benchmark provenance but does not own re-baseline governance.
9. `.omx/specs/thesis-llm-benchmark-rebaseline.json` is the named repo-native threshold-governance artifact.

## Risks And Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Manifest and fixture drift | false provenance claims | recompute canonical fixture fingerprint in tests and fail on manifest drift |
| Legacy comparison artifacts lack provenance | false historical comparisons | fail closed until artifacts are regenerated |
| Threshold change becomes subjective | governance churn | require the explicit overall + ambiguous-slice + per-scenario rule |
| Expanded benchmark still too easy | threshold remains under-informative | allow raise recommendation only with very high ambiguous-slice evidence |

## ADR

### Decision

Use a hybrid benchmark-governance model with manifest-owned benchmark identity, inline ambiguous-row rationale, provenance-aware generated artifacts, and a separate re-baseline governance artifact.

### Drivers

- benchmark-global ownership belongs in one place
- ambiguity rationale belongs on the row
- compare-against must stop making benchmark-blind claims
- eval-gate scope should stay narrow

### Alternatives Considered

- all identity inline in rows
- generated artifacts owning benchmark identity

### Consequences

- one new manifest/spec file next to the fixture
- stricter comparison behavior
- one additional governance artifact for re-baseline recommendations

### Follow-Ups

- regenerate baseline diagnostic artifacts under the new provenance contract
- implement the canonical fingerprint recomputation gate for `tests/fixtures/thesis_llm_golden_set.jsonl`
- run the first expanded-benchmark re-baseline pass

## Available-Agent-Types Roster

- `architect`
- `critic`
- `executor`
- `test-engineer`
- `verifier`
- `writer`

## Follow-Up Staffing Guidance

### `$ralph`

Recommended when one owner should carry benchmark, diagnostics, and governance updates sequentially.

### `$team`

Suggested lanes:
1. fixture + manifest + structural tests
2. diagnostic provenance + compare blocking
3. eval-gate echo + re-baseline artifact

Suggested reasoning:
- Lane 1: high
- Lane 2: high
- Lane 3: medium

## Launch Hints

`$ralph .omx/plans/prd-thesis-classifier-golden-set-expansion-followup.md`

`$team "Execute .omx/plans/prd-thesis-classifier-golden-set-expansion-followup.md and .omx/plans/test-spec-thesis-classifier-golden-set-expansion-followup.md. Keep benchmark identity manifest-owned, keep ambiguous rationale inline, block compare-against on benchmark mismatch, and put threshold recommendations only in .omx/specs/thesis-llm-benchmark-rebaseline.json."`

## Team Verification Path

Require proof that:

1. manifest is the only benchmark identity owner
2. fixture ambiguity rationale stays inline
3. manifest `dataset_fingerprint` is derived from the canonical JSONL serialization of `tests/fixtures/thesis_llm_golden_set.jsonl`
4. tests and validation recompute the fixture fingerprint and fail on manifest drift
5. expanded taxonomy counts match the `24`-row target
6. comparison blocks on benchmark mismatch
7. eval-gate echoes provenance without taking governance ownership
8. re-baseline recommendation is emitted to `.omx/specs/thesis-llm-benchmark-rebaseline.json`

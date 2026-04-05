# Thesis Classifier Golden-Set Expansion Follow-Up

## Scope

- Expand `tests/fixtures/thesis_llm_golden_set.jsonl` by `24` new ambiguous/edge-case samples, bringing the working target to `64` total rows.
- Adopt a hybrid benchmark-governance contract:
  - ambiguous sample rationale stays inline as `metadata.label_rationale`
  - benchmark identity/version/fingerprint/changelog live in one manifest/spec artifact
  - generated diagnostic and gate artifacts only echo benchmark identity/fingerprint provenance
- Make `--compare-against` benchmark-aware and block comparison claims on benchmark mismatch.
- Define a re-baseline recommendation artifact and a testable rule for keeping or changing the current `0.90` LLM gate.

Out of scope:
- Prompt redesign or prompt promotion.
- New dependencies.
- Letting `.omx/specs/thesis-llm-eval-gate.json` become the owner of benchmark governance.

## Grounded Facts

- The current context snapshot is `.omx/context/thesis-classifier-golden-set-expansion-followup-20260405T223034Z.md`.
- `tests/fixtures/thesis_llm_golden_set.jsonl` currently has `40` samples:
  - `clear_consumer=10`
  - `clear_b2b=10`
  - `b2b_in_disguise=5`
  - `ad_supported=3`
  - `employer_sponsored=3`
  - `two_sided_marketplace=3`
  - `gig_economy=3`
  - `creator_tools=3`
- `tests/utils/test_thesis_llm_golden_set.py` currently enforces count, unique ids, valid targets, and scenario presence only.
- `utils/thesis_evaluator.py` loads JSONL permissively, so richer sample metadata remains repo-compatible.
- `scripts/thesis_diagnostic_runner.py` currently writes per-sample JSONL plus summary JSON, and `--compare-against` compares by shared `sample_id` only.
- `scripts/run_thesis_llm_eval_gate.py` writes `.omx/specs/thesis-llm-eval-gate.json`, and `utils/thesis_eval_gate.py` currently owns only prompt/schema go-no-go fields.
- `.omx/specs/thesis-llm-eval-gate.json` already says `decision="go"`, `llm_accuracy=1.0`, `threshold=0.9`.

## RALPLAN-DR

### Principles

1. Keep benchmark governance separate from prompt-promotion governance.
2. Keep ambiguous-label rationale at the sample, not in a detached review note.
3. Give benchmark identity one owner only; generated artifacts may echo it, not define it.
4. Comparison claims are invalid if benchmark identity differs.
5. Threshold changes require explicit re-baseline evidence, not intuition.

### Decision Drivers

1. The current benchmark is saturated enough that a threshold discussion is meaningless without harder ambiguous coverage.
2. Ambiguity is row-local, but benchmark identity is benchmark-global; those ownership levels should stay separate.
3. The current `--compare-against` behavior is benchmark-blind and can overstate improvement claims.
4. The eval-gate artifact already exists and should remain a prompt/schema decision artifact, not a governance ledger.

### Viable Options

#### Option A: All governance inline in fixture rows

Pros:
- simple loader story
- rationale and identity travel with rows

Cons:
- benchmark identity duplicated across every fixture row
- version/fingerprint drift harder to review cleanly
- weak fit for benchmark-level changelog ownership

#### Option B: Hybrid contract

Shape:
- `metadata.label_rationale` inline only for ambiguous rows
- `tests/fixtures/thesis_llm_golden_set.manifest.json` is the single owner of:
  - `benchmark_id`
  - `benchmark_version`
  - `dataset_path`
  - `dataset_fingerprint`
  - `sample_count`
  - `scenario_counts`
  - `ambiguous_scenarios`
  - `changelog`
- generated artifacts echo benchmark identity/fingerprint only

Pros:
- matches the ownership split the reviews asked for
- keeps the fixture readable
- makes benchmark identity auditable without per-row duplication
- fits the current runner/gate surfaces with bounded changes

Cons:
- adds one manifest file and one loader/check path

#### Option C: Generated artifacts own benchmark identity

Pros:
- smallest immediate fixture diff

Cons:
- benchmark identity would be run-owned instead of benchmark-owned
- comparison on historical artifacts becomes brittle
- directly conflicts with the requested design direction

### Decision

Choose **Option B: hybrid contract**.

Decision detail:
- Ambiguous rows keep inline `metadata.label_rationale`.
- Benchmark identity is owned only by `tests/fixtures/thesis_llm_golden_set.manifest.json`.
- Diagnostic JSONL, summary JSON, and eval-gate JSON echo:
  - `benchmark_id`
  - `benchmark_version`
  - `benchmark_fingerprint`
  - `benchmark_manifest_path`
- The re-baseline recommendation lives in a distinct repo-native governance artifact:
  - `.omx/specs/thesis-llm-benchmark-rebaseline.json`
- `.omx/specs/thesis-llm-eval-gate.json` becomes provenance-aware only; it must not own threshold governance or benchmark policy.

## Concrete Implementation Plan

### Step 1: Define the benchmark identity owner and row-local ambiguity contract

- Add `tests/fixtures/thesis_llm_golden_set.manifest.json` as the only owner of benchmark identity/version/fingerprint/changelog expectations.
- Keep fixture rows free of duplicated benchmark identity.
- Require `metadata.label_rationale` only for rows whose scenario appears in manifest `ambiguous_scenarios`.
- Require manifest validation to recompute `dataset_fingerprint` from `tests/fixtures/thesis_llm_golden_set.jsonl` using the canonical serialization rule and fail on manifest drift.

Manifest contract:
- `benchmark_id`: `thesis_llm_golden_set`
- `benchmark_version`: new post-expansion version string
- `dataset_path`: `tests/fixtures/thesis_llm_golden_set.jsonl`
- `dataset_fingerprint`: lowercase SHA-256 hex digest over the UTF-8 bytes of the canonical JSONL serialization of benchmark rows
- `sample_count`
- `scenario_counts`
- `ambiguous_scenarios`
- `changelog`

Canonical `dataset_fingerprint` derivation rule:
- read `tests/fixtures/thesis_llm_golden_set.jsonl`
- parse each non-empty fixture line as JSON
- recursively sort object keys within each row
- preserve fixture row order exactly as committed
- serialize each normalized row as compact JSON with no extra whitespace
- join rows with `\n`
- append one trailing `\n`
- hash those UTF-8 bytes with SHA-256
- store the lowercase hex digest in manifest `dataset_fingerprint`

Mandatory parity gate:
- tests and validation must recompute `dataset_fingerprint` from `tests/fixtures/thesis_llm_golden_set.jsonl`
- fixture-to-manifest drift must fail validation if the recomputed digest differs from manifest `dataset_fingerprint`

### Step 2: Expand the benchmark with explicit taxonomy targets

Add `24` new cases using this taxonomy:
- `b2b_in_disguise`: `+6` new rows, target total `11`
- `ad_supported`: `+4` new rows, target total `7`
- `employer_sponsored`: `+4` new rows, target total `7`
- `two_sided_marketplace`: `+4` new rows, target total `7`
- `gig_economy`: `+3` new rows, target total `6`
- `creator_tools`: `+3` new rows, target total `6`

Distribution rule:
- keep `clear_consumer=10` and `clear_b2b=10` unchanged in this follow-up
- all `24` new rows belong to the six ambiguous scenarios above
- every new ambiguous row must include `metadata.label_rationale`

### Step 3: Make diagnostics provenance-aware and comparison-safe

Add these echo fields to each diagnostic JSONL row:
- `benchmark_id`
- `benchmark_version`
- `benchmark_fingerprint`
- `benchmark_manifest_path`

Add these fields to each diagnostic summary JSON:
- `benchmark_id`
- `benchmark_version`
- `benchmark_fingerprint`
- `benchmark_manifest_path`
- `benchmark_sample_count`

`--compare-against` contract:
- extract benchmark provenance from candidate artifact and baseline artifact
- fail comparison if either artifact is missing the benchmark fields
- fail comparison if `benchmark_id`, `benchmark_version`, or `benchmark_fingerprint` differ
- on failure:
  - exit non-zero
  - do not emit improved/regressed claims
  - write `comparison.status="blocked_benchmark_mismatch"` plus baseline/candidate identity and mismatch reasons in summary JSON
- shared `sample_id` overlap alone is no longer sufficient to authorize comparison claims

### Step 4: Make the eval gate provenance-aware but not governance-owning

Add these echo fields to `.omx/specs/thesis-llm-eval-gate.json`:
- `benchmark_id`
- `benchmark_version`
- `benchmark_fingerprint`
- `benchmark_manifest_path`

Do not add threshold-governance or re-baseline recommendation fields to the eval-gate artifact.

### Step 5: Re-baseline and make the threshold review testable

Generate `.omx/specs/thesis-llm-benchmark-rebaseline.json` after expansion.

Required contents:
- benchmark identity echo
- pre-expansion and post-expansion sample counts
- per-scenario counts
- overall LLM accuracy
- ambiguous-slice accuracy across the six ambiguous scenarios
- per-class metrics with support counts
- recommendation: `keep_0_90`, `raise_threshold`, or `lower_threshold`
- justification text tied to measured metrics

Threshold review rule:
- **Keep `0.90`** if all are true:
  - overall LLM accuracy on the expanded benchmark is `>= 0.90`
  - ambiguous-slice accuracy is `>= 0.85`
  - every ambiguous scenario with support `>= 6` scores at least `0.75`
- **Consider lowering below `0.90`** only if all are true:
  - benchmark manifest/version has changed and the expansion is approved
  - overall accuracy lands in `0.85-0.89`
  - ambiguous-slice accuracy is `>= 0.80`
  - every ambiguous scenario with support `>= 6` scores at least `0.67`
  - failures are concentrated in newly added ambiguous rows, not clear-control rows
- **Consider raising above `0.90`** only if all are true:
  - overall accuracy is `>= 0.97`
  - ambiguous-slice accuracy is `>= 0.95`
  - every ambiguous scenario with support `>= 6` scores at least `0.90`
  - the expanded benchmark still shows no meaningful discrimination across the ambiguous slice

Default bias:
- keep `0.90` unless the re-baseline artifact proves otherwise.

## Acceptance Criteria

1. The repo plan specifies a hybrid governance contract with:
   - inline `metadata.label_rationale` only for ambiguous rows
   - single-owned benchmark identity in `tests/fixtures/thesis_llm_golden_set.manifest.json`
   - explicit canonical `dataset_fingerprint` derivation from the committed fixture JSONL
2. The plan names exact benchmark provenance echo fields for diagnostic JSONL, summary JSON, and eval-gate JSON.
3. The plan specifies hard-block behavior for `--compare-against` on benchmark mismatch or missing benchmark provenance.
4. The plan replaces vague bucket language with the explicit six-scenario taxonomy and the `24`-row distribution target.
5. The plan defines a measurable keep/change rule for the `0.90` threshold using overall accuracy, ambiguous-slice accuracy, and per-scenario floors.
6. The plan names `.omx/specs/thesis-llm-benchmark-rebaseline.json` as the governance artifact for threshold recommendations.
7. The plan keeps `.omx/specs/thesis-llm-eval-gate.json` provenance-aware only, not governance-owning.
8. The plan requires tests and validation to recompute the fixture fingerprint from `tests/fixtures/thesis_llm_golden_set.jsonl` and fail on manifest drift.

## Verification Categories

### Plan/Fixture Contract

- `pytest tests/utils/test_thesis_llm_golden_set.py -q`
- validate that manifest `dataset_fingerprint` equals the recomputed SHA-256 digest of the canonical JSONL serialization of `tests/fixtures/thesis_llm_golden_set.jsonl`

### Diagnostic Provenance + Comparison

- `pytest tests/scripts/test_thesis_diagnostic_runner.py -q`
- `python scripts/thesis_diagnostic_runner.py --run-id benchmark_expansion_check --dataset tests/fixtures/thesis_llm_golden_set.jsonl --compare-against artifacts/thesis_diagnostics/candidate_v3.jsonl`

### Eval Gate Provenance Echo

- `pytest tests/scripts/test_run_thesis_llm_eval_gate.py -q`
- `python scripts/run_thesis_llm_eval_gate.py --dataset tests/fixtures/thesis_llm_golden_set.jsonl`

### Re-Baseline Governance

- verify `.omx/specs/thesis-llm-benchmark-rebaseline.json` is produced and remains distinct in purpose from `.omx/specs/thesis-llm-eval-gate.json`

## ADR

### Title

Use a hybrid benchmark-governance contract for thesis golden-set expansion.

### Status

Draft

### Decision

Keep ambiguous sample rationale inline, but move benchmark identity/version/fingerprint/changelog ownership into `tests/fixtures/thesis_llm_golden_set.manifest.json`. Make generated artifacts echo benchmark provenance only, and block `--compare-against` claims on benchmark mismatch.

### Drivers

- benchmark identity is benchmark-level, not row-level
- ambiguity rationale is row-level
- existing diagnostic comparison is benchmark-blind
- eval-gate ownership should remain narrow

### Alternatives Considered

- all identity inline in fixture rows
- generated-artifact-owned benchmark identity

### Why Chosen

This is the smallest repo-fit design that matches the review direction, preserves fixture readability, and makes comparison claims auditable.

### Consequences

- one manifest/spec file is added next to the dataset
- diagnostics and gate outputs gain benchmark provenance echo fields
- comparison becomes intentionally stricter
- threshold governance moves into a separate re-baseline artifact

### Follow-Ups

- implement the manifest loader, canonical fingerprinting rule, and parity gate
- add the `24` new ambiguous rows
- produce the first re-baseline artifact on the expanded benchmark

## Available-Agent-Types Roster

- `planner`
- `architect`
- `critic`
- `executor`
- `test-engineer`
- `verifier`
- `writer`

## Staffing Guidance

### `$ralph`

Use when one owner should carry the benchmark-governance lane end to end.

Suggested sequence:
1. fixture + manifest contract
2. diagnostic provenance + compare-against hard block
3. eval-gate provenance echo
4. re-baseline artifact + threshold recommendation

### `$team`

Recommended team split:
- Lane 1: fixture expansion + manifest + structural tests
- Lane 2: diagnostic provenance fields + mismatch blocking
- Lane 3: eval-gate echo + re-baseline artifact + threshold recommendation

Suggested reasoning by lane:
- Lane 1: high
- Lane 2: high
- Lane 3: medium

## Launch Hints

`$ralph ".omx/plans/prd-thesis-classifier-golden-set-expansion-followup.md and .omx/plans/test-spec-thesis-classifier-golden-set-expansion-followup.md"`

`$team "Execute .omx/plans/prd-thesis-classifier-golden-set-expansion-followup.md and .omx/plans/test-spec-thesis-classifier-golden-set-expansion-followup.md. Keep benchmark identity owned by tests/fixtures/thesis_llm_golden_set.manifest.json, keep label_rationale inline only for ambiguous rows, block compare-against on benchmark mismatch, and write threshold recommendations only to .omx/specs/thesis-llm-benchmark-rebaseline.json."`

## Team Verification Path

Before handoff close, require proof of:

1. fixture rows do not duplicate benchmark identity
2. ambiguous rows require inline `metadata.label_rationale`
3. manifest is the only benchmark identity owner and stores the lowercase hex `dataset_fingerprint`
4. diagnostic JSONL, summary JSON, and eval-gate JSON echo the exact benchmark provenance fields
5. tests and validation recompute the canonical fixture fingerprint from `tests/fixtures/thesis_llm_golden_set.jsonl` and fail on manifest drift
6. `--compare-against` blocks on benchmark mismatch and does not emit false improvement claims
7. `.omx/specs/thesis-llm-benchmark-rebaseline.json` owns the threshold recommendation
8. `.omx/specs/thesis-llm-eval-gate.json` remains a prompt/schema decision artifact only

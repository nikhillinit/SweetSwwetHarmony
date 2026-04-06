# Test Spec: Thesis Classifier Golden-Set Expansion Follow-Up

Date: 2026-04-05
Companion PRD: `.omx/plans/prd-thesis-classifier-golden-set-expansion-followup.md`

## Objective

Verify that the expanded thesis golden set gains auditable benchmark identity, stronger ambiguous coverage, benchmark-safe comparison behavior, and a separate threshold-governance artifact without overloading the eval-gate artifact.

## Verification Contract

### Benchmark Identity Contract

1. `tests/fixtures/thesis_llm_golden_set.manifest.json` is the only owner of benchmark identity/version/fingerprint/changelog.
2. `dataset_fingerprint` is the lowercase SHA-256 hex digest of the UTF-8 bytes of a canonical JSONL serialization of `tests/fixtures/thesis_llm_golden_set.jsonl`.
3. Canonical JSONL serialization:
   - parses each non-empty fixture line as JSON
   - recursively sorts object keys
   - preserves committed row order
   - serializes each normalized row as compact JSON with no extra whitespace
   - joins rows with `\n`
   - appends one trailing `\n`
4. Tests and validation recompute the fingerprint from `tests/fixtures/thesis_llm_golden_set.jsonl` and fail on manifest drift.
5. Fixture rows do not repeat benchmark identity fields.
6. Generated artifacts may echo benchmark identity fields, but they do not own them.

### Ambiguity Contract

1. `metadata.label_rationale` is required for scenarios listed in manifest `ambiguous_scenarios`.
2. Clear-control rows are not forced to carry rationale.

### Comparison Contract

1. `--compare-against` requires benchmark provenance in both artifacts.
2. Comparison blocks if `benchmark_id`, `benchmark_version`, or `benchmark_fingerprint` differ.
3. When blocked, no improved/regressed claims are emitted.

### Governance Contract

1. `.omx/specs/thesis-llm-benchmark-rebaseline.json` owns threshold recommendations.
2. `.omx/specs/thesis-llm-eval-gate.json` only echoes benchmark provenance and prompt/schema decision state.

## Unit Tests

1. Manifest loader or validator reads:
   - `benchmark_id`
   - `benchmark_version`
   - `dataset_path`
   - `dataset_fingerprint`
   - `sample_count`
   - `scenario_counts`
   - `ambiguous_scenarios`
2. Fingerprint validator proves:
   - canonical JSONL serialization is deterministic
   - manifest `dataset_fingerprint` equals the recomputed fingerprint from `tests/fixtures/thesis_llm_golden_set.jsonl`
   - manifest drift fails validation
3. Fixture validation proves:
   - manifest `sample_count` matches row count
   - manifest `scenario_counts` match actual counts
   - ambiguous rows require `metadata.label_rationale`
   - clear rows do not require rationale
4. Diagnostic record builder includes benchmark provenance echo fields in JSONL rows.
5. Summary builder includes benchmark provenance echo fields plus `benchmark_sample_count`.
6. Eval-gate artifact builder includes benchmark provenance echo fields without adding threshold-governance ownership.

## Integration Tests

1. Expanded fixture count and taxonomy distribution:
   - total rows `= 64`
   - `b2b_in_disguise=11`
   - `ad_supported=7`
   - `employer_sponsored=7`
   - `two_sided_marketplace=7`
   - `gig_economy=6`
   - `creator_tools=6`
   - `clear_consumer=10`
   - `clear_b2b=10`
2. Fixture-manifest parity gate:
   - recomputes fingerprint from `tests/fixtures/thesis_llm_golden_set.jsonl`
   - passes only when manifest `dataset_fingerprint` matches
   - fails on manifest drift
3. Diagnostic runner with benchmark-matched baseline artifact:
   - comparison proceeds
   - improved/regressed sets are produced normally
4. Diagnostic runner with mismatched baseline artifact:
   - exits non-zero
   - emits `comparison.status="blocked_benchmark_mismatch"`
   - emits mismatch reasons
   - emits no improved/regressed claims
5. Diagnostic runner with legacy artifact missing benchmark fields:
   - exits non-zero
   - emits blocked status for missing provenance

## E2E Tests

1. Diagnostic smoke run on the expanded benchmark writes:
   - per-sample JSONL with benchmark provenance echo
   - summary JSON with benchmark provenance echo
2. Diagnostic/eval validation recomputes the fixture fingerprint from `tests/fixtures/thesis_llm_golden_set.jsonl` before trusting manifest provenance.
3. Eval-gate run on the expanded benchmark writes benchmark provenance echo to `.omx/specs/thesis-llm-eval-gate.json`.
4. Re-baseline flow writes `.omx/specs/thesis-llm-benchmark-rebaseline.json` with:
   - benchmark identity echo
   - overall accuracy
   - ambiguous-slice accuracy
   - per-scenario support and metrics
   - threshold recommendation

## Observability Checks

1. Summary JSON exposes whether comparison was allowed or blocked.
2. Benchmark mismatch reasons are human-readable and machine-checkable.
3. Parity-gate failures identify manifest-vs-fixture fingerprint drift explicitly.
4. Re-baseline artifact states whether the recommendation is `keep_0_90`, `raise_threshold`, or `lower_threshold`.

## Threshold Review Assertions

### Keep `0.90`

Assert recommendation is `keep_0_90` only when:
- overall accuracy `>= 0.90`
- ambiguous-slice accuracy `>= 0.85`
- every ambiguous scenario with support `>= 6` scores `>= 0.75`

### Lower Threshold

Assert lowering is rejected unless:
- overall accuracy is in `0.85-0.89`
- ambiguous-slice accuracy `>= 0.80`
- every ambiguous scenario with support `>= 6` scores `>= 0.67`
- misses are concentrated in newly added ambiguous rows rather than clear controls

### Raise Threshold

Assert raising is rejected unless:
- overall accuracy `>= 0.97`
- ambiguous-slice accuracy `>= 0.95`
- every ambiguous scenario with support `>= 6` scores `>= 0.90`

## Exit Gates

1. Benchmark manifest ownership is enforced.
2. Canonical fixture fingerprint derivation is enforced.
3. Manifest/fixture parity gating is enforced.
4. Ambiguous-row rationale policy is enforced.
5. The expanded taxonomy reaches the exact `64`-row distribution.
6. Diagnostic artifacts echo benchmark provenance.
7. Benchmark-mismatch comparison blocking is enforced.
8. Eval-gate remains provenance-aware only.
9. Re-baseline recommendation is emitted to `.omx/specs/thesis-llm-benchmark-rebaseline.json`.

## Not-Tested / Deferred

1. Prompt redesign based on expanded benchmark outcomes.
2. Broad benchmark framework abstractions beyond this thesis fixture.
3. Cross-benchmark comparison semantics between unrelated benchmark ids.

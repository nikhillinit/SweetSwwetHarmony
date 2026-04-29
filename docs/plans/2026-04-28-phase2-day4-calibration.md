# Phase 2 Day 4: Conformal Recalibration Script

Date: 2026-04-28
Status: draft - awaiting approval before build
Base: `phase2/instrumentation@daab672`
Branch (to create from base): `phase2/day4-calibration`

## Goal

Produce a calibration script that fits a single overall conformal cut-off on
`state/calibration_ids.json` with bootstrap confidence intervals, plus a
`--fallback-cv` mode that fits via repeated K-fold stratified cross-validation
on `state/train_ids.json` only. The holdout split is never read except to
verify input/holdout disjointness.

## Why now

Day 2 (`phase2/instrumentation@3eb3550`) produced deterministic stratified
splits with 211 labeled rows: train=124, calibration=40 (TP=3, FP=36, UNSURE=1),
holdout=47 (TP=6, FP=40, UNSURE=1). Item 3 of the Day 0 blocker tracker
(`.omx/phase2_blocker_patch.md`) is the last unchecked instrumentation gate
that downstream Day 5+ work depends on. The Day 2 review course-correction
locked in five design constraints (paste-verbatim section below). Day 4 turns
those constraints into runnable code so the Day 8-10 learning-loop cycle can
consume an algorithmic, auditable cut-off rather than a hand-tuned constant.

## Five locked design constraints (verbatim from `.omx/phase2_blocker_patch.md` condition 3, sub-bullets 1-5)

1. **Default to bootstrap CIs over the calibration split.** For any conformal
   threshold or isotonic-regression fit, draw bootstrap samples (with
   replacement) from `state/calibration_ids.json` and report the distribution
   (mean + 5th/50th/95th percentiles + coefficient of variation). Point
   estimates from 3 TPs are misleading - show the uncertainty.

2. **Provide a `--fallback-cv` mode.** If bootstrap CIs are too wide to act
   on, switch to **repeated K-fold stratified cross-validation on the training
   set only** (`state/train_ids.json`). Holdout (`state/holdout_ids.json`)
   stays untouched in either mode.

3. **Do NOT attempt per-stratum cut-offs.** With 3 TPs total, attempting
   separate cut-offs per source_api (hacker_news / rss_feeds / job sources) is
   noise. Day 4 must produce a single overall conformal score adjustment that
   uses the calibration split as a whole.

4. **Surface instability prominently.** The calibration script's output must
   include an explicit warning when the bootstrap CV is large, and the
   dashboard block must show the percentile band, not just the median. Anyone
   reading the dashboard later needs to see "this cut-off is uncertain."

5. **Holdout protection enforced via `--holdout-file state/holdout_ids.json`.**
   Day 4 calibration must never read holdout signal_ids; the Day 4 PR review
   will fail if any code path can.

These constraints are paste-verbatim from the Day 2 review and govern every
design decision below. Any apparent conflict between this plan and the five
constraints is a plan bug; the constraints win.

## Non-goals

- Modifying `HIGH_CONFIDENCE_THRESHOLD` (frozen at 0.7 in `workflows/pipeline.py`
  via `self._gate.HIGH_CONFIDENCE_THRESHOLD` at line 2410). Day 4 produces a
  separate conformal cut-off artifact; it does not touch the constant.
- Per-stratum cut-offs (per source_api / signal_type / month) - explicitly
  forbidden by constraint 3.
- Reading `state/holdout_ids.json` from any code path other than the
  `--holdout-file` disjointness validator. Holdout IDs are loaded only to
  assert `intersection(input_ids, holdout_ids) == empty`; they are never used
  to fit the cut-off.
- Wiring the calibration artifact into the router config or Day 3 dashboard.
  Both are deferred to Day 5+ (see "Out of scope (deferred)").
- Any change to `scripts/create_evaluation_splits.py` or the
  `state/{train,calibration,holdout}_ids.json` schema. Day 4 is a pure
  consumer of those splits.
- Any change to `workflows/pipeline.py`, `connectors/notion_connector_v2.py`,
  `verification/`, `monitoring/`, or any pipeline path. Day 4 is a side-car
  script.
- Live `signals.db` migration or schema change.
- Re-running the LLM thesis classifier on calibration rows. The script
  consumes whatever scores already live in the DB at run time. Provenance
  records the active prompt version so Day 5+ readers know what the cut-off
  applies to.
- Changes to `.gitignore`, CI workflows, or governance state machinery.

## Deliverables

- `scripts/recalibrate_conformal.py` - the calibration script (placement
  rationale: parity with Day 3's `scripts/generate_strategy_dashboard.py`;
  no shared library extraction yet because Day 5+ has not declared its
  consumer surface).
- `tests/scripts/test_recalibrate_conformal.py` - TDD-built contract suite.
- Output artifact at `state/conformal_calibration.json` (default path; CLI
  flag `--out` to override). Gitignored per the existing `state/` pattern -
  see "Output artifact disposition" below.
- One-line addition to `.gitignore`: `state/conformal_calibration.json`,
  placed in the existing `state/` block alongside `train_ids.json`,
  `calibration_ids.json`, `holdout_ids.json`, and
  `evaluation_splits_summary.json`. This is the only source-tree edit
  outside `scripts/` and `tests/scripts/`.
- No other source-tree changes. No new `state/` schema entries beyond the
  new output file. No changes to `workflows/`, `verification/`,
  `monitoring/`, `dashboard/`, `connectors/`, `ops/`, `storage/`,
  `governance/`, or any pipeline/router path.
- No README or wiki updates in this PR. Run-instructions for the script live
  in the script's `--help` output and the test file. Documentation
  consolidation belongs in Day 5+ when the consumer side lands.

## Output artifact disposition

`state/conformal_calibration.json` is **gitignored**, consistent with the rest
of the `state/` directory:

```
state/train_ids.json
state/calibration_ids.json
state/holdout_ids.json
state/evaluation_splits_summary.json
```

Only `state/collectors.json` is tracked. The calibration artifact follows the
per-run-output convention, not the canonical-reference convention, because:

1. It is a *consumer* of the splits. Re-running it is cheap and deterministic
   given the same `--seed` and the same split files.
2. Provenance fields inside the artifact (split SHA, git commit, seed, mode,
   active LLM prompt version) make every run self-describing without needing
   git history.
3. Committing it would invite drift between the file and the live DB scores,
   which change as new LLM classifications land.

Add `state/conformal_calibration.json` to `.gitignore` in this PR. The
existing `state/` block in `.gitignore` is the right home; do not introduce a
new section.

## Branch state and inputs (verify before first commit)

- `git -C C:/dev/Harmonic branch --show-current` should report `phase2/day4-calibration`.
  If it reports `phase2/instrumentation`, run:
  `git -C C:/dev/Harmonic checkout -b phase2/day4-calibration daab672`.
- `state/calibration_ids.json`, `state/train_ids.json`, `state/holdout_ids.json`,
  and `state/evaluation_splits_summary.json` are all gitignored. On a fresh
  checkout they will be absent. Regenerate locally before invoking the Day 4
  script:
  `python scripts/create_evaluation_splits.py --seed 42`.
- `signals.db` must contain rows referenced by the split files. The split
  files were generated against `signals.db` at Day 2; if the DB has been
  reset, re-run `create_evaluation_splits.py` (which will produce a different
  set of IDs, and the calibration artifact's split SHA will reflect that).
- `.omx/wave6/live_schema_contract.json` is the canonical schema contract.
  The Day 4 script invokes `scripts.inspect_live_schema.load_contract` +
  `inspect_database` directly - never `main()`, which writes report files.

## CLI

```
recalibrate_conformal.py
  --calibration-file <path>     default: state/calibration_ids.json
  --train-file <path>           default: state/train_ids.json
  --holdout-file <path>         REQUIRED for the disjointness validator;
                                default: state/holdout_ids.json
  --db <path>                   default: $DISCOVERY_DB_PATH or signals.db
  --schema-contract <path>      default: .omx/wave6/live_schema_contract.json
  --mode <bootstrap|cv>         default: bootstrap
                                cv is shorthand; --fallback-cv is the
                                user-facing equivalent and sets mode=cv
  --fallback-cv                 boolean alias for --mode cv (matches the
                                language of constraint 2)
  --bootstrap-iterations <int>  default: 1000; range [100, 100000]
  --cv-folds <int>              default: 5; only consulted when mode=cv
  --cv-repeats <int>            default: 10; only consulted when mode=cv
  --min-calibration-size <int>  default: 4; calibration row count below this
                                exits 1. Bootstrap on 3 rows is meaningless;
                                4 is the minimum that supports at least one
                                resample with non-zero variance.
  --target-precision <float>    default: 0.90; the auto-push precision target
                                referenced in phase2_execution_plan.md
                                Days 8-10 step 3
  --seed <int>                  default: 42
  --instability-cv-warn <float> default: 0.20; bootstrap CV above this emits
                                a prominent warning in stdout and the
                                artifact's instability section
  --instability-cv-fail <float> default: 0.50; only consulted with
                                --strict-instability; otherwise informational
  --strict-instability          boolean; when set, exit 3 if bootstrap CV
                                exceeds --instability-cv-fail (default: off,
                                preserves Day 3 "WARN over hard failure"
                                preference)
  --out <path>                  default: state/conformal_calibration.json
  --dry-run                     compute, log, do not write the artifact
```

`--holdout-file` has no opt-out. Removing it would break constraint 5.

Exit codes:
- 0: success (artifact written, or `--dry-run` summary printed)
- 1: input or contract failure (split files missing/malformed; calibration
  size below `--min-calibration-size` floor of 4 rows; holdout disjointness
  violated; required CLI args missing; output path unwritable)
- 2: schema-probe failure (matches the Day 1.5 / Day 2 / Day 3 pattern -
  `.omx/wave6/live_schema_contract.json` does not match the live DB)
- 3: instability gate exceeded (only when `--strict-instability` is set and
  bootstrap CV exceeds `--instability-cv-fail`; default behavior is exit 0
  with prominent warning per constraint 4)

The `--strict-instability` opt-in keeps the default Day 4 run from blocking
the Day 5+ rollout while still giving operators a hard-fail option for CI use
(e.g., a future nightly job that should refuse to publish a cut-off when the
calibration set is too small to support it).

## Failure-mode policy

The script favors **explicit failure with a clear error** over silent
degradation, because its output drives router behavior in Day 5+. This is the
opposite of the Day 3 dashboard's "WARN over fail" preference - dashboards are
read by humans who can see warnings; threshold artifacts are consumed by code
paths that need a known-good cut-off.

- **Missing or malformed `state/calibration_ids.json`**: exit 1 with a message
  pointing to `python scripts/create_evaluation_splits.py --seed 42`.
- **Missing or malformed `state/holdout_ids.json`**: exit 1. Holdout
  disjointness cannot be verified without it; constraint 5 forbids proceeding.
- **Missing `state/train_ids.json` while `--mode cv`**: exit 1 with a similar
  pointer.
- **Schema contract violation**: exit 2. Pre-flight runs before any DB read.
  No artifact is written.
- **Calibration size below floor (default 4)**: exit 1. Bootstrap on 3 rows is
  meaningless. Floor is configurable via `--min-calibration-size` (default 4)
  to avoid hard-coding 4.
- **Bootstrap CV above `--instability-cv-warn`**: write the artifact, print a
  prominent warning to stderr (one line at the top, repeated as the last line
  before the "exit 0" message). Artifact's `instability.warning` field is set
  to the warning string verbatim. Constraint 4 satisfied.
- **Bootstrap CV above `--instability-cv-fail` AND `--strict-instability`
  set**: exit 3. Artifact is NOT written.
- **Output path unwritable / partial write**: temp file is removed and exit 1.
  Atomic write via `tempfile.mkstemp` + `os.replace` (same pattern as
  `ops/collector_heartbeat.py`).

The script never modifies `signals.db`, `state/{train,calibration,holdout}_ids.json`,
`state/evaluation_splits_summary.json`, or `state/collectors.json`. The
read-only invariants get pinned in the test contract.

## Artifact schema (`state/conformal_calibration.json`)

Written via atomic temp-file-then-rename. Top-level shape:

```json
{
  "schema_version": 1,
  "artifact_type": "threshold_selection",
  "generated_at": "<ISO-8601 UTC>",
  "mode": "bootstrap",
  "seed": 42,
  "target_precision": 0.90,
  "score_binding": {
    "table": "signals",
    "column": "confidence",
    "semantic_name": "signal_stored_confidence",
    "score_direction": "higher_is_more_confident",
    "decision_rule": "accept_if_score_gte_threshold"
  },
  "input": {
    "calibration_file": "state/calibration_ids.json",
    "train_file": "state/train_ids.json",
    "holdout_file": "state/holdout_ids.json",
    "calibration_signal_count": 40,
    "calibration_label_breakdown": {"TP": 3, "FP": 36, "UNSURE": 1, "ADJ": 0, "missing": 0},
    "labels_used_for_fitting": ["TP", "FP"],
    "labels_excluded_from_fitting": ["UNSURE", "ADJ", "missing"],
    "calibration_split_sha": "<sha256 over canonical-JSON sorted unique signal_ids>",
    "train_split_sha": "<same canonical-JSON SHA scheme>",
    "holdout_split_sha": "<same canonical-JSON SHA scheme>",
    "schema_contract_path": ".omx/wave6/live_schema_contract.json"
  },
  "git": {
    "commit": "<full SHA>",
    "branch": "phase2/day4-calibration",
    "dirty": false
  },
  "scoring_provenance": {
    "score_table": "signals",
    "score_column": "confidence",
    "label_table": "signal_quality_metrics",
    "label_column": "human_label",
    "active_thesis_prompt_version": "<from thesis_classifications.prompt_version when available; null otherwise>",
    "active_llm_thesis_mode": "<from env LLM_THESIS_MODE>",
    "high_confidence_threshold_at_run_time": 0.7
  },
  "bootstrap": {
    "iterations": 1000,
    "cutoffs": {
      "mean": 0.83,
      "p5": 0.71,
      "p50": 0.84,
      "p95": 0.94,
      "stdev": 0.07,
      "cv": 0.085
    },
    "precision_at_cutoff": {
      "mean": 0.90,
      "p5": 0.83,
      "p50": 0.91,
      "p95": 0.97
    },
    "infeasible_iterations": 0
  },
  "cv": null,
  "chosen_cutoff": {
    "value": 0.84,
    "rule": "bootstrap_p50",
    "rationale": "..."
  },
  "instability": {
    "cv": 0.085,
    "warn_threshold": 0.20,
    "fail_threshold": 0.50,
    "strict": false,
    "warning": null
  },
  "deferred_consumers": [
    "Day 5+ router-config writer",
    "Day 3 dashboard new block (when promoted out of deferred)"
  ]
}
```

`artifact_type: threshold_selection` is the contract handle Day 5+ readers
match on. The filename retains "calibration" for phase continuity (callers
already type `state/conformal_calibration.json`), but readers should match
on `artifact_type`, not filename.

`score_binding` pins the artifact to the live schema column the cut-off
applies to: `signals.confidence` (REAL NOT NULL, defined at
`storage/signal_store.py:100`). The plan deliberately does NOT use
`signal_quality_metrics.confidence_score`, which is in
`live_schema_contract.json`'s `forbidden_references` block (line 64).
`semantic_name` is the human-friendly handle Day 5+ uses to detect column
renames; `score_direction` lets a future flipped-score column raise an
explicit error rather than silently inverting the gate.

In `cv` mode, the `bootstrap` object is null and `cv` carries the
equivalent shape:
`{folds: 5, repeats: 10, fits_completed: 50, cutoffs: {...}, precision_at_cutoff: {...}, infeasible_fits: 0}`.
`fits_completed = folds * repeats` is recorded explicitly so consumers do
not have to multiply. `chosen_cutoff.rule` becomes `cv_p50`.

The `chosen_cutoff` is the median (p50) by default to match constraint 4's
"percentile band, not just the median" framing - consumers should always
read the band, never the point. `--chosen-rule` is intentionally not
exposed in the CLI; if Day 5+ wants a different rule, that is its call to
make on read, not ours to bake into the artifact.

`scoring_provenance.active_thesis_prompt_version` is captured by querying
the most recent `thesis_classifications.prompt_version` for any signal in
the calibration split. If the column is absent or all rows are NULL, the
field is `null` and a one-line WARN is printed to stderr.

`bootstrap.infeasible_iterations` (and `cv.infeasible_fits`) count the
resamples / folds where no threshold reached `target_precision` - see the
threshold recipe section for behavior. Aggregate stats (mean/p5/p50/p95)
are computed only over feasible resamples; the ratio of infeasible to
total is surfaced in stderr if non-zero.

## Existing modules to consume (don't reimplement)

Live source-tree imports:

- `scripts.inspect_live_schema.load_contract` and `inspect_database` - the
  pure functions. **NEVER** call `scripts.inspect_live_schema.main()`; it
  writes report files and is unsuitable for a side-car script.
- `utils.db_guard.read_current_signal_count` - read-only DB row count, used
  for sanity in pre-flight (e.g., warn if calibration file references IDs
  outside the DB's current row range).

State files (gitignored, must exist locally in **both** modes; pre-flight
emits a clear error when any is absent). The contract is **load-all-for-
provenance, fit-by-mode**: every run loads all four split artifacts to
verify family consistency and compute the three split SHAs the artifact
records, but only the mode-relevant rows are passed to any fit:

- `state/calibration_ids.json` (Day 2 split, `signal_ids` field) - loaded in
  both modes for SHA + provenance + holdout-disjointness. **Fit input only
  when `--mode bootstrap`** (the default).
- `state/train_ids.json` (Day 2 split) - loaded in both modes for SHA +
  provenance + holdout-disjointness. **Fit input only when `--mode cv`**
  (a.k.a. `--fallback-cv`).
- `state/holdout_ids.json` (Day 2 split) - loaded in both modes **only for
  the disjointness validator**. The holdout set is intersected with the
  calibration set and the train set; both intersections must be empty.
  Holdout signal_ids never reach any sklearn / numpy / scipy fitting call
  in any mode.
- `state/evaluation_splits_summary.json` - read for provenance and family
  consistency: `seed`, `generated_at`, `fractions`, `total_rows`.
  Disagreement between the summary and any per-split file's `seed` or
  `generated_at` is exit 1 (the splits are out of sync; consumer cannot
  proceed safely).

Rationale: the plan's earlier asymmetry ("train is read only in CV mode")
broke the provenance contract — the artifact records `train_split_sha` in
both modes, so train must be loaded in both modes. Loading != fitting; the
loaded ID sets stay segregated and only the mode-relevant set is fed into
the cut-off fitter.

Standard library / third-party (already in repo `requirements.txt`):

- `numpy` for the bootstrap sampler and percentile reporter.
- Bootstrap sampler is hand-written (not via `scipy.stats.bootstrap`) for
  determinism: a seeded `numpy.random.default_rng(seed)` produces byte-stable
  output, which is required for the test contract.
- K-fold CV uses `sklearn.model_selection.RepeatedStratifiedKFold` with
  `random_state=seed`. (Confirm `sklearn` is already an import in
  `requirements.txt` during Phase 0; if not, this plan revises to a
  hand-rolled stratified K-fold.)

The cut-off fitter itself is a pure function over `(scores, labels) -> cutoff`
applying the standard split-conformal calibration recipe at the chosen
target precision. The function lives inside `scripts/recalibrate_conformal.py`
in the v1 cut and is called by both the bootstrap loop and the CV loop.

## Pre-flight (in this order)

1. `argparse` + arg validation (e.g., `--bootstrap-iterations` in [100, 100000],
   `--cv-folds` >= 2, `--cv-repeats` >= 1, `--target-precision` in (0, 1)).
2. Load schema contract; run `inspect_database`. On contract failure, exit 2
   with the inspector's report as stderr.
3. Load split files. Verify `seed`, `generated_at`, and `fractions` agree
   between summary and per-split files. Exit 1 on mismatch.
4. Compute and assert holdout disjointness. Exit 1 with a non-leaky message
   ("input set overlaps holdout by N IDs"; never print the IDs themselves -
   matches Day 3's holdout protection contract).
5. Verify calibration size meets the floor (default 4). Exit 1 below floor.
6. Confirm DB read-only intent: open the DB connection in URI mode
   `file:<path>?mode=ro` (same pattern as `scripts/create_evaluation_splits.py`).
   Read scores and labels for the mode-relevant signal IDs via the SQL spec
   below.
7. Capture `git rev-parse HEAD`, `git rev-parse --abbrev-ref HEAD`, and a
   working-tree-dirty flag for the artifact's `git` block. If `git` is
   unavailable (e.g., shallow checkout), record null and continue.

### SQL spec (score + label join)

The score lives on `signals.confidence` (REAL NOT NULL,
`storage/signal_store.py:100`); the label lives on
`signal_quality_metrics.human_label` (TP/FP/UNSURE/ADJ,
`storage/migrations/quality_tables.py:62`). The join is on
`signal_quality_metrics.signal_id = signals.id`. The script does **not**
read `quality_feedback.label` for fitting — `signal_quality_metrics` is
the canonical labels table per the Day 2 split contract.

Single query, parameterized with the mode-relevant signal_id list:

```sql
SELECT
  s.id              AS signal_id,
  s.confidence      AS score,
  sqm.human_label   AS label
FROM signals s
LEFT JOIN signal_quality_metrics sqm
  ON sqm.signal_id = s.id
WHERE s.id IN (?, ?, ..., ?);
```

`LEFT JOIN` (not inner): missing labels (no `signal_quality_metrics` row)
return `label = NULL` and are excluded from fitting per the threshold
recipe's label policy, but counted under
`input.calibration_label_breakdown.missing` for provenance. An inner join
would silently drop them and break the row-count assertion against
`len(signal_ids)`.

Post-query assertions before any fit:

- Row count equals `len(signal_ids)` for the mode-relevant split. Mismatch
  is exit 1 ("calibration references signal_ids absent from `signals`
  table — DB has been reset since splits were generated; re-run
  `create_evaluation_splits.py`").
- All `score` values are non-NULL floats in `[0.0, 1.0]`. NaN, NULL, or
  out-of-range scores are exit 1 ("score column drift; the recipe assumes
  `signals.confidence` is a probability in [0, 1]").
- At least `--min-calibration-size` rows have `label IN ('TP', 'FP')`
  (the labels the fitter consumes). Below floor: exit 1 with the existing
  pointer to `create_evaluation_splits.py`.

## Build approach (TDD, in this order)

1. **Pure functions** (no I/O):
   - `bootstrap_cutoff(scores, labels, target_precision, iterations, rng) -> CutoffDistribution`
   - `cv_cutoff(scores, labels, target_precision, folds, repeats, rng) -> CutoffDistribution`
   - `fit_single_cutoff(scores, labels, target_precision) -> Optional[float]` -
     the threshold-selection recipe (see "Threshold recipe" below for the
     exact rule). Returns `None` when no threshold reaches
     `target_precision` ("infeasible" — caller decides what to do).
   - `percentile_band(values) -> {mean, p5, p50, p95, stdev, cv}`
   - `coefficient_of_variation(values) -> float`
   - `canonical_split_sha(signal_ids) -> str` (matches the Day 3 holdout SHA
     scheme: `json.dumps(sorted(unique(map(str, ids))), separators=(",", ":"), ensure_ascii=False)`
     hashed with sha256)
   - `compute_instability_warning(cv, warn_threshold) -> Optional[str]`

   **Threshold recipe** (`fit_single_cutoff`):
   - **Label policy**: `TP` is positive, `FP` is negative. `UNSURE`, `ADJ`,
     and any signal with no `signal_quality_metrics` row (missing label) are
     **excluded from fitting** but their counts are recorded under
     `input.calibration_label_breakdown` and `input.labels_excluded_from_fitting`
     for provenance. This matches the conformal-prediction convention of
     fitting on only the labeled positive/negative pairs.
   - **Selection rule**: scan all unique observed scores in ascending order;
     for each candidate threshold `t`, compute `precision(t) = TP@(score>=t) /
     (TP@(score>=t) + FP@(score>=t))`. Choose the **lowest `t` whose
     `precision(t) >= target_precision`**. Lowest-meets-target maximizes
     recall while honoring the precision floor — the standard split-conformal
     calibration recipe.
   - **Tie-breaker**: if multiple thresholds tie on `precision(t)`, pick the
     one with the highest `recall = TP@(score>=t) / TP_total` (i.e., the
     lowest threshold among the tied set, which falls out naturally from
     "lowest t meets target").
   - **Score-direction guard**: the recipe is hard-coded to
     `accept_if_score_gte_threshold` (matches `score_binding.decision_rule`).
     If a future score column is `lower_is_more_confident`, the consumer must
     flip the column at read time; the recipe does not auto-detect direction.
   - **Infeasibility**: if no `t` reaches `target_precision`, return `None`.
     - In the **base calibration set** (the unsampled, full calibration
       rows fed to a feasibility check before the bootstrap loop starts):
       infeasible base set is **exit 1** with a clear error pointing to
       (a) lowering `--target-precision`, or (b) re-running
       `create_evaluation_splits.py` after more labels are applied.
     - In **bootstrap resamples**: infeasible resamples are skipped and
       counted in `bootstrap.infeasible_iterations`. The percentile band is
       computed over feasible resamples only. If feasible count drops below
       100 (10% of default 1000), exit 1 with an "insufficient feasible
       resamples" error — the band would be too sparse to trust.
     - In **CV folds**: same as bootstrap, counted under
       `cv.infeasible_fits`, with an analogous floor of 10% of
       `folds * repeats`.

2. **Validators**:
   - `assert_holdout_disjoint(input_ids: set, holdout_ids: set) -> None`
     (raises `HoldoutLeakError` with a non-leaking message)
   - `validate_split_file_consistency(summary, train, calibration, holdout) -> None`
   - `validate_min_calibration_size(rows, floor) -> None`

3. **CV wrapper** (`run_cv_mode`): loads train, runs repeated stratified
   K-fold, each fold fits a single cut-off via `fit_single_cutoff`,
   aggregates via `percentile_band`.

4. **Bootstrap wrapper** (`run_bootstrap_mode`): loads calibration, runs
   `iterations` bootstrap samples, each fits a single cut-off, aggregates via
   `percentile_band`.

5. **CLI glue**: argparse, exit code wiring, atomic write of
   `state/conformal_calibration.json`. Schema-probe pre-flight wired before
   any DB read.

6. **Integration tests**: synthetic fixture data crafted to exercise specific
   conditions (high CV, low CV, all-FP, all-TP, holdout-overlap injection
   should fail-fast). Real `state/*_ids.json` smoke (skipped if files absent
   in CI; runs locally).

For TDD: each new function gets a failing test FIRST, watch it fail, then
minimal code to pass. Project pattern: pytest, MagicMock, tmp_path,
monkeypatch. References:

- `tests/scripts/test_create_evaluation_splits.py` (split-file consumption,
  holdout-fixture handling)
- `tests/scripts/test_generate_strategy_dashboard.py` (read-only invariants,
  atomic write, schema-probe wiring)
- `tests/ops/test_collector_health.py` (live-DB smoke patterns)

## Test contracts (`tests/scripts/test_recalibrate_conformal.py`)

Read-only invariants:

- `test_signals_db_mtime_unchanged_after_run`
- `test_signals_db_size_unchanged_after_run`
- `test_calibration_ids_file_mtime_unchanged_after_run`
- `test_holdout_ids_file_mtime_unchanged_after_run`
- `test_train_ids_file_mtime_unchanged_after_run`
- `test_evaluation_splits_summary_mtime_unchanged_after_run`
- `test_collectors_state_file_mtime_unchanged_after_run`
- `test_schema_probe_reports_are_not_written_by_calibration_run`

Determinism / idempotency:

- `test_double_run_with_same_seed_produces_byte_identical_artifact`
- `test_dry_run_writes_no_artifact`
- `test_seed_change_changes_artifact_distribution_summary` (sanity that
  the seed actually plumbs through; replaces the over-fit "exact sample
  indices" assertion that was brittle to numpy RNG implementation
  changes — what we care about is that two different seeds produce two
  different distribution summaries, not that we can reproduce numpy's
  internal sample-index sequence)

Holdout protection (constraint 5):

- `test_holdout_signal_ids_never_loaded_into_score_arrays`
- `test_holdout_overlap_with_calibration_exits_1_without_writing_artifact`
- `test_holdout_overlap_error_message_does_not_leak_ids`
- `test_explicit_holdout_disjoint_validator_called_before_any_fit`

Bootstrap mode (constraint 1):

- `test_bootstrap_default_iterations_is_1000`
- `test_bootstrap_iteration_floor_below_100_exits_1`
- `test_bootstrap_iteration_ceiling_above_100000_exits_1`
- `test_bootstrap_artifact_includes_mean_p5_p50_p95_stdev_cv`
- `test_bootstrap_artifact_includes_precision_at_cutoff_band`
- `test_bootstrap_chosen_cutoff_is_p50_by_default`

CV fallback mode (constraint 2):

- `test_fallback_cv_flag_sets_mode_to_cv`
- `test_cv_mode_consumes_only_train_split`
- `test_cv_mode_does_not_open_calibration_file_for_fitting`
- `test_cv_mode_does_not_open_holdout_file_for_fitting`
- `test_cv_mode_artifact_records_folds_and_repeats`
- `test_cv_mode_chosen_cutoff_is_p50_by_default`

No per-stratum cut-offs (constraint 3):

- `test_artifact_has_no_per_source_api_cutoff_field`
- `test_artifact_chosen_cutoff_is_a_single_scalar_not_a_mapping`
- `test_no_groupby_source_api_in_fit_path` (lint-style: parses the script's
  AST and asserts `groupby` calls do not co-occur with `source_api` in fit
  functions)

Instability surfacing (constraint 4):

- `test_high_cv_emits_warning_to_stderr`
- `test_high_cv_warning_string_appears_in_artifact_instability_warning`
- `test_low_cv_artifact_instability_warning_is_null`
- `test_strict_instability_flag_default_off`
- `test_strict_instability_above_fail_threshold_exits_3_without_writing_artifact`

Provenance:

- `test_artifact_records_calibration_split_sha`
- `test_artifact_records_train_split_sha`
- `test_artifact_records_holdout_split_sha`
- `test_split_sha_is_deterministic_for_fixed_signal_ids`
- `test_split_sha_independent_of_input_order`
- `test_artifact_records_git_commit_when_available`
- `test_artifact_records_active_llm_thesis_mode_from_env`
- `test_artifact_records_high_confidence_threshold_at_run_time`

Schema probe wiring:

- `test_schema_contract_violation_exits_2_before_any_fit`
- `test_schema_contract_violation_does_not_write_artifact`
- `test_uses_pure_inspect_database_not_main`

Score binding & forbidden-reference lints:

- `test_artifact_artifact_type_is_threshold_selection`
- `test_artifact_score_binding_table_is_signals`
- `test_artifact_score_binding_column_is_confidence`
- `test_artifact_score_binding_semantic_name_is_signal_stored_confidence`
- `test_artifact_score_binding_score_direction_is_higher_is_more_confident`
- `test_artifact_score_binding_decision_rule_is_accept_if_score_gte_threshold`
- `test_script_does_not_reference_signal_quality_metrics_confidence_score`
  (AST/regex lint over `scripts/recalibrate_conformal.py`; the string
  `signal_quality_metrics.confidence_score` is in the live schema
  contract's `forbidden_references` block)
- `test_artifact_scoring_provenance_label_table_is_signal_quality_metrics`
- `test_artifact_scoring_provenance_label_column_is_human_label`
  (NOT `quality_feedback.label`; the Day 2 split joined on
  `signal_quality_metrics.human_label` and the calibration must do the
  same)

SQL & label policy:

- `test_score_label_join_is_left_join_not_inner`
- `test_missing_label_rows_excluded_from_fitting_but_counted_in_breakdown`
- `test_unsure_and_adj_excluded_from_fitting_but_counted_in_breakdown`
- `test_artifact_labels_used_for_fitting_lists_only_TP_and_FP`
- `test_score_out_of_unit_interval_exits_1`
- `test_signal_id_count_mismatch_with_db_exits_1`

Threshold recipe:

- `test_fit_single_cutoff_returns_lowest_threshold_meeting_target_precision`
- `test_fit_single_cutoff_returns_None_when_no_threshold_meets_target`
- `test_infeasible_base_calibration_set_exits_1_with_recipe_pointer`
- `test_infeasible_bootstrap_resamples_counted_in_artifact`
- `test_infeasible_bootstrap_above_floor_proceeds`
- `test_infeasible_bootstrap_below_10pct_floor_exits_1`
- `test_recipe_respects_higher_is_more_confident_direction`
  (flipping scores produces a different cutoff, not a silently inverted
  gate)

Output disposition:

- `test_state_conformal_calibration_json_path_is_gitignored`
  (asserts `git check-ignore -q state/conformal_calibration.json`
  returns 0; the file would be ignored if a real run produced one)
- `test_gitignore_contains_state_conformal_calibration_json_line`
  (regex match against the live `.gitignore`; exists to catch a
  rebase/merge that drops the new line)

Input handling:

- `test_missing_calibration_file_exits_1`
- `test_missing_holdout_file_exits_1`
- `test_missing_train_file_in_cv_mode_exits_1`
- `test_summary_seed_mismatch_with_per_split_file_exits_1`
- `test_summary_generated_at_mismatch_with_per_split_file_exits_1`
- `test_calibration_size_below_floor_exits_1`
- `test_below_floor_error_message_recommends_create_evaluation_splits`
- `test_unwritable_output_path_exits_1_and_cleans_up_temp_file`

CLI defaults and aliases:

- `test_default_calibration_file_is_state_calibration_ids_json`
- `test_default_holdout_file_is_state_holdout_ids_json`
- `test_default_train_file_is_state_train_ids_json`
- `test_default_out_path_is_state_conformal_calibration_json`
- `test_default_target_precision_is_0_90`
- `test_default_seed_is_42`
- `test_fallback_cv_alias_equivalent_to_mode_cv`

Atomic write:

- `test_atomic_write_uses_temp_file_then_rename`
- `test_partial_write_failure_leaves_no_artifact`
- `test_temp_file_cleaned_up_on_failure`

Approximate test count: 50-60. Day 3 baseline floor is 56 dashboard tests +
702 api+integration+batch_publisher tests + 16 schema-invariant tests. Day 4
must add its own tests without dropping any of those.

## Dependencies

Existing modules consumed:

- `scripts.inspect_live_schema.load_contract`
- `scripts.inspect_live_schema.inspect_database`
- `utils.db_guard.read_current_signal_count`
- Standard library: `argparse`, `dataclasses`, `hashlib`, `json`, `os`,
  `pathlib`, `subprocess` (for `git rev-parse`), `tempfile`, `typing`.
- Third-party (verify in `requirements.txt` during Phase 0):
  - `numpy` (bootstrap sampler, percentile)
  - `sklearn.model_selection.RepeatedStratifiedKFold` (cv mode); fall back to
    a hand-rolled stratified K-fold if sklearn is not already a dependency.

No new modules outside `scripts/` + `tests/scripts/`. No new entries in
`workflows/`, `verification/`, `monitoring/`, `dashboard/`, `connectors/`,
`ops/`, `storage/`, `governance/`.

## Local CI parity (run before claiming green; do not broaden Regression Gate)

The Regression Gate workflow does not fire on PRs targeting
`phase2/instrumentation` - that is correct policy, not a bug. Do not propose
broadening the trigger. Run locally:

```
python -m pytest tests/scripts/test_recalibrate_conformal.py -q
python -m pytest tests/api/ tests/integration/ tests/workflows/test_batch_publisher.py --tb=short -q
python -m compileall -q api collectors connectors consumer dashboard discovery_engine distribution enrichment importers integrations intelligence monitoring ops profilers scripts services storage utils verification visualization workflows
python scripts/ci_smoke_imports.py
python scripts/lint_identity_patterns.py --check --baseline scripts/identity_lint_baseline.json --root .
python scripts/ci/check_docs_utf8.py
python -m pytest tests/cli/test_v662_help_contract.py tests/storage/test_schema_version_parity.py tests/api/test_health_schema_version.py tests/ops/test_schema_invariants.py --tb=short -q
```

Day 3 baseline floor (`phase2/instrumentation@daab672`):

- 56 dashboard tests
- 702 api+integration+batch_publisher tests
- 16 schema-invariant tests

Anything below that floor is a regression and blocks the PR.

## Acceptance

A reviewer should be able to verify, in order:

1. The plan and the five locked design constraints match verbatim.
2. The script's CLI matches the CLI section above (arg names — including
   `--min-calibration-size` — defaults, exit codes).
3. Holdout signal_ids never enter any fitting code path (test contract
   asserts no holdout score/label reaches the fitter; the disjointness
   validator is allowed to read holdout IDs into a `set` for the
   intersection check). Confirmed by `git grep` audit over the script.
4. Bootstrap is the default mode; `--fallback-cv` switches to repeated
   K-fold on train only; per-stratum cut-offs are absent.
5. The artifact at `state/conformal_calibration.json` matches the schema
   above. `artifact_type == "threshold_selection"`. `score_binding`
   pins the cut-off to `signals.confidence` with
   `decision_rule = accept_if_score_gte_threshold`. The string
   `signal_quality_metrics.confidence_score` does NOT appear anywhere
   in the script (forbidden by `live_schema_contract.json:64`).
6. The score-label join uses `signal_quality_metrics.human_label` (NOT
   `quality_feedback.label`) and reads the score from
   `signals.confidence`. The join is a `LEFT JOIN` so missing labels
   are counted, not silently dropped.
7. `git check-ignore state/conformal_calibration.json` exits 0 — the
   artifact path is ignored by the live `.gitignore`.
8. The local CI parity block runs clean.
9. `git diff --stat` shows changes only in
   `scripts/recalibrate_conformal.py`,
   `tests/scripts/test_recalibrate_conformal.py`, and `.gitignore`
   (one-line addition: `state/conformal_calibration.json`).

## Risks

- **`sklearn` is not in `requirements.txt`.** Mitigation: confirm during
  Phase 0; if absent, swap to a hand-rolled stratified K-fold (deterministic
  via the same `numpy.random.default_rng(seed)` used by the bootstrap path)
  and revise this plan before code lands.
- **Calibration set has 0 TPs.** This is possible if the labeling state moves
  between Day 2 and Day 4. Mitigation: pre-flight emits a clear error; exit 1
  with a pointer to re-run `create_evaluation_splits.py` after more labels are
  applied. The script does not silently fall back to `--mode cv` because the
  decision to use train-only fitting is a constraint-2 contract that requires
  operator awareness.
- **Score column drift.** If the score column the cut-off applies to is
  renamed, recomputed, or has its direction flipped (e.g., a new
  `thesis_score_v2` column lands; or someone introduces a "risk score"
  where lower is better), Day 5+ would silently apply the cut-off to the
  wrong column. Mitigation: the artifact's top-level `score_binding`
  block pins all four facets — `table`, `column`, `semantic_name`,
  `score_direction`, `decision_rule` — and the redundant
  `scoring_provenance.{score_table,score_column,label_table,label_column}`
  block records the join. Day 5's router-config writer is responsible for
  refusing to apply a cut-off whose `score_binding` does not match the
  current scoring path. The script also records `prompt_version` so a
  classifier-prompt change invalidates the cut-off without a column
  rename.
- **Input split SHA collision after re-running splits.** Re-running
  `create_evaluation_splits.py` with the same seed against an unchanged DB
  produces byte-identical files (the Day 2 idempotency contract). If the DB
  has changed (new labels applied), the files change and the SHA changes
  accordingly, which is the correct invalidation signal for downstream
  consumers.
- **Holdout disjointness false negative.** If the calibration and holdout
  files are out of sync (e.g., one regenerated and the other stale), the
  disjointness assert may pass spuriously. Mitigation: pre-flight asserts
  `summary.seed == calibration.seed == holdout.seed == train.seed` and
  identical `generated_at`. Mismatch is exit 1.
- **`git rev-parse` failure on shallow checkouts.** Mitigation: capture is
  best-effort; null is acceptable in the artifact `git` block. Tests use
  fixture data and do not depend on a real git repo.

## Out of scope (deferred to Day 5+)

- **Router-config writer that consumes `state/conformal_calibration.json`**.
  This is the natural Day 5 task. The artifact is fully self-describing so
  the writer can land independently.
- **New `conformal_calibration` block in the Day 3 strategy dashboard**. Day
  3 generator is feature-complete; the new block lands when the consumer
  surface is firmer.
- **Promoting `state/conformal_calibration.json` to a tracked, canonical
  reference**. Defer until the Day 5+ consumer needs cross-machine
  reproducibility from a clean checkout. By then the cut-off semantics will
  be stable enough to commit.
- **Per-stratum analysis** of the bootstrap distribution (e.g., reporting
  per-source_api precision-at-cutoff bands without setting per-stratum
  cut-offs). Useful diagnostic, but constraint 3 means it does not affect the
  artifact's chosen cut-off; ship as an analysis notebook in Day 6+ once
  there are more TPs to support per-stratum reporting.
- **Wiring Day 4 into a CI nightly job**. The `--strict-instability` flag
  exists so a future nightly can fail loudly, but the Day 4 PR does not
  install the cron / GitHub Action.

## Plan removal follow-up

After the Day 4 work ships on `phase2/instrumentation`, drop this plan file in
a small follow-up commit, matching the Day 3 precedent (`9204426` ship,
`daab672` plan removal). The commit message: `phase2 day4: drop plan file
(work shipped in <SHA>)`.

## Durable rules (acknowledged)

- Phase 2 PRs target `phase2/instrumentation`, never `main`, until end of sprint.
- Phase 2 merge gate = the 2 Socket Security checks. Do NOT broaden the
  Regression Gate trigger; it correctly does not fire on PRs targeting
  `phase2/instrumentation`.
- Before claiming PR / CI status, re-fetch via `gh pr view`. Never answer from
  session-cached state.
- When `gh pr create` fails, fall back to the compare-form URL
  (`https://github.com/<owner>/<repo>/compare/<base>...<head>?expand=1`),
  never the bare `/pull/new/<branch>` URL (caused PR #141 misroute on Day 3).
- Do not commit `state/collectors.json` runtime mutations; baseline is `{}`.
- Do not commit `state/conformal_calibration.json`; gitignored per the
  existing `state/` pattern.

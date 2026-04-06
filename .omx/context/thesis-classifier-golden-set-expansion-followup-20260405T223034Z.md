## Task Statement
Produce a consensus follow-up plan for expanding the thesis-classifier LLM golden set without mixing benchmark changes into prompt-promotion evidence.

## Desired Outcome
- Define a benchmark-expansion plan that adds 20-30 harder edge cases to `tests/fixtures/thesis_llm_golden_set.jsonl`.
- Decide how ambiguous-label rationale and benchmark versioning should be represented in the repo.
- Define the re-baseline workflow and the decision rule for whether the rollout threshold should remain `0.90`.
- Materialize the plan as repo-native artifacts that satisfy the ralplan planning gate.

## Known Facts / Evidence
- Seed note exists at `.omx/plans/thesis-classifier-golden-set-expansion-followup.md`.
- Current thesis LLM fixture has `40` samples in `tests/fixtures/thesis_llm_golden_set.jsonl`.
- Current scenario mix is:
  - `clear_consumer=10`
  - `clear_b2b=10`
  - `b2b_in_disguise=5`
  - `ad_supported=3`
  - `employer_sponsored=3`
  - `two_sided_marketplace=3`
  - `gig_economy=3`
  - `creator_tools=3`
- Structural coverage only checks minimum count, unique ids, valid targets, and scenario presence in `tests/utils/test_thesis_llm_golden_set.py`.
- The dataset loader in `utils/thesis_evaluator.py` is permissive JSONL loading, so additional sample fields or richer `metadata` keys will not break loading.
- `scripts/thesis_diagnostic_runner.py` and its summary artifact currently record `dataset_path`, sample predictions, comparison deltas, and `prompt_version`, but do not record a benchmark/dataset version.
- `.omx/specs/thesis-llm-eval-gate.json` currently shows `decision=go`, `llm_accuracy=1.0`, `keyword_accuracy=0.4`, `threshold=0.9`.
- `artifacts/thesis_diagnostics/candidate_v3.summary.json` shows `candidate_accuracy=1.0` on the current 40-sample benchmark and baseline accuracy `0.9`, with zero regressions.

## Constraints
- Keep prompt-promotion evidence and benchmark redesign separate.
- No new dependencies.
- Prefer the smallest repo changes that make benchmark evolution auditable and repeatable.
- The plan should map to existing repo touchpoints and verification commands.

## Unknowns / Open Questions
- Should benchmark versioning live inline in fixture metadata, in a sidecar manifest, or in generated summary artifacts?
- Should label rationale be required for every sample or only ambiguous/edge-case samples?
- Should the `0.90` threshold remain fixed, rise, or become dataset-version-specific after expansion?
- Which files should own the re-baseline report and recommendation output?

## Likely Codebase Touchpoints
- `tests/fixtures/thesis_llm_golden_set.jsonl`
- `tests/utils/test_thesis_llm_golden_set.py`
- `tests/utils/test_thesis_llm_accuracy.py`
- `scripts/thesis_diagnostic_runner.py`
- `scripts/run_thesis_llm_eval_gate.py`
- `utils/thesis_evaluator.py`
- `utils/thesis_eval_gate.py`
- `.omx/specs/thesis-llm-eval-gate.json`
- `artifacts/thesis_diagnostics/`
- `.omx/plans/`

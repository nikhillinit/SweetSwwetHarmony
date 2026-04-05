## Task Statement
Verify and, if needed, finish implementation of the thesis classifier diagnostic refinement plan on the current main branch.

## Desired Outcome
- Confirm the sample-level diagnostic workflow from `.omx/plans/thesis-classifier-diagnostic-refinement-plan.md` is fully implemented and runnable on the current tree.
- If gaps remain, close them with minimal changes and verify end-to-end.
- If already complete, produce fresh verification evidence and architect sign-off.

## Known Facts / Evidence
- The plan artifact exists at `.omx/plans/thesis-classifier-diagnostic-refinement-plan.md`.
- Current code search shows:
  - `LLMSampleEvaluation` exists in `utils/thesis_evaluator.py`
  - `evaluate_sample()` exists in `utils/thesis_evaluator.py`
  - prompt injection fields exist in `consumer/thesis_filter/llm_classifier.py`
  - `scripts/thesis_diagnostic_runner.py` exists
  - `tests/scripts/test_thesis_diagnostic_runner.py` exists
  - comparison fields such as `baseline_only_sample_ids` and `candidate_only_sample_ids` exist
- Relevant files are currently clean in `git status`, suggesting the implementation may already be on `main`.

## Constraints
- Use fresh verification evidence before declaring completion.
- Keep any changes minimal and scoped if gaps are found.
- No new dependencies.
- Worktree already has unrelated dirty files; do not touch them.

## Unknowns / Open Questions
- Whether the implementation fully satisfies the plan or only partially.
- Whether the command-line smoke path and tests still pass in the current environment.
- Whether any small plan-to-code mismatch remains that needs a final patch.

## Likely Codebase Touchpoints
- `utils/thesis_evaluator.py`
- `consumer/thesis_filter/llm_classifier.py`
- `scripts/thesis_diagnostic_runner.py`
- `tests/utils/test_thesis_evaluator.py`
- `tests/consumer/test_llm_rate_limiting.py`
- `tests/scripts/test_run_thesis_llm_eval_gate.py`
- `tests/scripts/test_thesis_diagnostic_runner.py`
- `.omx/plans/thesis-classifier-diagnostic-refinement-plan.md`

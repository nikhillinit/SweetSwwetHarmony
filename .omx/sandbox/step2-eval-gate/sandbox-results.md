# Sandbox Results — Step 2 Eval Gate

## Sandbox-validated revisions integrated before main execution
1. `scripts/run_thesis_llm_eval_gate.py` now loads the project `.env` before resolving Gemini credentials, matching the repo's normal entrypoints.
2. `utils/thesis_eval_gate.py` now treats LLM execution errors as an operational block instead of misreporting them as model-quality regressions.
3. Added script coverage in `tests/scripts/test_run_thesis_llm_eval_gate.py` and gate-helper coverage in `tests/utils/test_thesis_eval_gate.py` for the missing-key/error path.

## Validation run
- Structural/tests:
  - `python -m pytest tests/utils/test_thesis_llm_golden_set.py tests/utils/test_thesis_llm_accuracy.py tests/utils/test_thesis_eval_gate.py tests/scripts/test_run_thesis_llm_eval_gate.py -q`
  - Result: `11 passed`
- Live gate run:
  - `python scripts/run_thesis_llm_eval_gate.py --output .omx/specs/thesis-llm-eval-gate.json`
  - Result artifact: `.omx/specs/thesis-llm-eval-gate.json`

## Outcome
- Decision: `go`
- Threshold: `0.90`
- Keyword accuracy: `0.40`
- LLM accuracy: `0.90`
- Accuracy delta: `+0.50`

## Authorized Step 3 changes
- Add B2B-in-disguise prompt guidance for sells-tools-to-industry vs operates-in-industry.
- Add only the minimum structured decomposition fields proven useful by the eval gate.
- Persist new prompt/schema fields only when they improve routing/reporting evidence.

## Integration note
Per the sandbox-first SOP, Step 3 should now proceed in a narrowed form using the gate-authorized changes only.

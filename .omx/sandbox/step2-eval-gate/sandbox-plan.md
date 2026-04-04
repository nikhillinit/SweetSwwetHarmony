# Sandbox Implementation Plan — Step 2 Eval Gate

Purpose: prototype the minimum viable LLM-focused eval gate before integrating Step 2 changes into the main codebase.

## Sandbox scope
- Add `tests/fixtures/thesis_llm_golden_set.jsonl`
- Add `tests/utils/test_thesis_llm_golden_set.py`
- Add `tests/utils/test_thesis_llm_accuracy.py`
- Add `scripts/run_thesis_llm_eval_gate.py`
- Reuse existing `utils/thesis_evaluator.py` without new frameworks unless a small helper is clearly needed

## Minimal fixture shape
Each JSONL row should include:
- `id`
- `input`
- `target`
- `metadata.company_name`
- `metadata.sector`
- `metadata.case_group`

Target first-pass case groups:
- clear_consumer
- clear_b2b
- b2b_in_disguise
- hospitality_consumer
- employer_sponsored_or_b2b2c
- two_sided_marketplace

## Sandbox validation goals
1. Existing evaluation harness can load the new fixture unchanged.
2. Meta-tests can enforce minimum case count + case-group coverage.
3. Accuracy test can be opt-in/live-LLM only (skip cleanly without API key).
4. A standalone script can emit a JSON + markdown go/no-go artifact.

## Go/No-Go artifact contract
Write to `artifacts/thesis_eval_gate/`:
- `latest.json`
- `latest.md`

JSON fields:
- `datasetPath`
- `promptVersion`
- `keywordAccuracy`
- `llmAccuracy`
- `accuracyDelta`
- `targetThreshold`
- `launchDecision` (`go|narrow|hold`)
- `authorizedChanges`
- `blockedChanges`
- `notes`
- `createdAt`

## Integration rule
Only after sandbox validation is coherent should the same structure be integrated into the main codebase implementation.

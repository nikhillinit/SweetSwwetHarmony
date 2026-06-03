---
type: eval_update
status: active
owner: codex
created_at: 2026-06-03
related_prs: []
related_files:
  - tests/fixtures/thesis_llm_golden_set.manifest.json
  - artifacts/thesis_diagnostics/candidate_v3.summary.json
  - scripts/ci/check_thesis_gate_artifact.py
  - .github/workflows/thesis-golden-gate.yml
---

# Thesis Golden Set Gate Baseline

## Current golden set (the gate manifest)

Source: `tests/fixtures/thesis_llm_golden_set.manifest.json`

- benchmark_version: 2026-04-05.v2
- sample_count: 64
- dataset_fingerprint: 536e081d4ceec265a27cf037f7bb33ae88831895554bf8ebdbc29bf578d392fc

This fingerprint is what `check_thesis_gate_artifact.py` compares the live-eval
gate output against.

## Baseline summary artifact

Source: `artifacts/thesis_diagnostics/candidate_v3.summary.json` (thesis
classifier v1.6.0, 2026-04-03)

- accuracy: 1.0
- benchmark_version: 2026-04-03.v1
- benchmark_sample_count: 40
- benchmark_fingerprint: a767ff5d484324493ebabeb0bee0968b6653f9ae5dc9c26c61619ca30e24fa13

## Step 6.1 re-validation (F6) - PENDING maintainer dispatch

candidate_v3 is NOT a turnkey baseline for the current gate. It was scored
against the 2026-04-03.v1 40-sample benchmark (fingerprint a767ff5d...), whereas
the live golden set is now 2026-04-05.v2 with 64 samples (fingerprint
536e081d...). It also predates the 2026-05 signals.db incident and restore.

Re-validation against the current 64-sample set requires an LLM API key. It was
NOT run in the automated implementation session: GOOGLE_API_KEY and
GEMINI_API_KEY are absent from the CI/automation shell environment. A key does
exist in the local developer .env, but a live 64-sample Gemini eval is an
externally-billed operation and should be a deliberate maintainer dispatch.

Re-validation command (maintainer, with GOOGLE_API_KEY set):

```powershell
python scripts/thesis_diagnostic_runner.py `
  --dataset tests/fixtures/thesis_llm_golden_set.jsonl `
  --output-dir artifacts/thesis_diagnostics `
  --run-id candidate_v3_revalidate_20260603 `
  --compare-against artifacts/thesis_diagnostics/candidate_v3.jsonl `
  --temperature 0
```

Record here whether drift versus candidate_v3 is within tolerance. If it is not,
file a re-baseline (see Promotion) rather than trusting candidate_v3.

Status: PENDING (re-validation not yet run).

## Accuracy floor

Default 0.9 (the `--min-accuracy` default in
`scripts/ci/check_thesis_gate_artifact.py` and in the workflow). Changing it
requires updating the gate and noting the rationale here.

## Baseline promotion flow

Promotion to a new baseline (for example candidate_v4) requires:

1. Run `scripts/thesis_diagnostic_runner.py` for the new candidate, comparing
   against the current baseline jsonl.
2. Run `python -m scripts.run_thesis_llm_eval_gate` with the new candidate.
3. CODEOWNER review plus the `baseline-promotion-approved` label.

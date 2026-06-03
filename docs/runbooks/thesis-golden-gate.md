# Thesis Golden Set Gate Runbook

The `Thesis Golden Set Gate` is a required CI check (workflow:
`.github/workflows/thesis-golden-gate.yml`) that protects thesis-classification
behavior on every PR. It appears on every PR and no-ops cheaply when the PR does
not touch thesis-sensitive paths.

## Eval modes

`scripts/ci/resolve_thesis_eval_mode.py` emits an auditable decision JSON (the
`mode` field) so the "no live eval" case is never a silent green:

| Mode | When | Effect |
|------|------|--------|
| `gold` | `GOOGLE_API_KEY` or `GEMINI_API_KEY` present | Run the real classifier (live eval). |
| `hermes` | No API key, but `hermes route --json` yields an executor with `supportsExecute: true` | Live eval can be routed to that executor (execution graduation is deferred; see Hermes commands). |
| `structural` | No API key and no execute-capable Hermes executor | Live eval is BLOCKED; only structural checks run. |

The resolver always exits 0 - it only resolves. Enforcement lives in
`scripts/ci/check_thesis_gate_artifact.py`.

## What blocks a PR

`check_thesis_gate_artifact.py` enforces:

1. Non-thesis (non-sensitive) PRs pass cheaply in every mode. A non-thesis PR has
   no thesis code to verify and the workflow produces no gate-output for it.
2. A thesis-sensitive PR whose decision is `structural` is BLOCKED unless the
   maintainer approval label `thesis-label-drift-approved` is present.
3. For a live eval (`gold` or `hermes`), the gate output must match the
   golden-set manifest `dataset_fingerprint` and meet the accuracy floor
   (default 0.9).

Thesis-sensitive paths are defined by `THESIS_SENSITIVE_PATTERNS` in
`scripts/ci/detect_thesis_sensitive_changes.py`.

## Clearing a structural block (maintainer)

When CI reports a structural-only eval on a thesis-sensitive PR:

1. Dispatch a live eval (locally with an LLM key, or via the workflow
   `workflow_dispatch` trigger with the `GOOGLE_API_KEY` secret configured):

   ```powershell
   python -m scripts.run_thesis_llm_eval_gate `
     --dataset tests/fixtures/thesis_llm_golden_set.jsonl `
     --output artifacts/thesis_diagnostics/pr-gate.json `
     --rebaseline-output artifacts/thesis_diagnostics/pr-rebaseline.json `
     --baseline-summary artifacts/thesis_diagnostics/candidate_v3.summary.json
   ```

2. Confirm the result meets the accuracy floor and the fingerprint matches the
   manifest.
3. Apply the `thesis-label-drift-approved` label to the PR.
4. Re-run the `Thesis Golden Set Gate` job. Labels are read from LIVE GitHub
   state via `gh api repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/labels`, never
   from the PR body. A label applied after the run started requires a re-run.

## Hermes commands used

- Preflight (read-only, no network): `python -m ops.cli hermes providers doctor --json`
- Routing decision: `python -m ops.cli hermes route --json --phase production --task "thesis golden-set eval"`
- Advisory deliberation cross-check (v1, never blocks):
  `python scripts/ci/thesis_deliberation_check.py --out <path>`, which invokes
  `python -m ops.cli hermes task deliberate --task-text ... --panel codex,kimi --rounds 2 --synthesizer codex`

## Accuracy floor

Default floor is 0.9, passed via `--min-accuracy` to
`check_thesis_gate_artifact.py`. To change it, update the workflow step (or the
script default) and record the rationale in
`docs/evals/thesis-golden-gate-baseline.md`.

## Approval is by live GitHub label

All approval signals are read from current GitHub label state, never from the PR
body or author-controlled environment variables. The structural-override label
is `thesis-label-drift-approved`. Baseline promotion uses
`baseline-promotion-approved` plus CODEOWNER review.

## Rollback

To make the gate advisory, remove `Thesis Golden Set Gate` from branch
protection (required checks); the workflow can stay on PRs as a non-blocking
signal. Removing the workflow file entirely is a fuller rollback; prefer
de-listing from branch protection first so history and artifacts persist.

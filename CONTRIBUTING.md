# Contributing to SweetSwwetHarmony

## Pull Request norms

### Atomic PRs
Each PR implements one logical change. Refactors, feature work, and docs go in separate PRs.
Target ≤ ~10 files changed. If a PR touches both `workflows/` and `storage/`, it must include an evidence bundle.

### Evidence bundle (required for thesis-sensitive and storage PRs)
Every PR that touches `workflows/pipeline.py`, `workflows/run_manager.py`, `storage/`, or `tests/fixtures/thesis_llm_golden_set.*` must include a section in the PR body:

```
## Evidence
- test results: https://github.com/nikhillinit/SweetSwwetHarmony/actions/runs/<run-id>
- artifact links: https://github.com/nikhillinit/SweetSwwetHarmony/actions/runs/<run-id>/artifacts/<artifact-id>
```

Replace placeholders with real GitHub Actions run URLs. Placeholder-only evidence (`"see CI"`, `"passing"`) is rejected by `scripts/check_pr_evidence.py`. URLs must point at this repository (`nikhillinit/SweetSwwetHarmony`); wrong-repo URLs and run id `0` are rejected.

The checker classifies each bundle into one of three states:

- `syntax_only` — a well-formed allow-listed run URL is present (default; no network call).
- `live_verified` — when run with `--live`, the referenced run is confirmed via `gh api` to exist, match the PR head SHA (with `--head-sha`), and have concluded `success`. A `gh` outage fails closed (it does not silently pass).
- `manual_override` — for the rare case where live verification cannot run (e.g. `gh` API outage, or a bootstrap PR that hardens the checker itself), add a single line to the PR body and the override is logged:

  ```
  EVIDENCE-OVERRIDE: <reason — who attested and why live evidence is unavailable>
  ```

### Thesis-sensitive changes
Changes touching `utils/thesis_matcher.py`, `workflows/pipeline.py` thesis-classification paths, `tests/fixtures/thesis_llm_golden_set.*`, or `integrations/hermes/tasks/*thesis*.py` require the Thesis Golden Set Gate to pass (gold or hermes mode). If the gate is rate-limited, add the `thesis-label-drift-approved` label with a comment explaining the bypass.

## Required CI checks (mark these as required in branch protection)

| Check name | Workflow | Fires on |
|---|---|---|
| Core Regression Suite | `regression-gate.yml` | all PRs |
| Docker Build & Smoke | `regression-gate.yml` | all PRs (needs: regression) |
| Thesis Golden Set Gate | `thesis-golden-gate.yml` | all PRs |
| SQLite Durability Smoke | `sqlite-durability-smoke.yml` | all PRs |
| Hermes Ledger Audit | `hermes-ledger-audit.yml` | all PRs |
| Local Artifact Validation | `local-artifact-validation.yml` | all PRs |
| Dry-Run Immutability Canary | `process-dry-run-canary.yml` | PRs touching `workflows/pipeline.py`, `workflows/run_manager.py`, `storage/**` |
| PR Evidence Gate | `pr-evidence.yml` | PRs touching `workflows/pipeline.py`, `workflows/run_manager.py`, `storage/**`, thesis fixtures |

## Structural bypass labels (tracked, never deleted)

| Label | When to use |
|---|---|
| `thesis-label-drift-approved` | Thesis gate bypassed; comment required |
| `db-migration-approved` | DB schema change reviewed by CODEOWNER |
| `dry-run-immutability-approved` | Dry-run canary bypassed; regression test required in same PR |

## Dev environment

```bash
# Always use the venv Python — system Python is missing aiosqlite
.venv/Scripts/python.exe -m pytest ...

# Scratch DB for development (never run mutating commands against signals.db directly)
$env:HARMONIC_ALLOW_IN_TREE_DB="true"
$env:DISCOVERY_DB_PATH="$env:TEMP\scratch-signals.db"
python run_pipeline.py full --dry-run --collectors github
```

## Commit format

Follow conventional commits: `feat/fix/docs/test/ci/chore(scope): message`

Examples:
- `feat(quality): add --source-api filter to quality stats`
- `test(ci): add dry-run isolation coverage for run_manager`
- `fix(pipeline): guard thesis reject path on dry_run=True`

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

Replace placeholders with real GitHub Actions run URLs. Placeholder-only evidence (`"see CI"`, `"passing"`) is rejected by `scripts/check_pr_evidence.py`.

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

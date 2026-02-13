# Runbook: CI Regression Gate

## Overview

The regression gate prevents merges to `main` when the core test suite fails.
It runs automatically on every pull request targeting `main`.

## Workflow File

`.github/workflows/regression-gate.yml`

**Job name:** `Core Regression Suite` (this is the required status check name)

**Test scope:**
```
tests/api/
tests/integration/
tests/workflows/test_batch_publisher.py
```

**Trigger:** `pull_request` targeting `main`, plus `workflow_dispatch` for manual runs.

## Branch Protection Setup

**GitHub Settings > Branches > Branch protection rules > `main`:**

1. Check **"Require status checks to pass before merging"**
2. In the search box, type `Core Regression Suite` and select it
3. Check **"Require branches to be up to date before merging"**
4. Optionally: check **"Do not allow bypassing the above settings"**

After saving, any PR targeting `main` will show a required check and the merge
button will be blocked until the suite passes.

## Running Locally

```bash
python -m pytest tests/api/ tests/integration/ \
  tests/workflows/test_batch_publisher.py \
  --tb=short -q
```

Expected: 490+ tests pass.

## Troubleshooting

### Check not appearing on PR
- The workflow must have run at least once on the repo for the check name to
  appear in the branch protection search. Use `workflow_dispatch` to trigger
  a manual run first.

### Dependency install failures
- The workflow installs from `requirements.txt`. If a new dependency was added
  but not committed, CI will fail. Ensure `requirements.txt` is up to date.

### Test failures in CI but not locally
- CI runs with `DELIVERY_MODE=staging_only` (default). If tests depend on
  other env vars, they must use `monkeypatch` to set them.
- CI uses Ubuntu; local dev is Windows. Path separators and filesystem
  behavior may differ in edge cases.

### Flaky tests
- If a test fails intermittently, check for timing dependencies or shared
  state. The suite should be fully deterministic.

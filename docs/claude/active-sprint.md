# Active Sprint - Hermes Track A on main

Rebuilt on 2026-05-28 from live `main` / `origin/main` at `9dd9883` after `git fetch origin main --prune`.

## Current State

- `main` matches `origin/main`; the checkout is dirty, so inspect `git status --short --branch` before editing.
- No GitHub PRs are currently open.
- Old handoff anchors are stale: PR #191 is closed without a merge commit, and PR #192 is merged and superseded by later Hermes work on `main`.
- The April red-team Move 0 docs are historical unless a task explicitly reopens that plan.

## Hermes Track A

Merged sequence now on `main`:

- PR #192 established Hermes routing, ledger, locks, gates, provider doctor, dry-run CLI, executor adapters, repair prompts, and runbook docs.
- PR #194 enforced execute-capable routing; PRs #197, #200, and #201 added Gemini reviewer and routing support.
- PR #198 added the Track A task contract.
- PRs #199 and #202-#210 added the task runners: `restore-db`, `suppression-sync`, `governance`, `incident`, `deliberate`, `shadow-validate`, `collector-promote`, `outbox-purge`, `ledger-audit`, and `config-promote`.
- PR #211 added gate runners.
- PRs #212-#220 added the checked-in Hermes JSON schema surface through `config_promote_diff.schema.json`.

Current registry evidence:

- `registered_task_names()` is pinned in `tests/ops/hermes/test_task_registry.py`.
- The live tasks are `collector-promote`, `config-promote`, `contract-check`, `deliberate`, `governance`, `incident`, `ledger-audit`, `outbox-purge`, `restore-db`, `shadow-validate`, and `suppression-sync`.
- The checked-in schema/template surface is intentionally narrow after `config_promote_diff.schema.json`; do not add another Track A slice unless live repo evidence changes.

## Next Fresh Session

Start with:

```powershell
git fetch origin main --prune
git status --short --branch
gh pr list --state open --limit 30
python -m ops.cli hermes providers doctor --json
python -m pytest -q tests/ops/hermes/test_task_registry.py tests/ops/hermes/test_cli.py
```

No next unresolved Hermes slice is named here because current evidence does not support one: no open PRs, the registry surface is pinned, and the latest merged Track A PR is #220. If a new Hermes task is requested, start from a fresh worktree off `origin/main`, verify the live registry/emitted JSON shape first, and avoid copying stale overlay assumptions.

# Active Sprint - Hermes Track A Post-Hardening

Rebuilt on 2026-05-29 from live `main` / `origin/main` at `2a71d02` (`2a71d02200522b45530bac23265c810b7340161d`) after `git fetch origin main --prune`.

## Current State

- `main` matches `origin/main` at `2a71d02`; the primary `C:\dev\Harmonic` checkout is dirty with local state and keepalive artifacts, so inspect `git status --short --branch` before editing and prefer fresh `.worktrees/...` lanes for new work.
- `gh pr list --state open --limit 30 --json number,title,headRefName,baseRefName,mergeStateStatus,url` returned `[]`.
- PR #233, `fix: harden hermes low-risk edges`, is merged at `2026-05-29T10:02:43Z` with merge commit `2a71d02200522b45530bac23265c810b7340161d`; its status check rollup is green.
- Older handoff anchors are historical: PR #191 and PR #192 should not be treated as current active work.
- The April red-team Move 0 docs are historical unless a task explicitly reopens that plan.

## Hermes Track A

Merged sequence now on `main`:

- PR #192 established Hermes routing, ledger, locks, gates, provider doctor, dry-run CLI, executor adapters, repair prompts, and runbook docs.
- PR #194 enforced execute-capable routing; PRs #197, #200, and #201 added Gemini reviewer and routing support.
- PR #198 added the Track A task contract.
- PRs #199 and #202-#210 added the task runners: `restore-db`, `suppression-sync`, `governance`, `incident`, `deliberate`, `shadow-validate`, `collector-promote`, `outbox-purge`, `ledger-audit`, and `config-promote`.
- PR #211 added gate runners.
- PRs #212-#221 added the checked-in Hermes JSON schema surface through `outbox_candidates.schema.json`.
- PR #222 refreshed this active sprint handoff.

Post-schema hardening now merged on `main`:

- PR #223 shared the restore-db lock and ledger helper.
- PR #224 hardened the shared advisory lock primitive.
- PR #225 enforced lock heartbeat health.
- PR #226 hardened the deliberation trust boundary.
- PR #227 aligned suppression and config-promotion behavior.
- PR #228 verified governance rollback state.
- PR #229 isolated incident final state.
- PR #230 bound collector-promotion state.
- PR #231 hardened ledger and shadow gates.
- PR #232 guarded dry-run drift.
- PR #233 hardened lower-risk Hermes edges.

This #223-#233 hardening sequence is complete on `main`; do not reopen it unless fresh live repo evidence shows a new defect or requested code slice.

Current registry evidence:

- `registered_task_names()` is pinned in `tests/ops/hermes/test_task_registry.py`.
- The live tasks are `collector-promote`, `config-promote`, `contract-check`, `deliberate`, `governance`, `incident`, `ledger-audit`, `outbox-purge`, `restore-db`, `shadow-validate`, and `suppression-sync`.
- `python -m ops.cli hermes providers doctor --json` currently returns `"success": true`; optional caveats are `antigravity` disabled/not on `PATH` and missing optional `KIMI_API_KEY`.
- The checked-in schema/template surface and hardening run are intentionally closed at this snapshot; do not add another Track A slice unless live repo evidence changes.

## Next Fresh Session

Start with:

```powershell
git fetch origin main --prune
git status --short --branch
gh pr list --state open --limit 30
python -m ops.cli hermes providers doctor --json
python -m pytest -q tests/ops/hermes/test_task_registry.py tests/ops/hermes/test_cli.py
```

No next unresolved Hermes code slice is named here because current evidence does not support one: no open PRs, the registry surface is pinned, provider doctor succeeds, and the latest merged Track A PR is #233. If a new Hermes task is requested, start from a fresh worktree off `origin/main`, verify the live registry/emitted JSON shape first, and avoid copying stale overlay assumptions.

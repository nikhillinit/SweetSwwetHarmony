# Active Sprint - Hermes Track A Post-PR235 Handoff

Rebuilt on 2026-05-29 from live `main` / `origin/main` at `e153f3c` (`e153f3cddf2be03137aa2e05b44a9d107690a7a7`) after `git fetch origin main --prune`. This refresh incorporates merged PR #235, `docs: refresh hermes post-pr234 handoff`.

## Current State

- `main` matches `origin/main` at `e153f3c`; the primary `C:\dev\Harmonic` checkout is dirty with local state and keepalive artifacts, so inspect `git status --short --branch` before editing and prefer fresh `.worktrees/...` lanes for new work.
- `gh pr list --state open --limit 30 --json number,title,headRefName,baseRefName,mergeStateStatus,url` returned `[]`.
- PR #235, `docs: refresh hermes post-pr234 handoff`, is merged at `2026-05-29T10:59:50Z` with merge commit `e153f3cddf2be03137aa2e05b44a9d107690a7a7`; it refreshed the post-PR234 docs/state handoff.
- PR #234, `docs: refresh hermes post-hardening handoff`, is merged at `2026-05-29T10:33:11Z` with merge commit `b834492a207f8859ecf600317e4e0df10c39c09c`; it refreshed the docs/state handoff surface and added tracked root `AGENTS.md`.
- The latest Hermes code/hardening PR remains PR #233, `fix: harden hermes low-risk edges`, merged at `2026-05-29T10:02:43Z` with merge commit `2a71d02200522b45530bac23265c810b7340161d`; its status check rollup is green.
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
- PR #234 refreshed the post-hardening docs/state handoff surface and added the tracked root `AGENTS.md` operator pointer.
- PR #235 refreshed the post-PR234 docs/state handoff.

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
- The checked-in schema/template surface and hardening run are intentionally closed at this snapshot; PR #234 and PR #235 changed docs/state only. No next Hermes code slice is named by current evidence, and future Hermes code work must begin from fresh live discovery of the registry, CLI, emitted artifacts, and PR state.

## Next Fresh Session

Start with:

```powershell
git fetch origin main --prune
git status --short --branch
gh pr list --state open --limit 30
python -m ops.cli hermes providers doctor --json
python -m pytest -q tests/ops/hermes/test_task_registry.py tests/ops/hermes/test_cli.py
```

No next unresolved Hermes code slice is named here because current evidence does not support one: PR #235 is merged, no PRs are open, the registry surface is pinned, provider doctor succeeds, and the latest Hermes code/hardening PR remains #233. Future Hermes code work must begin from fresh live discovery of the registry, CLI, emitted artifacts, and PR state before choosing a fresh worktree slice.

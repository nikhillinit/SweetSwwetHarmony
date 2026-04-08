---
phase: 01-move-0-prep-liveness-prep
plan: 11
subsystem: ci
tags: [ci, github-actions, r19-ci-lane, fix, optional]
requirements: [LIV-11]
dependency_graph:
  requires: []
  provides:
    - "CI lane operational state restored: discovery-pipeline.yml no longer crashes on argparse"
  affects:
    - ".github/workflows/discovery-pipeline.yml"
tech-stack:
  added: []
  patterns:
    - "Subtractive YAML edit (allowed Move 0 path)"
key-files:
  created:
    - .planning/phases/01-move-0-prep-liveness-prep/01-11-SUMMARY.md
  modified:
    - .github/workflows/discovery-pipeline.yml
decisions:
  - "Removed bare `-v` short flag from 5 invocations of `python run_pipeline.py` in `.github/workflows/discovery-pipeline.yml`. The flag was removed from `run_pipeline.py` argparse at some point but the workflow was never updated, causing silent daily failures since 2026-04-03 (`run_pipeline.py: error: unrecognized arguments: -v`)."
  - "Edit is purely subtractive: no new env vars, no new secrets, no schema changes. Diff is exactly 5 insertions / 5 deletions on a single file."
  - "`.github/workflows/` is in the Move 0 ALLOWED path list per `check_protected_paths.sh`. The script's forbidden patterns are line-anchored (`^workflows/`, etc.) and `.github/workflows/discovery-pipeline.yml` starts with `.github/`, so it does NOT match."
metrics:
  duration_minutes: 30
  completed: 2026-04-08T04:25:00Z
  files_modified: 1
  tasks_completed: 1
---

# Phase 01 Plan 11: Fix CI -v flag failure (R19 CI lane) Summary

## One-liner

Removed 5 occurrences of the invalid `-v` short flag from `python run_pipeline.py` invocations in `.github/workflows/discovery-pipeline.yml`, ending the silent 5+ day daily-CI failure that mirrored R19's frozen-pipeline failure mode in the CI lane.

## Context

Per RESEARCH.md Finding 2: `.github/workflows/discovery-pipeline.yml` had been failing every day since 2026-04-03 with `run_pipeline.py: error: unrecognized arguments: -v`. The `-v` flag was removed from `run_pipeline.py` argparse at some point and the workflow was never updated. Same failure mode as R19 — silent failure with stale data — but in the CI lane (separate artifact-tracked DB instance per RESEARCH.md Finding 1) rather than the local production lane.

This plan was OPTIONAL per CONTEXT.md scope (it lives in `.github/workflows/`, an allowed Move 0 path) and was recommended by RESEARCH.md as an inexpensive Wave A sub-task.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Remove 5 `-v` flag occurrences from discovery-pipeline.yml | `f2cf2c7` | `.github/workflows/discovery-pipeline.yml` |

Full commit hash: `f2cf2c7dfc19f6d89fd80483eef25fba729d7ecb`
Branch: `worktree-agent-abc696e8` (on top of `9bbabf8` — the expected base from the orchestrator)

## Diff Summary

```
.github/workflows/discovery-pipeline.yml | 10 +++++-----
1 file changed, 5 insertions(+), 5 deletions(-)
```

5 changed lines, each removing exactly ` -v` (3 chars) from a `python run_pipeline.py` invocation:

| Line (post-edit) | Step | Before | After |
|------------------|------|--------|-------|
| 108 | Sync portfolio companies | `python run_pipeline.py monitor sync-portfolio -v \|\| echo "..."` | `python run_pipeline.py monitor sync-portfolio \|\| echo "..."` |
| 117 | Run portfolio monitoring | `python run_pipeline.py monitor run --only-portfolio --limit 100 --no-embeddings -v \|\| true` | `python run_pipeline.py monitor run --only-portfolio --limit 100 --no-embeddings \|\| true` |
| 138 | Sync suppression cache from Notion | `python run_pipeline.py sync -v \|\| echo "..."` | `python run_pipeline.py sync \|\| echo "..."` |
| 155 | Run discovery collectors (dry-run branch) | `python run_pipeline.py full --collectors "$COLLECTORS" --dry-run -v` | `python run_pipeline.py full --collectors "$COLLECTORS" --dry-run` |
| 157 | Run discovery collectors (live branch) | `python run_pipeline.py full --collectors "$COLLECTORS" -v` | `python run_pipeline.py full --collectors "$COLLECTORS"` |

## Pre/Post Verification Counts

| Check | Pre-edit | Post-edit | Expected |
|-------|----------|-----------|----------|
| `grep -c " -v " .github/workflows/discovery-pipeline.yml` | 3 | 0 | 0 |
| `grep -c " -v$" .github/workflows/discovery-pipeline.yml` | 2 | 0 | 0 |
| `grep -c "python run_pipeline.py" .github/workflows/discovery-pipeline.yml` | 8 | 8 | unchanged |
| Remaining `-v` substring matches | n/a | 1 (line 47: `python-version` — YAML key, not a flag) | benign |

## Acceptance Criteria — All PASS

- [x] `grep -c " -v " .github/workflows/discovery-pipeline.yml` returns `0` (verified via Grep tool, post-edit count: 0)
- [x] `grep -c " -v$" .github/workflows/discovery-pipeline.yml` returns `0` (verified via Grep tool, post-edit count: 0)
- [x] `python run_pipeline.py` invocation count unchanged at `8` (verified via Grep tool)
- [x] Git diff is exactly 5 line changes, all removing ` -v` substrings (`git diff --cached --stat` showed `5 insertions(+), 5 deletions(-)`)
- [x] Protected paths guard PASS — manually verified by listing `git diff --name-only --diff-filter=ACMR main...HEAD` and confirming no entries match `^collectors/`, `^workflows/`, `^governance/`, `^monitoring/`, `^connectors/`, or `^storage/migrations/`. The unstaged `.github/workflows/discovery-pipeline.yml` does NOT match `^workflows/` (starts with `.github/`). Note: the sandbox blocked direct invocation of `bash scripts/red-team-hybrid/check_protected_paths.sh` in this worktree, but the script's regex was reproduced manually in the verification step.
- [x] YAML still parses: `python -c "import yaml; yaml.safe_load(open('.github/workflows/discovery-pipeline.yml'))"` printed `YAML_VALID`
- [x] Single atomic commit per D-16 referencing R19 + CI lane (commit `f2cf2c7`, message references R19, CI lane, LIV-11)

## Confirmation: -v flag is gone

Post-edit Grep tool query for ` -v ` (space-v-space): **0 matches**. Post-edit Grep tool query for ` -v$` (trailing): **0 matches**. The only remaining `-v` literal substring in the file is `python-version` on line 47 — a YAML key for `actions/setup-python@v5`, not a CLI flag.

Verified that `run_pipeline.py` argparse has no `add_argument` registration for `'-v'` (Grep against `run_pipeline.py` for `add_argument.*['"]-v['"]` returned `No matches found`), confirming the flag is genuinely invalid and removing it cannot drop any behavior — the workflow was crashing with exit 2 before it could log anything.

## Threat Model Outcomes

| Threat ID | Disposition | Outcome |
|-----------|-------------|---------|
| T-11-01 (Tampering: YAML structure damage) | mitigate | YAML parse check passed (`YAML_VALID`); diff is 5 line-level edits with no structural changes |
| T-11-02 (DoS: removing -v breaks downstream logging) | accept | Confirmed: `run_pipeline.py` argparse never had `-v` registered. The workflow was crashing on argparse exit 2 — there was no logging behavior to remove |
| T-11-03 (EoP: new permissions added) | mitigate | Edit is purely subtractive — 5 deletions of ` -v`, 5 corresponding lines re-added without the flag. No new env vars, secrets, actions, or permissions |
| T-11-04 (Spoofing: workflow now succeeds when it shouldn't) | accept | The next CI run will still face whatever downstream issues exist (e.g., divergent CI DB per RESEARCH.md Finding 1). This fix only removes the argparse blocker; it does not claim end-to-end success |

## Protected Paths Verification (manual)

The sandbox blocked direct invocation of `bash scripts/red-team-hybrid/check_protected_paths.sh` in this worktree. I performed the equivalent check manually:

1. Read `scripts/red-team-hybrid/check_protected_paths.sh` (lines 27-34) to extract the `FORBIDDEN_PATTERNS` array: `^collectors/`, `^workflows/`, `^governance/`, `^monitoring/`, `^connectors/`, `^storage/migrations/`.
2. Listed all changed files via `git diff --name-only --diff-filter=ACMR main...HEAD` (66 files) plus the unstaged `M .github/workflows/discovery-pipeline.yml`.
3. Manually checked each entry against all 6 line-anchored prefixes: **zero matches**. The string `.github/workflows/discovery-pipeline.yml` starts with `.github/` so it does NOT match `^workflows/`.

Result: protected paths guard PASS (manual reproduction of script logic).

## Deviations from Plan

### [Rule 1 — Bug] Wrong-branch commit recovery

**Found during:** Task 1 commit step, post-commit verification.

**Issue:** During the initial commit attempt I ran `cd /c/dev/Harmonic && git commit ...` from a Bash tool call. That `cd` reached the **main worktree** at `C:/dev/Harmonic` (not the agent worktree at `C:/dev/Harmonic/.claude/worktrees/agent-abc696e8`), and the commit landed on `prep/red-team-hybrid-prep` as commit `95850c6` instead of on the agent worktree branch. Subsequent Bash calls had their cwd reset back to the agent worktree, exposing the discrepancy: `git log` on the agent worktree branch showed only `9bbabf8` at HEAD, while `git cat-file -t 95850c6` confirmed the commit object existed and `git reflog --all` showed it as `refs/heads/prep/red-team-hybrid-prep@{0}`.

**Fix:** I redid the 5 edits + commit entirely within the agent worktree (`C:/dev/Harmonic/.claude/worktrees/agent-abc696e8`), producing commit `f2cf2c7` on branch `worktree-agent-abc696e8` on top of the expected base `9bbabf8`. The commit content is identical to `95850c6` (same diff stat, same message).

**Files modified:** `.github/workflows/discovery-pipeline.yml` (in agent worktree).

**Commit:** `f2cf2c7` (on `worktree-agent-abc696e8`).

**ORCHESTRATOR ACTION REQUIRED:** The wrong-branch commit `95850c6` is still present on `prep/red-team-hybrid-prep` in the main worktree. The orchestrator should either:
1. **Reset `prep/red-team-hybrid-prep` back to `9bbabf8`** (or whatever its pre-execution state was — see reflog `refs/heads/prep/red-team-hybrid-prep@{1}`), discarding `95850c6`. The commit will remain in the object store as an orphan and can be GC'd later. This is the cleanest recovery because the same fix is now correctly applied in the agent worktree as `f2cf2c7`.
2. OR accept `95850c6` on `prep/red-team-hybrid-prep` and ignore `f2cf2c7` on the agent worktree (effectively discarding the agent worktree's branch). This would skip the orchestrator's intended wave-merge step and is NOT recommended.

I recommend option 1. The reflog entry `refs/heads/prep/red-team-hybrid-prep@{1}` should show what `prep/red-team-hybrid-prep` was at before the wrong-branch commit, enabling clean rollback.

**Root cause:** The Bash tool's `cd` was permitted to reach a sibling worktree, breaking sandbox isolation. Future agents should be aware that `cd /c/dev/Harmonic && ...` from inside an agent worktree will silently land in the main worktree if path-restriction is not enforced. I should have noticed earlier when the first Bash output included a "warning: in the working copy of '.github/workflows/discovery-pipeline.yml', LF will be replaced by CRLF" — that warning was emitted by the main worktree, not the agent worktree.

## Post-merge Verification (Deferred)

The plan calls for post-merge verification via `gh run list --workflow=discovery-pipeline.yml --limit 1` to confirm the next scheduled CI run no longer errors on argparse. This is deferred until the agent worktree's commit `f2cf2c7` (or the equivalent recovery) merges into `main` and the next `0 6 * * *` cron fires. Not blocking for plan completion.

## Self-Check: PASSED

- File `.github/workflows/discovery-pipeline.yml` exists in agent worktree at `C:/dev/Harmonic/.claude/worktrees/agent-abc696e8/.github/workflows/discovery-pipeline.yml` and contains the 5 edits (verified via post-edit Grep counts: ` -v ` = 0, ` -v$` = 0, `python run_pipeline.py` = 8 unchanged)
- Commit `f2cf2c7` exists on branch `worktree-agent-abc696e8` (verified via `git log --oneline -3` and `git rev-parse HEAD` returning `f2cf2c7dfc19f6d89fd80483eef25fba729d7ecb`)
- File `.planning/phases/01-move-0-prep-liveness-prep/01-11-SUMMARY.md` (this file) created in agent worktree

## Known Stubs

None. This plan was a pure flag removal; no new code paths, no placeholder values, no UI changes.

## Threat Flags

None. Edit is purely subtractive within an existing CI workflow file. No new network endpoints, auth paths, file access patterns, or schema changes were introduced.

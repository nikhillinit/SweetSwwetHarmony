# Keep-alive scheduled task — install DEFERRED to operator

**Plan:** `01-05-PLAN.md` (Phase 1 Move 0 Prep + Liveness Prep)
**REQ:** LIV-11 (R20 interim mitigation per CONTEXT.md D-29)
**Status:** Installer SHIPPED. Execution DEFERRED to operator on the canonical Windows machine.
**Created:** 2026-04-07
**Author:** plan executor (worktree `worktree-agent-ae0f67e5`)

## What was shipped

- `scripts/red-team-hybrid/install_keepalive_task.ps1` — idempotent Windows Task Scheduler installer (commit `d8bf597`)
- `artifacts/keepalive/.gitkeep` — git-tracked evidence directory (this commit)

## Why the installer was NOT executed by Phase 1

Per the explicit escape-hatch in `01-05-PLAN.md` Task 2 acceptance criteria:

> **OR if the executor's environment cannot install Windows scheduled tasks (e.g., running in a Linux sandbox for plan verification)**: the executor creates a `.planning/phases/01-move-0-prep-liveness-prep/KEEPALIVE-INSTALL-DEFERRED.md` note stating the installer was NOT run here and must be run by the user on the Windows production machine, AND flag this in plan 01-10's VERIFICATION.md as a Hard Rubric Gate 5 outstanding item.

The plan was executed inside the worktree at `C:\dev\Harmonic\.claude\worktrees\agent-ae0f67e5`. Two reasons the installer must NOT be `Register-ScheduledTask`'d from this worktree even though PowerShell is technically available:

1. **Path stability.** `Register-ScheduledTask` bakes the absolute `WorkingDirectory` (= worktree path) and inner-script path (`$ProjectRoot\scripts\red-team-hybrid\_keepalive_daily.cmd`) into Windows Task Scheduler. The worktree is ephemeral; once it is removed (post-merge cleanup) the registered task points at a non-existent path and silently fails every day, recreating the R19 failure mode the script was designed to close. The installer MUST be run from the canonical repo `C:\dev\Harmonic` after the worktree branch merges.
2. **Production DB scope.** The scheduled task invokes `python run_pipeline.py collect ...` against `signals.db`. The worktree should not be the path that owns that mutation — the canonical repo owns it. Running the installer from the worktree would have the scheduler hit the worktree's checkout, not the production checkout.

PowerShell IS available in this MSYS shell (`/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell` was found), so the technical capability exists. The decision to defer is policy, not capability.

## Operator runbook (run AFTER merging the prep/red-team-hybrid-prep branch)

### Pre-flight checklist

```powershell
# 1. Be at the canonical repo, NOT a worktree
cd C:\dev\Harmonic
git branch --show-current   # expect: a merged branch, e.g. main or prep/red-team-hybrid-prep

# 2. Verify the installer is present
Test-Path scripts\red-team-hybrid\install_keepalive_task.ps1   # expect: True

# 3. Confirm signals.db is the production one
Test-Path signals.db   # expect: True
python -c "import sqlite3, sys; c = sqlite3.connect('file:signals.db?mode=ro', uri=True); n = c.execute('SELECT COUNT(*) FROM signals').fetchone()[0]; print(f'signal_count={n}'); sys.exit(0 if n > 600 else 1)"
# expect: signal_count >> 612 once collection has caught up post-LIV-01

# 4. (Optional) verify the venv python.exe path if you use a venv
# e.g. C:\dev\Harmonic\.venv\Scripts\python.exe
```

### Install the task (default: 08:00 local, immediate test run)

```powershell
# Default invocation -- registers the task and triggers an immediate run
powershell -ExecutionPolicy Bypass -File scripts\red-team-hybrid\install_keepalive_task.ps1 -TestRun

# Or, if you use a venv:
powershell -ExecutionPolicy Bypass -File scripts\red-team-hybrid\install_keepalive_task.ps1 `
    -PythonExe "C:\dev\Harmonic\.venv\Scripts\python.exe" `
    -TestRun
```

### Verify the first run

```powershell
# Wait ~60 seconds, then:
Get-ChildItem artifacts\keepalive\
# expect: a YYYY-MM-DD.json file matching today's date

(Get-ScheduledTaskInfo -TaskName "HarmonicKeepAlive").LastTaskResult
# expect: 0 (success)

Get-Content (Get-ChildItem artifacts\keepalive\*.json | Select-Object -First 1) | Select-Object -First 20
# expect: JSON with "checked_at", "exit_code": 0, "status": "OK", "collectors": [...]
```

### Trigger a second run for Hard Rubric Gate 5 evidence

Hard Rubric Gate 5 requires "the task installed AND has run successfully at least twice". Two paths to satisfy this:

**Option A (preferred): wait until tomorrow 08:00 local, let the daily trigger fire naturally.**

```powershell
# Next day, after 08:00 local:
(Get-ScheduledTaskInfo -TaskName "HarmonicKeepAlive").LastRunTime
(Get-ScheduledTaskInfo -TaskName "HarmonicKeepAlive").LastTaskResult   # expect: 0
Get-ChildItem artifacts\keepalive\
# expect: today's YYYY-MM-DD.json AND yesterday's YYYY-MM-DD.json (2 distinct files)
```

**Option B (if you cannot wait): trigger a second manual run.**

```powershell
Start-ScheduledTask -TaskName "HarmonicKeepAlive"
# Wait ~60 seconds
(Get-ScheduledTaskInfo -TaskName "HarmonicKeepAlive").LastTaskResult   # expect: 0
# The same-day JSON file is overwritten in place, but the LastTaskResult having ticked twice is the >=2 evidence.
```

Per Plan 01-05 Task 2 acceptance criteria, document in plan 01-10's VERIFICATION.md which option you exercised (A = 2 distinct files; B = 1 file with overwritten content + 2 LastTaskResult successes).

### Commit the captured JSON evidence

```powershell
git add artifacts\keepalive\*.json
git commit --no-verify -m "evidence(01): keep-alive task runs (LIV-11)"
```

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `LastTaskResult != 0` | `python` not on PATH for the user the task runs as | Re-run installer with `-PythonExe <abs path to python.exe>` |
| JSON file empty | venv not activated, `.env` not loaded | Use venv python.exe path; verify `.env` is in `$ProjectRoot` |
| `LastTaskResult = 2147750687` (0x80070420) | Permission denied on cmd.exe | Re-run installer in an elevated PowerShell once; subsequent runs are fine |
| Scheduled task missing | Installer never ran or task got removed | Re-run installer (idempotent) |
| Filename in `artifacts\keepalive\` looks wrong (not YYYY-MM-DD) | Non-en-US date locale | See "Date format assumption" section in installer header; adjust the inner cmd.exe DATE slice positions |

## Cross-plan dependency

This deferral is a **Hard Rubric Gate 5 outstanding item** (per `1-CONTEXT.md` rubric §5). Plan `01-10-PLAN.md` (Phase 1 verification) MUST flag this in its `1-VERIFICATION.md` output as:

- **Hard Gate 5 status**: PARTIALLY GREEN — installer shipped (`scripts/red-team-hybrid/install_keepalive_task.ps1` commit `d8bf597`), execution DEFERRED to operator per `KEEPALIVE-INSTALL-DEFERRED.md`. Closing this gate requires the operator to run the installer on the canonical Windows repo and commit the resulting `artifacts/keepalive/*.json` evidence BEFORE 2026-04-18.
- **Owner**: operator (not Phase 1 plan executor)
- **Deadline**: 2026-04-18 (Step 4B regret check)
- **Blocker action if not done by 2026-04-18**: per LIV-03, the regret check POSTPONES until remediation.

## Why R19 root cause is still considered closed by Phase 1

The root cause of R19 was "no scheduled collection". Phase 1 ships the **mechanism** that closes that root cause: an idempotent, stdlib-only PowerShell installer with documented operator runbook. The remaining step (running the installer once) is operational, not architectural — it cannot be done from a worktree without injecting an unstable path into Windows Task Scheduler. Hard Rubric Gate 5 evaluates the operational completion separately from the architectural fix.

If Phase 1 had executed the installer from the worktree, it would have:
- Registered a scheduled task pointing at `C:\dev\Harmonic\.claude\worktrees\agent-ae0f67e5\...`
- Created a phantom task that fails silently after worktree cleanup
- Hidden the failure behind a "task installed" green tick
- Recreated the exact R19 failure mode (silent freeze) the plan was designed to close

The deferral is the *correct* execution path, not a workaround.

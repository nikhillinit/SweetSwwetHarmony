# Final RALPLAN-DR: Freeze Drill Reviewed Improvements

## Decision

Implement the smallest safe slice while the induced-freeze drill is already in
flight:

1. Add a scheduler-free `-GenerateOnly` seam to
   `scripts/red-team-hybrid/install_keepalive_task.ps1`.
2. Add repo-native pytest coverage for generated runner content.
3. Add a Monday readout runbook with the correct freshness proof semantics.
4. Tighten `docs/runbooks/db-ops-policy.md`.
5. Locally align only these `.omx` safety docs:
   - `.omx/plans/monday-am-freeze-drill-ralplan-20260510.md`
   - `.omx/plans/freeze-drill-reviewed-improvements-ralplan-dr-20260510.md`

Do not touch `.omx/context/*` in this implementation slice.

## Principles

1. Preserve the active drill proof surface before the Monday, 2026-05-11 readout.
2. Use `signals.created_at` as the only freshness clock for the Monday verdict.
3. Keep tests scheduler-free and repo-native.
4. Keep generated wrappers, keepalive JSON, and unrelated DB-hardening drift out
   of the focused package.

## Decision Drivers

1. Avoid a Monday false-pass from baseline-positive peers that are merely still
   under the 12-hour threshold.
2. Ensure `-GenerateOnly` cannot touch any Task Scheduler surface.
3. Avoid reinstalling or regenerating the live `HarmonicFreezeDrill` wrapper
   before the readout.

## Options Considered

| Option | Verdict | Reason |
|---|---|---|
| Docs only | Rejected | Leaves installer seam and safety gap untested. |
| Smallest safe slice | Chosen | Fixes the reliability gap without mutating the active drill. |
| Broader instrumentation, email, or Phase 5.2 build | Rejected | Scope drift during an active drill window. |

## Implementation Contract

### Installer

Edit `scripts/red-team-hybrid/install_keepalive_task.ps1`:

- Add `-GenerateOnly`.
- `-GenerateOnly` must write/report the generated runner and return before the
  entire ScheduledTasks section.
- Under `-GenerateOnly`, no ScheduledTasks cmdlet may run, including:
  `New-ScheduledTaskAction`, `New-ScheduledTaskTrigger`,
  `New-ScheduledTaskPrincipal`, `New-ScheduledTaskSettingsSet`,
  `Get-ScheduledTask`, `Unregister-ScheduledTask`, `Register-ScheduledTask`,
  and `Start-ScheduledTask`.
- Rewrite installer help/examples so they do not advertise live
  `HarmonicFreezeDrill ... -TestRun` before the Monday, 2026-05-11 readout.

### Tests

Add `tests/scripts/test_install_keepalive_task.py`:

- Use pytest and temp project roots only.
- The pytest fixture must precreate `<tmp>\scripts\red-team-hybrid`; installer
  subtree creation stays out of scope for this slice.
- Use alternate non-live task names only, never `HarmonicFreezeDrill` against
  `C:\dev\Harmonic`.
- Assert default `HarmonicKeepAlive` generated content preserves collectors and
  threshold and does not include forced watchdog-exit masking.
- Assert preview freeze-drill generated content includes custom collectors,
  `JOB_POSTING_DOMAINS`, 12-hour threshold, and `exit /b 0` when
  `-IgnoreWatchdogExitCode` is passed.

### Runbooks And Local Safety Docs

Create `docs/runbooks/monday-am-freeze-drill-readout.md`:

- Use the real watchdog JSON contract: top-level `status` and `exit_code`;
  `collectors` is a list of records with `source_api`, `category`,
  `last_created`, `age_hours`, and `status`.
- Require peer positive-control `MAX(signals.created_at)` strictly after the
  observed scheduled run start.
- Treat GitHub as auth/plumbing corroboration unless it inserts rows.
- Treat host sleep, hibernate, power-off, missed scheduler run, or missing
  positive-control DB rows as `AMBIGUOUS`.
- State that scheduler/file/wrapper evidence is corroboration, not primary
  freshness proof.
- State explicitly: do not invoke
  `install_keepalive_task.ps1 -TaskName HarmonicFreezeDrill` against the live
  repo/task before the Monday, 2026-05-11 readout because it can rewrite the
  live wrapper and/or re-register the active task.

Update `docs/runbooks/db-ops-policy.md`:

- Reference the Monday readout runbook.
- Replace live installer examples with temp-root or non-live preview examples.
- Add the same dated pre-Monday live-task warning.
- Keep email and Phase 5.2 implementation deferred.

Update only the pinned `.omx` plan files above to remove live installer examples
and use the same dated warning. Keep `.omx/context/*` out of scope.

## No-Touch List

Do not touch before the Monday, 2026-05-11 readout:

- `scripts/red-team-hybrid/_keepalive_HarmonicFreezeDrill.cmd`
- `artifacts/keepalive/*.json`
- scheduled task registration/state for `HarmonicFreezeDrill`
- live installer invocation against `-TaskName HarmonicFreezeDrill`
- sidecar exit-evidence implementation
- email alert implementation
- Phase 5.2 build/infrastructure files
- unrelated dirty DB-hardening files already in the worktree

## Deliberate Pre-Mortem

### Scenario 1: Monday false-pass from baseline peers

Failure mode: `greenhouse_jobs` or `ashby_jobs` remain under the threshold from
the Sunday baseline, so Monday appears healthy without overnight DB progress.

Mitigation: Monday pass requires watchdog JSON plus peer `MAX(created_at)`
strictly after the observed scheduled run start.

### Scenario 2: accidental live task regeneration or re-registration

Failure mode: an operator or verification command runs the installer against
`-TaskName HarmonicFreezeDrill` before readout, rewriting the live wrapper or
re-registering the active task.

Mitigation: remove live examples; use only temp-root or non-live preview task
names; add dated warnings in operator-facing docs.

### Scenario 3: `-GenerateOnly` still touches ScheduledTasks

Failure mode: dry generation still constructs, queries, unregisters, registers,
or starts a scheduled task.

Mitigation: branch and return before the entire ScheduledTasks section; verify
via temp-root pytest and a safe non-live `-GenerateOnly` check.

## Verification

Run after implementation:

```powershell
pytest tests/scripts/test_install_keepalive_task.py -q
```

Safe real-repo generation check using an alternate non-live task name:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\red-team-hybrid\install_keepalive_task.ps1 `
  -TaskName HarmonicFreezeDrillPreview `
  -Collectors "job_postings,github" `
  -WatchdogOperational "rss_feeds,greenhouse_jobs,ashby_jobs" `
  -WatchdogThresholdHours 12 `
  -JobPostingDomains "10beauty.com,cofertility.com,openai.com" `
  -IgnoreWatchdogExitCode `
  -GenerateOnly
```

Then confirm no `HarmonicFreezeDrillPreview` task was created or modified:

```powershell
Get-ScheduledTask -TaskName "HarmonicFreezeDrillPreview" -ErrorAction SilentlyContinue
```

Monday observability checks:

```powershell
python scripts/red-team-hybrid/freshness_watchdog.py --json --threshold-hours 12 --operational rss_feeds,greenhouse_jobs,ashby_jobs
```

```powershell
python -c "import sqlite3; conn=sqlite3.connect('signals.db'); cur=conn.cursor(); cur.execute(\"SELECT source_api, MAX(created_at) FROM signals WHERE source_api IN ('greenhouse_jobs','ashby_jobs') GROUP BY source_api\"); print(cur.fetchall())"
```

E2E is explicitly waived for this slice because the live drill is already
running and must not be perturbed.

## Acceptance Criteria

1. `-GenerateOnly` returns before any ScheduledTasks cmdlet executes.
2. Tests cover generated wrapper content and the no-scheduler seam.
3. Installer examples no longer advertise live `HarmonicFreezeDrill -TestRun`
   before the Monday, 2026-05-11 readout.
4. Monday readout requires both watchdog JSON evidence and peer post-run
   `MAX(created_at)` evidence.
5. No sidecar, email, Phase 5.2 build, generated wrapper, keepalive JSON, or
   unrelated dirty DB-hardening files are included.

# Monday-AM Freeze Drill RALPLAN-DR

## Status

Consensus status: approved after Planner -> Architect -> Critic loop.

Device-availability revision: applied. Host sleep, hibernate, power-off, or scheduler-unavailable windows are explicitly ambiguous, not negative evidence.

Peer-selection revision: applied after the 2026-05-10 probes. `job_postings` is the positive DB-progress control for the drill; GitHub authentication is live but the current `github` collector query returned zero rows and is not, by itself, positive progress evidence.

## Task

Plan the Sunday 2026-05-10 afternoon to Monday 2026-05-11 morning induced-freeze drill so the operator can tell whether `rss_feeds` omission is detected by the liveness/freshness path without mutating config, heartbeat state, overrides, or `signals.db`.

## Principles

1. Use one freshness clock for this drill: `signals.created_at` with `--threshold-hours 12`.
2. Separate host availability, collector health, and ingest freshness. These answer different questions.
3. Fail closed on ambiguity. A pass requires positive proof of host opportunity, omission, and `rss_feeds` staleness.
4. Do not disable YAML config, set overrides, corrupt DB/heartbeat state, or re-enable broad `HarmonicKeepAlive`.
5. Treat file/runner witnesses as host-opportunity evidence only; they do not prove collection freshness or source emptiness.
6. Treat synthetic alert delivery as visibility-only evidence, and count it only with external human receipt. Reserve synthetic canaries for the Phase 5.2 durability tranche, not the main freeze proof.

## Decision Drivers

1. `rss_feeds` has a 6-hour expected cadence, so a 12-hour threshold gives cadence slack while still allowing a Monday morning answer.
2. The default 36-hour watchdog threshold is too slow for this drill.
3. `ops.collector_health --format json` is health/config/heartbeat evidence, not the `signals.created_at` freshness source.
4. A missing run during host sleep, hibernation, power-off, or scheduler downtime is an unknown collection opportunity, not proof of source emptiness or collector failure.
5. The known broad keepalive wrapper includes `run_pipeline.py collect --collectors hacker_news,arxiv,rss_feeds,news_api`; if active, it refreshes `rss_feeds` and invalidates the omission drill.
6. `job_postings` has been validated as a plumbing/control collector: a scratch-DB pipeline run inserted 3 rows from known ATS-backed domains, with `rows_inserted_this_iter=3` and emitted `source_api` values `greenhouse_jobs` and `ashby_jobs`.
7. The seed-derived `JOB_POSTING_DOMAINS` list is not yet a reliable positive-row control. It found candidates, but a representative seed-domain probe returned zero rows and broader seed probes timed out.
8. `GITHUB_TOKEN` is live in `.env`, but it must be loaded into the runner environment. The current `github` collector dry-run authenticated successfully and returned zero rows, so GitHub is auth/plumbing corroboration unless it produces positive rows in the drill window.

## Alternatives Invalidated

1. Use watchdog default `36h`: too slow for Sunday afternoon to Monday morning.
2. Use `collector_health` as freshness proof: wrong clock.
3. Accept `MISSING` as success: ambiguous baseline or broader data absence.
4. Infer omission from `schedule list` alone: collector membership is hidden; inspect `pipeline_schedules.collectors` JSON directly.
5. Describe `pipeline_schedules` as collect-only by default: scheduler-backed runs route through `run_full_pipeline(collectors=...)` unless a wrapper literally calls `run_pipeline.py collect --collectors ...`.
6. Ignore device availability: would convert laptop sleep/power-off into false negative evidence.
7. Treat `github` success with zero rows as positive peer freshness: it proves authentication and API reachability, not DB progress.
8. Require all `job_postings` source APIs blindly: `job_postings` may emit `greenhouse_jobs`, `ashby_jobs`, or `lever_jobs`; only source APIs that are baseline-positive should be required as freshness peers.

## Process

### 1. Host Availability / Opportunity Gate

Before interpreting freshness, prove the host had a real chance to run.

Evidence may include:

- Task Scheduler trigger, `LastRunTime`, `LastTaskResult`, and task history if available.
- System power/sleep/resume/shutdown/start evidence for the observation window.
- Uptime or external monitor pings if already available.
- Exact window boundaries: omission start, expected trigger times, Monday evaluation time.

Interpretation:

- If the host was awake/available for enough time, continue.
- If the host was asleep, hibernated, powered off, or scheduler-unavailable during the run window, classify as `AMBIGUOUS: unknown collection opportunity`.
- If availability cannot be proven, classify as `AMBIGUOUS: host opportunity unproven`.

Use an availability-adjusted clock:

```text
availability_adjusted_age =
  wall_clock_age_since_last_rss_created_at
  - host_unavailable_overlap_with_observation_window
```

The Monday verdict can pass only if the adjusted age is at least 12 hours.

### 2. Sunday Baseline

Freshness source of record:

```powershell
python scripts/red-team-hybrid/freshness_watchdog.py --json --threshold-hours 12 --operational rss_feeds,greenhouse_jobs,ashby_jobs
```

Baseline acceptance:

- `rss_feeds` is present and fresh under the 12-hour threshold before omission begins.
- `job_postings` has run with explicit `JOB_POSTING_DOMAINS` and produced positive DB rows.
- Expected peer source APIs are present and fresh enough for comparison. For the validated fixture set, use `greenhouse_jobs` and `ashby_jobs` if both were produced at baseline. If the baseline fixture only emits `greenhouse_jobs`, gate only `rss_feeds,greenhouse_jobs` rather than requiring `ashby_jobs` or `lever_jobs` as missing peers.
- Any `MISSING` at baseline is a baseline failure, not a valid freeze drill.

Health/status corroboration only:

```powershell
python -m ops.collector_health --format json
```

Use this only for enabled/cadence and heartbeat interpretation, never as the `signals.created_at` freshness source.

Validated control setup:

```powershell
$env:JOB_POSTING_DOMAINS = "10beauty.com,cofertility.com,openai.com"
$env:GITHUB_TOKEN = "<load from .env or secret store; do not commit>"
python run_pipeline.py collect --collectors job_postings,github --parallel false
```

Installer preview only:

Before the Monday, 2026-05-11 readout, do not invoke
`install_keepalive_task.ps1 -TaskName HarmonicFreezeDrill` against the live
repo/task. That can rewrite the live wrapper and/or re-register the active
task, contaminating the in-flight drill. Use a temp root or non-live task name
for any pre-readout generation check.

```powershell
New-Item -ItemType Directory -Force "C:\tmp\harmonic-freeze-preview\scripts\red-team-hybrid" | Out-Null
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\red-team-hybrid\install_keepalive_task.ps1 `
  -ProjectRoot "C:\tmp\harmonic-freeze-preview" `
  -TaskName "HarmonicFreezeDrillPreview" `
  -Collectors "job_postings,github" `
  -WatchdogOperational "rss_feeds,greenhouse_jobs,ashby_jobs" `
  -WatchdogThresholdHours 12 `
  -JobPostingDomains "10beauty.com,cofertility.com,openai.com" `
  -IgnoreWatchdogExitCode `
  -GenerateOnly
```

Do not reuse the broad default `HarmonicKeepAlive` task for this drill; its default collector set intentionally preserves the historical keepalive behavior and includes `rss_feeds`.
For the induced-freeze task, keep the watchdog JSON failure as evidence while using `-IgnoreWatchdogExitCode` so Task Scheduler does not retry the expected `rss_feeds` failure and muddy the observation window.

Interpretation:

- `job_postings` is the positive DB-progress peer. It must show positive inserted rows and fresh emitted source APIs.
- `github` is optional corroboration. It proves authenticated GitHub access only if it succeeds, and it becomes a positive DB-progress peer only if `rows_inserted_this_iter > 0`.
- `hacker_news` or weekday `sec_edgar` remain fallback peers only if `job_postings` is unavailable or cannot be configured on the runner.

### 3. Omission-Path Proof

Prove the active Sunday-to-Monday runner excludes `rss_feeds`.

Required evidence:

- Direct `pipeline_schedules.collectors` JSON inspection for scheduler-backed paths.
- Resolved Task Scheduler action plus wrapper/cmd contents for Windows task paths.
- If using `pipeline_schedules`, describe it as `run_full_pipeline(collectors=...)`, not collect-only, unless the wrapper literally calls `run_pipeline.py collect --collectors ...`.
- Runner environment evidence that `JOB_POSTING_DOMAINS` is present and `GITHUB_TOKEN` is live if GitHub is included.
- File/runner witness output proving the runner fired during the window; use it only to support host-opportunity interpretation.

Disqualifier:

- Active broad `HarmonicKeepAlive`, because its wrapper includes `rss_feeds`.
- Any runner path that omits the validated positive DB-progress control while still claiming a positive peer comparison.

### 4. Synthetic Alert Visibility

Run one clearly labeled synthetic alert only to prove delivery visibility.

Acceptance:

- External human receipt with timestamp.
- Repo-side notifier/send logs alone do not prove human receipt.

### 5. Monday Pass / Non-Pass / Ambiguous Contract

Pass only if all are true:

- Host availability gate passes, with at least 12 availability-adjusted hours.
- Sunday baseline proved recent `rss_feeds` `signals.created_at` freshness under the 12-hour threshold.
- Overnight executable omission path was real, resolved, and excluded `rss_feeds`.
- Monday freshness evidence shows `rss_feeds=STALE`, not `MISSING`, under the 12-hour threshold.
- Required peer source APIs from the `job_postings` baseline are not `MISSING`, remain `FRESH`, and have `MAX(signals.created_at)` strictly after the observed scheduled run start, proving the broader system had live collection opportunity and DB progress after the baseline.
- Any synthetic alert sent was externally human-received.

Non-pass if any are true:

- Broad keepalive was active.
- Any unresolved scheduler/wrapper ambiguity remains.
- Any config disablement, override use, heartbeat manipulation, or DB corruption occurred.
- `collector_health` is used as freshness proof.
- The default 36-hour watchdog threshold is used for the Monday verdict.

Ambiguous if any are true:

- Host was asleep, hibernated, powered off, or scheduler-unavailable during the relevant window.
- Host availability cannot be proven.
- `rss_feeds` or required peers are `MISSING`.
- Peer collectors also degraded, making attribution unclear.
- `job_postings` had zero inserted rows or only runner-witness evidence exists.
- Less than 12 availability-adjusted hours elapsed.

## Success Statement

The drill worked only if Monday evidence can say:

`rss_feeds` remained enabled, the host had a real collection opportunity for at least 12 adjusted hours, `job_postings` proved positive DB progress through fresh emitted source APIs, and `rss_feeds` alone became `STALE` under the 12-hour `signals.created_at` freshness check through a clean omission path.

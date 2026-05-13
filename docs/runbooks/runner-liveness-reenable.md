# Runner Liveness Re-Enable Runbook

Status: provisional contract, no live scheduler mutation by reading this file
Date: 2026-05-13

## Boundary

Use this runbook to re-enable `HarmonicKeepAlive` after the post-freeze drill.
It owns runner liveness, monitor delivery, host opportunity, and trial pass
criteria. Phase 5.2 durability remains separate and owns restore drills, WAL
shipping, sidecar behavior, off-host ledger retention, filesystem controls, and
host-storage benchmarks.

Do not use this runbook to mutate `signals.db` directly or to close Phase 5.2.

## Evidence Used

Primary proof:

- `artifacts/keepalive/2026-05-12-freeze-drill-readout.md`

The readout records `HarmonicFreezeDrill` as `PASS`: the scheduled task ran on
2026-05-12, `rss_feeds` stayed stale as the intentionally omitted target, and
`greenhouse_jobs` plus `ashby_jobs` produced post-run rows in `signals.db`.
That proves omitted-target detection. It does not by itself re-enable
`HarmonicKeepAlive`.

## Drift Disposition

Keep these out of the re-enable PR unless a separate operator decision pulls
them in:

- `state/collectors.json`
- `.omx/`
- `.tmp/`
- generated `artifacts/keepalive/*.json`
- `backups/`
- local generated task wrappers such as
  `scripts/red-team-hybrid/_keepalive_HarmonicFreezeDrill.cmd`

## Preflight

Reverify live task state before any live change:

```powershell
Get-ScheduledTask -TaskName "HarmonicKeepAlive" -ErrorAction SilentlyContinue | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName "HarmonicKeepAlive" -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskName "HarmonicFreezeDrill" -ErrorAction SilentlyContinue | Select-Object TaskName, State
```

Choose host mode before registering the task:

- Local host: claim only provisional local-host liveness.
- Dedicated always-on host: claim broader runner availability only after host
  opportunity evidence exists.

## Monitor Delivery Gate

Configure a Healthchecks.io or self-hosted compatible ping URL in the host
environment:

```powershell
$env:HARMONIC_KEEPALIVE_PING_URL = "<secret ping URL>"
```

The ping URL must alert a real human on missed or failed runs. Verify that
alert delivery before live registration.

The generated runner posts the watchdog artifact through
`scripts/red-team-hybrid/keepalive_monitor_ping.py`. The helper appends the
watchdog exit status to the ping URL and includes these post-run DB proof
fields in the POST body:

- `source_of_record = signals.created_at`
- `watchdog.threshold_hours`
- `watchdog.min_created_at`
- `watchdog.status`
- `watchdog.sources.<source_api>.last_created`
- `watchdog.sources.<source_api>.required_after`
- `watchdog.sources.<source_api>.stale_reason`
- `watchdog.sources.<source_api>.status`

Reference for compatible endpoints: Healthchecks.io supports POST bodies on
ping requests and `/<exit-status>` URL suffixes for success or failure signals.

For live runs, the generated wrapper captures the observed run start and passes
it to `freshness_watchdog.py` as `--min-created-at`. A source with rows inside
the rolling threshold but no row after that boundary fails with
`no_post_run_rows`. This prevents a duplicate-only collector run from being
reported as fresh liveness.

Generated watchdog artifacts are task-specific:
`artifacts/keepalive/YYYY-MM-DD-<TaskName>.json`. Sibling tasks must not share
the same artifact path.

`collector_health`, scheduler metadata, wrapper files, and
`state/collectors.json` are corroboration. They are not the freshness clock.

## Generate-Only Preview

Preview the provisional positive-peer `HarmonicKeepAlive` runner before
registration:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\red-team-hybrid\install_keepalive_task.ps1 `
  -TaskName "HarmonicKeepAlive" `
  -Collectors "job_postings" `
  -WatchdogOperational "greenhouse_jobs,ashby_jobs" `
  -WatchdogThresholdHours 12 `
  -JobPostingDomains "10beauty.com,cofertility.com,openai.com" `
  -MonitorPingUrlEnvVar "HARMONIC_KEEPALIVE_PING_URL" `
  -GenerateOnly
```

This provisional contract intentionally excludes `rss_feeds`. That exclusion
does not define final production policy; it avoids rerunning the induced freeze
when the already recorded drill is sufficient.

Do not use `-IgnoreWatchdogExitCode` for the re-enable trial. A stale positive
peer should fail the task.

## Live Trial

After the host-mode and monitor gates pass, register and trigger one trial:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\red-team-hybrid\install_keepalive_task.ps1 `
  -TaskName "HarmonicKeepAlive" `
  -HostMode "<LocalHost|DedicatedHost>" `
  -Collectors "job_postings" `
  -WatchdogOperational "greenhouse_jobs,ashby_jobs" `
  -WatchdogThresholdHours 12 `
  -JobPostingDomains "10beauty.com,cofertility.com,openai.com" `
  -MonitorPingUrlEnvVar "HARMONIC_KEEPALIVE_PING_URL" `
  -MonitorAlertVerified `
  -TestRun
```

The task result must remain meaningful:

- watchdog exit `0` means the positive peers are fresh
- watchdog non-zero means the task fails
- `no_post_run_rows` means the collector did not prove post-run DB progress
- monitor delivery failure means the task fails

## Trial Pass Criteria

Pass only if all are true:

- host opportunity is proven for the trial window
- `HarmonicKeepAlive` ran and `HarmonicFreezeDrill` did not overlap
- the watchdog artifact name is task-specific:
  `YYYY-MM-DD-<TaskName>.json`
- the watchdog threshold is 12 hours
- the watchdog used `--min-created-at` from the observed run start
- `JOB_POSTING_DOMAINS` is explicit
- `greenhouse_jobs,ashby_jobs` are the operational watchdog sources
- the monitor received a success ping with the DB proof payload, including
  `min_created_at`, `required_after`, and any `stale_reason`
- the human alert recipient is known and verified
- both peer source APIs have `MAX(signals.created_at)` after the observed run
  start

Peer DB proof:

```powershell
$runStartLocal = (Get-ScheduledTaskInfo -TaskName "HarmonicKeepAlive").LastRunTime
$env:RUN_START_UTC = $runStartLocal.ToUniversalTime().ToString("o")
@'
import os
import sqlite3
from datetime import datetime

def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

peers = ("greenhouse_jobs", "ashby_jobs")
run_start = parse_ts(os.environ["RUN_START_UTC"])
conn = sqlite3.connect("signals.db")
rows = dict(conn.execute(
    "SELECT source_api, MAX(created_at) "
    "FROM signals "
    "WHERE source_api IN ('greenhouse_jobs','ashby_jobs') "
    "GROUP BY source_api"
))
missing = [
    peer for peer in peers
    if peer not in rows or parse_ts(rows[peer]) <= run_start
]
print({"max_created_at": rows, "missing_or_stale_peers": missing})
raise SystemExit(1 if missing else 0)
'@ | python -
```

## Interpretation

If the local-host path passes, record the result as provisional local-host
liveness. Do not claim durable production coverage.

If the dedicated-host path passes, record the result as host-scoped runner
liveness. Do not claim Phase 5.2 durability until restore and storage drills
also pass.

If monitor delivery is absent, human alert delivery is unverified, or the peer
DB proof is missing, do not re-enable production liveness claims.

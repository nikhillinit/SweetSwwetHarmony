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

Current disposition:

- `hacker_news`, `arxiv`, and `rss_feeds` are the default operational
  freshness set.
- `news_api` is optional enrichment. Its intended gap is mainstream press,
  funding announcements, and PR activity, but the current GNews-backed
  collector is quota-constrained and should be revisited with a provider swap
  or a manual weekly run before returning to the watchdog gate.
- `job_postings` with `greenhouse_jobs,ashby_jobs` remains a positive-peer
  diagnostic pattern for drills, not the default production heartbeat.

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

The generated runner posts the composite artifact through
`scripts/red-team-hybrid/keepalive_monitor_ping.py`. The runner first writes a
pre-monitor composite artifact, posts that artifact, then finalizes the local
artifact with monitor delivery status. The helper appends the composite
pre-monitor exit status to the ping URL and includes these fields in the POST
body:

- `source_of_record = signals.created_at`
- `keepalive.mode`
- `keepalive.collector_exit_code`
- `keepalive.collector_exit_status`
- `keepalive.db_progress_status`
- `keepalive.db_progress_reason`
- `keepalive.heartbeat_status`
- `keepalive.pre_monitor_exit_code`
- `watchdog.threshold_hours`
- `watchdog.min_created_at`
- `watchdog.status`
- `watchdog.sources.<source_api>.last_created`
- `watchdog.sources.<source_api>.required_after`
- `watchdog.sources.<source_api>.stale_reason`
- `watchdog.sources.<source_api>.status`

For backward compatibility with pre-composite artifacts, the monitor helper also
accepts a raw `freshness_watchdog.py` JSON payload. Raw watchdog compatibility
is a transport fallback only: generated runners should post the composite
artifact, and raw payloads are reported with `keepalive.mode =
raw_watchdog_compat` using the watchdog exit code directly. Raw watchdog mode
does not apply the `daily_heartbeat` duplicate-only downgrade. Legacy runners
that still pass `--watchdog-json` are accepted through a deprecated CLI alias.

Reference for compatible endpoints: Healthchecks.io supports POST bodies on
ping requests and `/<exit-status>` URL suffixes for success or failure signals.

For live runs, the generated wrapper captures the observed run start and passes
it to `freshness_watchdog.py` as `--min-created-at`. A source with rows inside
the rolling threshold but no row after that boundary fails with
`no_post_run_rows`. `freshness_watchdog.py` remains strict and DB-only; the
runner's composite verdict determines whether that strict DB failure is a
daily-heartbeat warning or a strict write-proof failure.

Daily `HarmonicKeepAlive` uses `daily_heartbeat` mode. If collection exits `0`,
all watchdog failures are `no_post_run_rows`, and monitor delivery succeeds,
the final local artifact records `overall_status=WARN_DUPLICATE_ONLY` and the
task exits `0`. This means the runner executed and reported successfully, not
that fresh rows were inserted.

Deliberate proof and drill runs use `strict_write_proof` mode. In that mode,
`no_post_run_rows` remains a hard failure and exits non-zero.

Generated composite artifacts are task-specific:
`artifacts/keepalive/YYYY-MM-DD-<TaskName>.json`. The nested DB proof comes from
the companion watchdog artifact
`artifacts/keepalive/YYYY-MM-DD-<TaskName>.watchdog.json`. Sibling tasks must
not share the same artifact path.

`collector_health`, scheduler metadata, wrapper files, and
`state/collectors.json` are corroboration. They are not the freshness clock.

## Generate-Only Preview

Preview the operational `HarmonicKeepAlive` runner before
registration:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\red-team-hybrid\install_keepalive_task.ps1 `
  -TaskName "HarmonicKeepAlive" `
  -Collectors "hacker_news,arxiv,rss_feeds" `
  -WatchdogOperational "hacker_news,arxiv,rss_feeds" `
  -WatchdogThresholdHours 36 `
  -MonitorPingUrlEnvVar "HARMONIC_KEEPALIVE_PING_URL" `
  -GenerateOnly
```

This contract intentionally excludes `news_api` from the freshness gate.
`news_api` still exists as optional public-buzz enrichment, but it is not a
daily liveness dependency while GNews quota and stale-result behavior are the
dominant failure mode.

Do not use `-IgnoreWatchdogExitCode` for the re-enable trial. A stale
operational source should fail the task unless the only DB failure is duplicate-only
`no_post_run_rows` in `daily_heartbeat` mode.

For a deliberate positive-peer diagnostic, pass explicit job-posting fixture
domains and operational peers. The installer exports those fixtures as
`JOB_POSTING_DOMAINS` inside the generated wrapper. This is not the default
production runner:

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

## Live Trial

After the host-mode and monitor gates pass, register and trigger one trial:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\red-team-hybrid\install_keepalive_task.ps1 `
  -TaskName "HarmonicKeepAlive" `
  -HostMode "<LocalHost|DedicatedHost>" `
  -Collectors "hacker_news,arxiv,rss_feeds" `
  -WatchdogOperational "hacker_news,arxiv,rss_feeds" `
  -WatchdogThresholdHours 36 `
  -MonitorPingUrlEnvVar "HARMONIC_KEEPALIVE_PING_URL" `
  -MonitorAlertVerified `
  -TestRun
```

## One-Shot Composite Verification Reminder

After registering `HarmonicKeepAlive`, install a one-shot verifier for the next
scheduled cycle:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\red-team-hybrid\install_keepalive_verification_reminder.ps1 `
  -KeepAliveTaskName "HarmonicKeepAlive" `
  -DelayMinutes 30
```

The verifier runs
`scripts/red-team-hybrid/verify_keepalive_composite_artifact.py` against the
expected UTC artifact date and writes
`artifacts/keepalive/YYYY-MM-DD-HarmonicKeepAlive-composite-verification.json`.
It fails closed if the artifact is still a raw watchdog payload, is unfinalized,
has monitor delivery failure, exits non-zero, or records any hard DB proof
failure. `PASS` and `WARN_DUPLICATE_ONLY` are both acceptable final outcomes
for daily heartbeat mode, but `WARN_DUPLICATE_ONLY` is accepted only when every
operational watchdog failure is `no_post_run_rows`.

Manual verification uses the same contract:

```powershell
python scripts\red-team-hybrid\verify_keepalive_composite_artifact.py `
  --artifact-dir artifacts\keepalive `
  --task-name HarmonicKeepAlive `
  --date YYYY-MM-DD
```

The task result must remain meaningful:

- `overall_status=PASS` means the positive peers are fresh
- `overall_status=WARN_DUPLICATE_ONLY` means the collector ran, monitor
  delivery succeeded, and DB proof was exclusively `no_post_run_rows`
- `overall_status=FAIL` means collection, DB proof, or monitor delivery failed
- `strict_write_proof` keeps `no_post_run_rows` as a hard failure
- monitor delivery failure means the task fails

## Trial Pass Criteria

Pass only if all are true:

- host opportunity is proven for the trial window
- `HarmonicKeepAlive` ran and `HarmonicFreezeDrill` did not overlap
- the watchdog artifact name is task-specific:
  `YYYY-MM-DD-<TaskName>.json`
- the watchdog threshold is 36 hours
- the watchdog used `--min-created-at` from the observed run start
- `hacker_news,arxiv,rss_feeds` are the operational watchdog sources
- `news_api` is absent from the operational watchdog sources unless a manual
  provider-swap or weekly-enrichment trial intentionally opts it in
- the monitor received a success ping with the composite verdict and DB proof
  payload, including `keepalive.db_progress_status`, `min_created_at`,
  `required_after`, and any `stale_reason`
- the human alert recipient is known and verified
- final `overall_status` is either `PASS` or `WARN_DUPLICATE_ONLY`
- if final `overall_status` is `PASS`, all operational source APIs have
  `MAX(signals.created_at)` after the observed run start
- if final `overall_status` is `WARN_DUPLICATE_ONLY`, all operational watchdog
  failures are `no_post_run_rows`

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

peers = ("hacker_news", "arxiv", "rss_feeds")
run_start = parse_ts(os.environ["RUN_START_UTC"])
conn = sqlite3.connect("signals.db")
rows = dict(conn.execute(
    "SELECT source_api, MAX(created_at) "
    "FROM signals "
    "WHERE source_api IN ('hacker_news','arxiv','rss_feeds') "
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

If monitor delivery is absent, human alert delivery is unverified, collection
failed, or DB proof failed for any reason other than daily duplicate-only
`no_post_run_rows`, do not re-enable production liveness claims.

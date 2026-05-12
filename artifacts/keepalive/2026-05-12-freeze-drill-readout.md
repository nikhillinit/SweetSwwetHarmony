# HarmonicFreezeDrill Readout - 2026-05-12

Status: PASS
Recorded: 2026-05-12T15:10 local

## Scope

This readout covers the scheduled `HarmonicFreezeDrill` run on 2026-05-12.
The operator confirmed the host-opportunity gate before steps 2-6 were captured:
the host was available for the observation window and the run was not
contaminated by broad keepalive overlap.

No manual collection was run for this readout.

## Task Evidence

Read-only task metadata:

```text
TaskName           : HarmonicFreezeDrill
LastRunTime        : 5/12/2026 8:00:01 AM
LastTaskResult     : 0
NextRunTime        : 5/13/2026 8:00:00 AM
NumberOfMissedRuns : 0
```

Run artifact:

```text
artifacts/keepalive/2026-05-12.json
LastWriteTime: 5/12/2026 8:00:24 AM
Length: 2205
```

## Freshness Watchdog

Command:

```powershell
python scripts\red-team-hybrid\freshness_watchdog.py --json --threshold-hours 12 --operational rss_feeds,greenhouse_jobs,ashby_jobs
```

Result captured at `2026-05-12T22:10:47.540871+00:00`:

| Source API | Status | Last created | Age hours |
|---|---|---|---:|
| `greenhouse_jobs` | `FRESH` | `2026-05-12T15:00:22.219611+00:00` | 7.17 |
| `ashby_jobs` | `FRESH` | `2026-05-12T15:00:23.174852+00:00` | 7.17 |
| `rss_feeds` | `STALE` | `2026-02-27T23:00:03.976742+00:00` | 1775.18 |

The watchdog returned `status: FAIL` and `exit_code: 1` only because
`rss_feeds` remained intentionally stale for the induced-freeze drill.

## Positive Peer DB Gate

Run start used for the DB gate:

```text
2026-05-12T15:00:01+00:00
```

DB query result:

```json
{
  "max_created_at": {
    "ashby_jobs": "2026-05-12T15:00:23.174852+00:00",
    "greenhouse_jobs": "2026-05-12T15:00:22.219611+00:00",
    "rss_feeds": "2026-02-27T23:00:03.976742+00:00"
  },
  "missing_or_stale_peers": [],
  "run_start_utc": "2026-05-12T15:00:01+00:00"
}
```

Both required peer source APIs produced DB rows after the observed scheduled run
start. The RSS source remained stale as intended.

## Verdict

PASS.

Rationale:

- Host opportunity gate was operator-confirmed.
- `HarmonicFreezeDrill` ran during the observation window.
- Task result was `0`.
- The 12-hour watchdog was used.
- `rss_feeds` was `STALE`, not `MISSING`.
- `greenhouse_jobs` and `ashby_jobs` were `FRESH`.
- Both positive peer `MAX(signals.created_at)` values were after the scheduled
  run start.

## Follow-Up

The induced-freeze proof is closed for the 2026-05-12 run. Normal collection or
post-drill refactor work should be decided separately from this readout.

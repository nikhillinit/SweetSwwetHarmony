# ADR 0004: Runner Liveness Re-Enable Contract

Status: Accepted
Date: 2026-05-13

## Context

ADR-043 closed the DB-tooling tranche and explicitly leaves runner liveness
outside DB durability. The 2026-05-12 `HarmonicFreezeDrill` readout proved the
omitted-target pattern: `rss_feeds` stayed stale while the positive peers
`greenhouse_jobs` and `ashby_jobs` produced `signals.created_at` rows after the
scheduled run start.

That proof is useful for re-enabling `HarmonicKeepAlive`, but it does not prove
Phase 5.2 durability, restore behavior, off-host ledger retention, or always-on
host suitability. A liveness re-enable needs a sibling decision record so the
runner contract can move without changing the DB durability policy.

On 2026-05-13, the morning `HarmonicKeepAlive` task ran successfully, but the
keepalive artifact was misleading: a date-only JSON artifact and a rolling
12-hour freshness check made older duplicate-only rows look like a fresh pass.
The incident showed that a live liveness claim must prove post-run DB progress,
not just that rows exist inside the threshold window.

On 2026-05-14, the next failure mode appeared: the scheduled collector ran and
found the positive peers, but inserted zero rows because the candidates were
already present or suppressed. The strict `--min-created-at` watchdog correctly
reported `no_post_run_rows`, but treating that as a failed daily heartbeat
confused runner execution with write-path proof.

## Decision

Runner liveness is governed by this ADR and
`docs/runbooks/runner-liveness-reenable.md`, not by the Phase 5.2 DB durability
runbook. This decision is a sibling to ADR-043 and keeps the liveness contract
outside DB durability.

The provisional `HarmonicKeepAlive` re-enable contract is:

- `Collectors=job_postings`
- `JOB_POSTING_DOMAINS` must be explicit and runner-scoped
- `WatchdogOperational=greenhouse_jobs,ashby_jobs`
- `WatchdogThresholdHours=12`
- `signals.created_at` is the freshness source of record
- generated keepalive artifacts use task-specific names:
  `YYYY-MM-DD-<TaskName>.json`
- live watchdog runs pass `--min-created-at` from the observed runner start and
  stay strict DB-only proof
- the runner writes a pre-monitor composite artifact, posts that artifact, and
  finalizes the local artifact after monitor delivery
- the daily heartbeat mode is named `daily_heartbeat`; deliberate proof and
  drill mode is named `strict_write_proof`
- `collector_health`, scheduler metadata, `state/collectors.json`, and JSON
  artifacts are corroboration only
- `rss_feeds` is excluded only from this provisional positive-peer contract; it
  remains the known omitted target from `HarmonicFreezeDrill`, not a permanent
  production policy

Monitor delivery is required before a live trial. The monitor may be
Healthchecks.io or a self-hosted compatible service, but it must reach a real
human alert recipient and the runner heartbeat must include composite verdict
fields plus post-run DB proof fields from the watchdog JSON, not only an alive
ping. Ping URLs are treated as secrets and are read from the host environment.
The proof payload must include `keepalive.mode`,
`keepalive.collector_exit_status`, `keepalive.db_progress_status`,
`keepalive.heartbeat_status`, `watchdog.min_created_at`, source-level
`required_after`, and source-level `stale_reason` when the watchdog fails.

Duplicate-only semantics are split by contract:

- In `daily_heartbeat`, if collection exits `0`, watchdog failures are
  exclusively `no_post_run_rows`, and monitor delivery succeeds, the final
  scheduler exit is `0` with `overall_status=WARN_DUPLICATE_ONLY`.
- In `strict_write_proof`, `no_post_run_rows` remains a hard failure and exits
  non-zero.

`freshness_watchdog.py` stays strict in both modes. The composite verdict layer
above it owns the daily-heartbeat warning policy.

`HarmonicFreezeDrill` is a sibling drill, not a concurrent proof path for normal
keepalive operation. It must not share the same artifact path or overlap the
live `HarmonicKeepAlive` trial schedule.

The host-mode gate remains separate:

- local-host re-enable may claim only provisional local-host liveness
- dedicated always-on host re-enable may claim broader runner availability only
  after host opportunity is proven
- neither mode closes Phase 5.2 durability

## Consequences

- The 2026-05-12 freeze drill can be cited as omitted-target evidence without
  rerunning an induced freeze.
- The first re-enable trial is a positive-peer run, not another RSS freeze.
- A successful trial can prove scheduled collection, composite verdicting, and
  monitor delivery for the selected host mode.
- Duplicate-only or pre-run rows fail strict write-proof, even when they are
  within the freshness threshold.
- Duplicate-only daily heartbeat runs are visible as
  `WARN_DUPLICATE_ONLY`; they do not prove fresh inserts.
- Restore drills, WAL streaming, external ledgers, sidecar handling, and
  host-storage benchmarks remain Phase 5.2 work.

## Acceptance Criteria

- The live task status is reverified immediately before any scheduler mutation.
- Live `HarmonicKeepAlive` registration requires an explicit host mode.
- Generated runner content uses explicit positive peers and no synthetic
  canary in place of real DB rows.
- Generated artifacts are task-specific and cannot be overwritten by sibling
  drill tasks.
- The monitor sink is configured, alert delivery to a human is verified, and
  the ping body includes composite verdict fields plus source-level freshness
  proof from `signals.created_at`.
- The pre-monitor composite artifact includes `collector_exit_status`,
  `db_progress_status`, `heartbeat_status`, and `pre_monitor_exit_code`.
- The finalized local artifact includes `monitor_delivery_status`,
  `overall_status`, `exit_code`, and `completed_at`.
- The watchdog uses `--min-created-at` for live proof and reports
  `no_post_run_rows` when a source has no row after that boundary.
- The monitor payload includes composite fields, `min_created_at`,
  `required_after`, and `stale_reason` so alert recipients can distinguish
  execution failure, stale DB proof, duplicate-only warning, and missing-source
  failures.
- Daily heartbeat may end in `WARN_DUPLICATE_ONLY`; strict write-proof keeps
  `no_post_run_rows` as a failure.
- `HarmonicFreezeDrill` does not overlap the live `HarmonicKeepAlive` trial.
- Any claim made after the trial is scoped to the chosen host mode.

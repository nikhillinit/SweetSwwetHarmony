# ADR 0004: Runner Liveness Re-Enable Contract

Status: Proposed
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
- `collector_health`, scheduler metadata, `state/collectors.json`, and JSON
  artifacts are corroboration only
- `rss_feeds` is excluded only from this provisional positive-peer contract; it
  remains the known omitted target from `HarmonicFreezeDrill`, not a permanent
  production policy

Monitor delivery is required before a live trial. The monitor may be
Healthchecks.io or a self-hosted compatible service, but it must reach a real
human alert recipient and the runner heartbeat must include post-run DB proof
fields from the watchdog JSON, not only an alive ping. Ping URLs are treated as
secrets and are read from the host environment.

The host-mode gate remains separate:

- local-host re-enable may claim only provisional local-host liveness
- dedicated always-on host re-enable may claim broader runner availability only
  after host opportunity is proven
- neither mode closes Phase 5.2 durability

## Consequences

- The 2026-05-12 freeze drill can be cited as omitted-target evidence without
  rerunning an induced freeze.
- The first re-enable trial is a positive-peer run, not another RSS freeze.
- A successful trial can prove scheduled collection and monitor delivery for
  the selected host mode.
- Restore drills, WAL streaming, external ledgers, sidecar handling, and
  host-storage benchmarks remain Phase 5.2 work.

## Acceptance Criteria

- The live task status is reverified immediately before any scheduler mutation.
- Live `HarmonicKeepAlive` registration requires an explicit host mode.
- Generated runner content uses explicit positive peers and no synthetic
  canary in place of real DB rows.
- The monitor sink is configured, alert delivery to a human is verified, and
  the ping body includes source-level freshness proof from `signals.created_at`.
- The trial records post-run `MAX(signals.created_at)` for `greenhouse_jobs`
  and `ashby_jobs` after the observed run start.
- Any claim made after the trial is scoped to the chosen host mode.

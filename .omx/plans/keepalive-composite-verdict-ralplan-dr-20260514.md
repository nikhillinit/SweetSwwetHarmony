# Final RALPLAN-DR: Keepalive Composite Verdict

## Scope

Plan the freshness-gate false-failure fix for daily `HarmonicKeepAlive`.
Preserve the existing DB watchdog contract and add a composite verdict layer
above it.

Do not touch:

- `signals.db`
- Task Scheduler live state
- generated keepalive artifacts
- `scripts/red-team-hybrid/_keepalive_daily.cmd`
- `state/collectors.json`
- Phase 5.2 durability or DB recovery work

Recommended implementation branch:

- `feat/keepalive-composite-verdict`

## Context

The 2026-05-14 daily keepalive run executed `job_postings`, found signals, and
inserted zero rows because the positive peers were already present or
suppressed. The strict `freshness_watchdog.py --min-created-at` proof then
failed with `no_post_run_rows` for `greenhouse_jobs` and `ashby_jobs`.

That is a false failure for daily heartbeat semantics, but it is still a real
failure for deliberate write-proof semantics.

## Principles

1. Keep `freshness_watchdog.py` strict and DB-only.
2. Make duplicate-only handling an explicit runner policy, not an implicit
   watchdog exception.
3. Separate pre-monitor execution truth from post-monitor delivery truth.
4. Keep monitor transport narrow: it sends the artifact it is given and returns
   transport success or failure.
5. Encode the policy in ADR, runbook, and contract tests together.

## Options Considered

### Option A: Keep current behavior everywhere

Reject. This preserves strictness but misclassifies duplicate-only daily runs as
liveness failures.

### Option B: Loosen `freshness_watchdog.py`

Reject. This would blur DB-proof semantics and make a DB-only component own
runner policy it cannot observe.

### Option C: Add a composite verdict layer above the unchanged watchdog

Choose. This preserves strict DB proof while allowing daily heartbeat to
classify duplicate-only runs as `WARN_DUPLICATE_ONLY`.

## Decision

Daily `HarmonicKeepAlive` uses composite semantics.

If:

- `collect` exits `0`,
- watchdog failure is exclusively `no_post_run_rows`, and
- monitor delivery succeeds,

then final scheduler exit is `0` with
`overall_status=WARN_DUPLICATE_ONLY`.

Strict write-proof and drill mode remain unchanged:

- `no_post_run_rows` is exit `1`.

This is an intentional policy change for the daily heartbeat only. Daily
heartbeat proves execution and observability, not guaranteed fresh inserts.

## Executable Plan

### 1. Add `keepalive_verdict.py`

Create `scripts/red-team-hybrid/keepalive_verdict.py` as a pure composer with
two phases: `compose` and `finalize`.

`compose` should:

- read collector exit plus watchdog JSON;
- write a pre-monitor artifact;
- include `mode`, `collector_exit_status`, `db_progress_status`,
  `db_progress_reason`, `heartbeat_status`, `pre_monitor_exit_code`,
  nested `watchdog`, and timestamps;
- perform no network I/O.

`finalize` should:

- update the same local artifact with `monitor_delivery_status`,
  `overall_status`, `exit_code`, and `completed_at`;
- perform no network I/O.

### 2. Rewire installer-generated runner

Update `scripts/red-team-hybrid/install_keepalive_task.ps1` so the generated
runner uses explicit exit captures:

1. Capture `collect` exit immediately after `run_pipeline.py collect`.
2. Run `freshness_watchdog.py` unchanged and persist its JSON output as DB-proof
   input.
3. Invoke `keepalive_verdict.py compose` before monitor send.
4. Invoke `keepalive_monitor_ping.py` with the pre-monitor artifact.
5. Capture monitor exit.
6. Invoke `keepalive_verdict.py finalize`.
7. Exit with the finalized composite exit code, not the raw watchdog exit.

Do not commit the generated `_keepalive_daily.cmd`.

### 3. Narrow `keepalive_monitor_ping.py`

Update `scripts/red-team-hybrid/keepalive_monitor_ping.py` so it accepts the
pre-monitor composite artifact instead of raw watchdog JSON.

Acceptance:

- The POST payload does not claim `monitor_delivery_status` for the same request.
- Any Healthchecks-style `/<exit-status>` suffix is derived from the composed
  pre-monitor verdict.
- The helper process exit reflects payload/build errors or transport
  success/failure.

### 4. Update ADR and runbook

Update:

- `docs/decisions/0004-runner-liveness-reenable.md`
- `docs/runbooks/runner-liveness-reenable.md`

Acceptance:

- Define `daily_heartbeat` and `strict_write_proof` as separate contracts.
- State that daily heartbeat may end in `WARN_DUPLICATE_ONLY` and exit `0`
  after successful monitor delivery.
- State that strict write-proof keeps `no_post_run_rows` as exit `1`.
- Document the two-phase artifact lifecycle: pre-monitor truth, then finalized
  local truth.
- Keep `freshness_watchdog.py` described as strict, DB-only, and not the owner
  of whole-runner liveness.

## Truth Table

| Scenario | Mode | Collector exit | Watchdog result | Monitor exit | Final status | Final exit |
|---|---|---:|---|---:|---|---:|
| All pass | `daily_heartbeat` or `strict_write_proof` | 0 | PASS | 0 | PASS | 0 |
| Duplicate-only | `daily_heartbeat` | 0 | FAIL, only `no_post_run_rows` | 0 | WARN_DUPLICATE_ONLY | 0 |
| Duplicate-only | `strict_write_proof` | 0 | FAIL, only `no_post_run_rows` | 0 | FAIL | 1 |
| Collector failed but DB pass | either | nonzero | PASS | any | FAIL | nonzero |
| DB threshold or missing fail | `daily_heartbeat` | 0 | FAIL, any reason other than pure `no_post_run_rows` | any | FAIL | 1 |
| DB threshold or missing fail | `strict_write_proof` | 0 | FAIL | any | FAIL | 1 |
| Monitor failed after pass | either | 0 | PASS or daily duplicate-only | nonzero | FAIL | nonzero |

Duplicate-only rule: every watchdog failure for operational sources must have
`stale_reason=no_post_run_rows`. Any mixed failure set is a hard `FAIL`.

## Tests

Run:

```powershell
pytest tests/scripts/test_freshness_watchdog.py tests/scripts/test_install_keepalive_task.py tests/scripts/test_keepalive_monitor_ping.py tests/scripts/test_keepalive_verdict.py tests/docs/test_runner_liveness_contract.py -q
```

Expected coverage:

- `tests/scripts/test_freshness_watchdog.py`
  - confirm no behavior change to strict `--min-created-at`.
- `tests/scripts/test_keepalive_verdict.py`
  - cover `daily_heartbeat` PASS;
  - cover `daily_heartbeat` duplicate-only WARN;
  - cover `strict_write_proof` duplicate-only FAIL;
  - cover collector-fail precedence;
  - cover mixed DB failures;
  - cover finalize behavior after monitor failure.
- `tests/scripts/test_install_keepalive_task.py`
  - verify generated runner captures collect exit, runs compose, sends
    pre-monitor artifact, captures monitor exit, finalizes, and exits on
    composite policy.
- `tests/scripts/test_keepalive_monitor_ping.py`
  - verify payload accepts pre-monitor composite artifact and does not author
    `monitor_delivery_status`.
- `tests/docs/test_runner_liveness_contract.py`
  - assert ADR/runbook mention `WARN_DUPLICATE_ONLY`, `daily_heartbeat`,
    `strict_write_proof`, pre-monitor/finalize flow, and unchanged
    `no_post_run_rows` strictness for write-proof mode.

## Done Criteria

- Daily duplicate-only run with collector success and monitor success exits `0`.
- Strict duplicate-only run exits `1`.
- Any collector failure, mixed DB failure, threshold failure, missing-source
  failure, or monitor failure exits nonzero.
- Watchdog remains unchanged as the DB source-of-record check.
- Monitor helper is transport-only.
- ADR, runbook, and tests encode the same semantics.
- No generated keepalive runner, live keepalive artifact, live scheduler state,
  `signals.db`, or `state/collectors.json` change is included in the slice.

## Suggested Commit Boundaries

1. `feat: add composite keepalive verdict helper`
   - `scripts/red-team-hybrid/keepalive_verdict.py`
   - `tests/scripts/test_keepalive_verdict.py`
2. `fix: route keepalive runner through composite verdict`
   - `scripts/red-team-hybrid/install_keepalive_task.ps1`
   - `scripts/red-team-hybrid/keepalive_monitor_ping.py`
   - coupled script tests
3. `docs: split heartbeat and write-proof liveness contracts`
   - ADR, runbook, and docs contract test

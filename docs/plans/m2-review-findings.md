# Findings: M2 Milestone Review — Critical Evaluation

## Date: 2026-02-12
## Context: Post-M3 (ed5fcd7 on main). User proposes 5 updates to sprint plan for M2.

---

## Verdict Summary

| # | User Claim | Verdict | Notes |
|---|-----------|---------|-------|
| 1 | M2 missing from plan doc | AGREE | Deliberate deferral, now due for addition |
| 2 | Trim already-done items | AGREE (with precision) | Must quantify exactly what exists |
| 3a | systemd ExecStartPost | PARTIAL PUSHBACK | ExecStartPost is wrong mechanism |
| 3b | API rate limiting | DEPRIORITIZE | Internal-only API, not public-facing |
| 3c | Prometheus endpoint | CONDITIONAL | Only if Prometheus is actually deployed |
| 3d | CI quality gate | STRONGEST AGREE | Highest-ROI item in M2 |
| 4 | Stabilization dependency | AGREE | Already documented, promote to hard gate |
| 5 | Measurable exit gates | AGREE | Well-formed, one caveat on Prometheus |

## Post-Review Corrections (from user feedback)

| # | Correction | Applied |
|---|-----------|---------|
| C1 | Metrics endpoint path inconsistent -- use `/api/v1/health/metrics` | YES -- fixed in task plan tests, gates, file list |
| C2 | curl is undeclared runtime dep -- switch to Python stdlib | YES -- `scripts/healthcheck_startup.py` uses `http.client` |
| C3 | CI gate needs explicit enforcement mechanism | YES -- M2.1.2 now specifies branch protection rule + check name |

---

## Finding 1: M2 Is Deliberately Absent, Not Accidentally Missing

The plan doc `2026-02-12-post-wave5-milestones.md` explicitly records the decision:
> "M3 before M2 | Batch publish has direct business ROI (deals reach CRM)"

And the progress doc lists the next step as:
> "Decide next milestone (M4 or M2)"

So M2 wasn't forgotten -- it was deferred. The user is correct that it's now time to
define it properly. But the framing should be "M2 was parked while M3 shipped; now
it needs a focused scope document" rather than "M2 was overlooked."

**Action:** Add M2 as the next phase in the sprint plan doc.

---

## Finding 2: What Already Exists (Precise Inventory)

The user correctly identifies that M2 should be gap-focused. Here's exactly what exists:

### Operator docs
- `docs/operator-guide.md` -- 160 lines. Covers: daily operations, cron setup,
  alert triage workflow, quality monitoring, database maintenance, feature flags,
  RBAC roles, dashboard access, troubleshooting. This is a solid operational runbook.
- `docs/architecture-overview.md` -- 162 lines. Exists and covers system structure.
- `docs/runbooks/feature-activation.md` -- Created in M1. Progressive activation
  runbook with 4 stages, rollback commands per stage.
- `docs/runbooks/migration-rollback.md` -- v35-v38 rollback procedures.
- `docs/runbooks/drift-*.md`, `spc-*.md`, `canary-*.md` -- 3 monitoring runbooks.

**Verdict:** Docs are NOT a gap. 5+ runbooks, operator guide, architecture overview
all exist. M2 should not include doc creation.

### Health endpoints
- `api/routers/health.py` -- 793 lines. Provides:
  - `GET /health/detailed` -- Full system health with all components
  - `GET /health/collectors` -- Collector status and last run times
  - `GET /health/database` -- Database stats and integrity
  - `GET /health/relationships` -- Email/LP staleness
  - `GET /health/jobs` -- Background job health
  - `GET /health/ops` -- Ops layer health with alert evaluation
  - `GET /health/ops/metrics` -- Full ops metrics snapshot (JSON)
  - `GET /health/ops/rules` -- Alert rules CRUD (GET/POST/PUT/DELETE)
  - `GET /health/ops/history` -- Metric snapshot history

**Verdict:** Health API is substantial (10+ endpoints, 793 lines). The ONLY gap
is output format (JSON, not Prometheus scrape format).

### In-process instrumentation
- `utils/instrumentation.py` -- 129 lines. Thread-safe counters + timers with
  `metrics.snapshot()` export. Explicitly says "no Prometheus/StatsD required."
  This is the data source that would feed a Prometheus endpoint.

### Middleware
- `api/middleware.py` -- 71 lines. Only `RequestIdMiddleware` and
  `ExceptionHandlerMiddleware`. No rate limiting, no CORS, no auth middleware.

### CI/CD
- `.github/workflows/thesis-eval.yml` -- Weekly thesis classification eval
  (schedule + manual dispatch). NOT a PR gate.
- `.github/workflows/discovery-pipeline.yml` -- Daily pipeline run (schedule +
  manual dispatch). NOT a PR gate.
- **Neither workflow runs the regression test suite on PRs.** The 490+ pass command
  is policy documentation, not automated enforcement.

### systemd service
- `scripts/sweetharmony.service` -- 57 lines. `Type=simple`, `Restart=always`,
  security hardening (NoNewPrivileges, ProtectSystem=strict), resource limits
  (2G memory, 80% CPU). NO health probe, NO ExecStartPost, NO Type=notify.

---

## Finding 3: Pushback on systemd ExecStartPost

The user suggests "systemd startup probe hardening (ExecStartPost)." This is the
wrong mechanism for the stated goal ("service fails fast on unhealthy start").

**Why ExecStartPost is wrong:**

With `Type=simple`, systemd considers the service "started" the instant `ExecStart`
begins. `ExecStartPost` runs immediately after -- BEFORE uvicorn has bound the port.
You'd need a retry loop:

```ini
ExecStartPost=/bin/sh -c 'for i in 1 2 3 4 5; do curl -sf http://localhost:8000/health && exit 0; sleep 2; done; exit 1'
```

This works but is fragile (hardcoded retries, shell dependency, curl dependency).

**Better alternatives (ranked):**

1. **`Type=notify` + `sd_notify(READY=1)`** -- The proper systemd mechanism. uvicorn
   supports `--lifespan on` and FastAPI's lifespan event fires after all startup
   completes. Send READY=1 at the end of lifespan startup. systemd waits until
   notified, with configurable `TimeoutStartSec`.
   - Requires: `python-systemd` or `sdnotify` package
   - Pro: Correct by construction, no polling
   - Con: Adds a dependency

2. **Wrapper script with health poll** -- A Python script that starts uvicorn, polls
   `/health`, and exits non-zero if unhealthy within timeout.
   - Pro: No new dependencies, works with `Type=simple`
   - Con: Extra script to maintain

3. **`ExecStartPost` with curl retry** (user's suggestion) -- Workable but least
   robust option.

**Recommendation:** Option 1 (`Type=notify`) if we accept the `sdnotify` dependency.
Option 2 if we want zero new deps. Option 3 only as a last resort.

---

## Finding 4: Rate Limiting Is Low Priority for Internal API

The user says "API rate limiting middleware (currently absent in api/middleware.py)."

**Fact check:** Correct. No rate limiting exists.

**Pushback:** This is an internal tool for Press On Ventures. The API runs on a
single VM behind whatever network the team uses. It's not public-facing. The threat
model for rate limiting is:

- Accidental infinite loops in client code
- Runaway batch operations
- Maybe: defense against compromised credentials hitting the API

For a 1-3 person team on an internal network, this is defense-in-depth, not a
critical gap. The existing `MemoryMax=2G` and `CPUQuota=80%` in systemd already
cap resource consumption at the process level.

**If we keep it:** Use `slowapi` (FastAPI-native, wraps `limits`). 5 lines of
middleware setup. The implementation cost is trivial, so I'm not saying drop it --
just deprioritize it behind CI gate and startup probe.

**Recommendation:** Move rate limiting from "must-have" to "should-have" in M2.
If time permits after the top 2, add it. Don't block the M2 gate on it.

---

## Finding 5: Prometheus Endpoint Has a Prerequisite

The user says "Prometheus/OpenMetrics endpoint (current /health/ops/metrics is JSON,
not scrape format)."

**Fact check:** Correct. `/health/ops/metrics` returns JSON via `snapshot.to_dict()`.
The `utils/instrumentation.py` module explicitly states "no Prometheus/StatsD required."

**Pushback:** Building a Prometheus-format endpoint only makes sense if Prometheus
is deployed to scrape it. The deployment target is "Systemd VM" (the plan doc says
"containerization is M5, not this sprint"). Is Prometheus running on this VM? If not,
you're building an endpoint for a consumer that doesn't exist.

**Two approaches:**

1. **Minimal: text/plain formatter only** -- Add a `/metrics` endpoint that emits
   OpenMetrics text format from the existing `metrics.snapshot()` data. ~30 lines.
   No Prometheus deployment. The endpoint exists and can be manually curled or
   consumed if/when Prometheus arrives.

2. **Full: Prometheus deployment + endpoint** -- Deploy Prometheus on the VM,
   configure scrape targets, build Grafana dashboards. This is M5-scope work (ops
   infrastructure), not M2.

**Recommendation:** Approach 1. Ship the text/plain formatter endpoint. Don't
deploy Prometheus in M2. The exit gate should be "/metrics returns valid OpenMetrics
text" not "Prometheus successfully scrapes."

---

## Finding 6: CI Quality Gate Is the Highest-ROI Item

The user says "CI quality gate workflow for test/regression command (your 490-pass
command is policy, not automated in .github/workflows)."

**Fact check:** Correct. Neither existing workflow runs on `pull_request` events.
Both are `schedule` + `workflow_dispatch` only.

**This is the most impactful M2 task because:**

1. It's the only item that prevents regressions from merging into main
2. The regression suite already exists and is documented (490+ tests)
3. It automates something currently done manually (and thus might get skipped)
4. It builds on existing infrastructure (GitHub Actions already configured)

**Implementation:**
```yaml
# .github/workflows/regression-gate.yml
name: Regression Gate
on:
  pull_request:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11', cache: 'pip' }
      - run: pip install -r requirements.txt
      - run: python -m pytest tests/api/ tests/integration/ tests/workflows/test_batch_publisher.py --tb=short
```

Plus branch protection rule: require `Regression Gate` to pass before merge.

**Recommendation:** This should be M2 task #1, not #4.

---

## Finding 7: Stabilization Dependency Already Documented

The user says "Add a strict dependency: complete the post-M3 stabilization
checkpoint first."

**Fact check:** The progress doc already contains a detailed stabilization
checkpoint (lines 133-145) with 3 verification steps:
1. Canary publish (1-2 items against Notion staging)
2. Log review (transport startup/shutdown messages)
3. Gate pass (only proceed after canary confirms)

**Verdict:** AGREE on promoting this to a hard prerequisite in the M2 plan.
Currently it's only in the progress file as a checklist item. It should be an
explicit Phase S0 (Stabilization) in the plan with its own gate.

---

## Finding 8: Proposed Task Priority Reordering

Based on the analysis above, the user's 4 tasks should be reordered by impact:

| Priority | Task | Justification |
|----------|------|--------------|
| 1 (MUST) | CI quality gate | Only item preventing regressions from shipping |
| 2 (MUST) | systemd startup probe | Prevents silent unhealthy starts (but use Type=notify, not ExecStartPost) |
| 3 (SHOULD) | OpenMetrics endpoint | Observability gap, but minimal scope (formatter only, no Prometheus deploy) |
| 4 (COULD) | API rate limiting | Defense-in-depth for internal API, lowest impact |

**Rationale:** CI gate protects ALL future work. Startup probe protects production
uptime. Metrics endpoint enables future monitoring. Rate limiting is insurance on
an internal API.

---

## Finding 9: Exit Gate Refinements

User's proposed gates with my annotations:

| Gate | User Version | My Revision |
|------|-------------|------------|
| Startup | "service fails fast on unhealthy start" | AGREE -- but test via sd_notify or wrapper, not ExecStartPost |
| Metrics | "/metrics scrape succeeds in Prometheus format" | REFINE -- "/metrics returns valid OpenMetrics text/plain" (no Prometheus deploy) |
| Rate limit | "rate-limit behavior tested (429 + headers)" | CONDITIONAL -- only if rate limiting makes the cut |
| CI | "CI blocks merges when core regression suite fails" | AGREE -- strongest gate, test with a failing PR |

**Additional gate I'd add:**
- "Zero new collection errors in `pytest --collect-only`" -- preserves the G0 baseline

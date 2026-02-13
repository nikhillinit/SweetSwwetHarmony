# Progress: M2 Ops Hardening Sprint

## Session: 2026-02-12
## Branch: main (ed5fcd7)

---

## Session Log

### 2026-02-12 -- Critical Review of User's M2 Proposal

**User proposed 5 updates. Evaluation:**

1. **M2 missing from plan doc** -- AGREED. M2 was deliberately deferred (M3 before
   M2 decision), now needs proper scope definition.

2. **Trim already-done items** -- AGREED with precision. Quantified existing assets:
   - `docs/operator-guide.md` (160 lines) -- not a gap
   - `docs/architecture-overview.md` (162 lines) -- not a gap
   - `api/routers/health.py` (793 lines, 10+ endpoints) -- not a gap
   - 5+ runbooks -- not a gap
   - Only gap: output format (JSON, not OpenMetrics)

3. **Top M2 tasks -- 3 pushbacks issued:**
   - systemd ExecStartPost: WRONG MECHANISM for Type=simple. Recommended
     health-check wrapper script (or future Type=notify). ExecStartPost runs
     before uvicorn binds port without retries.
   - API rate limiting: DEPRIORITIZED. Internal API, 1-3 person team, existing
     systemd resource caps. Moved to COULD-HAVE.
   - Prometheus endpoint: CONDITIONAL. No Prometheus deployed on VM. Scoped
     to text/plain formatter only, no Prometheus deployment in M2.
   - CI quality gate: STRONGEST AGREE. Highest-ROI item, reordered to #1.

4. **Stabilization dependency** -- AGREED. Promoted from progress-file checklist
   to hard Phase S0 prerequisite with explicit gate.

5. **Measurable exit gates** -- AGREED with MoSCoW categorization:
   - MUST: CI gate, startup probe, zero new collection errors
   - SHOULD: OpenMetrics endpoint
   - COULD: Rate limiting

**Files created:**
- `docs/plans/m2-review-findings.md` -- Detailed evidence + pushback
- `docs/plans/m2-task-plan.md` -- Revised M2 plan (4 phases + S0 prerequisite)
- `docs/plans/m2-progress.md` -- This file

### 2026-02-12 -- User Feedback: 3 Corrections Accepted

User approved overall direction + pushback/reprioritization. Three corrections applied:

1. **Metrics endpoint path** -- `/health/metrics` was wrong. Health router mounts at
   `/api/v1` (see `api/main.py:159`), so canonical path is `/api/v1/health/metrics`.
   Fixed in: task plan tests (M2.3.3), exit gate #4, files-to-modify, gate command.

2. **Startup probe curl dependency** -- Replaced `scripts/healthcheck-startup.sh`
   (bash + curl) with `scripts/healthcheck_startup.py` (Python stdlib `http.client`).
   Uses the venv Python already declared in systemd PATH. Zero runtime deps,
   directly testable via pytest.

3. **CI gate enforcement** -- M2.1.2 now specifies exact branch protection settings:
   required status check name (`Core Regression Suite`), "require up to date",
   optional "no bypass." Added `docs/runbooks/ci-regression-gate.md` to files-to-create.

**Decisions confirmed by user:**
- S0 as hard prerequisite: approved
- MoSCoW split (MUST/SHOULD/COULD): approved
- Rate limiting as COULD-HAVE: approved
- OpenMetrics formatter without Prometheus deployment: approved

### 2026-02-12 -- S0 Stabilization Gate: PASSED (dry-run)

**Canary sequence (dry-run only, no real Notion push):**
1. Seeded canary data: signal id=48, company_file, review id=1 (approved)
2. Started API with `DELIVERY_MODE=batch_publish` + Notion keys loaded
3. Authenticated as GP role via JWT
4. POST /api/v1/batches → 200, batch-20260213-073339-9fa544 (1 item)
5. GET /api/v1/batches/{id} → 200, items_hash=080a9ed428559ef6
6. POST /api/v1/batches/{id}/commit (dry_run=true) → 200, pending_count=1
7. Audit log: `batch_create` + `batch_commit_dry_run` entries confirmed
8. Startup log: `NotionConnector initialized (app-scoped)` confirmed
9. All canary data cleaned up (DB back to 47 signals baseline)

**Gate decision:** PASS — full code path works up to Notion push boundary.

---

## Next Steps
- [x] Complete S0 stabilization checkpoint (canary publish) — PASSED
- [ ] Begin M2.1 (CI gate)
- [ ] M2.2 (startup probe) after CI gate ships
- [ ] M2.3 (OpenMetrics) if time permits
- [ ] M2.4 (rate limiting) stretch goal

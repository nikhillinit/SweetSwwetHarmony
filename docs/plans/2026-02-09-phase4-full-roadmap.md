# Phase 4+ Full Roadmap v1.1.3 — Final Integrated

**Created:** 2026-02-09
**Revision:** v1.1.3 (integrated reviewer feedback — sequencing, security, ops hardening)
**Depends on:** Phase 3 (COMPLETE, PR #33)
**Duration:** ~12 weeks (Weeks 7-18)
**Estimate:** 170-220h (includes integration buffers + 20% contingency)
**Related:** task_plan_v1.1.md, phase4-findings.md

---

## Executive Summary

**Strategy shift from v1.1.1:** The original plan had a linear sub-phase sequence (4a→4b→4c→4d→4e). Reviews identified three critical gaps: (1) no platform contracts/security baseline before enabling UI actions, (2) entity resolution should precede hunter to establish identity shield, (3) canary/drift scaffolding should move earlier to baseline quality before adding new capabilities.

**v1.1.3 approach:** Wave-based delivery with explicit gates. Establish platform contracts + security first, deliver triage foundation, activate entity resolution shield before hunter scale-up, layer ACH on cleaner entity data, expand under strict gates.

---

## Non-Negotiable Architecture Decisions

### A1: Single-Writer Discipline
- Dashboard is a **dumb client** — never imports `storage/*` for mutations
- All writes go through FastAPI routers only
- WAL mode + retry/backoff for transient lock contention
- Streamlit reads via API, not direct DB access

### A2: API Contract-First (new Wave 0 task)
Before implementing 4a+ routers, define shared standards:
- Cursor-based pagination contract (for large triage lists)
- Uniform error envelope (`{"error": str, "code": str, "detail": dict}`)
- Idempotency keys on all mutation endpoints
- Optimistic concurrency via `updated_at` version checks
- OpenAPI examples + Pydantic v2 response schemas

### A3: Security Baseline is Blocking
Before enabling UI actions (approve/reject/merge/promote/commit):
- RBAC roles: `viewer` (read/search/comment), `operator` (approve/reject/defer), `admin` (merge/promote/publish)
- Signed operator identity on all state-changing actions
- Immutable audit events: `who`, `when`, `what`, `before/after`, `reason`, `correlation_id`
- At-rest protection posture review for secrets/keys

### A4: Run/Job Abstraction for Long-Running Workflows
Generalized pattern for hunter runs, canary scoring, entity resolution scans, ACH builds:
- `POST /runs` → returns `run_id` + status `queued`
- `GET /runs/{id}` → poll status (`queued` → `running` → `completed`/`failed`)
- `GET /runs/{id}/result` → retrieve output
- Store run metadata consistently across all workflow types
- UI uses single polling pattern (not 5 custom implementations)

### A5: Streamlit Pragmatic Decision
Keep Streamlit if triage throughput and latency goals are met with:
- `st.session_state` architecture for state management
- `st.data_editor` for batch edits (avoids per-row button reruns)
- API-side pagination (never pull full tables into Streamlit)
- **Trigger React/Vue migration** only if measured operator productivity/latency thresholds fail

---

## Wave Delivery Schedule (Risk-First, Value-Early)

```
Week 7:     Wave 0 — Platform Hardening & Contracts
Weeks 8-9:  Wave 1 — 4a Core Triage + 4b Deterministic ACH (CLI/API first)
Weeks 10-11: Wave 2 — 4d Shadow Entity Resolution + Canary Scaffold
Weeks 12-13: Wave 3 — 4c Hunter Sandbox (after identity shield)
Weeks 14-15: Wave 4 — Controlled Write Activation (merge + promote + bulk)
Weeks 16-18: Wave 5 — 4e Full Drift + Hardening + UX Polish + UAT
```

---

## Wave 0: Platform Hardening & Contracts (Week 7)

**Goal:** Cross-cutting foundations that every subsequent wave depends on.

| Task | Description | Files | Est. |
|------|-------------|-------|------|
| W0.1 | API contract design — pagination, error envelope, idempotency keys, versioning | `api/contracts.py`, `api/pagination.py` | 3h |
| W0.2 | RBAC foundation — role model, permission decorator, operator identity | `api/auth/rbac.py`, extend `api/auth/jwt_auth.py` | 4h |
| W0.3 | Immutable audit event log — unified event schema for all actions | `storage/audit_events.py` | 3h |
| W0.4 | Run/job abstraction — start/poll/result pattern for async workflows | `workflows/run_manager.py`, migration v35 `run_history` | 3h |
| W0.5 | Baseline instrumentation — latency counters, lock wait tracking, action failure rates | `utils/instrumentation.py` | 2h |
| W0.6 | Canary framework scaffold — golden set definition, re-score harness (CLI-only) | `monitoring/canary_checker.py` | 2h |
| W0.7 | Label taxonomy definition — operator labels vs eventual outcomes vs gold labels, lag windows | `docs/label-taxonomy.md`, `monitoring/label_definitions.py` | 2h |
| W0.8 | Migration/rollback runbook template | `docs/runbooks/migration-rollback.md` | 1h |
| W0.9 | Tests — contract tests, RBAC unit tests, audit event tests, run manager tests | `tests/api/test_contracts.py`, `tests/api/test_rbac.py`, etc. | 4h |

**Estimated total:** 24h

**Gate:** Contract tests pass. Auth checks pass. Baseline metrics report generated. Label taxonomy reviewed.

### Definition of Done
- **User-facing:** n/a (platform layer)
- **Data:** audit_events table created, run_history table created, label taxonomy documented
- **Reliability:** API contract tests enforce pagination/error/idempotency invariants
- **Security:** RBAC decorator tested, operator identity flows through all action endpoints

---

## Wave 1: 4a Core Triage + 4b Deterministic ACH (Weeks 8-9)

**Goal:** Read-only triage dashboard + single-item actions + deterministic ACH engine (CLI/API first, then UI).

**Key change from v1.1.1:** Ship in two releases — read-only first, then state-changing actions. ACH ships as CLI/API alongside triage (not delayed to separate wave).

### 4a Tasks

| Task | Description | Files | Est. |
|------|-------------|-------|------|
| 4a.1 | Triage API router — list (cursor-paginated), detail (with intelligence) | `api/routers/triage.py` | 3h |
| 4a.2 | Triage response DTOs — BFF models (stable schema, not internal store objects) | `api/routers/triage.py` | 1.5h |
| 4a.3 | Action endpoints — approve/reject/defer with RBAC + idempotency + optimistic concurrency | `api/routers/triage.py` | 2.5h |
| 4a.4 | Wire triage router into FastAPI app | `api/main.py` | 15m |
| 4a.5 | Fast Pass view — compact table with server-side pagination + filters sidebar | `dashboard/views/triage_fast.py` | 4h |
| 4a.6 | Deep Review view — intelligence panels + confirm dialogs for destructive actions | `dashboard/views/triage_detail.py` | 4h |
| 4a.7 | Batch Publish API — proxy to BatchPublisher with RBAC (`admin` role) | `api/routers/batch_publish.py` | 2h |
| 4a.8 | Batch Publish view — create/preview/commit/abort with confirmation dialog | `dashboard/views/batch_publish.py` | 3h |
| 4a.9 | Wire views into dashboard navigation | `dashboard/app.py` | 30m |
| 4a.10 | Tests — triage API (including concurrency, idempotency, RBAC) | `tests/api/test_triage_router.py` | 3h |
| 4a.11 | Tests — batch publish API | `tests/api/test_batch_publish_router.py` | 2h |
| 4a.12 | Tests — dashboard views (Streamlit mocks) | `tests/dashboard/test_triage_views.py` | 2h |

**4a estimated:** 28h

### 4b Tasks (Deterministic ACH — CLI/API first)

| Task | Description | Files | Est. |
|------|-------------|-------|------|
| 4b.1 | ACH data model — hypothesis catalog (IDs, names, definitions), evidence typing, scoring rubric | `intelligence/ach_matrix.py` | 3h |
| 4b.2 | Deterministic ACH builder — maps existing intelligence → cells, versioned + reproducible | `intelligence/ach_matrix.py` | 4h |
| 4b.3 | Migration v36 — ach_runs table (with `builder_version`, `inputs_hash`) | `storage/migrations/v36_ach_matrix.py` | 1h |
| 4b.4 | Tribunal narrator — template-based bull/bear/differentiators (cites evidence IDs) | `intelligence/tribunal.py` | 3h |
| 4b.5 | ACH API endpoint + CLI — `GET /triage/{id}/ach`, `triage ach <id>` | `api/routers/triage.py`, `run_pipeline.py` | 2h |
| 4b.6 | ACH summary columns in CSV export | `run_pipeline.py` export section | 1h |
| 4b.7 | Tests — ACH builder (including rubric validation, reproducibility) | `tests/intelligence/test_ach_matrix.py` | 3h |
| 4b.8 | Tests — tribunal narrator (evidence citation enforcement) | `tests/intelligence/test_tribunal.py` | 2h |

**4b estimated (Wave 1 portion):** 19h

**Wave 1 total:** 47h

**Gate:** Triage action failure <1%. ACH rubric agreement target met on 10+ test cases. Read-only dashboard stable.

### Definition of Done (4a)
- **User-facing:** Operator can view triage queue, filter/sort, open detail, approve/reject/defer single items, create/preview/commit batch
- **Data:** All actions create immutable audit events. Optimistic concurrency rejects stale updates.
- **Reliability:** List endpoint <500ms for 1000 items. Detail <200ms. Action idempotency tested.
- **Security:** RBAC enforced (viewer can't approve, operator can't batch commit, admin can do all)

### Definition of Done (4b)
- **User-facing:** `triage ach <id>` shows hypothesis grid + bull/bear case. CSV includes ACH columns.
- **Data:** ach_runs stored with `builder_version` + `inputs_hash`. Tribunal cites evidence IDs only (no fabrication).
- **Reliability:** ACH build <1s per company. Reproducible (same inputs → same matrix).

---

## Wave 2: 4d Shadow Entity Resolution + Canary Baseline (Weeks 10-11)

**Goal:** Start Phase G shadow soak, generate merge suggestions (read-only), baseline canary metrics before adding new capabilities.

**Key change from v1.1.1:** Moved entity resolution BEFORE hunter. Identity shield must be in place before proactive sourcing generates new entities.

| Task | Description | Files | Est. |
|------|-------------|-------|------|
| 4d.1 | Shadow mode — run Phase G alongside Phase 1a, log discrepancies (no mutations) | `storage/entity_identity_store.py` | 3h |
| 4d.2 | Shadow metrics — precision/recall vs labeled set, disagreement rate, top disagreement clusters | `workflows/entity_shadow_evaluator.py` | 2h |
| 4d.3 | Merge suggestion generator — Jaro-Winkler + shared aliases/domains, skip already-merged | `workflows/merge_suggester.py` | 4h |
| 4d.4 | Merge review API — list suggestions (read-only), dry-run merge preview (blast radius) | `api/routers/entities.py` (extend) | 3h |
| 4d.5 | Merge review dashboard — side-by-side comparison, shared evidence, blast radius display | `dashboard/views/entity_merge.py` | 4h |
| 4d.6 | Canary baseline run — execute golden set re-score, store results, CLI output | `monitoring/canary_checker.py` (extend W0.6) | 2h |
| 4d.7 | Canary API + drift alerts table | `api/routers/health.py` (extend), migration v37 `drift_alerts + canary_runs` | 2h |
| 4d.8 | Tests — shadow mode evaluation, merge suggestions, property-based tests for fuzzy matching | `tests/integration/test_entity_resolution_activation.py`, `tests/workflows/test_merge_suggester.py` | 5h |
| 4d.9 | Tests — canary harness + dashboard | `tests/monitoring/test_canary_checker.py` | 2h |

**Estimated total:** 27h

**Gate:** Shadow precision/consistency thresholds met on sampled review. Canary baseline established. Merge suggestions have <20% false positive rate on manual review.

### Definition of Done
- **User-facing:** Operator can view merge suggestions (read-only), see blast radius, view canary status
- **Data:** Shadow discrepancies logged. Merge candidates scored. Canary baseline stored.
- **Reliability:** Shadow mode adds <100ms latency to pipeline. Merge generator <5s for full corpus.
- **Security:** Merge review is read-only this wave. No write actions enabled yet.

---

## Wave 3: 4c Hunter Sandbox (Weeks 12-13)

**Goal:** Pattern-driven query generation with sandbox isolation, budget controls, and quality feedback loop. Runs AFTER identity shield is established.

**Key changes from v1.1.1:** Added bootstrap mode (manual seed targets), spend circuit breaker, negative-feedback loop, entity-normalized dedupe.

| Task | Description | Files | Est. |
|------|-------------|-------|------|
| 4c.1 | Pattern extractor — mine archetypes + exemplars + TP patterns for query templates | `intelligence/pattern_miner.py` | 3h |
| 4c.2 | Query generator — collector-specific queries with bootstrap mode (manual seed targets) | `intelligence/query_generator.py` | 4h |
| 4c.3 | Migration v38 — hunter_queries + hunter_results tables | `storage/migrations/v38_active_hunter.py` | 1h |
| 4c.4 | Hunter sandbox — isolated execution, results to `hunter_results` only, entity-normalized dedupe | `workflows/active_hunter.py` | 4h |
| 4c.5 | Cost circuit breaker — `MAX_DAILY_SPEND`, per-collector query caps, budget exhaustion alerts | `workflows/active_hunter.py` | 2h |
| 4c.6 | Quality scorer — exemplar similarity + operator feedback ("relevant/not relevant/already known") | `workflows/active_hunter.py` | 2h |
| 4c.7 | Negative feedback loop — auto-negative keywords from reviewer rejects | `intelligence/query_generator.py` | 2h |
| 4c.8 | CLI — `run_pipeline.py hunt generate|run|review|promote` (via run_manager) | `run_pipeline.py` | 3h |
| 4c.9 | Hunter API endpoints (uses run/job abstraction) | `api/routers/hunter.py` | 2h |
| 4c.10 | Hunter dashboard view — queries, results, feedback, promote UI | `dashboard/views/active_hunter.py` | 3h |
| 4c.11 | Sandbox safety tests — verify hunter NEVER writes to `signals` table | `tests/workflows/test_active_hunter.py` | 3h |
| 4c.12 | Tests — pattern miner, query generator, budget controls, feedback loop | `tests/intelligence/test_pattern_miner.py`, `tests/intelligence/test_query_generator.py` | 3h |
| 4c.13 | Tests — hunter API + CLI | `tests/api/test_hunter_router.py` | 2h |

**Estimated total:** 34h

**Gate:** Sandbox precision acceptable. Spend controls verified for 3+ consecutive test windows. No accidental `signals` writes in any test scenario.

### Definition of Done
- **User-facing:** Operator can generate queries, run in sandbox, review results, provide feedback
- **Data:** hunter_results isolated from signals. Budget tracked. Negative keywords persisted.
- **Reliability:** Budget circuit breaker enforced. Dedupe by canonical_key prevents duplicate processing.
- **Security:** Promotion requires `operator` role. Sandbox writes isolated (tested explicitly).

---

## Wave 4: Controlled Write Activation (Weeks 14-15)

**Goal:** Enable merge writes, hunter promotion, bulk triage actions — all behind flags + two-step confirmation.

| Task | Description | Files | Est. |
|------|-------------|-------|------|
| W4.1 | Enable entity merge writes — feature flag + two-step confirmation + RBAC (`admin`) | `storage/entity_identity_store.py`, `storage/merge_cascade.py` | 3h |
| W4.2 | Merge rollback capability — event-sourced lifecycle (proposed→approved→applied→rolled_back) | `storage/merge_cascade.py` (extend) | 3h |
| W4.3 | Merge rollback drills — verify cascade reversal on test data | `tests/integration/test_merge_rollback.py` | 2h |
| W4.4 | Enable hunter promotion — restricted to `operator` role, requires quality threshold | `workflows/active_hunter.py` | 2h |
| W4.5 | Bulk triage actions — multi-select approve/reject with confirmation + audit trail | `dashboard/views/triage_fast.py`, `api/routers/triage.py` | 3h |
| W4.6 | 4a hardening — advanced filters, UX responsiveness, cache intelligence panels server-side | `dashboard/views/triage_fast.py`, `dashboard/views/triage_detail.py` | 3h |
| W4.7 | Optional LLM enhancement for tribunal (evidence-cited, no fabrication, log prompts) | `intelligence/tribunal.py` | 2h |
| W4.8 | ACH dashboard view — hypothesis x evidence grid with clickable cells → evidence snippets | `dashboard/views/ach_matrix.py` | 3h |
| W4.9 | Wire ACH into Deep Review panel (tab) | `dashboard/views/triage_detail.py` | 1h |
| W4.10 | Tests — merge activation + rollback, promotion precision, bulk actions, LLM tribunal guardrails | Various test files | 5h |

**Estimated total:** 27h

**Gate:** Merge precision >=95% on sampled pairs. Rollback drill succeeds. Promotion precision threshold met. Bulk actions idempotent.

### Definition of Done
- **User-facing:** Admin can approve merges (with rollback), operator can promote hunter results, bulk triage works
- **Data:** Entity merges are event-sourced (reversible). Promotions tracked. LLM prompts/outputs logged.
- **Reliability:** Rollback restores pre-merge state. Bulk actions atomic per batch.
- **Security:** Merge requires `admin`. Promotion requires `operator`. LLM outputs audited.

---

## Wave 5: 4e Full Drift + Hardening + UAT (Weeks 16-18)

**Goal:** Complete drift monitoring, operational hardening, CI/CD integration, user acceptance testing.

### 4e Tasks

| Task | Description | Files | Est. |
|------|-------------|-------|------|
| 4e.1 | SPC-lite engine — FP rate, collector yield, quarantine regret, confidence calibration | `monitoring/spc_monitor.py` | 3h |
| 4e.2 | Expand canary strategy — stratify by archetype/collector/confidence band, grow golden set | `monitoring/canary_checker.py` | 2h |
| 4e.3 | Separate drift types — data drift, concept drift, model/heuristic drift (distinct alert types) | `monitoring/spc_monitor.py` | 2h |
| 4e.4 | Alert escalation + workflow — severity classification, ack/snooze, link to run, MTTA tracking | `monitoring/alert_escalation.py` | 3h |
| 4e.5 | Drift dashboard view — Altair trend charts, alert timeline, canary status, recommendations | `dashboard/views/drift_monitoring.py` | 4h |
| 4e.6 | Drift recommendation engine — suggest constraint changes, cite evidence | `monitoring/drift_recommendations.py` | 3h |
| 4e.7 | Confidence calibration — reliability diagrams, calibration error metrics | `monitoring/spc_monitor.py` | 2h |
| 4e.8 | CLI — `run_pipeline.py drift check|canary|alerts` | `run_pipeline.py` | 2h |
| 4e.9 | Tests — SPC, canary expansion, alert workflow, recommendations | `tests/monitoring/` | 4h |

**4e estimated:** 25h

### 4f Deployment & Platform Hardening Tasks

| Task | Description | Files | Est. |
|------|-------------|-------|------|
| 4f.1 | Migration downgrade tests — CI verifies v35-v38 rollback | `tests/storage/test_migration_rollback.py` | 3h |
| 4f.2 | Cross-phase integration suites — Suite M1 (Triage→ACH→Drift), Suite M2 (Hunter→Triage→ACH→Merge→Drift) | `tests/integration/test_cross_phase.py` | 4h |
| 4f.3 | Load/perf baselines — 10x data assumptions, latency budgets per endpoint | `tests/performance/test_phase4_slos.py` | 3h |
| 4f.4 | E2E UI test — triage→approve→batch publish→verify state (Playwright or similar) | `tests/e2e/test_triage_workflow.py` | 3h |
| 4f.5 | Incident runbooks + canary release policy | `docs/runbooks/` | 2h |

**4f estimated:** 15h

### 4g User Validation & Iteration Tasks

| Task | Description | Files | Est. |
|------|-------------|-------|------|
| 4g.1 | Checkpoint demo — structured walkthrough with stakeholders | n/a | 2h |
| 4g.2 | Feedback collection + refinement mini-sprint | Various | 4h |
| 4g.3 | Documentation — operator guide, architecture overview | `docs/` | 3h |

**4g estimated:** 9h

**Wave 5 total:** 49h

**Gate:** Cross-phase integration suite passes. Ops signoff. Load test within latency budgets. UAT feedback addressed.

### Definition of Done (4e)
- **User-facing:** Operator sees drift trends, canary pass/fail, actionable recommendations with ack/snooze
- **Data:** Drift types separated. Canary stratified. Alert lifecycle tracked (MTTA).
- **Reliability:** SPC alerts have <10% false alarm rate after tuning. Canary set >50 signals (stratified).
- **Security:** Alert ack requires operator role. Recommendations are advisory only.

---

## Migration Plan (4 new migrations)

| Version | Wave | Tables |
|---------|------|--------|
| v35 | W0 | `run_history` (generic run/job tracking) |
| v36 | W1 | `ach_runs` (hypothesis x evidence matrix) |
| v37 | W2 | `drift_alerts`, `canary_runs` |
| v38 | W3 | `hunter_queries`, `hunter_results` |

All migrations must have tested downgrade paths (Wave 5 4f.1).

---

## Dependency Graph

```
Phase 3 (COMPLETE)
  └─► Wave 0: Platform Contracts + Security + Canary Scaffold (Week 7)
        └─► Wave 1: 4a Triage + 4b ACH CLI/API (Weeks 8-9)
              ├─► Wave 2: 4d Shadow Entity Res + Canary Baseline (Weeks 10-11)
              │     └─► Wave 3: 4c Hunter Sandbox (Weeks 12-13)
              │           └─► Wave 4: Write Activation (Weeks 14-15)
              └─────────────────► Wave 5: 4e Drift + Hardening + UAT (Weeks 16-18)
```

**Critical path:** W0 → W1 → W2 → W3 → W4 → W5

---

## Estimated Hours Summary

| Wave | Content | Hours | Cumulative |
|------|---------|-------|------------|
| W0 | Platform hardening | 24h | 24h |
| W1 | 4a Triage + 4b ACH | 47h | 71h |
| W2 | 4d Shadow Entity + Canary | 27h | 98h |
| W3 | 4c Hunter Sandbox | 34h | 132h |
| W4 | Write Activation | 27h | 159h |
| W5 | 4e Drift + Hardening + UAT | 49h | 208h |
| **Total** | | **208h** | ~8-10 weeks |
| **Contingency (20%)** | | **+42h** | **250h max** |

---

## Test Strategy (Expanded)

### Standard (every wave)
1. Unit tests for core logic
2. API endpoint tests (FastAPI TestClient)
3. Dashboard view tests (Streamlit mocks)
4. Governance lint (no direct SignalStore construction)
5. Previous wave regression tests pass

### New Test Layers (from review)
6. **Contract tests** — API pagination, error envelope, idempotency invariants
7. **Property-based tests** — merge/fuzzy matching edge cases (hypothesis)
8. **Sandbox safety tests** — hunter NEVER writes to `signals` (explicit verification)
9. **Migration downgrade tests** — v35-v38 rollback in CI
10. **Cross-phase integration suites:**
    - Suite M1: Triage → ACH → Drift
    - Suite M2: Hunter → Triage → ACH → Entity Merge → Drift
11. **E2E UI test** — triage→approve→batch publish→verify state
12. **Perf baselines** — latency budgets per endpoint, 10x data assumptions

---

## Key Changes vs v1.1.1

| Area | v1.1.1 | v1.1.3 |
|------|--------|--------|
| **Sequencing** | Linear 4a→4b→4c→4d→4e | Wave-based: W0→W1→W2→W3→W4→W5 |
| **Entity resolution** | Week 13-14 (after hunter) | Week 10-11 (before hunter — identity shield first) |
| **Canary/drift** | Week 15-16 (all at end) | Scaffolded in W0, baselined in W2, full in W5 |
| **Security** | Not explicitly planned | Blocking W0 task: RBAC, audit events, operator identity |
| **API contracts** | Implicit | Explicit W0 task: pagination, error envelope, idempotency |
| **Run abstraction** | None (custom per workflow) | Generic run_manager for all async workflows |
| **Hunter guardrails** | Sandbox + budget | + Bootstrap mode, spend circuit breaker, negative feedback, entity dedupe |
| **ACH** | Week 9-10 standalone | CLI/API in W1 alongside triage, dashboard in W4 |
| **Merge safety** | Undo capability | Event-sourced lifecycle + dry-run preview + blast radius + rollback drills |
| **DoD** | Not defined | Per-wave: user-facing, data, reliability, security acceptance criteria |
| **Estimates** | 142-156h | 170-220h (208h planned + 20% contingency) |
| **Test layers** | 7 standard | 12 layers (+ contract, property, sandbox, downgrade, cross-phase, e2e, perf) |
| **New phases** | None | 4f (deployment hardening) + 4g (user validation) |

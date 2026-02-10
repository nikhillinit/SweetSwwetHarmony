# Task Plan: Wave 4 — Write Activation

## Goal
Enable controlled write operations — entity merges with rollback, hunter result promotion via API, bulk triage actions, and ACH matrix dashboard — all behind feature flags with two-step confirmation and full audit trails.

## Current Phase
Phase 1

## Phases

### Phase A: Entity Merge Activation (W4.1–W4.3) — 8 tasks
**Goal:** Enable merge writes with feature flag + RBAC + event-sourced lifecycle + rollback.

- [ ] A1: Add merge lifecycle DDL (v40 migration) — `merge_proposals` table with event-sourced states
- [ ] A2: Merge execution endpoint — `POST /entities/merge-suggestions/{id}/execute` with two-step (propose → apply)
- [ ] A3: Merge rollback endpoint — `POST /entities/merge-suggestions/{id}/rollback` with cascade reversal
- [ ] A4: Feature flag `MERGE_WRITES_ENABLED` (off/shadow/active) — gates merge execution
- [ ] A5: Entity merge dashboard view — two-step confirmation, blast radius preview
- [ ] A6: Tests — merge activation (feature flag gating, RBAC enforcement)
- [ ] A7: Tests — rollback drills (apply → verify → rollback → verify pre-merge state)
- [ ] A8: Tests — merge dashboard view (Streamlit mocks)
- **Status:** pending
- **Estimated:** 12h
- **Files:**
  - CREATE `storage/migrations/v40_merge_lifecycle.py`
  - EXTEND `api/routers/merge_review.py` (add execute + rollback endpoints)
  - CREATE `storage/merge_rollback.py` (rollback logic)
  - CREATE `dashboard/views/entity_merge.py`
  - CREATE `tests/integration/test_merge_rollback.py`
  - CREATE `tests/api/test_merge_write_endpoints.py`
  - CREATE `tests/dashboard/test_entity_merge_view.py`

### Phase B: Hunter Promotion API (W4.4) — 5 tasks
**Goal:** Expose hunter promotion via API with quality threshold gate and RBAC enforcement.

- [ ] B1: Create hunter API router — `POST /hunter/results/{id}/promote` wired to `hunter_promotion.py`
- [ ] B2: Quality threshold gate — configurable min confidence for promotion eligibility
- [ ] B3: Wire hunter router into FastAPI app (api/main.py)
- [ ] B4: Tests — promotion API (happy path, quality threshold rejection, RBAC, idempotency)
- [ ] B5: Tests — hunter router integration (feature flag gating)
- **Status:** pending
- **Estimated:** 5h
- **Files:**
  - CREATE `api/routers/hunter.py`
  - EXTEND `api/main.py` (add hunter router)
  - CREATE `tests/api/test_hunter_router.py`

### Phase C: Bulk Triage Actions (W4.5) — 5 tasks
**Goal:** Multi-select approve/reject/defer with confirmation dialog, partial success reporting, audit trail.

- [ ] C1: Bulk triage API endpoints — `POST /triage/bulk/{approve,reject,defer}` with `review_ids` list
- [ ] C2: Partial success reporting — atomicity per-item, aggregate report with success/fail counts
- [ ] C3: Dashboard multi-select — `st.data_editor` checkbox column + bulk action bar
- [ ] C4: Tests — bulk triage API (full success, partial success, RBAC, concurrency)
- [ ] C5: Tests — bulk triage dashboard view
- **Status:** pending
- **Estimated:** 7h
- **Files:**
  - EXTEND `api/routers/triage.py` (add bulk endpoints)
  - EXTEND `dashboard/views/triage_fast.py` (add multi-select + bulk bar)
  - CREATE `tests/api/test_bulk_triage.py`
  - EXTEND `tests/dashboard/test_triage_views.py` (add bulk tests)

### Phase D: ACH Dashboard + Triage Hardening (W4.6–W4.9) — 5 tasks
**Goal:** Interactive ACH matrix grid view, wire into Deep Review, triage UX polish.

- [ ] D1: ACH matrix grid view — hypothesis x evidence grid with color coding + cell click → evidence detail
- [ ] D2: Differentiator highlights + narrative view with clickable `[E{n}]` citations
- [ ] D3: Wire ACH matrix into triage_detail.py as enhanced tab content
- [ ] D4: Triage hardening — advanced filters, UX responsiveness
- [ ] D5: Tests — ACH dashboard view (rendering, interactions)
- **Status:** pending
- **Estimated:** 8h
- **Files:**
  - CREATE `dashboard/views/ach_matrix.py`
  - EXTEND `dashboard/views/triage_detail.py` (enhanced ACH tab)
  - EXTEND `dashboard/views/triage_fast.py` (filter/UX hardening)
  - CREATE `tests/dashboard/test_ach_matrix_view.py`

### Phase E: Optional LLM Tribunal Enhancement (W4.7) — 2 tasks
**Goal:** Optional LLM-powered tribunal narratives (evidence-cited, no fabrication, prompt logging).

- [ ] E1: LLM narrator mode — env flag `TRIBUNAL_LLM_MODE` (off/shadow/active), prompt template + output validation
- [ ] E2: Tests — LLM tribunal guardrails (citation validation, fabrication rejection, prompt logging)
- **Status:** pending
- **Estimated:** 4h
- **Files:**
  - EXTEND `intelligence/tribunal.py` (add LLM mode)
  - EXTEND `tests/intelligence/test_tribunal.py` (LLM guardrail tests)

### Phase F: Integration & Final Tests (W4.10) — 3 tasks
**Goal:** Cross-phase integration, regression verification, all-green test suite.

- [ ] F1: Cross-phase integration test — Merge → Triage → Promote → Verify state
- [ ] F2: Regression run — all existing 1553+ tests pass
- [ ] F3: Update planning docs, memory, and progress log
- **Status:** pending
- **Estimated:** 4h
- **Files:**
  - CREATE `tests/integration/test_wave4_cross_phase.py`

## Key Questions

1. **New migration needed?** Yes — v40 for `merge_proposals` table (event-sourced lifecycle). All other tables exist.
2. **Hunter router exists?** No — `api/routers/hunter.py` does NOT exist. Dashboard `hunter.py` calls API but no router serves it. Need to create.
3. **Merge review router writable?** Currently read-only (list suggestions, get detail). Need to add execute + rollback endpoints.
4. **Bulk triage atomicity model?** Per-item atomic (each review in own sub-transaction), aggregate report on partial success. NOT all-or-nothing.
5. **ACH interactive grid feasible in Streamlit?** Yes — `st.dataframe` with `on_select` or `st.data_editor` for clickable cells. Color via CSS.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| v40 migration for `merge_proposals` (NOT reusing merge_suggestions) | Event-sourced lifecycle (proposed→approved→applied→rolled_back) is a separate concern from auto-generated suggestions |
| Per-item atomicity for bulk triage | All-or-nothing fails UX when 1 of 20 has a concurrency conflict; partial success is standard batch pattern (see existing batch.py) |
| Hunter router as NEW file | Clean separation — hunter concerns (promote, feedback, budget) distinct from triage/entity concerns |
| Feature flag pattern: `off/shadow/active` | Consistent with existing `LLM_THESIS_MODE`, `ML_ENABLEMENT`, `HUNTER_ENABLEMENT` patterns |
| No separate `BULK_TRIAGE_ENABLED` flag | Bulk triage is just a multiplexer over existing single-item actions; RBAC via `Permission.BULK_TRIAGE` is sufficient |
| Rollback via reverse cascade (NOT event replay) | Simpler to implement — store before/after snapshots in `merge_proposals`, reverse specific operations |
| Phase E (LLM tribunal) is optional/deprioritized | Core value is in merge + promote + bulk (Phases A-C); LLM enhancement has highest risk and lowest priority |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |

## Build Order (Risk-First)

```
Phase A (merge writes) ─────────► Phase F (integration)
Phase B (hunter promotion API) ──► Phase F
Phase C (bulk triage) ───────────► Phase F
Phase D (ACH dashboard) ────────► Phase F
Phase E (LLM tribunal) ─────────► Phase F (optional)
```

Phases A–E are independent and can be worked in any order.
**Recommended sequence:** A → B → C → D → E → F
- A is highest risk (new DDL, rollback complexity)
- B is lowest risk (wiring existing `hunter_promotion.py` to API)
- C and D are moderate risk (UI + API extensions)
- E is optional (LLM integration, deprioritize if behind schedule)

## Existing Infrastructure (Ready to Use)

| Component | File | Status |
|-----------|------|--------|
| `merge_entities()` | `storage/entity_identity_store.py` | COMPLETE |
| `cascade_merge()` | `storage/merge_cascade.py` | COMPLETE |
| `promote_hunter_result()` | `workflows/hunter_promotion.py` | COMPLETE (CLI-only) |
| Single triage actions | `api/routers/triage.py` | COMPLETE |
| ACH builder + tribunal | `intelligence/ach_matrix.py`, `intelligence/tribunal.py` | COMPLETE |
| ACH API endpoints | `api/routers/triage.py` (GET/POST ach) | COMPLETE |
| ACH basic tab in Deep Review | `dashboard/views/triage_detail.py` | COMPLETE |
| Merge suggestions (read-only) | `api/routers/merge_review.py` | COMPLETE |
| Hunter dashboard view | `dashboard/views/hunter.py` | COMPLETE |
| RBAC framework | `api/auth/rbac.py` | COMPLETE |
| Audit events | `storage/audit_events.py` | COMPLETE |
| Idempotency infrastructure | `api/contracts.py` | COMPLETE |

## Notes
- Current branch: `feature/wave3-hunter-sandbox` — need to create `feature/wave4-write-activation` from `main`
- v39 is latest migration — v40 next
- 1553+ existing tests — regression baseline
- No hunter API router exists yet — dashboard's hunter view calls endpoints that don't exist as a router
- `Permission.ENTITY_MERGE`, `Permission.HUNTER_PROMOTE`, `Permission.BULK_TRIAGE` all exist in RBAC

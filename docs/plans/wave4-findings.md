# Wave 4 Write Activation — Findings

**Created:** 2026-02-10
**Task Plan:** `docs/plans/2026-02-10-wave4-write-activation.md`

---

## Finding 1: Entity Merge — Current Infrastructure

### Merge Engine (COMPLETE)
- `EntityIdentityStore.merge_entities(from_id, to_id, reason, tx)` — deterministic lexmin winner
- `cascade_merge(store, winner, loser, reason, actor, tx)` — 4-step cascade (reviews → signals → company_files → audit)
- Both accept optional `tx` param to join caller's transaction

### Merge Review Router (READ-ONLY)
- `api/routers/merge_review.py` — list suggestions (cursor-paginated) + detail with blast radius
- NO execute or rollback endpoints
- `Permission.ENTITY_MERGE` exists, granted to `Role.GP` only

### Missing for Wave 4
- **No `merge_proposals` table** — need event-sourced lifecycle tracking
- **No execute endpoint** — need `POST /entities/merge-suggestions/{id}/execute`
- **No rollback endpoint** — need `POST /entities/merge-suggestions/{id}/rollback`
- **No rollback logic** — `cascade_merge()` is one-way; need reverse cascade
- **No feature flag** — need `MERGE_WRITES_ENABLED` env var
- **No dashboard** — need `dashboard/views/entity_merge.py`

---

## Finding 2: Hunter Promotion — Current Infrastructure

### Promotion Logic (COMPLETE)
- `workflows/hunter_promotion.py` — `promote_hunter_result()` with full transactional flow
- Idempotency pre-check → BEGIN IMMEDIATE → validate → temporal race guard → INSERT signal → UPDATE result → audit
- Returns `PromotionResult` dataclass

### Hunter Dashboard (COMPLETE)
- `dashboard/views/hunter.py` — full UI with feedback + promote buttons
- Calls `/hunter/results/{id}/promote` endpoint (which doesn't exist as a router!)

### API Router (MISSING)
- **No `api/routers/hunter.py`** — zero hunter references in `api/routers/`
- Hunter router NOT mounted in `api/main.py`
- Dashboard calls endpoints that presumably return errors currently

### Quality Threshold
- No min confidence gate exists in `promote_hunter_result()`
- Hunter results have `confidence_score` and `thesis_fit_score` fields available

---

## Finding 3: Bulk Triage — Current Patterns

### Single-Item Pattern (reference for bulk)
```python
# api/routers/triage.py — _execute_triage_action()
# 1. Idempotency pre-check
# 2. BEGIN IMMEDIATE
#    a. Fetch review, validate
#    b. Optimistic concurrency (updated_at)
#    c. Validate transition
#    d. Update status
#    e. Audit event
#    f. Store idempotency
# 3. COMMIT
```

### Batch Publish Pattern (reference for bulk triage)
- `api/routers/batch.py` — create → preview → commit/abort
- Uses TOCTOU guard via `items_hash`
- Two-step confirmation

### RBAC
- `Permission.BULK_TRIAGE` exists, granted to `Role.GP` (admin) only
- Single-item actions use `TRIAGE_APPROVE`, `TRIAGE_REJECT`, `TRIAGE_DEFER`

### Dashboard
- `dashboard/views/triage_fast.py` — single-item selection, no multi-select
- 212 lines, row-by-row quick actions

---

## Finding 4: ACH Dashboard — Current State

### ACH Engine (COMPLETE)
- `intelligence/ach_matrix.py` — 5 hypotheses (H1-H5), 14 evidence types (E1-E14)
- `ACHMatrix` dataclass with cells, hypothesis_scores, top_hypothesis
- `ACHBuilder.build(company_id, db)` — deterministic, no LLM

### Tribunal (COMPLETE)
- `intelligence/tribunal.py` — template-based bull/bear narratives
- Citation format `[E{n}]` validated against available evidence
- `TribunalSummary` with bull_summary, bear_summary, differentiators

### ACH in Triage Detail (BASIC)
- `dashboard/views/triage_detail.py` lines 143-180 — `_render_ach_tab()`
- Shows: top_hypothesis, top_score, bull_summary, bear_summary, differentiator_count
- Rebuild button available
- **No matrix grid view** — just summary text

### ACH API (COMPLETE)
- `GET /triage/{review_id}/ach` — cached ACH or 404
- `POST /triage/{review_id}/ach/rebuild` — build fresh, store, return

### Missing for Wave 4
- Interactive hypothesis x evidence grid (color-coded cells)
- Clickable cells → evidence detail panel
- Differentiator visual highlights
- Narrative view with clickable `[E{n}]` citations

---

## Finding 5: Feature Flags — Existing Pattern

| Flag | Values | Pattern |
|------|--------|---------|
| `LLM_THESIS_MODE` | off, shadow, active | `os.environ.get("LLM_THESIS_MODE", "off")` |
| `ML_ENABLEMENT` | disabled, shadow, live | `os.environ.get("ML_ENABLEMENT", "disabled")` |
| `DELIVERY_MODE` | staging_only, manual_publish, batch_publish, auto_publish | String enum |
| `HUNTER_ENABLEMENT` | disabled, shadow, active | Same pattern |

**Convention:** String enums with sensible defaults. Shadow = logging/metrics only. Active = real writes.

**For Wave 4:** `MERGE_WRITES_ENABLED` should follow same `disabled/shadow/active` pattern.
- `disabled` (default): merge endpoints return 403 "feature disabled"
- `shadow`: log merge plan but don't execute
- `active`: execute merge with full cascade

---

## Finding 6: Migration State

- **Latest migration:** v39 (`active_hunter`)
- **Next available:** v40
- **Tables from v35-v39:** audit_events, run_history, ach_analyses, shadow_entity_runs, merge_suggestions, canary_runs, drift_alerts, hunter_queries, hunter_results, hunter_budget, hunter_budget_transactions, hunter_negative_keywords

### v40 Schema (proposed: `merge_proposals`)
```sql
CREATE TABLE IF NOT EXISTS merge_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_id INTEGER NOT NULL,           -- FK to merge_suggestions
    entity_a_company_id TEXT NOT NULL,
    entity_b_company_id TEXT NOT NULL,
    winner_company_id TEXT NOT NULL,
    loser_company_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',   -- proposed, approved, applied, rolled_back, rejected
    reason TEXT,
    proposed_by TEXT NOT NULL,
    proposed_at TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    applied_at TEXT,
    rolled_back_at TEXT,
    before_snapshot TEXT,                      -- JSON: pre-merge state for rollback
    after_snapshot TEXT,                       -- JSON: post-merge state for verification
    cascade_report TEXT,                       -- JSON: cascade_merge() return value
    updated_at TEXT NOT NULL,
    UNIQUE(suggestion_id, status)              -- prevent duplicate proposals for same suggestion
);
```

---

## Finding 7: Test Patterns

### Existing Test Infrastructure
- Streamlit mocks: `MockSessionState(dict)` + `_make_ctx_manager()` for st.tabs/columns
- RBAC tests: `require_permission()` decorator tested per role
- Idempotency tests: same key twice → cached response
- Optimistic concurrency: simulate concurrent updates → 409
- Feature flag tests: `monkeypatch.setenv()` / `monkeypatch.delenv()`
- Transaction tests: `PRAGMA foreign_keys = OFF` for standalone

### Key Test Files (existing)
- `tests/storage/test_merge_cascade.py` — 16 tests
- `tests/dashboard/test_triage_views.py` — 16 tests
- `tests/api/test_triage_router.py` — exists
- `tests/api/test_rbac.py` — exists
- `tests/workflows/test_hunter_promotion.py` — exists

---

## Finding 8: API App Structure

### Router Registration (api/main.py)
```python
app.include_router(auth.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(entities.router, prefix="/api/v1")
app.include_router(actions.router, prefix="/api/v1")
app.include_router(companies.router, prefix="/api/v1")
app.include_router(public.router, prefix="/api/v1")
app.include_router(scheduler.router, prefix="/api/v1")
app.include_router(triage.router, prefix="/api/v1")
app.include_router(batch.router, prefix="/api/v1")
app.include_router(merge_review.router, prefix="/api/v1")
app.include_router(canary.router, prefix="/api/v1")
# MISSING: hunter router
```

### Pattern for New Routers
1. Create `api/routers/hunter.py` with `router = APIRouter(prefix="/hunter", tags=["hunter"])`
2. Add `from api.routers import hunter` in main.py
3. Add `app.include_router(hunter.router, prefix="/api/v1")`

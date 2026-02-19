# Task Plan: UI Polish & Gap Remediation

**Branch:** `feature/ui-polish-safe-sprint`
**Date:** 2026-02-18
**Goal:** Identify and fix all remaining UI issues — bugs, design gaps, and polish items — before this branch merges to main.

---

## Phase 0: Audit & Inventory (RESEARCH ONLY)
**Status:** `complete`

Explored all 15 dashboard views, API routers, components, tests, and the findings.md from the prior hardening review.

---

## Phase 1: Fix Confirmed Bugs (HIGH PRIORITY)
**Status:** `complete`

### 1A. Health API — `check_health()` method does not exist
- **File:** `api/routers/health.py:174`
- **Bug:** Calls `await monitor.check_health()` — method doesn't exist on `SignalHealthMonitor`
- **Fix:** Change to `await monitor.generate_report()`, call `.to_dict()` on the dataclass result, fix anomaly key from `"message"` to `"description"`
- **Impact:** Health endpoint silently returns "Failed to check" instead of actual signal health data
- **Tests:** `tests/api/test_health_hardening.py`

### 1B. Inbox page uses raw `httpx.Client` instead of `APIClient`
- **File:** `dashboard/inbox_page.py:40-42`
- **Bug:** Creates standalone `httpx.Client(base_url=API_BASE_URL)` bypassing centralized `APIClient` error handling, auth headers, and response normalization
- **Fix:** Replace with `APIClient()` usage matching all other views
- **Impact:** Inbox page has inconsistent error handling compared to the 14 other views

---

## Phase 2: Design & UX Polish (MEDIUM PRIORITY)
**Status:** `complete`

### 2A. Triage table — no column headers
- **File:** `dashboard/views/triage_fast.py:186`
- **Gap:** Table rows render via `st.columns()` with data but no header row
- **Fix:** Add a header row above the loop with labels: Select | Company | Confidence | Signals | Source | Category | Actions

### 2B. Batch publish items — no column headers
- **File:** `dashboard/views/batch_publish.py:106`
- **Fix:** Add header row: Company | Confidence | Key | Status

### 2C. Hunter queries — no column headers
- **File:** `dashboard/views/hunter.py:131`
- **Fix:** Add header row: Query | Collector | Status | Results | Cost

### 2D. Hunter results — no column headers
- **File:** `dashboard/views/hunter.py:192`
- **Fix:** Add header row: Company | Confidence | Thesis | Status | Actions

### 2E. Active batches — no column headers
- **File:** `dashboard/views/batch_publish.py:191`
- **Fix:** Add header row: Batch ID | Status | Items | Pushed | Created

### 2F. Triage row company button styling
- **File:** `dashboard/views/triage_fast.py:198`
- **Gap:** Company name rendered as `st.button()` — looks like a generic Streamlit button, not a clickable row
- **Enhancement:** Could use a markdown link-styled approach or caption with callback

---

## Phase 3: Consistency & Hardening (LOWER PRIORITY)
**Status:** `pending`

### 3A. `is_error()` / `error_msg()` adoption inconsistency
- **Files:** `batch_publish.py`, `hunter.py` use raw `result.get("error")` checks
- **Gap:** `triage_fast.py` correctly uses centralized `is_error()` / `error_msg()` helpers
- **Fix:** Adopt `is_error()` / `error_msg()` in batch_publish and hunter views for consistency

### 3B. Drift monitoring `import Altair` guard
- **File:** `dashboard/views/drift_monitoring.py`
- **Check:** Verify `altair` import has a fallback or is in requirements

### 3C. Starwatcher feature flag
- **File:** `dashboard/app.py:71`
- **Status:** Gated behind `STARWATCHER_ENABLED` env var — currently not in `.env`
- **Decision:** Intentional (feature flag for gradual rollout) — no action needed

---

## Phase 4: Test Coverage for Fixes
**Status:** `pending`

### 4A. Health API fix — regression test
- Verify existing tests in `tests/api/test_health_hardening.py` cover the fix
- Add test for signal_monitor component returning actual anomaly data

### 4B. Inbox page — verify after refactor
- Check `tests/dashboard/` for inbox tests; add smoke test if missing

### 4C. Column header rendering — no test needed
- Pure UI presentation; manual verification sufficient

---

## Decisions Log

| Decision | Rationale | Date |
|----------|-----------|------|
| Skip v2.0 null-guard sweep | findings.md proved it unnecessary — API client never returns None | 2026-02-18 |
| Skip `inspect.iscoroutinefunction()` | `generate_report()` IS async def — check is unnecessary | 2026-02-18 |
| Skip test isolation changes | Tests already use `tempfile.mkstemp()` — no contamination risk | 2026-02-18 |
| Column headers = quick wins | All 5 views share the pattern; headers improve usability significantly | 2026-02-18 |

---

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |

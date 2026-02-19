# Findings: UI Audit — Gap Analysis & Design Review

**Date:** 2026-02-18
**Context:** Comprehensive audit of all 15 dashboard views, API layer, and components on `feature/ui-polish-safe-sprint`.

---

## Architecture Summary

| Layer | Technology | Files |
|-------|-----------|-------|
| Dashboard | Streamlit (Python) | `dashboard/app.py` + 13 views |
| API | FastAPI | `api/main.py` + 14 routers |
| Visualization | HTML5 Canvas (Starwatcher v9.1.6) | `dashboard/views/starwatcher.py` |
| CSS | Custom inline (~900 lines) | `dashboard/app.py:138-1050` |
| Fonts | Inter Bold + Poppins | Google Fonts CDN |
| Auth | JWT (session state) | `dashboard/api_client.py` |

---

## Bug Findings

### BUG-1: Health API — Non-existent method call (CONFIRMED)
- **Severity:** High
- **Location:** `api/routers/health.py:174`
- **Issue:** `await monitor.check_health()` — `SignalHealthMonitor` has no `check_health()` method
- **Correct call:** `await monitor.generate_report()`
- **Secondary issue:** Line 179 calls `.get("anomalies")` on a dataclass (not a dict) — needs `.to_dict()` first
- **Tertiary issue:** Line 186 reads `anomaly.get("message")` but `SignalAnomaly.to_dict()` uses key `"description"`
- **Current behavior:** Silently caught by `except Exception`, returns "Failed to check" status
- **Source:** First identified in prior findings.md review (Contention 2)

### BUG-2: Inbox page bypasses APIClient
- **Severity:** Medium
- **Location:** `dashboard/inbox_page.py:40-42`
- **Issue:** Uses raw `httpx.Client(base_url=API_BASE_URL)` instead of `APIClient()`
- **Impact:** No centralized error handling, no auth headers, inconsistent response format
- **All other 14 views** use `APIClient()` — this is the only outlier

---

## Design Gap Findings

### GAP-1: Missing column headers on 5 data tables
All five views use `st.columns()` with data rows but no header row:

| View | File:Line | Columns Shown |
|------|-----------|---------------|
| Triage Fast | `triage_fast.py:186` | Select, Company, Confidence, Signals, Source, Category, Actions |
| Batch Preview | `batch_publish.py:106` | Company, Confidence, Key, Status |
| Batch Active | `batch_publish.py:191` | Batch ID, Status, Items, Pushed, Created |
| Hunter Queries | `hunter.py:131` | Query, Collector, Status, Results, Cost |
| Hunter Results | `hunter.py:192` | Company, Confidence, Thesis, Status, Actions |

### GAP-2: Error handling pattern divergence
- `triage_fast.py` uses centralized `is_error()` / `error_msg()` helpers (good)
- `batch_publish.py` and `hunter.py` use raw `.get("error")` checks (legacy pattern)
- Not a bug, but inconsistency adds maintenance burden

### GAP-3: Triage company name as button
- `triage_fast.py:198` — company name rendered as `st.button()` for drill-down
- Looks like a generic Streamlit button (not obviously clickable as a navigation link)
- Enhancement: Could use a markdown link-styled approach or caption with callback

---

## What's Already Good (No Changes Needed)

| Area | Assessment |
|------|-----------|
| **Brand CSS** | 900 lines of custom CSS with Inter Bold + Poppins. Press On palette (dark/beige/white) is cohesive and editorial. |
| **Empty states** | All views have user-friendly empty state messages with guidance. |
| **Error guards** | API client returns `{"error": True, ...}` on failure — never None. All views handle this. |
| **Pagination** | Cursor-based with history stack for "Previous". Consistent across triage, hunter, results. |
| **Loading states** | `st.spinner()` used for all async operations. |
| **Status colors** | 8-color palette mapped to Notion statuses. Consistent across views. |
| **Feature flags** | Starwatcher gated behind `STARWATCHER_ENABLED`. Bulk triage returns 423 when disabled. |
| **Idempotency** | All write actions use UUID4 idempotency keys. |
| **Cache busting** | Session state counters invalidate `@st.cache_data` after mutations. |
| **Test coverage** | 10 dashboard test files, 23 API test files, 51 Starwatcher adapter tests. |

---

## Contention Review (from prior findings.md)

| Contention | Verdict | Action |
|-----------|---------|--------|
| AttributeError trap (API returns None) | INVALID | No action — API never returns None |
| `await` signature risk | INVALID (different bug) | Fix BUG-1 instead |
| Health endpoint 500 risk | PARTIALLY VALID | Fix BUG-1 (method + key + dict) |
| Pytest SQLite contamination | INVALID | No action — tests use temp DBs |

---

## Priority Matrix

| Priority | Item | Effort | Impact |
|----------|------|--------|--------|
| P0 | BUG-1: Health API fix | 15 min | Signal health data visible in dashboard |
| P1 | BUG-2: Inbox APIClient refactor | 20 min | Consistent error handling across all views |
| P1 | GAP-1: Column headers (5 tables) | 15 min | Immediate usability improvement |
| P2 | GAP-2: Error helper consistency | 10 min | Code maintenance |
| P3 | GAP-3: Triage button styling | 5 min | Minor UX polish |

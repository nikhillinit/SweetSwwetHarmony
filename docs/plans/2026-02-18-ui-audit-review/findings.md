# UI/UX Audit Report — Validated Findings

## Source Reports
- **Comprehensive UI/UX & System Response Audit** (Feb 19, 2026)
- **UI/UX Health Evaluation: SweetSwwetHarmony (Main Branch)** (Feb 19, 2026)
- Tested on `main` branch with Playwright + manual evaluation
- Report's Overall Grade: **D+**

---

## Claim Validation Summary

| # | Report Claim | Verdict | Severity | Notes |
|---|-------------|---------|----------|-------|
| 1 | `/api/v1/batches` failing (60% success) | **FALSE** | N/A | Rate limiting (20 req/60s write tier) working as designed |
| 2 | `/api/v1/entities` failing (0% success) | **FALSE** | N/A | Same rate limiter; test tool exceeded quota |
| 3 | Navigation requires JS workaround | **FALSE** | N/A | No CSS hides radio inputs; 14/14 work via label click |
| 4 | "Run Now" provides no feedback | **FALSE** | N/A | Code has `st.spinner`, `st.success`, `st.error`, `st.rerun` |
| 5 | URL Profiler button stays disabled | **MISCHARACTERIZED** | N/A | Intentional `disabled=not url` form validation |
| 6 | Session not persisted on reload | **TRUE** | P0 | Auth stored only in `st.session_state` (ephemeral) |
| 7 | Monitoring uses dark theme | **TRUE** | P1 | `monitoring_page.py:34-38` injects `#0a0a0a` background |
| 8 | Monitoring onboarding text misplaced | **TRUE** | P2 | Fallback to "Pipeline" dict entry; `app.py:1492` |
| 9 | Signals blank page on backend down | **TRUE** | P1 | No try-except around `load_signals()`; `app.py:2350-2351` |
| 10 | Cost Analysis technical error msg | **TRUE (expected)** | P3 | `cost_analysis.py:103` — proper error handling exists |
| 11 | Ops Monitoring technical error msg | **TRUE (expected)** | P3 | `ops_health.py:51` — proper error handling exists |
| 12 | Drift Monitoring "Unknown error" | **TRUE (expected)** | P2 | `drift_monitoring.py:90` — missing "message" in response |
| 13 | Keyboard login fails (Tab+Enter) | **TRUE (framework)** | P2 | Streamlit focus management limitation |
| 14 | 14/15 inputs unlabeled (ARIA) | **TRUE (framework)** | P2 | Streamlit generates inputs without labels by default |
| 15 | Color contrast fails WCAG AA | **TRUE (framework)** | P2 | Streamlit default + custom CSS contrast ratios ~1.44 |
| 16 | Not responsive on mobile | **TRUE (framework)** | P3 | Internal tool; Streamlit sidebar doesn't collapse |
| 17 | Touch targets < 44px on mobile | **TRUE (framework)** | P3 | Streamlit's 28px buttons are below WCAG minimum |
| 18 | 11 text elements below 12px | **TRUE** | P3 | Consistent across all viewports |

**Scorecard: 5 FALSE / 1 MISCHARACTERIZED / 12 TRUE (6 actionable + 3 expected + 3 framework-limited)**

---

## Detailed Analysis: FALSE CLAIMS

### Claims 1-2: API Endpoints "Failing" (429s)
- **Code:** `api/middleware.py:29-40` — `RATE_LIMIT_WRITE = 20` per 60s for WRITE_PREFIXES
- **Both `/api/v1/batches` and `/api/v1/entities` are in WRITE_PREFIXES**
- **What happened:** Audit tool made rapid successive requests, exceeded 20/60s limit
- **Evidence:** 30+ rate limiting tests pass; 429 response includes `Retry-After: 60`, proper error envelope
- **Conclusion:** Working as designed. Not a bug.

### Claim 3: Navigation Requires JS Workaround
- **Code:** `app.py:2146-2151` — Standard `st.radio()` with `label_visibility="collapsed"`
- **CSS inspection:** Lines 189-191, 279-281, 989-1001 — only styles text color/padding
- **No CSS rules hide radio inputs** (no `display:none`, `visibility:hidden`, `opacity:0`)
- **Report contradicts itself:** Comprehensive test shows 14/14 navigate successfully via label_click

### Claim 4: "Run Now" No Feedback
- **Code:** `dashboard/views/health.py:241-248`
- **Four feedback mechanisms:** `st.spinner()` + `st.success()` + `st.error()` + `st.rerun()`
- **Conclusion:** Factually wrong. Spinner shown during execution, success/error after.

### Claim 5: URL Profiler Button Stays Disabled
- **Code:** `dashboard/url_profiler_page.py:416-423`
- **Logic:** `disabled=not url` — standard form validation pattern
- **Button becomes enabled as soon as user types a URL**
- **Also has spinner:** `st.spinner("Fetching and analyzing website...")`

---

## Detailed Analysis: TRUE CLAIMS — Actionable

### Claim 6: Session Persistence (P0)
- **Code:** `dashboard/api_client.py:57-98`
- **Storage:** Only `st.session_state.auth_token` (in-memory, ephemeral)
- **No cookies, no localStorage, no token refresh mechanism**
- **On page reload:** `st.session_state` resets → `is_authenticated()` returns False → login page
- **Fix:** Cookie-based or query-param token persistence

### Claim 7: Monitoring Dark Theme (P1)
- **Code:** `dashboard/monitoring_page.py:34-38`
- **Injects:** `background-color: #0a0a0a` overriding app's `--press-white: #FFFFFF` from `app.py:163-181`
- **Also:** Card styling `background: #111111` (line 46)
- **Fix:** Remove dark theme CSS injection, inherit main app theme

### Claim 9: Signals Error Handling (P1)
- **Code:** `dashboard/app.py:2350-2351`
- **`load_signals()` and `load_health_report()` have NO try-except**
- **Compare:** Cost Analysis (line 103), Ops Monitoring (line 51) DO have proper error handling
- **Fix:** Wrap in try-except with `st.warning()` like other pages

### Claim 8: Monitoring Onboarding Text (P2)
- **Code:** `dashboard/app.py:1457-1492`
- **`messages.get(view, messages["Pipeline"])` — "Website Monitoring" not in dict**
- **Falls back to "Welcome to Your Deal Pipeline"**
- **Fix:** Add "Website Monitoring" key to messages dict

### Claim 12: Drift SPC "Unknown error" (P2)
- **Code:** `dashboard/views/drift_monitoring.py:78-91`
- **`result.get("message", "Unknown error")` — API response lacks "message" field**
- **Fix:** Improve API response or provide descriptive fallback text

### Claims 13-15: Accessibility (P2 — partial fixes possible)
- Input labels: Streamlit limitation; partial mitigation via custom `st.markdown()` with ARIA
- Color contrast: Custom CSS can override Streamlit defaults for sidebar labels
- Keyboard nav: Streamlit form_submit_button works with Enter; full keyboard nav is framework-limited

---

## Detailed Analysis: TRUE CLAIMS — Low Priority

### Claims 10-11: Cost Analysis & Ops Monitoring Error Messages
- These ARE proper error handling working correctly
- Messages appear when API/ops layer isn't ready (expected during initial setup)
- Could use friendlier language but functionally correct

### Claims 16-18: Responsive Design
- This is an internal VC tool, not a public-facing consumer app
- Streamlit doesn't support responsive layouts natively
- Mobile support is very low priority for this use case
- Text size (10.4px minimum) is Streamlit's default

---

## Corrected Assessment

**The report's D+ grade is inflated by 5 false/mischaracterized claims and framework limitations.**

| Category | Report Said | Actual |
|----------|------------|--------|
| Interaction Response | PASS (partial) | **PASS** — navigation and Run Now both work correctly |
| Performance Audit | FAIL | **PASS** — 429s are rate limiting, not failures |
| Error & Recovery | FAIL | **PARTIAL** — Signals page needs fix; others work correctly |
| Accessibility | FAIL | **VALID** — but mostly Streamlit framework limitations |
| Responsiveness | FAIL | **VALID** — but low priority for internal desktop tool |
| State Management | FAIL | **PARTIAL** — session persistence is real issue; Run Now feedback exists |

**Corrected grade: C+/B-** (one true P0, two true P1s, rest are framework or low-priority)

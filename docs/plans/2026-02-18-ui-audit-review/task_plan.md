# Task Plan: UI/UX Audit Remediation

## Goal
Address the validated findings from the external UI/UX audit report. Fix only confirmed issues; ignore false claims and deprioritize framework limitations.

## Current Phase
Phase 5 (verification)

## Phases

### Phase 1: Quick Wins (code-level fixes, < 30 min each)
- [x] **Fix Monitoring dark theme** — Replaced dark CSS with light theme in `monitoring_page.py:30-123`
- [x] **Fix Monitoring onboarding text** — Added "Website Monitoring" entry to `app.py` messages dict
- [x] **Fix Signals error handling** — Wrapped `load_signals()` / `load_health_report()` in try-except in `app.py`
- [x] **Fix Drift SPC error message** — Replaced "Unknown error" with descriptive text in `drift_monitoring.py:89`
- **Status:** complete

### Phase 1.5: Steelmanned Improvements (kernel of truth from rejected claims)
- [x] **429 transparent retry** — Added auto-retry in `api_client.py` get()/post() on 429 with Retry-After
- [x] **Run Now persistent toast** — Added session_state feedback that survives st.rerun() in `health.py:241-254`
- [x] **Cost Analysis error wording** — User-friendly message in `cost_analysis.py:103`
- [x] **Ops Health error wording** — User-friendly messages in `ops_health.py:47,51`
- [x] **Sidebar label contrast** — Upgraded radio labels to `--text-primary` + font-weight 500 in `app.py:279-281`
- **Status:** complete

### Phase 2: Session Persistence (P0 — deferred to Step 3)
- [ ] Design token persistence approach (cookie vs query param vs Streamlit extras)
- [ ] Implement cookie-based JWT storage in `api_client.py`
- [ ] Add token recovery on app initialization in `app.py`
- [ ] Add token refresh/expiry handling
- [ ] Test: page reload preserves auth
- [ ] Test: expired token forces re-login
- **Status:** deferred (revisit at Step 3 activation)

### Phase 3: Accessibility Improvements (P2 — what's feasible in Streamlit)
- [x] Sidebar label contrast improved (moved to Phase 1.5)
- [ ] Evaluate `st.markdown()` ARIA label injection for unlabeled inputs
- [ ] Review Streamlit `st.form_submit_button` keyboard behavior
- **Status:** partial (contrast done, ARIA deferred)

### Phase 4: Error Message Polish (P3 — nice-to-have)
- [x] Improve Cost Analysis error wording (moved to Phase 1.5)
- [x] Improve Ops Monitoring error wording (moved to Phase 1.5)
- [ ] Add minimum font-size CSS override for sub-12px text elements
- **Status:** partial (error messages done, font-size deferred)

### Phase 5: Verification & Delivery
- [x] Run targeted tests for modified files (23/23 passed)
- [x] All module imports verified clean
- [ ] Update findings.md with completion status
- **Status:** in progress

## Key Questions
1. Is session persistence worth implementing now, or defer to Step 3 activation? → **Decide before Phase 2**
2. Should accessibility fixes target WCAG AA or just "good enough"? → **Good enough for internal tool**
3. Should responsive design be addressed at all? → **No — internal desktop tool, Streamlit limitation**

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Reject 5 false claims | Code evidence directly contradicts report (rate limiting, nav, Run Now, Profile button) |
| Skip responsive fixes | Internal desktop tool; Streamlit framework limitation; not worth effort |
| Phase 1 before Phase 2 | Quick wins first to show progress; session persistence is larger effort |
| Cookie-based persistence | Standard web approach; Streamlit extras (streamlit-cookies-manager) adds dependency |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |

## Notes
- The audit was conducted on a remote Ubuntu sandbox against a deployed instance
- Some test behaviors (rapid API calls hitting rate limits) are test methodology issues, not app bugs
- Re-read this plan before major decisions (attention manipulation)
- Log ALL errors — they help avoid repetition

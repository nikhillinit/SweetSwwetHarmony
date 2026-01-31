# Progressive Content Pipeline - Refined Implementation Plan

## Goal
Implement a progressive, per-watch configurable content pipeline that outputs canonical representations (text or JSON) with provenance and maintenance safeguards, using headless rendering and transport impersonation only as escalations.

## Analysis Summary (2026-01-30)

**Proposal Assessment:** Well-architected with sound principles.

**Weaknesses Found:** 6 (addressed in plan below)
**Opportunities Found:** 8 (incorporated where high-value)

See `findings.md` for detailed analysis.

---

## Revised Implementation Plan

### Phase 0: Foundation (No Behavior Change) - 2-3 days

| Task | Description | Status |
|------|-------------|--------|
| 0.1 | Add `config_json` column to watches table (migration) | pending |
| 0.2 | Add `last_etag`, `last_modified` to watches (O5) | pending |
| 0.3 | Parse `config_json` into Watch model | pending |
| 0.4 | Create `monitoring/content_pipeline/` skeleton | pending |
| 0.5 | Add `FetchArtifact`, `PipelineResult` types | pending |
| 0.6 | Create preset registry file `config/watch_presets.yaml` (O7) | pending |
| 0.7 | Unit tests for config parsing | pending |

**Exit Criteria:**
- [ ] Migration runs successfully
- [ ] Watch model parses config_json
- [ ] Preset registry loads

### Phase 1: Conditional Requests + Selector Scoping - 3-4 days

| Task | Description | Status |
|------|-------------|--------|
| 1.1 | Implement conditional requests (ETag/Last-Modified) | pending |
| 1.2 | Integrate parsel for CSS/XPath selection | pending |
| 1.3 | Implement selector fallback chains (W1) | pending |
| 1.4 | Wire PageTypeClassifier → preset auto-selection (O1) | pending |
| 1.5 | Add content size limits (W3: 5MB HTML, 2MB JSON) | pending |
| 1.6 | Add `pricing_table_v1` preset | pending |
| 1.7 | Golden-file tests with fixtures | pending |

**Exit Criteria:**
- [ ] 304 responses skip extraction
- [ ] Selector scoping works with fallback
- [ ] Size limits enforced

### Phase 2: Layout-Aware Extraction - 2-3 days

| Task | Description | Status |
|------|-------------|--------|
| 2.1 | Integrate inscriptis for table/grid preservation | pending |
| 2.2 | Add whitespace normalization | pending |
| 2.3 | Integrate extruct for JSON-LD/microdata | pending |
| 2.4 | Wire structured metadata as alternative representation | pending |
| 2.5 | Pricing page fixture tests | pending |

**Exit Criteria:**
- [ ] Pricing tables preserve alignment
- [ ] JSON-LD extracted when available

### Phase 3: Hydration + JSON Canonicalization - 3-4 days

| Task | Description | Status |
|------|-------------|--------|
| 3.1 | Implement `__NEXT_DATA__` extraction | pending |
| 3.2 | Integrate chompjs with timeout wrapper (W2) | pending |
| 3.3 | Add input size cap (100KB) for chompjs (W2) | pending |
| 3.4 | Implement JSON canonicalization (key sorting, volatile drop) | pending |
| 3.5 | Add `spa_hydration_v1` preset | pending |
| 3.6 | Structured diff for JSON (O6) | pending |
| 3.7 | SPA fixture tests | pending |

**Exit Criteria:**
- [ ] Next.js hydration extracted without browser
- [ ] Volatile keys dropped deterministically
- [ ] JSON diffs show semantic changes

### Phase 4: ATS API Discovery - 2-3 days

| Task | Description | Status |
|------|-------------|--------|
| 4.1 | Implement ATS embed signature detection (O2) | pending |
| 4.2 | Add Greenhouse API discovery | pending |
| 4.3 | Add Lever API discovery | pending |
| 4.4 | Add Ashby API discovery | pending |
| 4.5 | Add `ats_api_v1` preset | pending |
| 4.6 | Job board fixture tests | pending |

**Exit Criteria:**
- [ ] ATS embeds auto-discovered
- [ ] Direct API calls instead of DOM scraping

### Phase 5: Transport Escalation - 2-3 days ✅ COMPLETE

| Task | Description | Status |
|------|-------------|--------|
| 5.1 | Integrate curl_cffi as fallback transport | complete |
| 5.2 | Wire escalation triggers (403/429/blocked) | complete |
| 5.3 | Add `transport.user_agent_profile` config | complete |
| 5.4 | Enable HTTP/2 by default (O3) | complete |
| 5.5 | Tests for blocked site recovery | complete |

**Exit Criteria:**
- [x] curl_cffi used only when needed
- [x] Previously blocked sites recoverable

**Phase 5 Summary (2026-01-30):**
- Created `CurlCffiTransport` with browser impersonation (18 tests)
- Added `user_agent_profile` to TransportConfig (8 tests)
- Enabled HTTP/2 by default in HttpxTransport (2 tests)
- Created `TransportEscalator` with auto-fallback on 403/429/blocked (15 tests)
- Wired TransportEscalator into ContentPipeline orchestrator (3 integration tests)
- Added comprehensive blocked site recovery tests (8 tests)
- **Total new tests:** 54
- **Commit:** TBD

### Phase 6: Headless Fallback - 3-4 days ✅ CORE COMPLETE

| Task | Description | Status |
|------|-------------|--------|
| 6.1 | Integrate Playwright with semaphore gating (W4) | complete |
| 6.2 | Implement selector-based wait (not networkidle) | complete |
| 6.3 | Add `js_rendered_v1` preset | complete |
| 6.4 | Add browser context timeout/recycling | complete |
| 6.5 | Add observability metrics (O8) | deferred |
| 6.6 | Full pipeline integration tests | deferred |

**Exit Criteria:**
- [x] Concurrency capped at 2
- [x] True JS-only sites work
- [ ] Metrics emitted (deferred)

**Phase 6 Summary (2026-01-30):**
- Created `PlaywrightTransport` with Chromium headless browser (21 tests)
- Added global semaphore gating limiting to 2 concurrent browsers
- Implemented intelligent wait strategies (explicit selector, auto-detect main content, DOM stability)
- Added `playwright_wait_selector` and `playwright_timeout_ms` to TransportConfig
- Added `on_blocked` config option for 3-tier escalation
- Created `js_rendered_v1` and `spa_hydration_v1` presets
- Updated `TransportEscalator` for 3-tier escalation: httpx → curl_cffi → playwright (5 new tests)
- Graceful degradation when Playwright not installed
- **Total new tests:** 26 (21 playwright + 5 escalator)
- **Total monitoring tests:** 687 passing

### Phase 7: Rollout + Maintenance Mode - 2 days

| Task | Description | Status |
|------|-------------|--------|
| 7.1 | Wire maintenance diff detection (hasher_version change) | pending |
| 7.2 | Add preset versioning to snapshot metadata (W5) | pending |
| 7.3 | Create migration guide for existing watches | pending |
| 7.4 | Gradual rollout: 10% → 50% → 100% | pending |

**Exit Criteria:**
- [ ] No alert storms on rollout
- [ ] Presets traceable in snapshots

---

## Scope Exclusions (Deferred)

| Feature | Reason | Future Phase |
|---------|--------|--------------|
| visual_hash representation | Undefined, separate concern (W6) | Phase 8+ |
| App Store monitoring | No "new apps" API | TBD |
| Play Store monitoring | ToS issues | TBD |
| Advanced image diff | Perceptual hashing | Phase 8+ |

---

## Dependencies to Install

```bash
pip install parsel inscriptis extruct chompjs curl_cffi playwright
playwright install chromium --with-deps
```

---

## Decision Log

| Decision | Date | Rationale |
|----------|------|-----------|
| Remove visual_hash from v1 | 2026-01-30 | No implementation specified |
| Add ATS auto-discovery in Phase 4 | 2026-01-30 | High value, reduces config burden |
| Use preset registry file | 2026-01-30 | Updateable without code changes |
| Semaphore for Playwright | 2026-01-30 | Prevents resource exhaustion |
| chompjs timeout + size cap | 2026-01-30 | Security mitigation |

---

## Previous Context

# Discovery Engine: Development Review & Remaining Work

## Goal
Review latest development work and identify remaining high-value work items.

## Current State (2026-01-30)

### Latest Completed Work
| Date | Commit | Work |
|------|--------|------|
| 2026-01-30 | `f585e3c` | **CI/CD Remediation** - Fixed GitHub Actions workflow failures |
| 2026-01-30 | `c5d8dba` | Storage fix - id tiebreaker for community mentions query |
| 2026-01-30 | `440f609` | Storage fix - id tiebreaker for thesis classification query |
| 2026-01-29 | `58ca2ca` | Pipeline - timeout config, retry fix, warm intro enrichment |
| 2026-01-29 | `e404bb0` | **Thesis Evaluation Harness** - 82 tests, 86.7% keyword accuracy |

### CI/CD Status ✅ FIXED
- **Workflows:** 2 active (discovery-pipeline.yml, thesis-eval.yml)
- **Root cause:** `secrets.*` in step-level `if:` conditions broke GitHub's parser
- **Fix:** Moved secret checks from YAML `if:` to shell `if [ -n "$VAR" ]`

### Test Coverage
- **Total tests:** 2,777 collected
- **Status:** All passing

### Sprint Status (from CLAUDE.md)
- **Signal Quality & Enrichment:** ✅ COMPLETE (all 5 phases)
- **Operational Excellence:** ✅ COMPLETE
- **Production Hardening:** ✅ COMPLETE
- **Storage & Collectors:** ✅ COMPLETE
- **Phase G Sprint 2 (Identity Resolution):** ✅ COMPLETE (feature-flagged)

---

## Remaining Work (Prioritized)

### Priority 1: Production Readiness
| Task | Effort | Value | Status |
|------|--------|-------|--------|
| Configure API credentials (NOTION, GITHUB) | 30 min | HIGH | Blocked on secrets |
| Enable Phase G features in production | 2-3 hrs | MEDIUM | Ready |
| Run production pipeline with real data | 1 hr | HIGH | Needs credentials |

### Priority 2: Network Scout v2.0
**Design:** `docs/plans/2026-01-28-network-scout-design.md`
**Effort:** 1-2 weeks
**Value:** HIGH - Enables warm intros via Gmail + Notion LP data

**Components:**
1. MBOX streaming parser for Gmail Takeout
2. Notion LP sync connector
3. Warm intro boost calculation
4. Relationship health monitoring
5. CLI commands (import-emails, sync-lps, relationship-health)

**Prerequisites:**
- Gmail MBOX file available
- Notion LP database configured

### Priority 3: LLM Observability
**Tool:** Langfuse (19k★, MIT license)
**Effort:** 1-2 days
**Value:** MEDIUM - Better visibility into LLM classifier performance

**Integration points:**
- `consumer/thesis_filter/llm_classifier.py`
- Thesis evaluation harness

### Priority 4: Advanced Entity Resolution (Phase G.5)
**Tools:** Splink or Dedupe
**Effort:** 1 week
**Value:** MEDIUM - Better fuzzy matching for weak keys

**Current state:** Using simple RapidFuzz + deterministic canonical keys
**When to upgrade:** If simple fuzzy matching proves insufficient

### Priority 5: Future Enhancements
From `docs/future-ideas/2026-01-29-oss-tools-research.md`:

| Phase | Focus | Tools |
|-------|-------|-------|
| Phase 3 | LLM Observability | Langfuse |
| Phase 4 | Security/Privacy | OSV.dev + Wappalyzer |
| Phase G.5 | Advanced Entity Resolution | Splink (if needed) |
| Phase 5+ | Relationship Graph | Neo4j |

---

## Quick Wins (Can Do Now)

1. **Run thesis evaluation with LLM** (~30 min)
   - Requires: GOOGLE_API_KEY
   - Command: `python run_pipeline.py eval run --type llm`

2. **Enable Phase G features** (~2-3 hrs)
   - Set `USE_PHASE_G_IDENTITY_RESOLUTION=true`
   - Set `USE_CLAIM_FACTS=true`
   - Monitor production run

3. **Expand thesis evaluation dataset** (~1 hr)
   - Requires: Notion API access
   - Command: `python run_pipeline.py eval export --output datasets/ground_truth.jsonl`

---

## Key Questions for User

1. **API Credentials:** Are NOTION_API_KEY, GITHUB_TOKEN, GOOGLE_API_KEY available?
2. **Network Scout:** Is Gmail MBOX file and Notion LP database ready?
3. **Priority:** What's the highest-value next feature for the business?

---

## Decisions Made
| Decision | Date | Rationale |
|----------|------|-----------|
| CI/CD: Move secret checks to shell | 2026-01-30 | GitHub parser limitation |
| CI/CD: Rename workflows | 2026-01-30 | Avoid cached broken definitions |
| Phase G: Keep disabled by default | 2026-01-29 | Needs production validation |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| GitHub Actions 0 jobs executed | 1 | Quote `on:` key - didn't work |
| GitHub Actions 0 jobs executed | 2 | Add `branches-ignore` - didn't work |
| GitHub Actions 0 jobs executed | 3 | Found: `secrets.*` in `if:` breaks parser |
| GitHub Actions 0 jobs executed | Fix | Move check to shell script |

---

## Notes
- Update phase status as you progress: pending → in_progress → complete
- Re-read this plan before major decisions
- Log ALL errors - they help avoid repetition

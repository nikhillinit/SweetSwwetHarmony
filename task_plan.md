# Task Plan: Phase 9 — Quality Ops Production Integration

**Related Docs:**
- [Findings](findings.md) - research & decisions
- [Progress](progress.md) - daily log & results

**Last Synced:** 2026-02-06 18:00 UTC

---

## Goal
Complete the Quality Ops integration started in Phase 8 by wiring LLM thesis classification into the production pipeline, enabling automatic feedback loops, and making the quality flywheel fully operational.

**Target FP Reduction:** 30% → <15% via LLM classification + pattern-driven tuning

---

## Current Phase
Phase 1: LLM Thesis Classification Integration

---

## Phases Overview

| Phase | Description | Estimated Time | Status |
|-------|-------------|----------------|--------|
| 1 | LLM Thesis Classification Integration | 1-2 days | complete ✅ |
| 2 | Verification Gate LLM Integration | 0.5 days | complete ✅ |
| 3 | Scheduler Integration (3 workflows) | 1 day | pending |
| 4 | Disagreement Detection & Reporting | 0.5 days | pending |
| 5 | Integration Testing & Validation | 1 day | pending |
| 6 | Documentation & Deployment | 0.5 days | pending |

**Total Estimated Time:** 4-5 days

---

## Phase 1: LLM Thesis Classification Integration

**Goal:** Enable LLM thesis classification in the pipeline with feature flag control.

### Tasks
- [x] **1.1: Add env var `LLM_THESIS_MODE`** ✅ ALREADY COMPLETE
  - File: `.env.example` (lines 152-158)
  - ✅ `LLM_THESIS_MODE=off` added (default off)
  - ✅ Comment: "LLM thesis classification mode (off/shadow/active). Gemini free tier: 15 RPM, 1500 RPD"
  - ✅ Three modes documented:
    - `off` - No LLM calls (keyword-only classification) - DEFAULT
    - `shadow` - Run LLM, store results in DB, DON'T affect routing (testing)
    - `active` - Full integration, LLM scores affect confidence & routing (production)
  - ✅ BONUS: Tuning thresholds externalized (lines 161-165)

- [x] **1.2: Update pipeline to respect `LLM_THESIS_MODE`** ✅ ALREADY COMPLETE
  - File: `workflows/pipeline.py:1564-1566`
  - ✅ Implemented: `llm_mode = os.getenv("LLM_THESIS_MODE", "off").lower()`
  - ✅ Logic: `skip_llm = (llm_mode == "off")`
  - ✅ Import: `import os` at line 36
  - ✅ Case-insensitive matching with .lower()
  - Tests: `tests/workflows/test_pipeline_llm_modes.py` (4 tests, all passing)

- [x] **1.3: Add rate limiting safeguards** ✅ ALREADY COMPLETE
  - File: `consumer/thesis_filter/llm_classifier.py`
  - ✅ Existing RateLimiter class (lines 98-166): RPM=15, RPD=1500
  - ✅ Integration in LLMClassifier.classify() (lines 303-318)
  - ✅ Fallback on quota exceeded: Returns safe ThesisClassification
  - ✅ Circuit breaker integration (lines 252-256, 286-379)
  - Tests: `tests/consumer/test_llm_rate_limiting.py` (11 tests, all passing)

- [x] **1.4: Add LLM quota tracking to ops metrics** ✅ ALREADY COMPLETE
  - File: `ops/monitoring/metrics.py`
  - ✅ Fields added to `OpsMetricsSnapshot` (lines 47-55):
    - `llm_calls_today: int = 0`
    - `llm_calls_last_hour: int = 0` (BONUS)
    - `llm_rate_limited_today: int = 0` (quota exhaustion tracking)
    - `llm_timeouts_today: int = 0` (BONUS)
    - `llm_errors_today: int = 0`
    - `llm_circuit_breaker_tripped: int = 0` (BONUS)
    - `thesis_disagreement_count: int = 0` (Phase 4)
    - `thesis_disagreement_rate: float = 0.0` (Phase 4)
  - ✅ Collection logic in `collect()` (lines 88-116)
  - ✅ `_get_llm_metrics()` implementation (lines 249-385)
  - Tests: `tests/ops/test_monitoring_metrics.py` (11 tests passing)

**Status:** complete ✅
**Files Created/Modified:** None (all implementations already exist)
**Blockers:** None

### Definition of Done
- [ ] Code implemented and committed
- [ ] Unit tests passing (new tests for rate limiting)
- [ ] Manual smoke test: Set `LLM_THESIS_MODE=active`, process 5 signals, verify LLM scores in DB
- [ ] No regression in baseline tests (4619 tests still pass)
- [ ] Ops health metrics show LLM quota tracking

---

## Phase 2: Verification Gate LLM Integration

**Goal:** Use LLM scores to boost/penalize confidence in verification gate.

### Tasks
- [x] **2.1: Add LLM confidence adjustment logic** ✅ ALREADY COMPLETE
  - File: `verification/verification_gate_v2.py` (lines 554-622)
  - ✅ Method: `_apply_llm_adjustment(base_confidence, keyword_score, llm_score) -> tuple[float, str]`
  - ✅ Returns tuple: (adjusted_confidence, adjustment_reason) for audit trail
  - ✅ Logic implemented:
    - Checks LLM_THESIS_MODE=active (lines 577-579)
    - Agreement boost: keyword≥0.7 AND llm≥0.7 → +0.10
    - Disagreement penalty: keyword≥0.7 AND llm<0.4 → -0.15
    - Weak keyword, strong LLM: keyword<0.4 AND llm≥0.7 → +0.05
    - Clamps to [0.0, 1.0] (line 612)
  - ✅ Externalized thresholds via env vars (lines 585-588)
  - ✅ Detailed logging for observability (lines 615-620)
  - Tests: `tests/verification/test_verification_gate_llm.py` (8 tests, all passing)

- [x] **2.2: Wire `_apply_llm_adjustment` into confidence scoring** ✅ ALREADY COMPLETE
  - File: `verification/verification_gate_v2.py` (evaluate() method)
  - ✅ Integration points:
    - Lines 319-326: Base confidence calculated (breakdown.overall)
    - Lines 332-336: Calls `_apply_llm_adjustment()` with base confidence
    - Line 369: Uses `final_confidence` (adjusted value) in VerificationResult
    - Lines 356-364: Adds LLM adjustment to verification_details (audit trail)
    - Lines 615-620: Logs adjustment in `_apply_llm_adjustment()` method
  - ✅ Full observability: base, delta, clamped, reason, keyword, llm scores logged
  - Tests: Integration test in tests/verification/test_verification_gate_llm.py (test_evaluate_passes_thesis_scores)

**Status:** complete ✅
**Files Created/Modified:** None (all implementations already exist)
**Blockers:** None

### Definition of Done
- [ ] Code implemented and committed
- [ ] Unit tests passing (LLM confidence adjustments)
- [ ] Manual smoke test: Process 5 signals with known keyword-LLM disagreements, verify confidence changes
- [ ] No regression in baseline tests
- [ ] Verification gate logs show LLM adjustments

---

## Phase 3: Scheduler Integration (3 Workflows)

**Goal:** Automate critical quality workflows via scheduler.

### Tasks
- [ ] **3.1: Create schedule config for `sync-status-events`**
  - Method: `ops.scheduler.PipelineScheduler.create_schedule()`
  - Config:
    ```python
    ScheduleConfig(
        name="quality-sync-notion-status",
        cron_expression="0 */6 * * *",  # Every 6 hours
        collectors=[],
        mode="quality-sync",  # New mode
        enabled=True
    )
    ```
  - Handler: Add `quality-sync` mode to scheduler execution
  - Tests: `tests/ops/test_scheduler_quality.py` (new)

- [ ] **3.2: Create schedule config for `thesis-classify-batch`**
  - Config:
    ```python
    ScheduleConfig(
        name="quality-thesis-classify-batch",
        cron_expression="0 2 * * *",  # Daily at 2am
        collectors=[],
        mode="quality-classify",
        enabled=True
    )
    ```
  - Handler: Call `ops.quality.thesis.batch_classify_recent(limit=50)`
  - Tests: `tests/ops/test_scheduler_quality.py`

- [ ] **3.3: Create schedule config for `find-patterns`**
  - Config:
    ```python
    ScheduleConfig(
        name="quality-find-patterns",
        cron_expression="0 3 * * 0",  # Sundays at 3am
        collectors=[],
        mode="quality-patterns",
        enabled=True
    )
    ```
  - Handler: Call `ops.quality.patterns.detect_patterns(days=30)`
  - Store results to file: `data/patterns/YYYY-MM-DD-patterns.json`
  - Tests: `tests/ops/test_scheduler_quality.py`

- [ ] **3.4: Add quality modes to scheduler CLI**
  - File: `ops/cli.py`
  - Add quality schedule creation commands:
    - `python -m ops.cli schedule add-quality-sync`
    - `python -m ops.cli schedule add-quality-classify`
    - `python -m ops.cli schedule add-quality-patterns`
  - Tests: `tests/ops/test_cli_schedule_quality.py` (new)

**Status:** pending
**Files Created/Modified:** None yet
**Blockers:** Phase 1-2 complete

### Definition of Done
- [ ] Code implemented and committed
- [ ] Unit tests passing (scheduler handlers)
- [ ] Manual smoke test: Verify all 3 schedules created, trigger one manually, verify it runs
- [ ] No regression in baseline tests
- [ ] `python -m ops.cli schedule list` shows quality schedules

### Scheduler Implementation Notes
- **Timezone:** UTC for all cron schedules
- **Idempotency:** Batch classify checks `WHERE llm_score IS NULL` to avoid reclassification
- **No complex locking needed:** OpsStorage already handles concurrent access

---

## Phase 4: Disagreement Detection & Reporting (COMPLETE ✅)

**Goal:** Track keyword-LLM disagreements for quality improvement.

**Status:** Complete (2026-02-06)
**Files Modified:**
- `storage/signal_store.py` (migration 26 + logic)
- `ops/quality/thesis.py` (store + report functions)
- `tests/storage/test_disagreement_detection.py` (new, 5 tests)
- `tests/ops/quality/test_disagreement_report.py` (new, 5 tests)

### Tasks
- [x] **4.1: Add disagreement flag to `thesis_classifications` table**
  - File: `storage/signal_store.py` or create migration 26
  - Add column: `disagreement_detected BOOLEAN DEFAULT 0`
  - Logic: disagreement = `(keyword_score >= 0.7 AND llm_score < 0.4) OR (keyword_score < 0.4 AND llm_score >= 0.7)`
  - Update: `save_thesis_classification()` to compute and store flag
  - Tests: `tests/storage/test_thesis_classification_storage.py` (extend) ✅ Done

- [x] **4.2: Add disagreement metrics to ops health**
  - File: `ops/monitoring/metrics.py`
  - Add to `OpsMetricsSnapshot`:
    - `thesis_disagreement_count: int`
    - `thesis_disagreement_rate: float`
  - Compute from `thesis_classifications` where `disagreement_detected=1`
  - Tests: `tests/ops/test_monitoring_metrics.py` (extend) ✅ Done

- [x] **4.3: Extend disagreement report CLI**
  - File: `ops/quality_cli.py` (already has `thesis-disagreement-report`)
  - Verify it reads `disagreement_detected` flag
  - Add summary stats: total disagreements, rate, by category
  - Tests: `tests/ops/quality/test_quality_cli.py` (extend) ✅ Done

**Status:** complete ✅
**Files Created/Modified:** See above
**Blockers:** None

### Definition of Done (Complete)
- [x] Code implemented and committed
- [x] Unit tests passing (22 tests: 5 disagreement detection + 5 report + 12 existing)
- [x] Manual smoke test: N/A (Phase 4 standalone)
- [x] No regression in baseline tests
- [x] `python -m ops.cli quality thesis-disagreement-report` works

---

## Phase 5: Integration Testing & Validation

**Goal:** Verify end-to-end quality ops workflows work in production.

**Strategy:** Incremental testing approach - test what's ready now, defer blocked scenarios.

### Tasks (Revised 2026-02-06)

- [ ] **5.1: Write unit tests for LLM rate limiting**
  - File: `tests/consumer/test_llm_rate_limiting.py` (new)
  - Test scenarios:
    - Gemini API quota exceeded → fallback to keyword-only
    - Network timeout → graceful degradation
    - Invalid API key → error handling
  - Mock: `google.generativeai` errors
  - Tests: 2-3 unit tests (~15 assertions total)
  - **Status:** Can write now (no blockers)

- [ ] **5.2: Run baseline test suite**
  - Commands:
    ```bash
    pytest tests/ops/ -v --tb=short
    pytest tests/storage/ -v --tb=short
    pytest tests/verification/ -v --tb=short
    pytest tests/integration/ -v --tb=short
    ```
  - Expected: 605+ tests pass (includes Phase 4's 22 tests)
  - Fix any regressions from Phase 4 changes
  - Tests: Baseline regression check
  - **Status:** Can run now

- [ ] **5.3: Verify Phase 4 disagreement detection E2E**
  - File: `tests/storage/test_disagreement_detection.py` (already exists)
  - Run specific test: `pytest tests/storage/test_disagreement_detection.py -v`
  - Expected: 5 tests pass (disagreement flag logic)
  - Run CLI report: `python -m ops.cli quality thesis-disagreement-report --days 7`
  - Verify report output format
  - **Status:** Can test now (Phase 4 complete)

- [ ] **5.4: Document test coverage gaps**
  - File: `progress.md` (update)
  - Create table:
    - What's tested (unit tests, Phase 4 E2E)
    - What's deferred (pipeline LLM E2E, scheduler E2E)
    - Blockers (Phases 1-3 incomplete)
  - **Status:** Can do now

- [ ] **5.5: Write E2E test stubs for deferred scenarios** (OPTIONAL)
  - File: `tests/workflows/test_pipeline_llm_modes.py` (new)
  - File: `tests/ops/test_scheduler_quality.py` (new)
  - File: `tests/verification/test_verification_gate_llm.py` (new)
  - Mark with `@pytest.mark.skip(reason="Blocked by Phase 1/2/3")`
  - Tests: 3 stub files with docstrings
  - **Status:** Optional (for future readiness)

- [ ] **5.6: Prepare manual validation checklist**
  - File: `docs/phase9-manual-validation.md` (new)
  - Checklist items:
    - Enable LLM mode in `.env`
    - Run pipeline on 10 signals
    - Check DB for LLM scores
    - Verify ops health metrics
    - Run disagreement report
    - Screenshot results
  - To be executed after Phases 1-2-3 complete
  - **Status:** Can write now

**Status:** in_progress
**Files Created/Modified:** None yet
**Blockers:**
- Tasks 5.1-5.4: No blockers (can do now)
- Full E2E tests (deferred): Phase 1-2-3 incomplete

### Definition of Done
- [ ] Rate limiting unit tests written and passing
- [ ] Baseline test suite runs (4619 tests pass)
- [ ] Phase 4 disagreement detection verified end-to-end
- [ ] Test coverage documented (what's tested, what's deferred)
- [ ] Manual validation checklist prepared (for post-Phase 1-2-3)
- [ ] Gaps documented for Phase 6

---

## Phase 6: Documentation & Deployment

**Goal:** Update docs and deploy to production.

### Tasks
- [ ] **6.1: Update CLAUDE.md**
  - Add Phase 9 to Current State section
  - Update Quick Commands with quality schedule examples
  - Tests: None (docs only)

- [ ] **6.2: Update .claude/auto-memory/MEMORY.md**
  - Add Phase 9 completion note
  - Document LLM integration pattern
  - Note any gotchas (rate limits, env var)
  - Tests: None (docs only)

- [ ] **6.3: Create deployment checklist**
  - File: `docs/phase9-deployment-checklist.md` (new)
  - Checklist items:
    - [ ] Set `LLM_THESIS_MODE=active` in production `.env`
    - [ ] Verify `GOOGLE_API_KEY` configured
    - [ ] Create 3 quality schedules via CLI
    - [ ] Run manual validation (10 signals)
    - [ ] Monitor ops health dashboard for 24h
    - [ ] Check disagreement report after 1 week
  - Tests: None (docs only)

- [ ] **6.4: Add rollback plan**
  - File: `docs/phase9-rollback-procedure.md` (new)
  - Document immediate rollback steps if LLM integration causes issues:
    ```markdown
    ## Rollback Procedure

    If LLM integration causes issues:

    ### 1. Immediate (< 5 min)
    - Set `LLM_THESIS_MODE=off` in `.env`
    - Restart pipeline
    - System reverts to keyword-only classification

    ### 2. If env var toggle insufficient
    - `git revert [phase9-commit]`
    - `python -m ops.cli schedule disable quality-*`
    - Keep Phase 4 changes (disagreement detection is safe)

    ### 3. Rollback triggers
    - LLM error rate > 25%
    - Pipeline latency > 2x baseline
    - FP rate spike > 10 percentage points
    ```
  - Tests: None (docs only)

- [ ] **6.5: Create PR**
  - Branch: `feature/phase9-quality-ops-production`
  - Title: `feat(quality): complete Phase 9 Quality Ops production integration — LLM thesis classification, scheduler, disagreement detection`
  - Description: Link to task_plan.md, findings.md, list key changes
  - Tests: CI must pass (all 620+ tests)

**Status:** pending
**Files Created/Modified:** None yet
**Blockers:** Phase 5 complete

### Definition of Done
- [ ] CLAUDE.md updated with Phase 9 completion
- [ ] MEMORY.md updated with Phase 9 notes
- [ ] Deployment checklist created
- [ ] Rollback procedure documented
- [ ] PR created with passing CI (all 4634+ tests)
- [ ] PR merged to main

---

## Key Decisions

| Decision | Rationale |
|----------|-----------|
| LLM feature flag (`LLM_THESIS_MODE`) with 3 modes | Safe rollout, shadow mode for testing, easy to disable if quota issues |
| LLM confidence boost/penalty (not replacement) | Conservative, doesn't break existing routing |
| Schedule 3 workflows (not all 14) | Closes feedback loop without over-engineering |
| Defer enrichment stubs to Phase 10 | Focus on thesis + scheduler, enrichment orthogonal |
| Disagreement detection via flag in DB | Enables efficient querying for reports |
| Daily batch classification at 2am | Off-peak, respects rate limits |

---

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
|       | 1       |            |

---

## Notes

### Test Coverage Target
- **Current:** 4619 tests collected (verified 2026-02-06)
- **New tests Phase 9:** ~15 tests (3 E2E + 12 unit/integration)
- **Target:** 4634+ tests, 0 failures

### File Locations
- **Pipeline:** `workflows/pipeline.py:1566` (skip_llm flag)
- **Verification:** `verification/verification_gate_v2.py`
- **Scheduler:** `ops/scheduler.py`, `ops/cli.py`
- **Quality ops:** `ops/quality/*.py` (13 modules)
- **Tests:** `tests/ops/quality/`, `tests/integration/`

### Dependencies
- ✅ Gemini API (`GOOGLE_API_KEY` configured)
- ✅ `google-genai` package installed
- ✅ Quality tables (migration 25 applied)
- ✅ Scheduler infrastructure ready

### Related Work (Deferred)
- **Phase 0B-2:** ThesisMatcher YAML wiring (separate effort, 11 tasks)
- **Enrichment stubs:** brand_sentiment, community_metrics (Phase 10)
- **Weak canonical keys:** name_loc strengthening (future)

---

## Progress Tracking

Update phase status as you progress:
- `pending` → `in_progress` → `complete`

### Phase 1: LLM Thesis Classification Integration
- **Status:** pending
- **Started:**
- **Completed:**
- **Files modified:**

### Phase 2: Verification Gate LLM Integration
- **Status:** pending
- **Started:**
- **Completed:**
- **Files modified:**

### Phase 3: Scheduler Integration
- **Status:** pending
- **Started:**
- **Completed:**
- **Files modified:**

### Phase 4: Disagreement Detection
- **Status:** pending
- **Started:**
- **Completed:**
- **Files modified:**

### Phase 5: Integration Testing
- **Status:** pending
- **Started:**
- **Completed:**
- **Files modified:**

### Phase 6: Documentation & Deployment
- **Status:** pending
- **Started:**
- **Completed:**
- **Files modified:**

---

## Success Criteria

- [x] Phase 8 merged to main (efbad57) ✅
- [ ] LLM thesis classification runs when `LLM_THESIS_MODE=active`
- [ ] LLM scores affect confidence in verification gate
- [ ] 3 quality workflows scheduled and functional
- [ ] Disagreement detection working and reportable
- [ ] All 620+ tests pass
- [ ] Manual validation: 10+ signals classified with LLM
- [ ] Ops health dashboard shows LLM metrics
- [ ] PR merged to main

---

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | Phase 9 planning complete, ready to start Phase 1 |
| Where am I going? | Enable LLM thesis classification in pipeline |
| What's the goal? | Complete Quality Ops integration (40% → 100%) |
| What have I learned? | See findings.md — LLM disabled, verification ignores LLM, no scheduler integration |
| What have I done? | Created planning files (task_plan, findings, progress) |

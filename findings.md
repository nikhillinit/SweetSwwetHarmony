# Findings & Research — Phase 9: Quality Ops Production Integration

**Related Docs:**
- [Task Plan](task_plan.md) - execution roadmap
- [Progress](progress.md) - daily log & results

**Last Synced:** 2026-02-06 18:00 UTC

---

## Executive Summary
Phase 8 successfully delivered Quality Ops infrastructure (7,674 lines, 112 tests, all passing) but left it ~40% integrated. The feedback flywheel is built but disconnected from the main pipeline. Phase 9 completes the wiring to make quality ops production-ready.

**Phase 4 Status (2026-02-06):** ✅ **COMPLETE** — Disagreement detection & reporting wired into ops health metrics and CLI. Migration 26 applied, all 22 tests passing.

---

## Current State Analysis (Updated 2026-02-06)

### What's Already Complete ✅

#### Phase 4: Disagreement Detection (COMPLETE)
- ✅ Migration 26 applied: `disagreement_detected` column added to `thesis_classifications`
- ✅ Logic implemented in `signal_store.save_thesis_classification()` (lines 1035-1043)
- ✅ Ops health metrics extended: `thesis_disagreement_count`, `thesis_disagreement_rate`
- ✅ CLI report extended: category breakdowns, summary stats
- ✅ Tests: 5 new disagreement detection + 5 new report tests + 12 existing = 22 total
- ✅ Files modified:
  - `storage/signal_store.py` (migration + logic)
  - `ops/quality/thesis.py` (store + report functions)
  - `tests/storage/test_disagreement_detection.py` (new)
  - `tests/ops/quality/test_disagreement_report.py` (new)

### Phases 1-3 Status

#### Phase 1: LLM Thesis Classification Integration ✅ COMPLETE
**Status:** 4/4 tasks complete — All implementations existed from prior work
- ✅ **Env var `LLM_THESIS_MODE` COMPLETE** (Task 1.1) — Discovery: Already in .env.example (lines 152-158)
  - Implementation: Three modes (off/shadow/active) with clear documentation
  - BONUS: Tuning thresholds externalized (lines 161-165) for Phase 2 confidence adjustments
- ✅ **Pipeline respects LLM_THESIS_MODE COMPLETE** (Task 1.2) — Discovery: Already implemented (workflows/pipeline.py:1564-1566)
  - Implementation: Case-insensitive env var check, skip_llm=(llm_mode == "off")
  - Tests: tests/workflows/test_pipeline_llm_modes.py (4 tests, all passing)
- ✅ **Rate limiting safeguards COMPLETE** (Task 1.3) — Discovery: Already implemented + tested
  - Implementation: consumer/thesis_filter/llm_classifier.py (RateLimiter class, circuit breaker)
  - Tests: tests/consumer/test_llm_rate_limiting.py (11 tests, all passing)
- ✅ **LLM quota tracking in ops metrics COMPLETE** (Task 1.4) — Discovery: Already implemented (ops/monitoring/metrics.py:47-55)
  - Implementation: 8 LLM metric fields in OpsMetricsSnapshot
  - Collection: _get_llm_metrics() queries signals DB for thesis_classifications
  - BONUS metrics: calls_last_hour, timeouts, circuit_breaker_tripped
  - Tests: tests/ops/test_monitoring_metrics.py (11 tests, all passing)

#### Phase 2: Verification Gate LLM Integration ✅ COMPLETE
**Status:** 2/2 tasks complete — All implementations existed from prior work
- ✅ **LLM confidence adjustment method COMPLETE** (Task 2.1) — Discovery: Already in verification_gate_v2.py (lines 554-622)
  - Implementation: _apply_llm_adjustment(base_confidence, keyword_score, llm_score) -> tuple[float, str]
  - Logic: Agreement boost (+0.10), disagreement penalty (-0.15), weak keyword/strong LLM (+0.05)
  - Externalized thresholds: LLM_AGREEMENT_THRESHOLD, LLM_DISAGREEMENT_THRESHOLD, etc.
  - Tests: tests/verification/test_verification_gate_llm.py (8 tests, all passing)
- ✅ **Adjustment wired into evaluate() COMPLETE** (Task 2.2) — Discovery: Already integrated (lines 332-336, 369)
  - Integration: Calls _apply_llm_adjustment() with base confidence, uses final_confidence in result
  - Audit trail: Adds LLM adjustment details to verification_details (lines 356-364)
  - Observability: Logs adjustment details (lines 615-620)

#### Phase 3: Scheduler Integration
**Status:** Infrastructure ready, not wired
- ✅ Scheduler infrastructure exists (`ops/scheduler.py`)
- ❌ No quality mode handlers (`quality-sync`, `quality-classify`, `quality-patterns`)
- ❌ No quality schedule CLI commands

---

## Phase 5 Planning: Integration Testing & Validation

### Goal
Verify that Phases 1-4 work end-to-end in production scenarios. Test the complete quality ops flywheel from LLM classification → disagreement detection → ops health metrics → scheduled workflows.

### Current Test Coverage
- **E2E tests existing:** 5 tests in `tests/ops/quality/test_e2e_integration.py`
  - Flywheel: label → stats
  - Flywheel: pattern detection
  - Flywheel: tuning proposal
  - Flywheel: CSV export
  - FK constraint enforcement
- **Integration tests needed:** 3 new E2E tests for Phases 1-4
- **Target:** 4634+ tests total (4619 existing verified 2026-02-06 + 15 new across Phases 1-5)

### Test Scenarios Needed

#### Scenario 1: Pipeline with LLM Enabled (E2E)
**Purpose:** Verify LLM classification runs in pipeline when `LLM_THESIS_MODE=active`

**Test flow:**
1. Set `LLM_THESIS_MODE=active` in test env
2. Mock Gemini API response (use fixtures from `tests/ops/quality/test_thesis.py`)
3. Create test signal with known consumer description
4. Run pipeline's `_process_signals_stage()`
5. Verify `thesis_classifications` table has:
   - `keyword_score` populated
   - `llm_score` populated (not NULL)
   - `llm_category` set
   - `disagreement_detected` calculated correctly
6. Verify confidence score adjusted in verification gate (if Phase 2 complete)

**Mock requirements:**
- Gemini API (google.generativeai)
- Database fixture with signals table

**Assertions:** ~10 (DB checks, score ranges, disagreement logic)

---

#### Scenario 2: Scheduled Thesis Classification (E2E)
**Purpose:** Verify batch LLM classification via scheduler

**Test flow:**
1. Create 10 signals in DB without `thesis_classifications` rows
2. Mock scheduler execution context
3. Call quality workflow handler: `quality-classify` mode
4. Verify all 10 signals now have `thesis_classifications` rows with LLM scores
5. Verify ops metrics updated (llm_calls_today incremented)

**Mock requirements:**
- Gemini API batch calls
- Scheduler execution context

**Assertions:** ~8 (row counts, LLM score presence, metrics)

**Note:** Requires Phase 3 (scheduler integration) to be complete. If Phase 3 not done, test can be skipped or mocked.

---

#### Scenario 3: Disagreement Detection & Reporting (E2E)
**Purpose:** Verify keyword-LLM disagreements are detected and reported

**Test flow:**
1. Create signal with ambiguous description:
   - Keyword matcher: HIGH score (0.8) due to "consumer" keyword
   - LLM classifier (mocked): LOW score (0.2) because it's actually B2B SaaS
2. Run pipeline with LLM enabled
3. Verify DB state:
   - `thesis_classifications.disagreement_detected = 1`
   - `ops_health` metrics show `thesis_disagreement_count = 1`
4. Run disagreement report CLI: `python -m ops.cli quality thesis-disagreement-report`
5. Verify signal appears in report with category breakdown

**Mock requirements:**
- Gemini API response (low score for consumer description)
- Keyword matcher tuned to give high score

**Assertions:** ~6 (disagreement flag, metrics, report content)

**Status:** Partially testable now (Phase 4 complete), fully testable after Phases 1-2.

---

#### Scenario 4: LLM Rate Limiting & Fallback (Unit)
**Purpose:** Verify graceful degradation when Gemini quota exceeded

**Test flow:**
1. Mock Gemini API to raise `ResourceExhausted` error
2. Call `ThesisFilter.classify()` with `skip_llm=False`
3. Verify fallback behavior:
   - `llm_skipped=True` in result
   - `llm_score = None`
   - Keyword score still populated
   - Warning logged
4. Verify pipeline continues (doesn't crash)

**Mock requirements:**
- Gemini API error simulation

**Assertions:** ~5 (error handling, fallback state, logging)

---

#### Scenario 5: Full Test Suite Baseline
**Purpose:** Ensure no regressions in existing tests

**Commands:**
```bash
# Run all ops tests
pytest tests/ops/ -v --tb=short

# Run storage tests (includes migration 26)
pytest tests/storage/ -v --tb=short

# Run verification tests
pytest tests/verification/ -v --tb=short

# Run integration tests
pytest tests/integration/ -v --tb=short
```

**Expected:**
- **605 existing tests pass** (429 ops + 74 dashboard + 102 API)
- **20+ new tests pass** (Phases 1-5)
- **Total: 625+ tests, 0 failures**

**Failure triage:**
- Check migration 26 applied correctly
- Check env var `LLM_THESIS_MODE` not leaking between tests
- Check mocks for Gemini API

---

### Test File Locations (New)

| File | Tests | Purpose |
|------|-------|---------|
| `tests/workflows/test_pipeline_llm_modes.py` | 3-4 | Pipeline LLM integration (off/shadow/active modes) |
| `tests/consumer/test_llm_rate_limiting.py` | 2-3 | Rate limiting & fallback behavior |
| `tests/ops/test_scheduler_quality.py` | 3-4 | Scheduler quality mode handlers |
| `tests/verification/test_verification_gate_llm.py` | 3-4 | LLM confidence adjustments (Phase 2) |
| `tests/integration/test_quality_ops_e2e.py` | 3 | End-to-end integration scenarios |

**Total new tests:** ~18-20 across 5 files

---

### Manual Validation Plan

After automated tests pass, run manual validation:

1. **Enable LLM in local env:**
   ```bash
   # Edit .env
   LLM_THESIS_MODE=active
   ```

2. **Run pipeline on 10 real signals:**
   ```bash
   python run_pipeline.py full --collectors github --limit 10
   ```

3. **Check DB for LLM results:**
   ```bash
   sqlite3 signals.db "SELECT id, keyword_score, llm_score, thesis_match, disagreement_detected
                       FROM thesis_classifications
                       ORDER BY id DESC LIMIT 10"
   ```

4. **Verify ops health metrics:**
   ```bash
   python -m ops.cli monitor status
   python -m ops.cli quality stats --days 7
   ```

5. **Check disagreement report:**
   ```bash
   python -m ops.cli quality thesis-disagreement-report --days 7
   ```

6. **Screenshot results** and document in `progress.md`

---

### Blockers & Dependencies

#### For Full Phase 5 Testing
- ✅ **Phase 4 complete** (disagreement detection)
- ❓ **Phase 1 status unclear** (LLM env var wired, but still disabled?)
- ❌ **Phase 2 not started** (verification gate LLM integration)
- ❌ **Phase 3 not started** (scheduler quality modes)

#### Recommendation: Incremental Testing
Test what's ready now, defer blocked scenarios:

**Can test now:**
- ✅ Disagreement detection & reporting (Phase 4 complete)
- ✅ Rate limiting & fallback (Phase 1 partial)
- ✅ Full test suite baseline (always runnable)

**Defer until Phases 1-2 complete:**
- ❌ Pipeline with LLM enabled E2E (needs Phase 1 fully wired + Phase 2)
- ❌ LLM confidence adjustments (needs Phase 2)

**Defer until Phase 3 complete:**
- ❌ Scheduled thesis classification E2E

---

### Test Strategy Summary

| Test Type | Count | Status | Blockers |
|-----------|-------|--------|----------|
| Unit tests (rate limiting) | 2-3 | Can write now | None |
| Unit tests (disagreement) | 5 | ✅ Already passing | None |
| Integration (pipeline LLM) | 3-4 | Blocked | Phase 1-2 |
| Integration (scheduler) | 3-4 | Blocked | Phase 3 |
| E2E (full flywheel) | 3 | Partial | Phase 1-2-3 |
| Baseline regression | 605 | ✅ Should pass | None |

**Actionable now:** Write unit tests for rate limiting, run baseline tests, verify Phase 4 works end-to-end.

**Deferred:** Pipeline E2E and scheduler tests wait for Phases 1-3 completion.

---

## Risks & Mitigations (Updated)

### Risk 1: Test Isolation Issues
**Scenario:** Env var `LLM_THESIS_MODE` leaks between tests
**Impact:** Flaky tests, non-deterministic failures
**Mitigation:**
- Use `monkeypatch.setenv()` in pytest fixtures
- Reset to default (`off`) in teardown
- Use separate DB files per test class

### Risk 2: Mock Complexity
**Scenario:** Gemini API mocking too complex, tests become brittle
**Impact:** Tests break on minor API changes
**Mitigation:**
- Use fixtures from existing `tests/ops/quality/test_thesis.py`
- Keep mocks simple (return fixed responses)
- Document mock behavior in test docstrings

### Risk 3: Incomplete Phase 1-3 Blocks E2E Tests
**Scenario:** Phases 1-3 not fully complete, can't write full E2E tests
**Impact:** Phase 5 can't validate end-to-end flows
**Mitigation:**
- Write incremental tests for what's ready (Phase 4)
- Defer blocked tests to Phase 6 or post-merge validation
- Document test coverage gaps in `progress.md`

---

## Success Criteria for Phase 5

- [x] Phase 4 complete (disagreement detection) ✅
- [ ] Unit tests for rate limiting written and passing
- [ ] Baseline test suite runs (4619 tests pass)
- [ ] Phase 4 disagreement detection verified end-to-end
- [ ] Test coverage documented (what's tested, what's deferred)
- [ ] Manual validation checklist prepared (for post-Phase 1-2-3)
- [ ] Gaps documented for Phase 6 (E2E tests blocked by Phases 1-3)

---

## Mock Boundaries for Testing

Testing strategy for LLM integration uses mocks at API boundaries:

| Layer | Mock Strategy | Fixture Location |
|-------|--------------|------------------|
| Gemini API | `unittest.mock` of `google.generativeai.GenerativeModel` | `tests/ops/quality/test_thesis.py` (reuse existing fixtures) |
| ThesisFilter | Pass mock client via dependency injection | Inline in test |
| Pipeline | Mock `_process_signals_stage()` return | `conftest.py` |

**Important rules:**
- Never call real Gemini API in tests
- Always mock at API boundary (not implementation internals)
- Reuse fixtures from `tests/ops/quality/test_thesis.py` where possible
- Use `monkeypatch.setenv()` for `LLM_THESIS_MODE` isolation

**Example mock setup:**
```python
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_gemini_api(monkeypatch):
    """Mock Gemini API to return fixed LLM scores."""
    mock_model = MagicMock()
    mock_model.generate_content.return_value.text = '{"score": 0.85, "category": "Consumer Health Tech"}'

    with patch('google.generativeai.GenerativeModel', return_value=mock_model):
        yield mock_model
```

---

## Related Documents
- `task_plan.md` — Full 6-phase plan with 26 tasks
- `progress.md` — Session log and test results
- `tests/ops/quality/test_e2e_integration.py` — Existing E2E tests
- `docs/QUALITY_OPS_ARCHITECTURE.md` — Phase 8 architecture reference

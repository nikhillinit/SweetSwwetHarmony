# Phase 2 Findings — Functional Schema + Web3 + Intelligence Visibility

**Session:** Phase 2 Planning (2026-02-08)
**Status:** Research complete

---

## Finding 1: Thesis Filter Architecture is Two-Layer, Not One

The codebase has TWO thesis filter implementations:
1. **`consumer/thesis_filter/`** — Two-stage pipeline (hard_disqualifiers.py + llm_classifier.py + pipeline.py)
2. **`utils/thesis_filter.py`** — ThesisFilter class used by `workflows/pipeline.py`

The pipeline (`workflows/pipeline.py:1695`) uses `utils.thesis_filter.ThesisFilter`, NOT `consumer.thesis_filter.pipeline.ThesisFilterPipeline`.

**Impact on Phase 2:**
- Web3 detector integration must target `consumer/thesis_filter/hard_disqualifiers.py` (Stage 1 of the two-stage filter)
- Pipeline wiring for schema extraction must hook into `workflows/pipeline.py` after the `ThesisFilter.classify()` call
- LLM prompt update targets `consumer/thesis_filter/llm_classifier.py`

---

## Finding 2: Current Web3 Handling is Aggressive

`hard_disqualifiers.py` line 56-64: CRYPTO_KEYWORDS includes ambiguous terms:
- `"dao"` — could mean Data Access Object
- `"token"` — could mean auth token, loyalty token
- `"wallet"` — could mean digital payment wallet (non-crypto)
- `"mining"` — could mean data mining
- `"metaverse"` — could mean virtual experiences (non-crypto)
- `"web3"` — always crypto (unambiguous)

Current logic: ANY keyword match → immediate reject. No co-occurrence check.

**Impact:** Legitimate consumer companies using terms like "access tokens" or "DAO pattern" are falsely rejected.

---

## Finding 3: CSV Export is Minimal

`run_pipeline.py:4663-4724`: Current CSV columns:
```
signal_id, company_name, canonical_key, confidence, signal_type, source_api, detected_at, status, company_id
```

No intelligence columns (thesis category, rationale, schema, etc.). The `thesis_classifications` table exists but is not joined into exports.

**Impact:** Operators reviewing CSV have no context beyond basic signal metadata.

---

## Finding 4: Triage CLI Shows Raw Data Only

`run_pipeline.py:4482-4560`: Compact list shows:
- ID, Company (25 chars), Summary (from raw_data), Confidence, Source, Status

Summary is extracted from `raw_data` JSON (description/title/name). No thesis classification or functional schema info.

**Impact:** Operators must mentally reconstruct "why" a signal was flagged.

---

## Finding 5: LLM Prompt Lacks Edge Case Guidance

`llm_classifier.py:39-91`: System prompt defines 4 core categories and exclusions, but no guidance for edge cases:
- Creator economy tools (consumer-facing vs B2B)
- Pet tech (consumer product vs B2B vet software)
- EdTech (consumer app vs enterprise LMS)
- FinTech (consumer budgeting vs B2B payments)
- FoodTech (DTC meal kit vs supply chain)

**Impact:** LLM makes inconsistent decisions on edge cases without explicit guidance.

---

## Finding 6: Schema Extraction Can Reuse Existing LLM Infrastructure

`llm_classifier.py` already has:
- Gemini client with lazy loading
- RateLimiter (15 RPM, 1500 RPD)
- CircuitBreaker (5 failures, 600s timeout)
- JSON response parsing with markdown stripping
- Error handling with graceful degradation

`FunctionalExtractor` can reuse this infrastructure rather than building from scratch. Key reuse:
- Same API key (GOOGLE_API_KEY)
- Shared rate limiter instance
- Same circuit breaker
- Same response parsing logic

**Decision:** FunctionalExtractor should accept optional shared rate_limiter and circuit_breaker instances.

---

## Finding 7: Migration Registration Pattern

Looking at `signal_store.py:74`, the migration system uses `CURRENT_SCHEMA_VERSION = 31`. Migrations are registered in the `_apply_migrations` method. Each migration version has a corresponding function.

**Pattern to follow:**
1. Create `storage/migrations/v32_functional_schema.py` with the DDL
2. Import and register in `signal_store.py`'s migration dict
3. Bump `CURRENT_SCHEMA_VERSION` to 32

---

## Finding 8: Governance Lint Constraint

`tests/test_no_direct_signalstore.py` maintains an ALLOWLIST of files permitted to construct `SignalStore()`. New files that need `SignalStore` must either:
- Be added to the ALLOWLIST, or
- Accept a store instance via dependency injection

**Decision:** `consumer/functional_extractor.py` should accept a store parameter, NOT construct its own. Pipeline wiring passes the existing store.

---

## Finding 9: Test File Naming Convention

Test files follow pattern:
- `tests/storage/test_<module>.py` — storage layer tests
- `tests/consumer/test_<module>.py` — consumer module tests
- `tests/workflows/test_<module>.py` — workflow tests
- `tests/integration/test_<feature>.py` — end-to-end tests
- `tests/performance/test_<feature>_slos.py` — SLO tests

Phase 2 tests should follow this convention.

---

## Finding 10: Consumer Positive Keywords Can Improve Schema Extraction

`hard_disqualifiers.py:88-114` defines `CONSUMER_POSITIVE_KEYWORDS` by category (CPG, Health & Wellness, Travel, Consumer Apps, DTC). These categories align with the customer archetypes needed for functional schema extraction.

**Opportunity:** The functional extractor could use these keywords as hints for archetype classification when LLM is unavailable (heuristic fallback).

---

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| None yet | — | — |

---

## Key Decisions Made

| Decision | Rationale |
|----------|-----------|
| Schema extraction is optional (env var) | Non-breaking, gradual rollout |
| Web3 detector uses 100-char co-occurrence window | Balances precision vs recall |
| FunctionalExtractor reuses Gemini infrastructure | DRY principle, shared rate limits |
| Schema versioning (not overwrite) | Audit trail for company evolution |
| Advisory flag for low-confidence schemas | Don't hide data, but mark reliability |
| Pipeline hook after thesis filter | Only extract for passing signals (LLM budget) |

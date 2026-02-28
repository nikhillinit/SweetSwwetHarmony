# Progress Log: Evidence-Key Dedup

## Session: 2026-02-28

### Phase 1: Codebase Verification
- **Status:** complete
- See findings.md for verified discrepancies (D1-D4, all resolved)

### Phase 2: Test Suite Design
- **Status:** complete
- 67 total tests written across 5 test files

### Phase 3: Implementation (PR-1)
- **Status:** complete
- **Files created:**
  - `utils/evidence_key.py` — normalize_url, compute_evidence_key, extract_source_url_from_raw_data
  - `storage/migrations/v45_evidence_key.py` — ADD COLUMN + non-unique partial index
  - `tests/utils/test_evidence_key.py` — 33 tests
  - `tests/storage/test_evidence_key_dedup.py` — 12 tests
  - `tests/collectors/test_provenance_retrofit.py` — 11 tests
- **Files modified:**
  - `storage/signal_store.py` — v45, evidence_key kwarg, SELECT-then-INSERT, tx_immediate
  - `collectors/base.py` — evidence_key in _save_signals + _check_duplicates
  - `collectors/provenance.py` — container-URL warning helper
  - `collectors/news_api.py` — _provenance block
  - `collectors/rss_feeds.py` — _provenance block
  - `collectors/telegram.py` — _provenance block
  - `collectors/discord.py` — _provenance block

### Phase 4: Implementation (PR-2)
- **Status:** complete
- **Files created:**
  - `scripts/backfill_evidence_keys.py` — backfill + --preflight + --dry-run
  - `storage/migrations/v46_evidence_key_unique.py` — UNIQUE partial index
  - `tests/scripts/test_backfill_evidence_keys.py` — 8 tests
  - `tests/storage/test_v46_unique_constraint.py` — 3 tests
- **Note:** v46 migration NOT wired into signal_store.py yet — requires backfill first

### Phase 5: Verification
- **Status:** complete
- All 67 new tests pass
- Regression gate: 660 passed, 0 failed

## Test Results
| Suite | Count | Status |
|-------|-------|--------|
| test_evidence_key.py | 33 | PASS |
| test_evidence_key_dedup.py | 12 | PASS |
| test_provenance_retrofit.py | 11 | PASS |
| test_backfill_evidence_keys.py | 8 | PASS |
| test_v46_unique_constraint.py | 3 | PASS |
| Regression gate (api+integration) | 660 | PASS |

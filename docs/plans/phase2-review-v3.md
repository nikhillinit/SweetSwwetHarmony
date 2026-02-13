# Phase 2 Plan — Critical Review of External Comments (v3, revised)

**Date:** 2026-02-09 (revised after feedback round)
**Reviewer:** Claude (evidence-based, against actual codebase)
**Input:** 10 comment groups from external reviewer + 5 correction points on v3
**Method:** Each comment verified against codebase before accepting/rejecting

---

## Corrections Applied to v3

Five factual errors in the original v3 review were identified and corrected:

1. **Comment 2 (production path):** The plan *already* targets `utils/thesis_matcher.py` for production wiring and labels `consumer/` changes as parity. My original characterization of "plan targets wrong module" was overstated. Corrected to: plan is directionally right; ensure production wiring is primary and consumer changes are optional parity.

2. **Comments 3.1–3.3 (re-extraction/staleness/budget):** My rejection said "Phase 2 doesn't have re-extraction." But the plan text (Task 2.8) explicitly includes `update_schema_on_new_evidence()` and the plan describes conditional refresh semantics. Either (a) the plan must be trimmed to match the extract-once intent, or (b) the critiques stand. **Resolution: trim the plan** — remove `update_schema_on_new_evidence` from Phase 2 scope, defer to Phase 3.

3. **Comment 1.1 (dao/mining/metaverse):** My claim that these are "already in AMBIGUOUS_TERMS" was wrong. The plan text lines 184-191 show `dao`, `mining`, `metaverse` in the `AMBIGUOUS_TERMS` set, BUT the test expectations on line 213 ("DAO pattern in code → PASS") contradict lines 175-181 where they'd actually be in UNAMBIGUOUS_CRYPTO if the comment's concern is valid. **Re-examining:** the plan code *does* put them in AMBIGUOUS_TERMS (line 184-191). However, line 244 says "crypto keywords that are unambiguous still reject" — this creates ambiguity about intent. **Resolution: the plan text is internally consistent** (AMBIGUOUS_TERMS, not UNAMBIGUOUS), but the plan should add an explicit note confirming this classification.

4. **Comment 8 (rollback):** Rejection stands, but should include the exact rollback order as a one-liner: `DROP INDEX → DROP TABLE → reset version`. Cheap insurance.

5. **Concurrency rejections (4.1, 6.1, 6.2):** Rejections are correct, but should be stated as an explicit plan invariant rather than left implicit.

---

## Verdict Summary (Revised)

| # | Comment | Verdict | Rationale |
|---|---------|---------|-----------|
| 1.1 | dao/mining/metaverse should be ambiguous | **ACCEPT** | Plan has them in AMBIGUOUS_TERMS. Add explicit note confirming classification. |
| 1.2 | Rule ordering unsafe | **ACCEPT with modification** | Adopt 5-step formalization. Add test cases. |
| 1.3 | Rescue phrase boundary-aware | **ACCEPT** | Legitimate substring trap risk. |
| 1.4 | "crypto" ambiguity with "cryptography" | **REJECT — OVERENGINEERED** | No real-world FP evidence. "cryptography" companies aren't in deal flow. |
| 1.5 | Co-occurrence window tuning hooks | **ACCEPT (env var only)** | Structured logging counters are premature instrumentation. |
| 1.6 | Module placement layering smell | **ACCEPT with different location** | Agree on principle; `utils/web3_detector.py` not `consumer/exclusions/`. |
| 2 | Confirm production reject path | **ACCEPT (rephrased)** | Plan correctly targets `utils/` for production. Consumer changes are optional parity. Detector should live in `utils/`. |
| 3.1 | extracted_at vs updated_at | **ACCEPT → trim plan** | Plan includes `update_schema_on_new_evidence()` which creates the problem. Fix: remove that method from Phase 2 scope. With extract-once, `created_at` suffices. |
| 3.2 | should_re_extract_schema underdefined | **ACCEPT → trim plan** | Same: plan includes re-extraction semantics that should be deferred. Remove from Phase 2. |
| 3.3 | Budget exhaustion defers refresh | **ACCEPT → trim plan** | Same: with re-extraction removed, this concern disappears. |
| 4.1 | Evidence update concurrent overwrite | **REJECT — no concurrency** | Pipeline is sequential. Add explicit invariant to plan. |
| 4.2 | Validate evidence belongs to company | **ACCEPT (cheap guard)** | Simple assertion, low cost, prevents silent corruption. |
| 5 | Archetype-only versioning locks stale text | **PARTIAL ACCEPT** | Advisory→non-advisory upgrade valid. Text improvement deferred to Phase 3. |
| 6.1 | Shared rate limiter starvation | **REJECT** | Pipeline is sequential. Add explicit invariant to plan. |
| 6.2 | Budget enforcement concurrency-safe | **REJECT** | Same: sequential processing. Add explicit invariant to plan. |
| 7.1 | Performance validation missing | **ACCEPT** | Lightweight benchmark test is good practice. |
| 7.2 | Advisory visibility inconsistent | **ACCEPT** | Unify policy: always show, always flag. |
| 7.3 | Test file naming conventions | **ACCEPT** | Trivial fix, should match existing patterns. |
| 8 | Migration rollback not implemented | **REJECT (tightened)** | No downgrade framework needed. Add exact rollback order as one-liner in plan. |
| 9.1 | "professionals" archetype undefined | **ACCEPT** | If added, must be in prompt. |
| 9.2 | Truncation mid-negation | **REJECT — theoretical** | 4000 chars is generous. No evidence this causes real problems. |
| 10 | Minimum must-add tests | **PARTIAL ACCEPT** | Web3 ordering, dao/mining ambiguity, perf benchmark valid. Re-extraction tests deferred with re-extraction. |

---

## Detailed Analysis

### Comment 1.1: dao/mining/metaverse taxonomy — ACCEPT

**Evidence:** The Phase 2 plan (lines 184-191) places `dao`, `mining`, `metaverse`, `web3` in `AMBIGUOUS_TERMS`, not in `UNAMBIGUOUS_CRYPTO`. The plan's test expectations (line 213: "DAO pattern in code → PASS") confirm the intent is ambiguous treatment.

However, line 244 ("crypto keywords that are unambiguous still reject") creates a confusing statement that could be misread. The plan should add an explicit note: "dao, mining, metaverse are classified as AMBIGUOUS (co-occurrence required), not unambiguous."

**Action:**
- Confirm AMBIGUOUS_TERMS classification in plan with explicit note
- Accept rescue phrases (`"data access object"`, `"data mining"`, `"process mining"`) as a refinement
- Ensure test expectations are consistent with ambiguous classification throughout

---

### Comment 1.2: Rule ordering — ACCEPT with modification

**Evidence:** The plan's algorithm (Task 2.5, lines 204-208) already specifies:
1. Unambiguous crypto → reject immediately
2. Ambiguous term found → scan ±100 chars for CRYPTO_CONTEXT
3. Co-occurrence → reject; no co-occurrence → pass

This is already safe: unambiguous terms always win. The comment's concern about "rescue phrases checked first" doesn't match the plan — the plan has no rescue-first ordering.

**However**, the comment's proposed 5-step formalization is clearer than the plan's prose. And the test case `"blockchain cryptography library" → REJECT` is valuable because it tests that unambiguous terms can't be neutralized.

**Action:** Adopt the clearer 5-step decision order from the comment. Add the test cases. No architectural change needed.

---

### Comment 1.3: Rescue phrase boundary-aware — ACCEPT

**Evidence:** Valid concern. If we add rescue phrases, naive substring matching on `"access token"` would fail on `"access tokens"` (plural) or `"access-token"` (hyphenated). The `_contains_keywords()` function in `hard_disqualifiers.py:126-143` already uses `\b` word boundaries for single words and substring for phrases. The Web3Detector should follow the same boundary-aware pattern.

**Action:** Implement rescue phrase matching with `\b` word boundaries. Support common variations. Add the specified test cases.

---

### Comment 1.4: "crypto" ambiguity with "cryptography" — REJECT (overengineered)

**Evidence:** The plan already has `"crypto"` in `AMBIGUOUS_TERMS` (line 189 shows it's not there — wait, checking the plan again... actually `"crypto"` is NOT in the plan's AMBIGUOUS_TERMS list, it's not in UNAMBIGUOUS_CRYPTO either). Let me re-examine.

Looking at the plan's Task 2.5:
- UNAMBIGUOUS_CRYPTO includes: `"blockchain", "cryptocurrency", "bitcoin", ...`
- AMBIGUOUS_TERMS includes: `"token", "dao", "wallet", "mining", "metaverse", "web3"`
- `"crypto"` appears nowhere explicitly → it would remain handled by ThesisMatcher's NEGATIVE_KEYWORDS as a soft penalty

**Reality check:** How often do "cryptography" companies appear in consumer deal flow? Press On Ventures invests in consumer CPG, health tech, travel, and marketplaces. The probability of a pure cryptography library showing up as a consumer signal is near zero. This is a theoretical concern with no practical impact.

**Verdict:** Not worth the complexity. If it ever becomes a real FP source, add it then. YAGNI.

---

### Comment 1.5: Co-occurrence window tuning hooks — PARTIAL ACCEPT

**Accept:** Making `CO_OCCURRENCE_WINDOW` configurable via env var is cheap and useful.

**Reject:** "Structured logging counters (term found, rescued occurrence count, co-occurrence rejects, unambiguous rejects)" is premature instrumentation. The Web3 detector will run on <50 signals/day. A simple `logger.info()` on each reject decision is sufficient. Full counters add code and maintenance for a feature we don't yet know needs tuning.

**Action:** Add env var for window size. Use standard logging. Skip structured counters.

---

### Comment 1.6: Module placement — ACCEPT (different location)

**Evidence:** The comment correctly identifies that `consumer/exclusions/` is an odd place for a utility used by `utils/thesis_matcher.py`.

**However**, the comment's suggestion of `utils/exclusions/web3_detector.py` or `common/exclusions/` doesn't match codebase conventions. There is no `common/` directory. The `utils/` directory is flat (no subdirectories for specific features).

**Action:** Place as `utils/web3_detector.py` (flat, matching existing `utils/thesis_matcher.py` pattern). No new subdirectory needed.

---

### Comment 2: Production reject path — ACCEPT (rephrased)

**Evidence:**

```
workflows/pipeline.py:47 → from utils.thesis_filter import ThesisFilter
ThesisFilter.__init__:129 → self._keyword_matcher = ThesisMatcher()
ThesisFilter.classify():163 → keyword_fit = self._keyword_matcher.score(...)
```

Production does NOT use `consumer/thesis_filter/hard_disqualifiers.py`. The `HardDisqualifiers` class is only used in tests and quality ops.

**Correction from v3 feedback:** The plan already identifies `utils/thesis_matcher.py` as the production target and labels the `consumer/thesis_filter/hard_disqualifiers.py` change as parity. My original characterization of "plan targets wrong module" was overstated.

**What the plan should clarify:**
- Production wiring (`utils/thesis_filter.py` → `Web3Detector`) is the **primary** deliverable
- Consumer wiring (`consumer/thesis_filter/hard_disqualifiers.py`) is **optional parity** and should be labeled as such
- The Web3Detector should live at `utils/web3_detector.py` (not `consumer/exclusions/`), since its primary consumer is the `utils/` path

**Recommended wiring:** Add a pre-check in `ThesisFilter.classify()` before `ThesisMatcher.score()` that calls `Web3Detector.detect()`. If crypto detected → short-circuit to REJECTED. If not → proceed with normal scoring. This keeps routing decisions in the filter and scoring in the matcher.

---

### Comments 3.1–3.3: Re-extraction, staleness, budget — ACCEPT (plan must be trimmed)

**v3 correction:** My original rejection said "Phase 2 doesn't have re-extraction." But the plan text contradicts this — Task 2.8 explicitly defines `update_schema_on_new_evidence(company_id, new_schema)` with schema comparison, versioning logic, and evidence tracking. This IS re-extraction/refresh logic, even if Task 2.4 also says "skip if schema exists."

**The plan is internally inconsistent:**
- Task 2.4 says "skip extraction if schema already exists" (extract-once)
- Task 2.8 says "supersede current schema with new version if meaningfully different" (re-extraction)

These can't both be true. Either Phase 2 handles updates or it doesn't.

**Resolution: trim the plan to extract-once.**

The comments on `extracted_at` vs `updated_at`, `should_re_extract_schema`, and budget-exhaustion-deferred-refresh are all valid critiques **of the plan as written**. Rather than addressing them with additional complexity in Phase 2, remove the contradiction:

1. **Task 2.8:** Remove `update_schema_on_new_evidence()`. Keep only `save_functional_schema()`, `get_active_schema()`, `get_schema_history()`.
2. **Task 2.4:** Confirm extract-once: "if active schema exists for company_id → skip extraction."
3. **DDL:** `created_at` only. No `updated_at` needed for immutable version rows.
4. **Plan:** Add explicit note: "Schema refresh/re-extraction is Phase 3+ scope."

This makes the rejections correct by making the plan consistent. The comments' concerns about timestamp semantics, refresh triggers, and budget exhaustion become irrelevant for Phase 2 and correctly scoped for Phase 3.

---

### Comment 4.1: Evidence update concurrent overwrite — REJECT (with documented invariant)

**Evidence from pipeline.py:**
- Collectors run in parallel (line 1040-1048: `asyncio.gather`)
- But signal PROCESSING is sequential per company group (line 1695+: `for canonical_key, signals in groups`)
- There is no concurrent processing of the same company
- SQLite uses WAL mode with single-writer lock — concurrent writes from different processes would serialize anyway

The comment assumes concurrent workers writing to the same schema row. This doesn't happen in the current architecture.

**Action:** Rejection stands, but add an explicit plan invariant:

> **Concurrency invariant:** Phase 2 assumes single-run, sequential writes per company_id. Signal processing iterates company groups sequentially (`for canonical_key in groups`). Collectors run in parallel, but schema extraction occurs during sequential processing. If concurrent pipeline runs or parallel company processing are added in future, schema writes must be wrapped in `BEGIN IMMEDIATE` transactions with proper conflict handling.

---

### Comment 4.2: Validate evidence belongs to company — ACCEPT

**Evidence:** This is a cheap defensive check. When appending signal_ids to evidence_signal_ids JSON, verifying `signal.company_id == schema.company_id` prevents silent data corruption from bugs elsewhere. One `assert` or check in the store method.

**Action:** Add a guard in `save_functional_schema()` that verifies signal_ids belong to the company.

---

### Comment 5: Archetype-only versioning — PARTIAL ACCEPT

**Evidence:** The versioning rule (same archetype → no new version, different archetype → new version) is simple and deterministic. The comment correctly identifies two gaps:

1. **Advisory → non-advisory upgrade:** If confidence rises above threshold on re-extraction, the schema should transition from advisory to non-advisory. This is valid and cheap.

2. **Text improvement:** "You can never improve problem_solved_text even if later evidence yields better description." This is a Phase 3 concern since Phase 2 doesn't re-extract. But the versioning policy should be documented to allow future refinement.

**Action:**
- Add: advisory→non-advisory transition when confidence rises above threshold (Phase 2)
- Document: text improvement rules deferred to Phase 3 re-extraction feature

---

### Comments 6.1 & 6.2: Rate limiter starvation and budget concurrency — REJECT (with documented invariant)

**Evidence:**
- Pipeline processes signals sequentially (one company at a time)
- Schema extraction happens after thesis classification for each signal
- No concurrent LLM calls within a single pipeline run
- The shared rate limiter (15 RPM) processes one call at a time

Starvation requires concurrent consumers. There is one consumer. The "two-bucket limiter" suggestion adds complexity with zero benefit in a sequential pipeline.

**Action:** Rejection stands. Covered by the same concurrency invariant as Comment 4.1. If concurrent processing is introduced, rate limiter partitioning becomes a prerequisite.

---

### Comment 7.1: Performance validation — ACCEPT

**Evidence:** The plan claims SLOs (CSV export < 2s for 500 signals) but adds JOINs. A lightweight smoke test is good practice to catch regressions.

**Action:** Add a performance smoke test in `tests/performance/test_phase2_slos.py` — populate representative data, measure export time, assert under SLO.

---

### Comment 7.2: Advisory visibility — ACCEPT

**Evidence:** The plan is inconsistent: CSV export "always shows advisory flagged" but triage CLI "shows advisory in verbose mode only." Users switching between CSV and CLI will see different data.

**Action:** Unify: always show schemas, always flag advisory with a marker (e.g., `*` suffix on archetype or `[advisory]` text). Verbose mode shows full evidence details, not advisory-filtered view.

---

### Comment 7.3: Test file naming — ACCEPT

**Evidence:** Existing convention: `tests/<layer>/test_<module>.py`. The plan's test files already follow this. Trivial alignment.

---

### Comment 8: Migration rollback — REJECT (tightened)

**Evidence:** The migration system in `signal_store.py` has no `downgrade()` mechanism. Every migration in `storage/migrations/` is forward-only. Adding a `v32_rollback.sql` artifact creates a maintenance burden for a scenario that has never occurred in 31 prior migrations.

**However**, the feedback is right that specifying the exact rollback order is cheap insurance. During an incident, getting the order wrong (dropping table before indexes that reference it, or forgetting to reset the version number) wastes time.

**Action:** Add a one-liner rollback block to the plan:
```sql
-- v32 rollback (execute in order):
DROP INDEX IF EXISTS idx_fs_archetype;
DROP INDEX IF EXISTS idx_fs_company_active;
DROP TABLE IF EXISTS functional_schemas;
-- Then reset: UPDATE schema_version SET version = 31 WHERE version = 32;
```
No downgrade framework or separate artifact needed.

---

### Comment 9.1: "professionals" archetype — ACCEPT

**Evidence:** If the archetype list is expanded, the LLM prompt must include all valid values with definitions. Undefined archetypes cause validation failures.

**Action:** Ensure all archetypes in the `customer_archetype` validation list are documented in the prompt.

---

### Comment 9.2: Truncation mid-negation — REJECT

**Evidence:** The existing LLM classifier truncates at 500 chars (llm_classifier.py:327-328). The plan proposes 4000 chars for schema extraction. At 4000 chars, the probability of cutting a meaningful negation is negligible. Sentence-boundary truncation adds regex complexity for no demonstrated benefit.

The comment's example ("not crypto", "not blockchain") is unrealistic — signals don't contain self-negating descriptions. If a signal says "not crypto," the keyword matcher has already handled it.

**Action:** No change. Keep simple truncation.

---

### Comment 10: Must-add tests — PARTIAL ACCEPT

**Accept these tests:**
1. Web3 ordering + rescue interaction (unambiguous overrides rescue)
2. DAO/mining/metaverse ambiguity (DAO pattern PASS; DAO governance REJECT)
3. Export performance smoke benchmark

**Reject these tests:**
4. Budget exhaustion + future re-extraction → Phase 2 doesn't re-extract
5. Evidence update concurrency safety → no concurrent writes
6. Age-based refresh uses extracted_at → no re-extraction in Phase 2

---

## Required Plan Changes (Prioritized)

### CRITICAL (must fix before implementation)

1. **Task 2.8:** Remove `update_schema_on_new_evidence()` from Phase 2 scope. Keep only `save_functional_schema()`, `get_active_schema()`, `get_schema_history()`. Defer refresh/re-extraction to Phase 3.
2. **Task 2.5:** Move Web3Detector to `utils/web3_detector.py` (not `consumer/exclusions/`). Production wiring is primary; consumer parity is optional.
3. **Task 2.6:** Clarify that production integration (`utils/thesis_filter.py`) is the primary deliverable. Consumer integration (`hard_disqualifiers.py`) is labeled as optional parity.
4. **Task 2.5:** Add rescue phrase mechanism with word-boundary matching.

### IMPORTANT (should fix)

5. **Plan:** Add explicit concurrency invariant: "Phase 2 assumes single-run, sequential writes per company_id."
6. **Task 2.5:** Add env var for co-occurrence window (`WEB3_COOCCURRENCE_WINDOW`)
7. **Task 2.9/2.10:** Unify advisory visibility policy (always show, always flag)
8. **Tests:** Add performance smoke test for CSV export with JOINs
9. **Task 2.3:** Allow advisory→non-advisory transition when confidence rises
10. **Task 2.1:** Add exact rollback order as one-liner in plan (DROP INDEX → DROP TABLE → reset version)
11. **Task 1.1:** Add explicit note confirming dao/mining/metaverse as AMBIGUOUS classification
12. **Task 2.8:** Add company_id guard in `save_functional_schema()` for evidence signal_ids

### MINOR (nice to have)

13. **Task 2.5:** Adopt 5-step decision order formalization in docstring
14. **Plan:** Add note: "Schema refresh/re-extraction is Phase 3+ scope"

---

## Rejected Comments (with rationale)

| Comment | Why Rejected |
|---------|-------------|
| 1.4 "crypto"/"cryptography" ambiguity | No real-world FP evidence; cryptography companies not in consumer deal flow |
| 4.1 Evidence concurrent overwrite | Pipeline is sequential; documented as invariant |
| 6.1 Shared rate limiter starvation | Sequential pipeline; documented as invariant |
| 6.2 Budget enforcement concurrency | Same; documented as invariant |
| 9.2 Truncation mid-negation | 4000 chars is generous; no evidence of real-world issue |

### Comments resolved by plan trimming (no longer applicable)

| Comment | Resolution |
|---------|-----------|
| 3.1 extracted_at vs updated_at | Removing `update_schema_on_new_evidence` eliminates the timestamp confusion. Extract-once with versioned rows means `created_at` = extraction time. |
| 3.2 should_re_extract_schema | Removing re-extraction from Phase 2 scope makes this moot. Deferred to Phase 3. |
| 3.3 Budget exhaustion refresh | With extract-once, budget exhaustion is self-healing (retry next run). No persistent "needs_refresh" flag needed. |

---

## Key Codebase Evidence

### Production flow (verified):
```
workflows/pipeline.py:47    → imports utils.thesis_filter.ThesisFilter
utils/thesis_filter.py:129  → creates ThesisMatcher()
utils/thesis_filter.py:163  → calls ThesisMatcher.score()
utils/thesis_matcher.py:159 → NEGATIVE_KEYWORDS dict (soft penalties)
utils/thesis_filter.py:173  → if negative_keywords matched → REJECTED
```

### consumer/thesis_filter/ is NOT used in production:
```
$ grep -r "consumer.thesis_filter\|consumer/thesis_filter" workflows/pipeline.py
(no results)
```

### Pipeline processing is sequential per company:
```
workflows/pipeline.py:1695  → for canonical_key in groups (sequential)
Collectors are parallel (line 1048), processing is NOT.
```

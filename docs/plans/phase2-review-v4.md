# Phase 2 Plan — Review of External Comments (v4, final amendments)

**Date:** 2026-02-09
**Reviewer:** Claude (evidence-based, against actual plan text + codebase)
**Input:** 10 comments from external reviewer on v3 plan
**Method:** Each comment verified against the **current** plan text (post-v3 edits) and codebase

---

## Overall Verdict

**Approve with 4 targeted amendments.** Six of the ten comments are already addressed by the v3 edits applied earlier today, or are based on stale readings of the plan. Four comments identify real gaps that should be fixed before implementation. None require architectural changes — they're all precision edits.

---

## Comment-by-Comment Verdict

| # | Comment | Verdict | Rationale |
|---|---------|---------|-----------|
| 1 | Web3 production integration inconsistent | **ALREADY FIXED** | v3 edits corrected file paths, task titles, and files table |
| 2 | Invariant vs Task 2.8 contradiction | **ALREADY FIXED** | v3 edits removed `update_schema_on_new_evidence()` from Task 2.8 |
| 3 | Prompt missing `schema_confidence` field | **ACCEPT — must fix** | Real gap: prompt asks for 5 fields, confidence derivation unspecified |
| 4 | `"proof of"` in CRYPTO_CONTEXT | **ACCEPT — must fix** | "proof of concept" is common in startup descriptions |
| 5 | `web3` ambiguous vs unambiguous | **ACCEPT — policy decision needed** | Investment thesis says "no crypto/Web3" |
| 6 | Hyphen normalization scoping | **ACCEPT — minor** | Scope to rescue phrase matching only |
| 7 | Verify ThesisFilterResult interface | **VERIFIED COMPATIBLE** | Actual interface has `rejection_reason` + `negative_keywords` |
| 8 | `has_active_schema()` index-friendly | **ALREADY ADDRESSED** | Plan says "EXISTS query", index `idx_fs_company_active` covers it |
| 9 | Circular import prevention | **ACCEPT — minor** | Add one-line note to Task 2.5 |
| 10 | Rollback is manual | **ALREADY ADDRESSED** | Plan shows manual SQL block with ordering |
| — | Signal selection for extract-once | **ACCEPT — must fix** | Quality lever: which signal feeds extraction? |

---

## Detailed Analysis

### Comment 1: Web3 production integration — ALREADY FIXED

**Reviewer claim:** "Tasks and file lists still emphasize HardDisqualifiers / consumer/ integration and even list `consumer/exclusions/web3_detector.py` as a 'New File'."

**Evidence from current plan text:**
- Invariant #4 (line 26): "Production path is `utils/`." ✅
- Architecture table (line 39): "Web3 module location: `utils/web3_detector.py`" ✅
- Architecture table (line 40): "Pre-check in `ThesisFilter.classify()`" ✅
- Task 2.5 (line 186): "File: `utils/web3_detector.py`" ✅
- Task 2.6 (line 282): "Primary: Production path (`utils/thesis_filter.py`)" ✅
- Task 2.6 (line 311): "Optional parity: Consumer path" ✅ labeled optional
- Files table (line 531): `utils/web3_detector.py` ✅ (no `consumer/exclusions/`)
- Files table (line 543): `utils/thesis_filter.py` as modified ✅
- Files table (line 544): `hard_disqualifiers.py` labeled "(Optional parity)" ✅

**Verdict:** The reviewer read a stale version. All 8 locations in the plan are now consistent. No action needed.

---

### Comment 2: Invariant vs Task 2.8 contradiction — ALREADY FIXED

**Reviewer claim:** "Task 2.8 still includes `update_schema_on_new_evidence`, version superseding, and even 'update in place'."

**Evidence from current plan text:**
- Task 2.8 title (line 353): "Schema storage methods (extract-once)" ✅
- Task 2.8 methods (lines 360-375): Only `save_`, `get_active_`, `get_history_`, `has_active_` ✅
- Task 2.8 (line 378): "**Deferred to Phase 3:** `update_schema_on_new_evidence()`, conditional refresh, and age-based re-extraction." ✅
- Task 2.8 (line 383): "Schema rows are immutable once created (Phase 2 does not update in-place)" ✅

**Verdict:** The reviewer read a stale version. The contradiction was resolved in v3 edits.

**One remaining artifact:** Risk mitigation (line 558) says "Schema versioning bloat | Only create new version if archetype/problem changed, not minor text." This implies schema updates that don't exist in extract-once Phase 2. **Should be reworded** to: "Schema table growth | One row per company; bounded by company count, not signal count."

---

### Comment 3: Prompt missing `schema_confidence` field — ACCEPT (must fix)

**Evidence from current plan text:**
- Task 2.2 prompt (lines 109-116): Asks for 5 fields. Does NOT include confidence.
- Task 2.2 (line 122): "Confidence score derived from LLM's self-assessment"
- Task 2.3 (lines 139-141): Uses confidence for `is_advisory` gating

The plan claims confidence comes from "LLM's self-assessment" but the prompt never asks for it. This is a real contract gap — the extractor code would have no confidence value to gate on.

**Plan edit:**
- Add field 6 to the prompt: `"6. Confidence: How confident are you in this extraction? (0.0 = guessing, 1.0 = certain). Output as a number."`
- Add to prompt output contract: `{"problem_solved": "...", "customer": "...", "approach": "...", "customer_archetype": "...", "problem_archetypes": [...], "schema_confidence": 0.85}`
- Add test: confidence value validated as float in [0.0, 1.0]

---

### Comment 4: `"proof of"` in CRYPTO_CONTEXT — ACCEPT (must fix)

**Evidence from current plan text:**
- Line 240: `"proof of"` is in CRYPTO_CONTEXT
- Startup signal descriptions commonly contain "proof of concept" phrases
- A signal like "access token proof of concept for loyalty platform" would trigger: ambiguous `"token"` near context `"proof of"` → false REJECT

**Plan edit:**
- Replace `"proof of"` with `"proof of work"`, `"proof of stake"` in CRYPTO_CONTEXT
- Also phrase-scope `"mint"` → `"mint nft"`, `"minting tokens"` (otherwise "mint condition" near "wallet" could trigger)
- Keep `"ledger"` as-is — "ledger" in accounting context is unlikely to co-occur with ambiguous crypto terms in consumer deal flow, and adding rescue phrases for it would be overengineering
- Add test: "proof of concept token launch" — the word "token" is ambiguous, but "proof of concept" should NOT be crypto context. After the fix, this requires actual crypto context (`"proof of work"`, `"proof of stake"`) to trigger.

---

### Comment 5: `web3` policy — ACCEPT (policy decision needed)

**Evidence:**
- Investment thesis exclusion list: "crypto/Web3" — listed as a category-level exclusion
- Current plan (line 220): `"web3"` is in AMBIGUOUS_TERMS
- This means "web3 marketplace" passes if no CRYPTO_CONTEXT terms nearby

**Analysis:** A company self-describing as "web3" is almost certainly in the crypto/Web3 space. The only non-crypto use is loose "Web 3.0 = next-gen internet" marketing, which is heavily crypto-adjacent. Since the thesis explicitly excludes "Web3" as a category, `"web3"` should be **unambiguous**.

**Plan edit:**
- Move `"web3"` from AMBIGUOUS_TERMS to UNAMBIGUOUS_CRYPTO
- Add test: `"web3 marketplace for food delivery"` → REJECT (unambiguous, no rescue)
- Add test: `"web 3.0 era consumer app"` → PASS (`"web 3.0"` ≠ `"web3"`, word-boundary matching)

---

### Comment 6: Hyphen normalization scoping — ACCEPT (minor)

**Evidence from current plan text:**
- Line 255: "Hyphenated forms (`access-token`) handled by normalizing hyphens to spaces before matching."

This is ambiguous about scope. Global normalization would mangle "co-op", "well-being", etc.

**Plan edit:**
- Clarify line 255: "Hyphenated forms handled by normalizing hyphens to spaces **during rescue phrase matching only** (not applied to the full input text)."

---

### Comment 7: ThesisFilterResult interface — VERIFIED COMPATIBLE

**Evidence from codebase (`utils/thesis_filter.py:47-67`):**
```python
class ThesisFilterResult:
    routing: RoutingDecision                    # ✅ plan uses RoutingDecision.REJECTED
    negative_keywords: List[str] = ...          # ✅ plan uses [web3_result.matched_term]
    rejection_reason: Optional[str] = None      # ✅ plan uses web3_result.reason
```

The plan's Task 2.6 code (lines 295-300) is fully compatible with the actual interface. No field name mismatch.

**Action:** No plan change needed. Add a one-line verification note to Task 2.6: "Verified: `ThesisFilterResult` has `rejection_reason` and `negative_keywords` fields (see `utils/thesis_filter.py:47`)."

---

### Comment 8: `has_active_schema()` index-friendly — ALREADY ADDRESSED

Plan line 384 says "lightweight `EXISTS` query." Index `idx_fs_company_active` on `(company_id, is_active)` covers this. Implementation detail (`SELECT 1 ... LIMIT 1`) is consistent with the plan's intent.

---

### Comment 9: Circular import prevention — ACCEPT (minor)

**Plan edit:**
- Add one-line note to Task 2.5: "**Import constraint:** `utils/web3_detector.py` must remain self-contained. No imports from `consumer/`, `workflows/`, or `storage/`. Only stdlib + `os` + `re`."

---

### Comment 10: Rollback is manual — ALREADY ADDRESSED

Plan lines 78-84 show manual SQL with ordering. The "if needed" framing and comment-only format match the existing convention (31 prior migrations, zero downgrades).

---

### Additional: Signal selection for extract-once — ACCEPT (must fix)

**Evidence from current plan text:**
- Task 2.4 (line 161): `if schema_extractor and thesis_result.routing != "REJECT":`
- This runs per-signal after classify. With `has_active_schema` guard, the first passing signal in the company group wins.
- Signal ordering within a group may be arbitrary (insertion order, source order, etc.)

**Problem:** If a company has 3 signals (one low-quality github mention, one high-quality SEC filing, one medium job posting), the github mention could win the extraction race simply because it was processed first. This is a quality lever that doesn't add complexity.

**Plan edit — add to Task 2.4:**
> **Signal selection for extraction:** When a company group contains multiple passing signals, use the signal with the highest confidence score for schema extraction. If tied, prefer `sec_edgar` > `job_postings` > `news_api` > other. This is implemented as a sort before the extraction loop, not a separate selection step.

---

## Summary of Required Amendments

### Must-fix (4 items)

| # | What | Where | Edit |
|---|------|-------|------|
| 3 | Add `schema_confidence` to prompt contract | Task 2.2 | Add field 6 + JSON output example |
| 4 | Fix `"proof of"` in CRYPTO_CONTEXT | Task 2.5 | → `"proof of work"`, `"proof of stake"`; phrase-scope `"mint"` |
| 5 | Move `web3` to UNAMBIGUOUS_CRYPTO | Task 2.5 | Policy: thesis excludes Web3 categorically |
| — | Signal selection for extract-once | Task 2.4 | Highest-confidence signal feeds extraction |

### Should-fix (3 items)

| # | What | Where | Edit |
|---|------|-------|------|
| 6 | Scope hyphen normalization | Task 2.5 | Rescue phrase matching only, not global |
| 9 | Import constraint note | Task 2.5 | One-line: no `consumer/`/`workflows/`/`storage/` imports |
| — | Risk table artifact | Risk Mitigation | Reword "versioning bloat" to match extract-once |

### Already addressed (5 items) — no action needed

Comments 1, 2, 7, 8, 10

---

## Test Additions from Reviewer

| Test | Verdict | Rationale |
|------|---------|-----------|
| Window boundary (position 100/101) | **Already in plan** (line 274) | Listed as "Window boundary test" |
| Empty/None input → PASS | **ACCEPT** | Defensive, add to Task 2.5 tests |
| "cryptography + access tokens" not crypto | **REJECT** | `"cryptography"` is not in CRYPTO_CONTEXT or UNAMBIGUOUS_CRYPTO. Would already pass. |
| Ordering: "bitcoin access token" → REJECT | **Already in plan** (line 272) | Listed explicitly |
| `web3` policy test | **ACCEPT** | Covered by Comment 5 fix above |
| Perf smoke test | **Already in plan** (line 536) | `tests/performance/test_phase2_slos.py` |

**Net new test to add:** Empty/None input to `Web3Detector.detect()` → PASS (defensive guard).

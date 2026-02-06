# Critical Review Findings: Hard/Soft Negative Strategy (vNext)

## Review Sources
- Logic Review Agent (code-reviewer)
- Security Auditor Agent (implementation risks)
- Explore Agent (codebase validation)

---

## BLOCKING ISSUES (Must Fix Before Implementation)

### 1. Category vs Hardcoded Conflict
**Severity:** BLOCKING

**Problem:** Document presents TWO conflicting strategies:
- Strategy document: `HARD_CATEGORIES = {CRYPTO_WEB3}` only
- Previous iteration log: `HARD = {CRYPTO_WEB3, DEVTOOLS, EDUCATIONAL, STAGES, SERVICES}`

**Resolution:** The vNext document is CORRECT. Only `CRYPTO_WEB3` should be hard-by-default. Use explicit `HARD_KEYWORD_OVERRIDES` for truly unambiguous terms from other categories.

### 2. STAGES Category Contains Series B
**Severity:** HIGH

**Problem:** YAML shows `STAGES` includes "series b" (weight 0.3). If STAGES were hard-by-default, Series B would be hard-rejected. But investment thesis says "Pre-Seed to Series A" - Series B is borderline.

**Resolution:** Keep STAGES as SOFT. Only promote explicit terms like "series c", "series d", "series e" to `HARD_KEYWORD_OVERRIDES`.

### 3. is_hard_negative() Performance + Normalization
**Severity:** HIGH

**Problem:** Per-call O(N) scan over policy keywords is inefficient. Also, normalization mismatch between policy keys and extracted negatives.

**Resolution:** Precompute normalized lookup index at policy load time:
```python
def build_normalized_index(self) -> dict[str, KeywordDetails]:
    return {_norm_kw(raw_kw): details for raw_kw, details in self.keywords.items()}
```

### 4. Three-Tier Routing Gap
**Severity:** CRITICAL

**Problem:** Missing case in routing logic:
- No hard matches
- No soft matches
- score < hold_threshold

This would incorrectly QUALIFY low-score signals.

**Resolution:** Add explicit else branch:
```python
else:
    if keyword_fit.score >= self.config.hold_threshold:
        routing = RoutingDecision.QUALIFIED
    else:
        routing = RoutingDecision.HELD
    rejection_type = None
```

### 5. Rescue Signal Tokenization
**Severity:** MEDIUM

**Problem:** Naive `split()` misses "fertility," or "egg-freezing" forms.

**Resolution:** Use punctuation-safe tokenizer that mirrors normalization:
```python
def _tokenize_for_rescue(text: str) -> set[str]:
    s = text.lower().replace("-", " ").replace("/", " ").replace("_", " ")
    s = s.translate(_PUNCT_XLATE)
    return set(s.split())
```

### 6. Circuit Breaker Missing
**Severity:** BLOCKING

**Problem:** No specification or implementation for circuit breaker safety mechanism.

**Resolution:** Add circuit breaker with:
- 30% soft-reject rate OR 30% HELD rate threshold
- deque(maxlen=1000) for sliding window
- Trip behavior: force legacy routing until process restart
- Use `asyncio.Lock` for async-safe implementation

### 7. Soft Persistence Prefix
**Severity:** MEDIUM

**Problem:** `[SOFT]` prefix has special meaning in some SQL dialects.

**Resolution:** Use `SOFT:` prefix instead (DB-portable).

---

## IMPLEMENTATION RISKS (From Security Audit)

### Thread Safety
**Risk:** CRITICAL

Using `threading.Lock` with asyncio code causes deadlocks. The existing codebase uses `asyncio.Semaphore`.

**Mitigation:** Use `asyncio.Lock` for async contexts.

### Lazy Initialization Race
**Risk:** HIGH

`getattr(policy, "normalized_index", None)` pattern is unsafe in concurrent contexts.

**Mitigation:** Initialize in `__init__` or use `functools.cached_property` (Python 3.12+).

### Migration Method Missing
**Risk:** HIGH

`mark_pending()` method DOES NOT EXIST in SignalStore. Only `mark_rejected()` and `mark_pushed()` exist.

**Mitigation:** Use existing `update_signal_status(canonical_key, "pending")` method.

### Soft Reject Counter Storage Undefined
**Risk:** MEDIUM

Decision D9 specifies "after 3 soft-rejects, escalate to hard" but no schema for storing counter.

**Mitigation:** Either:
- Add `soft_reject_count` column to signal_processing
- Or store in metadata JSON field
- Or defer escalation logic to Phase 2

---

## CODEBASE VALIDATION RESULTS

| Component | Status | Notes |
|-----------|--------|-------|
| NegativeKeywordCategory enum | ✅ MATCH | All 6 categories present |
| rejection_reason field | ✅ PRESENT | Line 59 in ThesisFilterResult |
| rejection_type field | ❌ MISSING | Needs to be added |
| YAML keyword categories | ✅ MATCH | 40 keywords correctly categorized |
| _normalize() method | ✅ EQUIVALENT | Lines 646-649 in thesis_matcher.py |
| update_signal_status() | ✅ EXISTS | Lines 2626-2661 in signal_store.py |
| HELD_SOFT enum | ❌ MISSING | NOT adding per reviewer guidance |
| is_hard_negative() | ❌ MISSING | Needs implementation |
| handle_rejection() | ❌ MISSING | Put in pipeline.py, not policy.py |

---

## SATURATION CAPS SEMANTICS (Corrected)

**Formula:** `score = total_weight / (max_possible * 0.15)`

**Correct semantics:**
- Higher cap = LARGER denominator = LOWER score (stricter)
- Lower cap = SMALLER denominator = HIGHER score (more permissive)

**Defaults:**
```python
DEFAULT_CAP_KNOWN_UNMAPPED = 12.0    # conservative-ish
DEFAULT_CAP_UNKNOWN_THESIS = 10.0   # moderately permissive
# If you want more permissive unknowns, LOWER this number (not higher)
```

---

## HARD KEYWORD OVERRIDES (Audited)

Only truly unambiguous terms (removed generic ones like "framework"):

```python
HARD_KEYWORD_OVERRIDES = {
    # Unambiguous services/agency
    "consulting", "agency", "outsourcing", "recruiting",
    # Unambiguous devtools
    "sdk", "cli", "linter", "devops",
    # Unambiguous crypto
    "blockchain", "crypto", "cryptocurrency", "web3", "nft", "defi",
    # Unambiguous late-stage
    "series c", "series d", "series e", "series f", "series g",
    # Unambiguous education-as-product
    "bootcamp",
}
```

**Removed from overrides:** "framework", "library", "plugin" (too generic)

---

## PRE-CODING CHECKLIST (Updated)

- [ ] Audit keywords in STAGES category (verify "series b" handling)
- [ ] Remove generic override tokens ("framework" etc)
- [ ] Confirm `SOFT:` prefix; ensure rollback queries cover legacy `[SOFT]` too
- [ ] Confirm breaker behavior after trip (legacy forced via in-memory flag)
- [ ] Add rescue tokenizer tests for punctuation forms
- [ ] Verify update_signal_status() accepts signal_id (not just canonical_key)
- [ ] Define soft_reject_count storage mechanism

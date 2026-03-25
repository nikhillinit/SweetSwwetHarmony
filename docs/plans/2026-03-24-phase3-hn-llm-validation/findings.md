# Phase 3 Findings: HN LLM Thesis Validation

## Plan Suitability Assessment (2026-03-24)

### Verdict: PLAN IS SOUND — with one critical caveat

The Phase 3 plan to run HN-only LLM classification on a scratch DB is well-structured and ready for execution.

---

### What's Ready

1. **--source-api filter works end-to-end** (PR #126, merged)
   - CLI → pipeline → storage → SQL WHERE clause
   - Tested with 5 new tests (3 storage + 2 pipeline threading)

2. **THESIS_SKIP_LLM_BELOW=0.0 math is correct**
   - Current: `0 < 0.2` → True → LLM skipped
   - Override: `0 < 0.0` → False → LLM runs
   - Python float comparison: `0 < 0.0` is `False` ✓

3. **Scratch DB isolation is clean**
   - `--db-path scratch_phase3.db` routes all reads/writes to copy
   - Production signals.db is never touched
   - `--dry-run` additionally blocks Notion writes (belt + suspenders)

4. **Gemini API is configured**
   - GOOGLE_API_KEY ✅ in .env
   - Rate limits: 15 RPM / 1500 RPD — 28 signals is well within budget
   - Model: gemini-2.0-flash (fast, cheap)

5. **Pending HN signals confirmed: 28**
   - All with keyword_score=0, thesis_category=UNKNOWN
   - Root cause: HN parser doesn't generate keyword-matchable content

---

### Critical Caveat: --dry-run Does NOT Prevent All Mutations

Per `plan-verification.md` rule and prior investigation:

> In active (non-shadow) LLM_THESIS_MODE, thesis reject/hold paths call `mark_rejected()` and `update_signal_status()` BEFORE the dry-run Notion push guard. `--dry-run` only prevents Notion writes, not processing state mutations.

**Impact on Phase 3:** The scratch DB WILL be mutated — signals will move from `pending` to `rejected`/`held` based on LLM classification. This is **acceptable** because:
- Scratch DB is disposable
- Mutations are actually what we want to analyze (which signals got rejected vs held)
- Production DB is untouched

**This is NOT a bug — it's the desired behavior for this experiment.**

---

### Previous Rehearsal (2026-03-24) — Why It Failed

The scratch rehearsal used `LLM_THESIS_MODE=active` BUT kept `THESIS_SKIP_LLM_BELOW=0.2` (default).

Result: All 34 HN signals were HELD (not rejected) because:
1. keyword_score=0 → `0 < 0.2` → True → **LLM skipped**
2. Without LLM, keyword-only routing saw score=0 → HOLD (below hold threshold)
3. The LLM never ran at all

**Fix in Phase 3:** Set `THESIS_SKIP_LLM_BELOW=0.0` so `0 < 0.0` → False → LLM runs.

---

### Open Questions

1. **Does the scratch DB need fresh pending signals?**
   - The rehearsal DB (`hn_llm_active_rehearsal_2026-03-24.db`) has signals already held — can't re-process
   - Need a fresh copy of `signals.db` which has 28 pending HN signals
   - Answer: YES, use fresh copy

2. **Will --dry-run interact badly with active mode + source-api filter?**
   - --dry-run blocks Notion push only
   - --source-api filters which signals enter the processing stage
   - Active mode mutations happen in _process_signals_stage, after the filter
   - No interaction — they're orthogonal ✓

3. **What about the other 101 pending signals (arxiv:56, rss:35, news_api:10)?**
   - --source-api=hacker_news means ONLY HN signals are fetched from DB
   - Other signals remain untouched in scratch DB
   - No contamination risk ✓

---

## Bug Found: Pipeline Ignores THESIS_SKIP_LLM_BELOW Env Var (2026-03-25)

**Root cause:** `workflows/pipeline.py:489-491` creates `ThesisFilterConfig(hold_threshold=...)` with
a hardcoded constructor instead of `ThesisFilterConfig.from_env()`. This means `skip_llm_if_keyword_below`
always defaults to 0.2, regardless of the `THESIS_SKIP_LLM_BELOW` env var.

**Impact:** Setting `THESIS_SKIP_LLM_BELOW=0.0` via env var or shell export has NO EFFECT when
running through `run_pipeline.py process`. The LLM is always skipped for keyword_score=0 signals.

**Workaround for Phase 3:** Use a standalone script (`run_phase3_hn_llm.py`) that creates
`ThesisFilterConfig.from_env()` directly, bypassing the pipeline's hardcoded config.

**Proper fix (post-Phase 3):** Change `pipeline.py:489` from:
```python
thesis_config = ThesisFilterConfig(hold_threshold=self.config.thesis_hold_threshold)
```
to:
```python
thesis_config = ThesisFilterConfig.from_env()
```

---

## Systems Thinking Analysis (2026-03-24)

### Archetype: Shifting the Burden

The keyword threshold (THESIS_SKIP_LLM_BELOW=0.2) was set to save Gemini API calls (symptomatic
solution), but it completely blocks the LLM (fundamental solution) from ever seeing HN signals
with keyword_score=0. The keyword matcher was never improved for HN content because the LLM was
"supposed to handle it" — but the threshold prevents that.

### Feedback Loop: B1 (LLM Quality Gate) is BLOCKED

```
keyword_score=0 --> 0 < 0.2? YES --> SKIP LLM --> ALL HOLD --> FPs never filtered
                                                                    |
Phase 3 fix:     --> 0 < 0.0? NO  --> RUN LLM --> Reject B2B  <----+
                                      (loop restored)         (balancing loop)
```

### Leverage Assessment

Phase 3 targets a level-12 parameter (THESIS_SKIP_LLM_BELOW), but its actual effect is level-8:
it restores a blocked balancing feedback loop. This is a low-cost parameter change with
disproportionate structural impact. Correct leverage point for this intervention.

### Watch-outs for Post-Phase-3

1. **Global application risk**: If THESIS_SKIP_LLM_BELOW=0.0 applied to prod, ALL collectors
   with keyword_score=0 hit Gemini API — may exhaust rate limits. Prod needs per-collector threshold.
2. **Archetype trap**: Success may delay fundamental fix (better HN parser). Set exit criteria:
   even if LLM catches >80% FP, still pursue parser improvement.
3. **Systemic blind spot**: Check if other collectors (arxiv, rss_feeds, news_api) also have
   keyword_score=0 signals being skipped by the threshold. Pattern may not be HN-specific.

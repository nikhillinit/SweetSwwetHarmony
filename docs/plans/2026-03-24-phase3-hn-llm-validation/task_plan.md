# Phase 3: HN-Only LLM Thesis Validation Run

**Goal:** Validate that Gemini LLM thesis classification correctly filters HN false positives when THESIS_SKIP_LLM_BELOW=0.0 forces LLM to run on keyword_score=0 signals.

**Success criteria:**
- LLM correctly rejects B2B/dev-tool/crypto HN signals (>80% of known FPs)
- No false rejection of the 2 historical TPs (Wildex, FlightDeepResearch-type consumer signals)
- Classification results logged and reviewable

**Status:** COMPLETE (2026-03-25)

---

## Phases

### Phase 0: Pre-flight Verification `[pending]`
- [ ] Verify GOOGLE_API_KEY is valid (not expired/revoked)
- [ ] Confirm 28 pending HN signals in production signals.db
- [ ] Create scratch DB copy: `cp signals.db scratch_phase3.db`
- [ ] Verify scratch copy has 28 pending HN signals via query
- [ ] Confirm Gemini rate limits acceptable (28 signals << 1500 RPD)

### Phase 1: Scratch Run with LLM Active `[pending]`
- [ ] Set env overrides: `THESIS_SKIP_LLM_BELOW=0.0`, `LLM_THESIS_MODE=active`
- [ ] Run: `python run_pipeline.py process --source-api hacker_news --db-path scratch_phase3.db --dry-run`
- [ ] Capture stdout/stderr for analysis
- [ ] Verify all 28 signals were processed (not skipped)

### Phase 2: Analyze Results `[pending]`
- [ ] Query scratch DB for thesis_classifications rows
- [ ] Count: rejected vs held vs passed by LLM
- [ ] Cross-reference with HN FP investigation categories (B2B leak, parsing artifacts, category misroute)
- [ ] Identify any signals LLM missed (false negatives — FPs that LLM let through)
- [ ] Save classification report to artifacts/

### Phase 3: Decision Point `[pending]`
- [ ] If >80% FP correctly rejected: recommend lowering THESIS_SKIP_LLM_BELOW in prod
- [ ] If <80%: investigate LLM prompt quality, consider HN parser improvements
- [ ] Update memory with findings and next steps

---

## Environment Setup (exact commands)

```powershell
# Phase 0: Create scratch DB
Copy-Item signals.db scratch_phase3.db

# Phase 1: Run with overrides (PowerShell)
$env:THESIS_SKIP_LLM_BELOW="0.0"
$env:LLM_THESIS_MODE="active"
python run_pipeline.py process --source-api hacker_news --db-path scratch_phase3.db --dry-run
```

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| --dry-run still mutates processing state in active mode | Scratch DB only — no prod impact | Using separate scratch_phase3.db |
| Gemini API key expired | Run fails immediately | Pre-flight check in Phase 0 |
| LLM returns excluded/error for all signals | No useful data | Circuit breaker logs will show API failures |
| 28 signals too small for statistical confidence | Weak signal | Acceptable for directional validation; follow up with batch backfill |
| THESIS_SKIP_LLM_BELOW=0.0 forces LLM on ALL sources if applied to prod | Unintended cost/latency | Phase 3 scratch run only; prod change needs separate governance |

---

## Key Code References

| Component | File:Line | Purpose |
|-----------|-----------|---------|
| Skip threshold check | `utils/thesis_filter.py:602` | `keyword_fit.score < skip_llm_if_keyword_below` |
| Config default | `utils/thesis_filter.py:158` | `THESIS_SKIP_LLM_BELOW` env var, default 0.2 |
| LLM mode routing | `workflows/pipeline.py:2042-2044` | off/shadow/active branch |
| Active mode mutations | `workflows/pipeline.py:2102-2122` | mark_rejected / update_signal_status |
| --source-api filter | `storage/signal_store.py:2811-2813` | SQL WHERE s.source_api = ? |
| Gemini classifier | `consumer/thesis_filter/llm_classifier.py:283-480` | LLM API call |

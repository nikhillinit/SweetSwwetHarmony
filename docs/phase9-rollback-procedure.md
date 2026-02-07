# Phase 9 Rollback Procedure

## Overview

This document describes how to safely rollback Phase 9 (Quality Ops Production Integration) if issues arise in production.

Phase 9 introduced:
- LLM thesis classification with feature flag control
- Verification gate LLM confidence adjustments
- Scheduler integration for 3 quality workflows
- Disagreement detection and reporting

The rollback strategy is **layered**: start with the quickest, least invasive option and escalate if needed.

---

## Rollback Triggers

Execute rollback if any of these conditions occur:

| Trigger | Threshold | Severity |
|---------|-----------|----------|
| LLM error rate | > 25% | High |
| Pipeline latency | > 2x baseline | High |
| FP rate spike | > +10 percentage points | Medium |
| Circuit breaker open | > 1 hour continuously | Medium |
| Rate limit exhaustion | Daily quota hit before 6pm | Low |
| Disagreement rate | > 50% | Low (investigate first) |

---

## Layer 1: Feature Flag Disable (< 5 minutes)

**Fastest rollback** - Disables LLM without code changes.

### Steps

1. **Set LLM mode to off:**
   ```bash
   # Edit .env
   LLM_THESIS_MODE=off
   ```

2. **Restart pipeline:**
   ```bash
   # Kill running pipeline processes
   pkill -f run_pipeline.py

   # Restart (if using systemd/supervisor)
   systemctl restart harmonic-pipeline
   ```

3. **Verify rollback:**
   ```bash
   # Check that LLM calls drop to zero
   python -m ops.cli monitor status
   # Should show: llm_calls_today = 0, llm_calls_last_hour = 0
   ```

### Impact
- **LLM classification:** Disabled (reverts to keyword-only)
- **Verification gate:** Continues working (no LLM adjustments applied)
- **Scheduler jobs:** Continue running (quality-sync, quality-patterns unaffected)
- **Disagreement detection:** Continues collecting data (keyword-only scores)

### When to use
- LLM error rate spike
- Rate limit exhaustion
- Gemini API outage

---

## Layer 2: Disable Scheduled Jobs (< 10 minutes)

**Partially rollback** automated workflows if they're causing issues.

### Steps

1. **Disable quality schedules:**
   ```bash
   # Disable all quality schedules
   python -m ops.cli schedule disable quality-sync
   python -m ops.cli schedule disable quality-classify
   python -m ops.cli schedule disable quality-patterns
   ```

2. **Verify schedules disabled:**
   ```bash
   python -m ops.cli schedule list
   # Should show: enabled=False for all quality-* schedules
   ```

### Impact
- **LLM classification:** Manual only (no automated batch processing)
- **Status sync:** Manual only (no automated Notion sync)
- **Pattern detection:** Manual only (no automated reports)

### When to use
- Scheduled jobs causing DB lock contention
- Batch classification hitting rate limits
- Need to pause automation while investigating

---

## Layer 3: Git Revert (< 30 minutes)

**Full code rollback** if feature flag toggle insufficient.

### Steps

1. **Identify Phase 9 merge commit:**
   ```bash
   git log --oneline --grep="Phase 9" -n 5
   # Find the merge commit hash (e.g., abc1234)
   ```

2. **Create revert commit:**
   ```bash
   git revert -m 1 <merge-commit-hash>
   ```

3. **Test revert locally:**
   ```bash
   # Run baseline tests
   pytest tests/ops/ tests/storage/ tests/workflows/ -v

   # Verify pipeline still works
   python run_pipeline.py full --collectors github --dry-run
   ```

4. **Push revert:**
   ```bash
   git push origin main
   ```

5. **Redeploy:**
   ```bash
   # Deploy reverted code to production
   # (Your deployment process here)
   ```

### Impact
- **LLM classification:** Code removed entirely
- **Verification gate:** Reverts to pre-Phase 9 logic
- **Scheduler:** Quality modes removed (schedules will fail if not disabled first)
- **Disagreement detection:** Preserved (migration 26 stays, safe to keep)

### When to use
- Feature flag toggle doesn't resolve the issue
- Need to fully remove Phase 9 code
- Confidence adjustment logic causing routing problems

---

## Layer 4: Database Rollback (Last Resort)

**Rarely needed** - Only if disagreement detection causing issues.

### Steps

1. **Disable FK constraints temporarily:**
   ```bash
   sqlite3 signals.db "PRAGMA foreign_keys=OFF;"
   ```

2. **Drop disagreement column:**
   ```sql
   ALTER TABLE thesis_classifications DROP COLUMN disagreement_detected;
   ```

3. **Re-enable FK constraints:**
   ```bash
   sqlite3 signals.db "PRAGMA foreign_keys=ON;"
   ```

4. **Revert migration 26 in code:**
   ```bash
   # Remove migration 26 from storage/migrations/quality_tables.py
   # or storage/signal_store.py (wherever it was added)
   ```

### Impact
- **Disagreement detection:** Removed entirely
- **Disagreement reports:** Will fail (missing column)

### When to use
- Only if disagreement detection causing DB performance issues (unlikely)
- NOT recommended unless absolutely necessary

---

## Post-Rollback Checklist

After executing rollback:

- [ ] Verify baseline tests pass: `pytest tests/ -v`
- [ ] Verify pipeline processes signals: `python run_pipeline.py full --collectors github --limit 5`
- [ ] Check ops health: `python -m ops.cli monitor status`
- [ ] Monitor for 1 hour to ensure stability
- [ ] Document root cause in incident report
- [ ] Create GitHub issue with "phase9-rollback" label

---

## Recovery Plan

Once root cause fixed:

1. **Fix the issue** in a new branch
2. **Test thoroughly** (shadow mode for 48 hours)
3. **Redeploy** with monitoring
4. **Update this rollback doc** with lessons learned

---

**Last Updated:** 2026-02-07
**Owner:** Quality Ops Team

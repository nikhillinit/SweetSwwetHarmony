# Phase 9 Deployment Checklist

## Pre-Deployment Verification

- [ ] All 712 tests passing (run: `pytest tests/consumer/ tests/workflows/ tests/verification/ tests/ops/test_scheduler_quality.py tests/storage/test_disagreement_detection.py -v`)
- [ ] Git status clean (no uncommitted changes)
- [ ] Phase 9 branch merged to main
- [ ] Database migrations applied (migration 26 for disagreement_detected column)

## Environment Configuration

- [ ] Set `LLM_THESIS_MODE=shadow` in `.env` (start in testing mode)
- [ ] Verify `GOOGLE_API_KEY` configured and valid
- [ ] Verify tuning thresholds in `.env` (optional, defaults work):
  ```
  LLM_AGREEMENT_BOOST=0.10
  LLM_DISAGREEMENT_PENALTY=0.15
  WEAK_KEYWORD_STRONG_LLM_BOOST=0.05
  ```

## Create Quality Schedules

Run these commands to set up automated quality workflows:

```bash
# Sync Notion status events every 6 hours
python -m ops.cli schedule create quality-sync \
  --cron "0 */6 * * *" \
  --description "Sync Notion suppression statuses for outcome labeling"

# Batch classify recent signals daily at 2am
python -m ops.cli schedule create quality-classify \
  --cron "0 2 * * *" \
  --description "LLM thesis classification for unclassified signals (limit 50/day)"

# Detect FP patterns weekly on Sunday at 3am
python -m ops.cli schedule create quality-patterns \
  --cron "0 3 * * 0" \
  --description "Detect recurring false positive patterns (30-day window)"
```

Verify schedules created:
```bash
python -m ops.cli schedule list
```

## Shadow Mode Validation (24-48 hours)

- [ ] Process 20+ signals with `LLM_THESIS_MODE=shadow`
- [ ] Verify LLM scores stored in `thesis_classifications` table
- [ ] Check ops health dashboard: `python -m ops.cli monitor status`
  - [ ] LLM calls > 0
  - [ ] LLM error rate < 5%
  - [ ] No circuit breaker trips
- [ ] Run disagreement report: `python -m ops.cli quality thesis-disagreement-report --days 2`
  - [ ] Disagreement rate < 30% (acceptable range: 10-30%)

## Switch to Active Mode

Once shadow mode validation passes:

- [ ] Set `LLM_THESIS_MODE=active` in `.env`
- [ ] Restart pipeline
- [ ] Process 10 signals and verify confidence adjustments in verification gate logs

## Post-Deployment Monitoring (7 days)

- [ ] Daily: Check ops health dashboard for LLM metrics
- [ ] Day 3: Run stats report: `python -m ops.cli quality stats --days 3`
  - [ ] Target: FP rate reduction (baseline: 30% → target: <20%)
- [ ] Day 7: Run disagreement report: `python -m ops.cli quality thesis-disagreement-report --days 7`
  - [ ] Review high-disagreement signals for keyword tuning opportunities
- [ ] Day 7: Check scheduled job history: `python -m ops.cli schedule history quality-sync --limit 10`

## Success Criteria

- [ ] LLM classification runs without errors
- [ ] FP rate improved by ≥5 percentage points
- [ ] All 3 scheduled jobs running successfully
- [ ] Disagreement detection working (reports show data)
- [ ] No pipeline latency increase > 50%

## Rollback Trigger

If any of these occur, see rollback procedure (docs/phase9-rollback-procedure.md):
- LLM error rate > 25%
- Pipeline latency > 2x baseline
- FP rate spike > 10 percentage points above baseline
- Circuit breaker stays open > 1 hour

---

**Deployment Date:** _________
**Deployed By:** _________
**Shadow Mode Start:** _________
**Active Mode Start:** _________

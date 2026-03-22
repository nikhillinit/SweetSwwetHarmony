# Step 4A Monitoring Plan

## Scope
This is the **initial 48h stabilization window** for the Step 4A promotion
(DELIVERY_MODE=batch_publish). It is NOT the full 7-day Step 4A dwell period
required before advancing to Step 4B (see "Step 4A: Batch Publish" in feature-activation.md).

The full 7-day dwell runs 2026-03-16 through 2026-03-23. This plan covers
the first 48h with intensive checkpoints. After 48h, monitoring continues at
reduced cadence (daily) through day 7.

## Timeline

| Phase | Window | Cadence | Checkpoints |
|-------|--------|---------|-------------|
| Stabilization | 2026-03-16T19:15Z to 2026-03-18T19:15Z | every 6h | 8 |
| Sustained observation | 2026-03-18T19:15Z to 2026-03-23T19:15Z | daily | 5 |
| Regret check | 2026-03-30T19:13Z | once | 1 |

## Checkpoint Owner
**operator:nikhil** owns all checkpoints. If a checkpoint is missed by >2h,
the next checkpoint must include a note explaining the gap and whether any
pipeline runs occurred during the unmonitored interval.

## Checkpoint Commands
Run these at each checkpoint:

```bash
# 1. Canary check (store results)
python -m monitoring.canary_checker run --db signals.db --store-results

# 2. Drift check
python run_pipeline.py drift check --json 2>/dev/null

# 3. Activation gate
python run_pipeline.py activation-check --step 4 --json 2>/dev/null

# 4. SPC override decision
python scripts/spc_override_decision.py --db signals.db --json

# 5. Overdue regret checks
python -m monitoring.feature_gate overdue --db signals.db --json
```

## Controlled Batch Commit (REQUIRED)

A controlled batch commit must be completed within the first 48h to exercise
the batch_publish write path. This is the core Step 4A validation per
the "Step 4A: Batch Publish" section of feature-activation.md.

### Batch Commit Procedure

```bash
# 1. Create a batch from approved review_items
python run_pipeline.py publish create --limit 3

# 2. Preview the batch (dry-run)
python run_pipeline.py publish preview <batch_id>

# 3. Commit the batch (non-interactive, creates Notion pages)
python run_pipeline.py publish commit <batch_id> --yes
#    - commit_batch() will enforce activation gate (step=4)
#    - If gate is non-ready, commit will fail with ActivationGateError
#    - Override: python run_pipeline.py publish commit <batch_id> --yes --override-reason "controlled Step 4A validation test"
```

### Batch Commit Verification Checklist
- [ ] `commit_batch()` completed without ActivationGateError
- [ ] Notion pages created (check Notion CRM)
- [ ] `notion_page_id` persisted on batch_items (`SELECT notion_page_id FROM batch_items WHERE batch_id = ?`)
- [ ] No duplicate Notion pages for same canonical_key
- [ ] Correct status mapping (Source/Tracking) based on confidence
- [ ] audit_log entry recorded for batch_commit

### Fallback if no approved review_items exist
If the review queue is empty, seed signals without triggering the push path:
```bash
# Collect + process only (dry-run skips push, which would hit AUTO_PUSH block under batch_publish)
python run_pipeline.py full --collectors github,hacker_news,rss_feeds --dry-run
# Then approve 2-3 signals via CLI or API before creating the batch
```

## Success Criteria
- [ ] Canary pass rate stays > 80% across all checkpoints (stabilization + sustained)
- [ ] No new critical or warning drift alerts
- [ ] Activation gate stays verdict=ready
- [ ] At least one controlled batch commit succeeds with notion_page_id verification
- [ ] Batch push success rate > 95% (across all commits)
- [ ] No duplicate Notion pages (canonical key dedup working)
- [ ] SPC override decision remains proceed_with_exception or proceed_without_exception

## Rollback Triggers
- Canary drops below 70%
- Critical drift alert opens
- Batch push failure rate > 10%
- Duplicate Notion pages detected
- `ActivationGateError` raised during batch commit (without --override-reason)
- SPC override decision changes to `hold` after patch

## Rollback Procedure

```bash
# 1. Change .env
#    DELIVERY_MODE=manual_publish

# 2. Verify
python run_pipeline.py health --json

# 3. Record demotion audit event (CLI-based, canonical)
DISCOVERY_DB_PATH=signals.db python -m governance feature demote DELIVERY_MODE \
  --from batch_publish --to manual_publish \
  --reason "<REASON>"
```

> **Historical note:** Prior to commit 89259c5 (DELIVERY_MODE contract support),
> this rollback procedure used raw SQL because the governance CLI did not support
> DELIVERY_MODE transitions. The CLI now handles these transitions canonically.

## Regret Check
Due: 2026-03-30T19:13:54Z (14 days from promotion)
```bash
python -m monitoring.feature_gate overdue --db signals.db --json
```

## Step 4B Eligibility

Step 4B promotion is governed by a two-layer gate:

- Repo-enforced earliest: after 7 clean days on Step 4A (2026-03-23) with activation gate green
- Default local policy: wait until the regret check due on 2026-03-30
- Earlier promotion before 2026-03-30 requires explicit written sign-off in `2026-03-19-step4b-promotion-checklist.md`

If promotion proceeds:
- Promote MERGE_WRITES_ENABLED from shadow to active
- Record feature_promote for MERGE_WRITES_ENABLED

## Stabilization Checkpoint Log (48h, every 6h)
| # | Time | Canary | Gate | Drift | Batch | Notes |
|---|------|--------|------|-------|-------|-------|
| 1 | 2026-03-17T01:15Z | | | | | |
| 2 | 2026-03-17T07:15Z | | | | | |
| 3 | 2026-03-17T13:15Z | | | | | |
| 4 | 2026-03-17T19:15Z | | | | | |
| 5 | 2026-03-18T01:15Z | | | | | |
| 6 | 2026-03-18T07:15Z | | | | | |
| 7 | 2026-03-18T13:15Z | | | | | |
| 8 | 2026-03-18T19:15Z | | | | | |

## Sustained Observation Log (days 3-7, daily)
| # | Date | Canary | Gate | Drift | Batch | Notes |
|---|------|--------|------|-------|-------|-------|
| 9 | 2026-03-19 | pass 93.14% (#48) | ready | in_control | — | Conditional pass; SPC proceed_with_exception (see checkpoint-2026-03-19-summary.md) |
| 10 | 2026-03-20 | pass 93.14% (#51) | ready | in_control | — | No variance from #48; SPC proceed_with_exception |
| 11 | 2026-03-21 | pass 93.14% (#52) | ready | in_control | — | No variance from #48/#51; SPC proceed_with_exception |
| 12 | 2026-03-22 | pass 93.14% (#53) | ready | in_control | — | No variance from #48/#51/#52; SPC proceed_with_exception |
| 13 | 2026-03-23 | | | | 7-day review | |

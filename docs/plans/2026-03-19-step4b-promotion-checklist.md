# Step 4B Promotion Checklist

**Created:** 2026-03-19
**Branch:** `obs/step4a-window-mar19-23`
**Scope:** MERGE_WRITES_ENABLED shadow → active (merge writes only)

## Independence Statement

This checklist is **fully independent** of the HN paired replay experiment.
The HN experiment outcome does not gate, accelerate, or delay Step 4B.

---

## What Changes in Step 4B

| Setting | Step 4A (current) | Step 4B (target) |
|---------|-------------------|------------------|
| `DELIVERY_MODE` | `batch_publish` | `batch_publish` (unchanged) |
| `MERGE_WRITES_ENABLED` | `shadow` | `active` |

**Effect:** Entity resolution merge writes become applied (not just logged).
All other settings remain at their Step 4A values.

---

## Dates

| Milestone | Date | Notes |
|-----------|------|-------|
| Step 4A promoted | 2026-03-16 | DELIVERY_MODE=batch_publish |
| Repo-enforced earliest | 2026-03-23 | 7 clean days on 4A + activation gate green |
| Regret check due | 2026-03-30 | 14-day window from 4A promotion |
| **Default local policy** | **2026-03-30** | Do not schedule before this date unless earlier execution gets explicit written sign-off below |

### Early Execution Sign-Off

If promoting before 2026-03-30, fill in both fields:

- **Approved by:** _______________
- **Justification:** _______________
- **Date of approval:** _______________

Without both fields completed, promotion before 2026-03-30 is not authorized.

---

## Prerequisites

All must be green before promotion.

### P1: 7 Clean Days on Step 4A

```bash
# Verify observation window complete (7 days from 2026-03-16)
python run_pipeline.py activation-check --step 4 --json 2>/dev/null
```

- [ ] Verdict: `ready` or `warn` (not `blocked`)
- [ ] No critical drift alerts during window
- [ ] Canary pass rate > 80% across all checkpoints

### P2: Regret Check

```bash
python -m monitoring.feature_gate overdue --db signals.db --json
```

- [ ] No overdue regret checks
- [ ] `DELIVERY_MODE` regret window (due 2026-03-30) either:
  - Passed (if running on/after 2026-03-30), OR
  - Explicitly waived via early execution sign-off above

### P3: SPC Override Decision

```bash
python scripts/spc_override_decision.py --db signals.db --json
```

- [ ] Decision is `proceed_with_exception` or `proceed_without_exception`
- [ ] No `halt` or `blocked` verdicts

### P4: Pre-Promotion Backup

```bash
python scripts/backup_db.py --db signals.db --out-dir backups/ --retain 7
```

- [ ] Backup created successfully
- [ ] Integrity check passes
- [ ] Backup path recorded: _______________

### P5: Current Canary Baseline

```bash
python -m monitoring.canary_checker run --db signals.db --store-results
```

- [ ] Pass rate: _____ % (must be > 80%)
- [ ] No hot-path defects in recent failures

---

## Promotion Steps

Execute in order. Stop if any step fails.

### Step 1: Record Pre-Promotion State

```bash
# Capture current state
python run_pipeline.py health --json > artifacts/step4b-pre-health.json
python run_pipeline.py activation-check --step 4 --json > artifacts/step4b-pre-activation.json
python -m monitoring.canary_checker run --db signals.db --store-results > artifacts/step4b-pre-canary.json
```

- [ ] All three artifacts saved

### Step 2: Update .env

Change only this single variable:

```bash
# In .env, change:
MERGE_WRITES_ENABLED=active   # was: shadow
```

- [ ] Only `MERGE_WRITES_ENABLED` changed
- [ ] All other env vars unchanged
- [ ] `.env` saved

### Step 3: Governance Audit Event

```bash
python -m governance.cli promote MERGE_WRITES_ENABLED active \
  --from-state shadow \
  --regret-due-days 14 \
  --db signals.db
```

- [ ] Audit event recorded
- [ ] Regret due date: _______________

### Step 4: Smoke Test

```bash
# Verify the pipeline starts correctly with new config
python run_pipeline.py health --json
python run_pipeline.py activation-check --step 4 --json
```

- [ ] Health check passes
- [ ] Activation gate: `ready` or `warn`
- [ ] No config validation errors

### Step 5: Post-Promotion Canary

```bash
python -m monitoring.canary_checker run --db signals.db --store-results
```

- [ ] Pass rate: _____ % (must be > 80%)
- [ ] No degradation vs pre-promotion baseline

---

## Post-Promotion Monitoring

### First 48 Hours

Run every 6 hours:

```bash
python -m monitoring.canary_checker run --db signals.db --store-results
python run_pipeline.py drift check --json 2>/dev/null
python run_pipeline.py activation-check --step 4 --json 2>/dev/null
python scripts/spc_override_decision.py --db signals.db --json
```

Check for:

- [ ] Entity merges produce correct `entity_migrations` records
- [ ] Drift fingerprints computed post-merge
- [ ] No duplicate Notion pages after merge cascade
- [ ] Canary stable at full production throughput
- [ ] Batch push success rate > 95%

### Days 3-14

Run daily:

- [ ] Same checkpoint commands as above
- [ ] Monitor for unexpected entity merge patterns
- [ ] Review any new merge-related errors in logs

---

## Rollback Plan

If any monitoring check fails critically:

```bash
# Step 1: Revert .env
MERGE_WRITES_ENABLED=shadow   # active → shadow

# Step 2: Record rollback event
python -m governance.cli demote MERGE_WRITES_ENABLED shadow \
  --from-state active \
  --reason "Step 4B rollback: <describe issue>" \
  --db signals.db

# Step 3: Verify
python run_pipeline.py health --json
python -m monitoring.canary_checker run --db signals.db --store-results
```

### Rollback Triggers

- Canary drops below 70%
- Critical drift alert opens
- Duplicate Notion pages detected
- Entity merge produces incorrect migrations
- Batch push failure rate > 10%

---

## Success Criteria

Step 4B is considered stable when:

- [ ] 7 days with no rollback triggers
- [ ] Entity merges produce correct records consistently
- [ ] No duplicate Notion pages
- [ ] Canary pass rate sustained > 80%
- [ ] Regret check passes (14 days from promotion)

---

## Artifact Locations

| Artifact | Path |
|----------|------|
| Pre-promotion health | `artifacts/step4b-pre-health.json` |
| Pre-promotion activation | `artifacts/step4b-pre-activation.json` |
| Pre-promotion canary | `artifacts/step4b-pre-canary.json` |
| DB backup | `backups/signals-YYYYMMDD-HHMMSS.db` |

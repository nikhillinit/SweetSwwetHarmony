# Feature Activation Runbook

## Overview

Progressive activation sequence for the Discovery Engine's feature flags. Each step
increases the mutation surface. Do not advance to the next step until the current
step has run clean for the specified monitoring period.

## Prerequisites

- G0 baseline green (smoke suite passes, no new test failures vs baseline)
- `STRICT_CONFIG_VALIDATION=true` in production `.env`
- Canary golden set defined (`monitoring/canary_checker.py`)
- SPC baseline computed (`monitoring/spc_monitor.py`)

### Automated Gate Check (M4)

Before advancing to any step, run the activation readiness gate:

```bash
# Check readiness for a specific step
python run_pipeline.py activation-check --step N

# JSON output for automation
python run_pipeline.py activation-check --step N --json

# API endpoint (no auth required)
curl http://localhost:8000/api/v1/health/activation-readiness?step=N
```

Exit code 0 = can proceed (ready or warn). Exit code 1 = blocked.

**Step-specific policy thresholds:**

| Condition | Step 1 (Shadow) | Step 2 (Low-risk) | Step 3 (Write) | Step 4 (Batch) |
|-----------|-----------------|-------------------|----------------|----------------|
| No canary data | warn | warn | **blocked** | **blocked** |
| Canary verdict=fail | **blocked** | **blocked** | **blocked** | **blocked** |
| Canary verdict=degraded | warn | **blocked** | **blocked** | **blocked** |
| Canary stale (>threshold) | warn | warn | **blocked** | **blocked** |
| Critical drift alert open | **blocked** | **blocked** | **blocked** | **blocked** |
| Warning drift alert open | pass | warn | warn | **blocked** |
| Max canary age (hours) | 48 | 48 | 24 | 24 |

## Step 1: Shadow Activation (observe, no mutations)

**Duration:** Run for 48h minimum before advancing.

**Gate check:** `python run_pipeline.py activation-check --step 1`

**Set these env vars:**

```bash
LLM_THESIS_MODE=shadow
ML_ENABLEMENT=shadow
MERGE_WRITES_ENABLED=shadow
USE_SHADOW_ENTITY_RESOLUTION=true
```

**What changes:** LLM and ML classifiers run on every signal but results are logged,
not acted upon. Merge suggestions are computed but not applied. Entity resolution
runs in shadow mode alongside existing canonical keys.

**Monitoring checklist:**
- [ ] Canary baseline stable (no regressions from golden set)
- [ ] SPC charts within control limits
- [ ] No drift alerts
- [ ] Gemini rate limits not exceeded (15 RPM / 1500 RPD free tier)
- [ ] ML inference latency < 500ms p99
- [ ] No errors in `logs/` related to shadow features

**Rollback:**

```bash
LLM_THESIS_MODE=off
ML_ENABLEMENT=disabled
MERGE_WRITES_ENABLED=disabled
USE_SHADOW_ENTITY_RESOLUTION=false
```

---

## Step 2: Low-risk Activation (48h after Step 1 clean)

**Duration:** Run for 48h minimum before advancing.

**Gate check:** `python run_pipeline.py activation-check --step 2`

**Set these env vars:**

```bash
DRIFT_MONITORING_ENABLED=active
USE_THIN_FILES=true
V2_ENABLEMENT=live
```

**What changes:** Drift monitoring begins writing alerts and SPC data. Thin files
(company_files table) begin populating from multi-source signals. V2 scoring
policy becomes active (changes confidence calculations).

**NOTE:** These flags change persisted and runtime behavior. Canary gate required
before advancing.

**Monitoring checklist:**
- [ ] Drift check clean (`python run_pipeline.py drift check`)
- [ ] Thin files populating (`SELECT COUNT(*) FROM company_files WHERE status='thin'`)
- [ ] Canary stable after V2 scoring change
- [ ] No unexpected status transitions in review_items
- [ ] SPC charts still within control limits

**Rollback:**

```bash
DRIFT_MONITORING_ENABLED=disabled
USE_THIN_FILES=false
V2_ENABLEMENT=shadow
```

---

## Step 3: Write Activation (after Step 2 clean)

**Duration:** Run for 24h minimum before advancing.

**Gate check:** `python run_pipeline.py activation-check --step 3`

**Set these env vars:**

```bash
DELIVERY_MODE=manual_publish
BULK_TRIAGE_ENABLED=active
HUNTER_PROMOTE_ENABLED=active
```

**What changes:** Manual push to Notion is enabled (one signal at a time via CLI
or API). Bulk triage actions (approve/reject multiple) become available. Hunter
results can be promoted to review queue.

**Monitoring checklist:**
- [ ] Manual push succeeds (`python run_pipeline.py pipeline push --confirm`)
- [ ] Triage approve/reject actions persist correctly
- [ ] Hunter promote creates review_items
- [ ] Notion CRM shows correct status mapping (Source/Tracking)
- [ ] Audit events recorded for all write operations

**Rollback:**

```bash
DELIVERY_MODE=staging_only
BULK_TRIAGE_ENABLED=disabled
HUNTER_PROMOTE_ENABLED=disabled
```

---

## Step 4: Batch Activation (after Step 3 clean)

**Duration:** Ongoing (production state).

**Gate check:** `python run_pipeline.py activation-check --step 4`

**Set these env vars:**

```bash
DELIVERY_MODE=batch_publish
MERGE_WRITES_ENABLED=active
```

**What changes:** Batch commit creates Notion pages for approved signals. Entity
merges are applied (not just logged). This is the full production state.

**Monitoring checklist:**
- [ ] Batch commit creates Notion pages (`/api/v1/batch/{id}/commit`)
- [ ] `notion_page_id` persisted on committed review_items
- [ ] Entity merges produce correct `entity_migrations` records
- [ ] Drift fingerprints computed post-merge
- [ ] No duplicate Notion pages (canonical key dedup working)
- [ ] Canary stable at full production throughput

**Rollback:**

```bash
DELIVERY_MODE=manual_publish
MERGE_WRITES_ENABLED=shadow
```

---

## Emergency Full Rollback

To disable all features and return to baseline:

```bash
LLM_THESIS_MODE=off
ML_ENABLEMENT=disabled
MERGE_WRITES_ENABLED=disabled
USE_SHADOW_ENTITY_RESOLUTION=false
DRIFT_MONITORING_ENABLED=disabled
USE_THIN_FILES=false
V2_ENABLEMENT=shadow
DELIVERY_MODE=staging_only
BULK_TRIAGE_ENABLED=disabled
HUNTER_PROMOTE_ENABLED=disabled
```

Then restart the API server and verify smoke suite passes.

## Feature Flag Reference

| Flag | Values | Default | Step |
|------|--------|---------|------|
| `LLM_THESIS_MODE` | off / shadow / active | off | 1 |
| `ML_ENABLEMENT` | disabled / shadow / live | disabled | 1 |
| `MERGE_WRITES_ENABLED` | disabled / shadow / active | disabled | 1, 4 |
| `USE_SHADOW_ENTITY_RESOLUTION` | true / false | false | 1 |
| `DRIFT_MONITORING_ENABLED` | disabled / active | disabled | 2 |
| `USE_THIN_FILES` | true / false | false | 2 |
| `V2_ENABLEMENT` | shadow / live | shadow | 2 |
| `DELIVERY_MODE` | staging_only / manual_publish / batch_publish / auto_publish | staging_only | 3, 4 |
| `BULK_TRIAGE_ENABLED` | disabled / active | disabled | 3 |
| `HUNTER_PROMOTE_ENABLED` | disabled / active | disabled | 3 |

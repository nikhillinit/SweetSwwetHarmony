# Activation Ladder Map

> **Scope:** Production activation steps 1-4, SPC gates, Phase G activation, feature flag progression, and go-live dependencies
> **Current position:** Steps 1-3 COMPLETE, Step 4A ACTIVE (promoted 2026-03-16), Step 4B earliest 2026-03-23 / default local policy 2026-03-30
> **Last updated:** 2026-03-20

---

## 1. Activation Ladder Overview

```
  BASELINE (all features off)
       |
       | Preflight + backup + smoke suite
       |
  +====v=====================================================+
  |  STEP 1: SHADOW ACTIVATION         STATUS: COMPLETE      |
  |  Duration: 48h minimum              Completed: 2026-02-24|
  |                                                           |
  |  LLM_THESIS_MODE=shadow                                   |
  |  ML_ENABLEMENT=shadow                                     |
  |  MERGE_WRITES_ENABLED=shadow                              |
  |  USE_SHADOW_ENTITY_RESOLUTION=true                        |
  |                                                           |
  |  Mutation surface: NONE (observe + log only)              |
  +=====+=====================================================+
        |
        | activation-check --step 2
        | canary_verdict != fail/degraded
        | max_canary_age <= 48h
        |
  +====v=====================================================+
  |  STEP 2: LOW-RISK ACTIVATION       STATUS: COMPLETE      |
  |  Duration: 48h minimum              Completed: 2026-02-24|
  |                                                           |
  |  DRIFT_MONITORING_ENABLED=active                          |
  |  USE_THIN_FILES=true                                      |
  |  V2_ENABLEMENT=live                                       |
  |                                                           |
  |  Mutation surface: drift alerts, thin files, V2 scoring   |
  +=====+=====================================================+
        |
        | activation-check --step 3
        | canary_verdict != fail/degraded
        | block_on_no_canary=true
        | max_canary_age <= 24h
        | SPC required: collector_volume, overall_fp_rate (WARN)
        |
  +====v=====================================================+
  |  STEP 3: WRITE ACTIVATION          STATUS: COMPLETE      |
  |  Duration: 24h minimum              Completed: 2026-02-24|
  |                                                           |
  |  DELIVERY_MODE=manual_publish                             |
  |  BULK_TRIAGE_ENABLED=active                               |
  |  HUNTER_PROMOTE_ENABLED=active                            |
  |                                                           |
  |  Mutation surface: Notion push (manual), triage, hunter   |
  +=====+=====================================================+
        |
        | activation-check --step 4
        | canary_verdict != fail/degraded
        | block_on_warning_alerts=true
        | max_canary_age <= 24h
        | SPC required: collector_volume, overall_fp_rate (BLOCK)
        |  ^^^ overall_fp_rate = insufficient_data ^^^
        |
  +====v=====================================================+
  |  STEP 4A: BATCH PUBLISH            STATUS: ACTIVE        |
  |  Duration: 7 days minimum           Promoted: 2026-03-16  |
  |                                                           |
  |  DELIVERY_MODE=batch_publish                              |
  |  MERGE_WRITES_ENABLED=shadow   (merges stay shadow)       |
  |                                                           |
  |  Mutation surface: batch Notion commit                    |
  |  Rollback: DELIVERY_MODE=manual_publish                   |
  +=====+=====================================================+
        |
        | repo minimum: 7 clean days
        | default local policy: wait for regret check or approved sign-off
        |
  +====v=====================================================+
  |  STEP 4B: LIVE MERGES              STATUS: DEFERRED       |
  |  Earliest: 2026-03-23 / Default: 2026-03-30 regret check |
  |                                                           |
  |  MERGE_WRITES_ENABLED=active                              |
  |                                                           |
  |  Mutation surface: entity merges + batch commit           |
  |  Rollback: MERGE_WRITES_ENABLED=shadow                    |
  +=====+=====================================================+
        |
        | (future — after Step 4B clean)
        |
  +====v=====================================================+
  |  STEP 3B: TIER 3 AUTO-PUBLISH      STATUS: DEFERRED      |
  |                                     Blocker: NER/convergence|
  |  DELIVERY_MODE=auto_publish                               |
  |  MERGE_WRITES_ENABLED=active                              |
  +==========================================================+
```

\* Earlier Step 4B promotion before 2026-03-30 requires explicit written sign-off per the Step 4B checklist.

---

## 2. Gate Policy Matrix

Each step has progressively stricter gate conditions. The activation gate evaluates 5 dimensions:

```
                    Step 1       Step 2       Step 3       Step 4
                   (Shadow)    (Low-risk)    (Write)      (Batch)
  +-----------+  +---------+  +---------+  +---------+  +---------+
  | No canary |  |  warn   |  |  warn   |  | BLOCKED |  | BLOCKED |
  | data      |  |         |  |         |  |         |  |         |
  +-----------+  +---------+  +---------+  +---------+  +---------+
  | Canary    |  |         |  |         |  |         |  |         |
  | verdict = |  | BLOCKED |  | BLOCKED |  | BLOCKED |  | BLOCKED |
  | fail      |  |         |  |         |  |         |  |         |
  +-----------+  +---------+  +---------+  +---------+  +---------+
  | Canary    |  |         |  |         |  |         |  |         |
  | verdict = |  |  warn   |  | BLOCKED |  | BLOCKED |  | BLOCKED |
  | degraded  |  |         |  |         |  |         |  |         |
  +-----------+  +---------+  +---------+  +---------+  +---------+
  | Canary    |  |         |  |         |  |         |  |         |
  | stale     |  |  warn   |  |  warn   |  | BLOCKED |  | BLOCKED |
  | (>max age)|  | (48h)   |  | (48h)   |  | (24h)   |  | (24h)   |
  +-----------+  +---------+  +---------+  +---------+  +---------+
  | Critical  |  |         |  |         |  |         |  |         |
  | drift     |  | BLOCKED |  | BLOCKED |  | BLOCKED |  | BLOCKED |
  | alert     |  |         |  |         |  |         |  |         |
  +-----------+  +---------+  +---------+  +---------+  +---------+
  | Warning   |  |         |  |         |  |         |  |         |
  | drift     |  |  pass   |  |  warn   |  |  warn   |  | BLOCKED |
  | alert     |  |         |  |         |  |         |  |         |
  +-----------+  +---------+  +---------+  +---------+  +---------+
  | SPC       |  |  none   |  |  none   |  |  warn   |  | BLOCKED |
  | required  |  |required |  |required |  |         |  |         |
  +-----------+  +---------+  +---------+  +---------+  +---------+
```

---

## 3. SPC Coverage Gate Detail

SPC (Statistical Process Control) monitors enforce data-driven activation. Each metric needs 14+ days of data with 100+ total samples.

```
  +=======================================+
  |         SPC METRIC READINESS          |
  +=======================================+
  |                                       |
  |  REQUIRED for Steps 3-4:             |
  |  +-------------------------------+   |
  |  | collector_volume              |   |
  |  | Status: OK (in_control)       |   |   <-- label-independent
  |  | mean=19.4, UCL=108.8, 3-sigma |   |
  |  +-------------------------------+   |
  |  +-------------------------------+   |
  |  | overall_fp_rate               |   |
  |  | Status: INSUFFICIENT_DATA     |   |   <-- needs TP/FP labels
  |  | Blocker: <14 days of labels   |   |
  |  +-------------------------------+   |
  |                                       |
  |  OPTIONAL for Steps 3-4:            |
  |  +-------------------------------+   |
  |  | confidence_calibration_ece    |   |
  |  | Status: INSUFFICIENT_DATA     |   |   <-- needs TP/FP labels
  |  +-------------------------------+   |
  |  +-------------------------------+   |
  |  | quarantine_regret             |   |
  |  | Status: INSUFFICIENT_DATA     |   |   <-- needs TP/FP labels
  |  +-------------------------------+   |
  |                                       |
  |  Step 3: gaps = WARN (non-blocking)  |
  |  Step 4: gaps = BLOCKED             |
  |                                       |
  +=======================================+

  SPC Gating Rules:
  +------------------------------------------------------+
  | MIN_BASELINE_DAYS   = 14  (env: SPC_MIN_BASELINE_DAYS)   |
  | MIN_TOTAL_SAMPLES   = 100 (env: SPC_MIN_TOTAL_SAMPLES)   |
  | Method: Wilson interval (n<30), 3-sigma (n>=30)      |
  | Ratio metrics clamped to [0,1]                       |
  | FP rate is one-sided (only alerts on increase)       |
  +------------------------------------------------------+
```

### What Unblocks Step 4

```
  TODAY                    +14 days
    |                         |
    v                         v
  Start labeling           SPC has enough data
  10+ signals/day          overall_fp_rate
  as TP or FP              compute_control_limits
    |                      returns non-None
    |                         |
    +---- quality label ------+
    |     command:            |
    |     python -m ops.cli   |
    |     quality label       |
    |     <id> FP --reason    |
    |     "B2B SaaS"          |
    |                         |
    v                         v
  quality_metrics_daily    activation-check --step 4
  backfill populates       verdict = "ready" or "warn"
  daily aggregates         (not "blocked")
```

---

## 4. Feature Flag Progression

Flags organized by when they activate in the ladder. Arrows show the progression from default to current to target state.

```
  +------------------------------------------------------------------+
  | FLAG                           | DEFAULT  | CURRENT  | 4A       | 4B       |
  +------------------------------------------------------------------------+
  |                                                                        |
  | STEP 1 FLAGS (Shadow, observe-only)                                    |
  | --------------------------------                                       |
  | LLM_THESIS_MODE                | off      | shadow   | active   | active   |
  | ML_ENABLEMENT                  | disabled | shadow   | live     | live     |
  | MERGE_WRITES_ENABLED           | disabled | shadow   | shadow   | active   |
  | USE_SHADOW_ENTITY_RESOLUTION   | false    | true     | true     | true     |
  |                                                                        |
  | STEP 2 FLAGS (Low-risk writes)                                         |
  | --------------------------------                                       |
  | DRIFT_MONITORING_ENABLED       | disabled | active   | active   | active   |
  | USE_THIN_FILES                 | false    | true     | true     | true     |
  | V2_ENABLEMENT                  | shadow   | live     | live     | live     |
  |                                                                        |
  | STEP 3 FLAGS (Manual writes)                                           |
  | --------------------------------                                       |
  | DELIVERY_MODE                  | staging  | manual   | batch    | batch    |
  | BULK_TRIAGE_ENABLED            | disabled | active   | active   | active   |
  | HUNTER_PROMOTE_ENABLED         | disabled | active   | active   | active   |
  |                                                                        |
  | STEP 4A FLAGS (Batch publish, merges shadow)                           |
  | --------------------------------                                       |
  | DELIVERY_MODE                  | (above)  | manual   | batch    | batch    |
  | MERGE_WRITES_ENABLED           | (above)  | shadow   | shadow   | active   |
  |                                                                  |
  | PHASE G FLAGS (Parallel track)                                   |
  | --------------------------------                                 |
  | USE_PHASE_G_IDENTITY_RESOLUTION| false    | false    | true     |
  | USE_CLAIM_FACTS                | false    | true     | true     |
  |                                                                  |
  +------------------------------------------------------------------+
```

### State Transition Diagram

```
  DEFAULT ─────> STEP 1 ─────> STEP 2 ─────> STEP 3 ─────> STEP 4A ─────> STEP 4B
                                                                ^
    all off       shadow       low-risk      manual write    |BLOCKED|      live
                  observe      thin files    Notion push     batch         merges
                  log only     drift alerts  triage          (merges       (full
                                                              shadow)      production)

  Each arrow requires:
    1. activation-check --step N (must return ready/warn)
    2. Preflight + backup
    3. Monitoring window elapsed (48h for 1-2, 24h for 3, 7d for 4A→4B)
```

---

## 5. Phase G Activation Track (Parallel)

Phase G has its own 4-phase activation sequence, partially overlapping with the main ladder:

```
  Main Ladder Step 1
        |
        v
  +==========================+
  |  PHASE G-1: SHADOW PILOT |   STATUS: COMPLETE
  |  48h minimum             |
  |                          |
  |  USE_SHADOW_ENTITY_      |   Entity blocking index populated (209 rows)
  |  RESOLUTION=true         |   Merge suggestions computed (shadow)
  |  MERGE_WRITES_ENABLED=   |   No merges applied
  |  shadow                  |
  +============+=============+
               |
               | phase-g-check verdict = ready/warn
               | blocking_index_populated
               | merge rejection rate < 10%
               |
  +============v=============+
  |  PHASE G-2: REVIEW       |   STATUS: COMPLETE
  |  (Manual quality check)  |
  |                          |
  |  entity-merge-preview    |   24 shadow runs, 100% agreement
  |  --limit 10              |   0 merges proposed (clean separation)
  +============+=============+
               |
               | Main Ladder at Step 4 (MERGE_WRITES_ENABLED=active)
               |
  +============v=============+
  |  PHASE G-3: ACTIVATE     |   STATUS: DEFERRED
  |  Entity Resolution       |   Blocked on Step 4
  |                          |
  |  USE_PHASE_G_IDENTITY_   |   merge_entities() applies merges
  |  RESOLUTION=true         |   cascade_merge() reassigns records
  |  MERGE_WRITES_ENABLED=   |   entity_migrations tracked
  |  active                  |
  +============+=============+
               |
               | 1 week clean
               |
  +============v=============+
  |  PHASE G-4: CLAIM FACTS  |   STATUS: COMPLETE (dry-run guard)
  |  (Optional, bi-temporal) |
  |                          |
  |  USE_CLAIM_FACTS=true    |   55 claim facts persisted
  |                          |   Governance eval recorded
  +==========================+
```

### Phase G Readiness Gate (5 checks)

```
  +---------------------------------------+--------+---------+----------+
  | Check                                 | Pass   | Warn    | Blocked  |
  +---------------------------------------+--------+---------+----------+
  | 1. Entity tables present (4 tables)   | all 4  |   --    | any      |
  |    entity_aliases, entity_migrations  | exist  |         | missing  |
  |    entity_key_aliases, blocking_index |        |         |          |
  +---------------------------------------+--------+---------+----------+
  | 2. Blocking index populated           | > 0    | 0 rows  |   --     |
  |    (shadow data exists)               | rows   |         |          |
  +---------------------------------------+--------+---------+----------+
  | 3. Shadow merge quality               | < 10%  |   --    | > 10%    |
  |    (rejection rate)                   | reject |         | reject   |
  +---------------------------------------+--------+---------+----------+
  | 4. No orphaned entities               | 0      | > 0     |   --     |
  |    (signals → valid entity roots)     | orphan | orphan  |          |
  +---------------------------------------+--------+---------+----------+
  | 5. Claim facts consistent             | no     | contra- |   --     |
  |    (no contradictions)                | issues | dictions|          |
  +---------------------------------------+--------+---------+----------+

  Verdict: any BLOCKED → overall BLOCKED
           any WARN → overall WARN (can proceed)
           all PASS → READY
```

---

## 6. LOB v7 Sub-Steps (0D/0E)

Between Steps 3 and 4, two validation sub-steps were completed:

```
  STEP 3 (Write Activation) ── COMPLETE
        |
        v
  +==========================+
  |  STEP 0D: CLAIM FACTS    |   STATUS: COMPLETE (2026-03-01)
  |  SHADOW                   |
  |                           |
  |  USE_CLAIM_FACTS=true     |   50 facts persisted
  |  dry_run guard active     |   Governance eval recorded
  |  RULE1-compliant          |   (feature_eval_completed)
  +============+=============+
               |
  +============v=============+
  |  STEP 0E: PHASE G        |   STATUS: COMPLETE (2026-03-01)
  |  BASELINE                 |
  |                           |
  |  24 shadow runs           |   100% agreement rate
  |  0 actual merges          |   Governance eval recorded
  |  phase_g_readiness=ready  |
  +============+=============+
               |
        v
  STEP 4A (Batch Publish) ── ACTIVE (promoted 2026-03-16)
```

---

## 7. Config Snapshot System

Each pipeline run captures a deterministic config snapshot for audit and regret analysis:

```
  Pipeline run starts
        |
        v
  compute_config_snapshot()
        |
        v
  Read 13 env vars:
  +------------------------------------+
  | BULK_TRIAGE_ENABLED                |
  | DELIVERY_MODE                      |
  | DRIFT_MONITORING_ENABLED           |
  | HUNTER_ENABLEMENT                  |
  | HUNTER_PROMOTE_ENABLED             |
  | LLM_THESIS_MODE                    |
  | MERGE_WRITES_ENABLED               |
  | ML_ENABLEMENT                      |
  | USE_CLAIM_FACTS                    |
  | USE_PHASE_G_IDENTITY_RESOLUTION    |
  | USE_SHADOW_ENTITY_RESOLUTION       |
  | USE_THIN_FILES                     |
  | V2_ENABLEMENT                      |
  +------------------------------------+
        |
        v
  JSON-serialize (sorted keys) → SHA256[:16] hash
        |
        v
  Stored in pipeline_runs.config_snapshot
        |
        v
  Used for:
    - Regret checks (did flag change cause regressions?)
    - Audit trail (what was active when a signal was processed?)
    - Rollback decisions (restore exact flag state)
```

---

## 8. Canary System

The canary system provides the primary health signal for activation gates:

```
  +===============================================+
  |              CANARY PIPELINE                  |
  +===============================================+
  |                                               |
  |  Golden Set: 30 known-good signals            |
  |  Cadence: Every 6 hours (canary-monitor-6h)   |
  |  Pass threshold: 90% (27/30)                  |
  |                                               |
  |  Verdicts:                                    |
  |    pass     = >= 90% golden signals verified  |
  |    degraded = 70-90% (some signals failing)   |
  |    fail     = < 70% (serious regression)      |
  |                                               |
  +========================+======================+
                           |
                           v
  +===============================================+
  |           CANARY → ACTIVATION GATE            |
  +===============================================+
  |                                               |
  |  canary_runs table                            |
  |    - verdict, pass_rate, created_at           |
  |    - Queried: ORDER BY created_at DESC LIMIT 1|
  |                                               |
  |  canary_drift_alerts table                    |
  |    - severity: critical / warning             |
  |    - status: open / resolved                  |
  |    - Queried: WHERE status = 'open'           |
  |                                               |
  +===============================================+
```

---

## 9. Rollback Paths

Each step has a defined rollback to the previous safe state:

```
  STEP 4B ──rollback──> STEP 4A
    MERGE_WRITES_ENABLED=shadow

  STEP 4A ──rollback──> STEP 3
    DELIVERY_MODE=manual_publish

  STEP 3 ──rollback──> STEP 2
    DELIVERY_MODE=staging_only
    BULK_TRIAGE_ENABLED=disabled
    HUNTER_PROMOTE_ENABLED=disabled

  STEP 2 ──rollback──> STEP 1
    DRIFT_MONITORING_ENABLED=disabled
    USE_THIN_FILES=false
    V2_ENABLEMENT=shadow

  STEP 1 ──rollback──> BASELINE
    LLM_THESIS_MODE=off
    ML_ENABLEMENT=disabled
    MERGE_WRITES_ENABLED=disabled
    USE_SHADOW_ENTITY_RESOLUTION=false

  EMERGENCY FULL ROLLBACK:
    All flags → default values
    Restart API server
    Verify smoke suite
```

---

## 10. Current Production State Summary

```
  +==================================================+
  |  PRODUCTION STATE (2026-03-20, point-in-time)     |
  +==================================================+
  |                                                    |
  |  LADDER POSITION: Step 4A (Batch Publish) - ACTIVE|
  |  NEXT STEP: 4B (Live Merges)                      |
  |    Repo-enforced earliest: 2026-03-23             |
  |    Default local policy: 14-day regret check due  |
  |    2026-03-30; earlier promotion requires explicit |
  |    written sign-off                                |
  |                                                    |
  |  Active Feature Flags:                             |
  |  +------------------------------------------------+
  |  | LLM_THESIS_MODE         = shadow               |
  |  | ML_ENABLEMENT           = shadow               |
  |  | MERGE_WRITES_ENABLED    = shadow               |
  |  | USE_SHADOW_ENTITY_RES.  = true                  |
  |  | DRIFT_MONITORING_ENABLED= active                |
  |  | USE_THIN_FILES          = true                  |
  |  | V2_ENABLEMENT           = live                  |
  |  | DELIVERY_MODE           = batch_publish         |
  |  | BULK_TRIAGE_ENABLED     = active                |
  |  | HUNTER_PROMOTE_ENABLED  = active                |
  |  | USE_CLAIM_FACTS         = true                  |
  |  +------------------------------------------------+
  |                                                    |
  |  Key Metrics:                                      |
  |  +------------------------------------------------+
  |  | Canary #48: 93.14%                              |
  |  | SPC collector_volume: in_control                |
  |  | SPC overall_fp_rate: insufficient_data          |
  |  | Signals: 612 | Labels: 211 | Company files: 503|
  |  |   (verified against signals.db on 2026-03-20)  |
  |  +------------------------------------------------+
  |                                                    |
  |  Step 4B Gating:                                   |
  |  +------------------------------------------------+
  |  | Layer 1 (repo-enforced):                        |
  |  |   7 clean days on 4A + activation gate green    |
  |  |   Earliest: 2026-03-23                          |
  |  | Layer 2 (default local policy):                 |
  |  |   14-day regret check due 2026-03-30            |
  |  |   Earlier promotion requires explicit written   |
  |  |   sign-off                                      |
  |  +------------------------------------------------+
  |                                                    |
  |  Deferred:                                         |
  |  +------------------------------------------------+
  |  | Step 3B (auto_publish): needs NER convergence   |
  |  | Phase G-3 (live merges): needs Step 4B          |
  |  +------------------------------------------------+
  |                                                    |
  +==================================================+
```

---

## Legend

| Symbol | Meaning |
|--------|---------|
| `COMPLETE` | Step successfully activated and monitoring clean |
| `BLOCKED` | Cannot proceed (gate check returns "blocked") |
| `DEFERRED` | Not yet attempted (dependency on future work) |
| `warn` | Gate returns warning (can proceed with caution) |
| `pass` | Gate condition satisfied |
| `BLOCKED` (in gate matrix) | Gate blocks advancement |

---

## Key Files Index

| File | Purpose |
|------|---------|
| `monitoring/activation_gate.py` | STEP_POLICY, check_activation_readiness() |
| `monitoring/spc_monitor.py` | SPCMonitor, VALID_SPC_METRICS, control limits |
| `monitoring/phase_g_readiness.py` | Phase G readiness gate (5 checks) |
| `monitoring/feature_gate.py` | Config snapshot (13 keys), regret checks |
| `monitoring/canary_checker.py` | Golden set canary runner |
| `monitoring/daily_aggregator.py` | backfill_daily_metrics() for SPC data |
| `utils/config_validator.py` | WRITE_FEATURE_ENV_VARS, THRESHOLD_ENV_VARS |
| `utils/feature_states.py` | FeatureRegistry (ACTIVE/SHADOW/OFF) |
| `docs/runbooks/feature-activation.md` | Step 1-4 activation runbook |
| `docs/runbooks/phase-g-activation.md` | Phase G 4-phase activation runbook |
| `docs/runbooks/step2-promote-gate.md` | Step 2 promote/hold checklist |

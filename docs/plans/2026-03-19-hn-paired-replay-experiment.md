# HN Twin-Snapshot Paired Replay Experiment

**Created:** 2026-03-19
**Branch:** `obs/step4a-window-mar19-23`
**Status:** GATES FROZEN — do not edit thresholds after this commit

## Overview

A twin-snapshot paired replay experiment to produce auditable evidence for whether
`LLM_THESIS_MODE=active` improves Hacker News signal quality enough to justify a
post-window Option 2 spike (HN-scoped LLM activation).

### What This Experiment Can Prove

- Whether active thesis classification reduces HN false positives on a **replayed benchmark set**
- Whether a blind holdout set shows improvement under active classification
- Sufficient evidence to **approve an Option 2 spike** (HN-scoped activation)

### What This Experiment Cannot Prove

- Cross-collector generalization (Option 1 requires separate validation)
- Live production behavior (this is a replay, not a randomized A/B test)
- Sustained performance over time (single snapshot, single run)

### Decision Standard

| Outcome | Interpretation |
|---------|---------------|
| Benchmark + blind holdout pass | Approve Option 2 spike |
| Benchmark-only pass | Suggestive evidence only — no rollout decision |
| Any validity gate fails | Invalidate run, fix setup, rerun |
| Benchmark or holdout gates fail | Do not broaden activation; investigate |

---

## 1. Replay Manifest

The replay manifest is the union of three disjoint sets, frozen before either arm runs.

### 1.1 Set Definitions

| Set | Description | Minimum Size | Source |
|-----|-------------|-------------|--------|
| `known_bad` | Labeled FP HN signals that should be rejected/held | >= 10 rows | `signal_quality_metrics` WHERE `human_label = 'FP'` AND source is `hacker_news` |
| `known_good` | Labeled TP HN signals that should remain qualified | >= 5 rows | `signal_quality_metrics` WHERE `human_label = 'TP'` AND source is `hacker_news` |
| `blind_holdout` | Unlabeled HN signals for blind adjudication | exactly 10 rows | Random sample from HN signals without labels |

### 1.2 Minimum Size Rule

If the known-bad minimum (10) or known-good minimum (5) cannot be met from the
production snapshot, **downgrade the result to suggestive only** — do not claim the
experiment meets the full evidence standard.

### 1.3 Manifest Construction Query

```sql
-- known_bad: FP-labeled HN signals
SELECT s.id, s.canonical_key, s.company_name, 'known_bad' AS manifest_set
FROM signals s
JOIN signal_quality_metrics sqm ON s.id = sqm.signal_id
WHERE s.source_api = 'hacker_news'
  AND sqm.human_label = 'FP'
ORDER BY s.detected_at DESC;

-- known_good: TP-labeled HN signals
SELECT s.id, s.canonical_key, s.company_name, 'known_good' AS manifest_set
FROM signals s
JOIN signal_quality_metrics sqm ON s.id = sqm.signal_id
WHERE s.source_api = 'hacker_news'
  AND sqm.human_label = 'TP'
ORDER BY s.detected_at DESC;

-- blind_holdout: unlabeled HN signals (random 10)
SELECT s.id, s.canonical_key, s.company_name, 'blind_holdout' AS manifest_set
FROM signals s
LEFT JOIN signal_quality_metrics sqm ON s.id = sqm.signal_id
WHERE s.source_api = 'hacker_news'
  AND sqm.id IS NULL
ORDER BY RANDOM()
LIMIT 10;
```

### 1.4 Manifest Rules

- Every replay-manifest row must exist in the copied snapshot
- All replay-manifest rows are requeued in both copied DBs
- All replay-manifest rows are included in execution and analysis
- Reporting is split into `known_bad`, `known_good`, and `blind_holdout`
- The manifest is saved as `artifacts/hn_trial/replay-manifest.json` before either arm runs

### 1.5 Manifest Schema

```json
{
  "created_at": "2026-03-XX",
  "commit_sha": "<sha>",
  "sets": {
    "known_bad": [
      {"signal_id": 123, "canonical_key": "domain:example.com", "company_name": "..."}
    ],
    "known_good": [...],
    "blind_holdout": [...]
  },
  "counts": {
    "known_bad": 0,
    "known_good": 0,
    "blind_holdout": 10,
    "total": 0
  },
  "minimum_met": true
}
```

---

## 2. Snapshot Method

Use `backup_db.py` for WAL-safe online copies — **not** raw file copy of `signals.db`.

### 2.1 Snapshot Creation

```powershell
# Step 1: Create base snapshot
python scripts/backup_db.py --db signals.db --out-dir artifacts/hn_trial_base --retain 1

# Step 2: Copy base to control and active arms
New-Item -ItemType Directory -Path artifacts/hn_trial -Force
Copy-Item artifacts/hn_trial_base/signals-*.db artifacts/hn_trial/control.db
Copy-Item artifacts/hn_trial_base/signals-*.db artifacts/hn_trial/active.db
```

### 2.2 Provenance Record

Save as `artifacts/hn_trial/provenance.json`:

```json
{
  "base_snapshot": "artifacts/hn_trial_base/signals-YYYYMMDD-HHMMSS.db",
  "control_db": "artifacts/hn_trial/control.db",
  "active_db": "artifacts/hn_trial/active.db",
  "commit_sha": "<git rev-parse HEAD>",
  "snapshot_timestamp": "<ISO 8601>",
  "trial_start": null,
  "trial_end": null,
  "control_env": {
    "LLM_THESIS_MODE": "off",
    "DELIVERY_MODE": "batch_publish",
    "MERGE_WRITES_ENABLED": "shadow"
  },
  "active_env": {
    "LLM_THESIS_MODE": "active",
    "DELIVERY_MODE": "batch_publish",
    "MERGE_WRITES_ENABLED": "shadow"
  },
  "model_name": null,
  "prompt_version": null,
  "commands": {
    "backup": "python scripts/backup_db.py --db signals.db --out-dir artifacts/hn_trial_base --retain 1",
    "control_run": "see Section 4",
    "active_run": "see Section 4"
  }
}
```

---

## 3. Queue Isolation

Because `process` drains all pending rows, isolate the copied DBs so only replay-manifest
HN rows are pending.

### 3.1 Pre-Run Boundary Exports

Before any queue manipulation, record boundaries in each copied DB:

```sql
-- Run against both control.db and active.db BEFORE queue isolation
SELECT MAX(id) AS max_signal_id FROM signals;
SELECT MAX(id) AS max_classification_id FROM thesis_classifications;
```

Save as `artifacts/hn_trial/boundaries.json`:

```json
{
  "control": {
    "max_signal_id": null,
    "max_classification_id": null
  },
  "active": {
    "max_signal_id": null,
    "max_classification_id": null
  }
}
```

### 3.2 Pre-Run Snapshots

For each replay-manifest signal_id, capture pre-run state:

```sql
-- Pre-run status snapshot
SELECT sp.signal_id, sp.status, sp.processed_at, sp.error_message, sp.metadata
FROM signal_processing sp
WHERE sp.signal_id IN (<replay_manifest_signal_ids>);

-- Pre-run classification snapshot
SELECT tc.signal_id, tc.keyword_score, tc.category, tc.thesis_fit_score,
       tc.rationale, tc.classified_at
FROM thesis_classifications tc
WHERE tc.signal_id IN (<replay_manifest_signal_ids>);
```

Save as `artifacts/hn_trial/pre-run-snapshot-control.json` and `artifacts/hn_trial/pre-run-snapshot-active.json`.

### 3.3 Queue Isolation SQL

Run against **both** `control.db` and `active.db`:

```sql
-- Step 1: Verify 'held' is inert (not returned by get_pending_signals)
-- This is proven by test_held_not_in_pending_signals in test_signal_store_status.py
-- get_pending_signals() uses WHERE p.status = 'pending' — held rows are excluded.

-- Step 2: Move ALL non-manifest rows to 'held' (inert sink status)
UPDATE signal_processing
SET status = 'held',
    error_message = 'queue_isolation: parked for HN replay trial'
WHERE signal_id NOT IN (<replay_manifest_signal_ids>)
  AND status = 'pending';

-- Step 3: Requeue all replay-manifest rows to 'pending'
UPDATE signal_processing
SET status = 'pending',
    processed_at = NULL,
    error_message = NULL,
    metadata = NULL
WHERE signal_id IN (<replay_manifest_signal_ids>);

-- Step 4: Verify isolation
SELECT COUNT(*) AS pending_count FROM signal_processing WHERE status = 'pending';
-- Must equal replay manifest count exactly

SELECT COUNT(*) AS non_manifest_pending
FROM signal_processing
WHERE status = 'pending'
  AND signal_id NOT IN (<replay_manifest_signal_ids>);
-- Must be 0
```

### 3.4 Held Inertness Verification

The `held` status is inert because `get_pending_signals()` filters with
`WHERE p.status = 'pending'`. This is:

- Verified by reading `storage/signal_store.py:2800` (`WHERE p.status = 'pending'`)
- Proven by `tests/storage/test_signal_store_status.py::TestHeldExcludedFromPending`

---

## 4. Run Shape

Use `process`, not `full`. Keep `--dry-run` on both arms.

### 4.1 Control Arm

```powershell
powershell -NoProfile -Command @"
  `$env:LLM_THESIS_MODE='off'
  `$env:DELIVERY_MODE='batch_publish'
  `$env:MERGE_WRITES_ENABLED='shadow'
  python run_pipeline.py process --db-path artifacts/hn_trial/control.db --dry-run --batch-size <MANIFEST_COUNT_PLUS_5>
"@
```

**Critical:** Set `LLM_THESIS_MODE=off` explicitly. Do not rely on ambient env, empty string, or "unset".

### 4.2 Active Arm

```powershell
powershell -NoProfile -Command @"
  `$env:LLM_THESIS_MODE='active'
  `$env:DELIVERY_MODE='batch_publish'
  `$env:MERGE_WRITES_ENABLED='shadow'
  python run_pipeline.py process --db-path artifacts/hn_trial/active.db --dry-run --batch-size <MANIFEST_COUNT_PLUS_5>
"@
```

### 4.3 Run Rules

- `--dry-run` is mandatory on both arms
- Copied SQLite DB mutation is expected (thesis_classifications rows, status changes)
- Zero external side effects is mandatory (no Notion writes, no production DB mutation)
- Batch size = replay manifest count + 5 buffer
- Capture stdout/stderr to `artifacts/hn_trial/control-stdout.log` and `artifacts/hn_trial/active-stdout.log`
- Record trial start/end timestamps in provenance.json
- Record model name and prompt version after active arm completes

### 4.4 Stdout/Stderr Capture

```powershell
# Control arm with logging
powershell -NoProfile -Command @"
  `$env:LLM_THESIS_MODE='off'
  `$env:DELIVERY_MODE='batch_publish'
  `$env:MERGE_WRITES_ENABLED='shadow'
  python run_pipeline.py process --db-path artifacts/hn_trial/control.db --dry-run --batch-size <N> 2>&1 | Tee-Object -FilePath artifacts/hn_trial/control-stdout.log
"@

# Active arm with logging
powershell -NoProfile -Command @"
  `$env:LLM_THESIS_MODE='active'
  `$env:DELIVERY_MODE='batch_publish'
  `$env:MERGE_WRITES_ENABLED='shadow'
  python run_pipeline.py process --db-path artifacts/hn_trial/active.db --dry-run --batch-size <N> 2>&1 | Tee-Object -FilePath artifacts/hn_trial/active-stdout.log
"@
```

---

## 5. Metrics

### 5.1 Analysis Scope

Analyze **only**:

- Replay-manifest rows
- Rows/classifications created after the recorded boundaries (Section 3.1)

### 5.2 Metric Definitions

| Metric | Definition |
|--------|-----------|
| **Structured classification coverage** | Replay-manifest rows with a new `thesis_classifications` row after the recorded boundary AND non-null structured thesis fields (`category`, `thesis_fit_score`) |
| **Fallback proxy rate** | Fraction of new classification rows whose `rationale` starts with one of: `Circuit breaker OPEN`, `Rate limit exceeded`, `Classification failed`, `Failed to parse response` |
| **Known-bad correction** | Seeded bad rows no longer QUALIFIED (status != pending after reprocessing, or routing != QUALIFIED) |
| **Known-good retention** | Seeded good rows still QUALIFIED (status remains processable or routing == QUALIFIED) |
| **Blind holdout correctness** | Manual adjudication of the final routing against the frozen reviewer rubric (Section 5.5) |

### 5.3 Important Caveats

- Structured output is **not proof** of successful LLM participation — fallback paths can populate structured fields
- `llm_skipped` is **not persisted** through the pipeline path — use the fallback proxy heuristic instead
- HELD and REJECTED must be reported **separately** in known-bad outcomes
- HELD counts as a **safety improvement** but not as a precision-equivalent win
- A row with a new classification row whose rationale matches a fallback proxy pattern should be counted as a fallback, not as a genuine LLM classification

### 5.4 Export Queries

```sql
-- Per-signal before/after diff (run against each arm's DB after the run)
-- Compare against pre-run snapshots from Section 3.2

-- New classifications created after boundary
SELECT tc.*
FROM thesis_classifications tc
WHERE tc.id > <max_classification_id_boundary>
  AND tc.signal_id IN (<replay_manifest_signal_ids>);

-- Post-run status for replay-manifest rows
SELECT sp.signal_id, sp.status, sp.processed_at, sp.error_message
FROM signal_processing sp
WHERE sp.signal_id IN (<replay_manifest_signal_ids>);
```

### 5.5 Blind Holdout Reviewer Rubric

For each of the 10 blind holdout rows, adjudicate:

1. Read `signals.raw_data` for the signal
2. Determine: Is this company a thesis-fit consumer startup (CPG, health tech, travel, marketplace)?
3. Rate: `correct_qualified`, `correct_rejected`, `correct_held`, `false_positive`, `false_negative`
4. The adjudicator must not see which arm produced which result until after rating

### 5.6 Summary Report Template

```
# HN Paired Replay Trial Results
Date: YYYY-MM-DD
Commit: <sha>

## Validity Gates
- Replay-manifest reprocessed rate: control=X/Y, active=X/Y  [PASS/FAIL]
- Non-HN contamination: control=N, active=N                   [PASS/FAIL]
- External side effects: 0                                     [PASS/FAIL]
- Live Notion writes: 0                                        [PASS/FAIL]
- Production DB mutation: 0                                    [PASS/FAIL]

## Interpretability Gates
- Active structured classification coverage: X%                [PASS/FAIL >= 80%]
- Active fallback proxy rate: X%                               [PASS/FAIL <= 20%]

## Known-Bad Results (N rows)
| Metric              | Control | Active | Delta |
|---------------------|---------|--------|-------|
| QUALIFIED count     |         |        |       |
| HELD count          |         |        |       |
| REJECTED count      |         |        |       |
| QUALIFIED rate      |         |        |       |
| REJECTED rate       |         |        |       |

- Active known-bad QUALIFIED rate improvement: X pp            [PASS/FAIL >= 30pp]
- Active known-bad REJECTED rate improvement: X pp             [PASS/FAIL >= 20pp]
- HELD share of improvement: X%                                [INCONCLUSIVE if > 50%]

## Known-Good Results (N rows)
| Metric              | Control | Active | Delta |
|---------------------|---------|--------|-------|
| QUALIFIED count     |         |        |       |
| Retention rate      |         |        |       |

- Active known-good retention: X%                              [PASS/FAIL >= 80%]
- Retention drop vs control: X pp                              [PASS/FAIL <= 10pp]

## Blind Holdout Results (10 rows)
| Signal ID | Control Routing | Active Routing | Adjudicated | Control Correct? | Active Correct? |
|-----------|----------------|----------------|-------------|-----------------|----------------|

- Active additional correct outcomes vs control: N             [PASS/FAIL >= 2]
- Active additional false negatives vs control: N              [PASS/FAIL <= 1]

## Operational Metrics
- Runtime: control=Xs, active=Xs
- Model call count: control=N, active=N
- Replay-manifest rows actually reprocessed: control=N, active=N
- Non-HN contamination: control=N, active=N
- External side effects: 0

## Decision
[APPROVE OPTION 2 SPIKE / SUGGESTIVE ONLY / INVALIDATE / FAIL]
```

---

## 6. Exact Gates

**These thresholds are frozen at commit time. Do not edit after the first arm runs.**

### 6.1 Hard Validity Gates

All must pass for the run to be valid.

| Gate | Threshold | Measurement |
|------|-----------|-------------|
| Replay-manifest reprocessed rate | = 100% in both arms | Count of manifest rows with changed status or new classification / total manifest rows |
| Non-HN contamination | = 0 | Count of non-`hacker_news` source_api rows with new classifications after boundary |
| Zero external side effects | = 0 | No Notion API calls, no external HTTP requests |
| Zero live Notion writes | = 0 | No Notion page creates or updates |
| Zero production DB mutation | = 0 | Only copied DBs are modified |

### 6.2 Interpretability Gates

Both must pass for the result to be interpretable.

| Gate | Threshold |
|------|-----------|
| Active structured classification coverage | >= 80% |
| Active fallback proxy rate | <= 20% |

### 6.3 Known-Bad Gates

| Gate | Threshold |
|------|-----------|
| Active known-bad QUALIFIED rate improvement | >= 30 percentage points vs control |
| Active known-bad REJECTED rate improvement | >= 20 percentage points vs control |
| HELD share of improvement | If > 50% of active's bad-set improvement comes from HELD rather than REJECTED, mark result **inconclusive** |

### 6.4 Known-Good Gates

| Gate | Threshold |
|------|-----------|
| Active known-good retention | >= 80% |
| Retention drop vs control | <= 10 percentage points |

### 6.5 Blind Holdout Gate

| Gate | Threshold |
|------|-----------|
| Active additional correct outcomes | >= 2 more than control across 10-row holdout |
| Active additional false negatives | <= 1 more than control on holdout |

---

## 7. Preflight Validation

Run these checks before the real trial. All must pass.

### 7.1 Snapshot Integrity

```powershell
python -c "import sqlite3; c=sqlite3.connect('artifacts/hn_trial/control.db'); print(c.execute('PRAGMA integrity_check').fetchone())"
python -c "import sqlite3; c=sqlite3.connect('artifacts/hn_trial/active.db'); print(c.execute('PRAGMA integrity_check').fetchone())"
```

Both must return `('ok',)`.

### 7.2 Schema Readability

```powershell
python -c "
import sqlite3
for db in ['artifacts/hn_trial/control.db', 'artifacts/hn_trial/active.db']:
    c = sqlite3.connect(db)
    tables = [r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
    for t in ['signals', 'signal_processing', 'thesis_classifications']:
        assert t in tables, f'{t} missing from {db}'
    print(f'{db}: schema OK')
"
```

### 7.3 Replay-Manifest Row Presence

```sql
-- Run against both DBs: every manifest signal_id must exist
SELECT COUNT(*) FROM signals WHERE id IN (<replay_manifest_signal_ids>);
-- Must equal manifest total count
```

### 7.4 Queue Isolation Verification

```sql
-- Only manifest rows are pending
SELECT COUNT(*) FROM signal_processing WHERE status = 'pending';
-- Must equal manifest count

SELECT COUNT(*) FROM signal_processing
WHERE status = 'pending' AND signal_id NOT IN (<replay_manifest_signal_ids>);
-- Must be 0
```

### 7.5 Held Inertness

Verified by `tests/storage/test_signal_store_status.py::TestHeldExcludedFromPending::test_held_not_in_pending_signals`.

The `get_pending_signals()` implementation uses `WHERE p.status = 'pending'` — held rows
are never returned.

### 7.6 Dry-Run Semantics

Verified by `tests/integration/test_thesis_pipeline_m0.py::TestReplaySemantics`:

- `--dry-run` persists thesis_classifications rows in the local copied DB
- External writer paths (Notion) stay suppressed under `--dry-run`
- Replay-manifest reruns create detectable post-run evidence (new classification rows)
- Replay-manifest rows are actually reprocessed (status changes from pending)

### 7.7 Rerun Semantics

If a replay-manifest row already has a thesis_classification, the pipeline will:
- Create a **new** classification row (not update the existing one)
- The new row will have `id > max_classification_id_boundary`
- This is how we distinguish pre-existing vs trial-generated classifications

### 7.8 Proof That Every Replay-Manifest Row Is Reprocessed

After each arm completes:

```sql
-- Every manifest row must have EITHER:
-- (a) A new thesis_classifications row with id > boundary, OR
-- (b) A status change from 'pending' to something else
SELECT s.id,
       (SELECT COUNT(*) FROM thesis_classifications tc
        WHERE tc.signal_id = s.id AND tc.id > <boundary>) AS new_classifications,
       sp.status AS post_status
FROM signals s
JOIN signal_processing sp ON s.id = sp.signal_id
WHERE s.id IN (<replay_manifest_signal_ids>);
-- Every row must have new_classifications > 0 OR post_status != 'pending'
```

---

## 8. Decision Logic

```
IF all hard validity gates pass
   AND benchmark gates pass
   AND holdout gate passes
THEN → proceed to Option 2 spike

IF benchmark gates pass
   BUT holdout gate fails or is unavailable
THEN → suggestive only, no rollout decision

IF any validity gate fails
THEN → invalidate run, fix setup, rerun

IF benchmark or holdout gates fail
THEN → do not broaden activation
       investigate prompt/parser/collector path

NOTE: Option 1 (cross-collector activation) requires separate
      cross-collector validation — this experiment cannot support it
```

---

## 9. Retention

### Durable Records (keep indefinitely)

- `artifacts/hn_trial/replay-manifest.json` — replay manifest
- `artifacts/hn_trial/blind-holdout-adjudication.json` — blind holdout results
- `artifacts/hn_trial/per-signal-diff.json` — per-signal before/after diff
- `artifacts/hn_trial/summary-metrics.json` — summary metrics
- `artifacts/hn_trial/decision-memo.md` — decision memo (within 72h of trial)
- `artifacts/hn_trial/provenance.json` — commit SHA and config provenance

### Ephemeral Records (may expire after 30 days)

- `artifacts/hn_trial/control-stdout.log`
- `artifacts/hn_trial/active-stdout.log`
- `artifacts/hn_trial/control.db`
- `artifacts/hn_trial/active.db`
- `artifacts/hn_trial_base/`

---

## 10. Decision Memo Template

Save as `artifacts/hn_trial/decision-memo.md` within 72 hours of trial completion.

```markdown
# HN Paired Replay Decision Memo

**Date:** YYYY-MM-DD
**Commit:** <sha>
**Adjudicator:** <name>

## Trial Summary
- Control: LLM_THESIS_MODE=off
- Active: LLM_THESIS_MODE=active
- Manifest: N known-bad, N known-good, 10 blind holdout

## Gate Results
[Paste completed summary report from Section 5.6]

## Decision
[ ] APPROVE Option 2 spike — all gates pass
[ ] SUGGESTIVE ONLY — benchmark pass but holdout fail/unavailable
[ ] INVALIDATE — validity gate failure (describe which)
[ ] FAIL — benchmark/holdout gates fail (describe which)

## Reasoning
[1-3 sentences explaining the decision]

## Next Steps
[What happens after this decision]
```

---

## Appendix A: Execution Checklist

```
Pre-Trial
[ ] Replay manifest constructed and saved
[ ] Minimum sizes met (10 bad, 5 good, 10 holdout) — or downgraded to suggestive
[ ] Base snapshot created via backup_db.py
[ ] control.db and active.db copied from base
[ ] Boundaries recorded (max signal_id, max classification_id)
[ ] Pre-run snapshots captured for both DBs
[ ] Queue isolation SQL applied to both DBs
[ ] Queue isolation verified (pending count = manifest count)
[ ] Preflight checks all pass (integrity, schema, presence, isolation, held inertness)

Control Arm
[ ] LLM_THESIS_MODE=off set explicitly
[ ] --dry-run flag present
[ ] stdout/stderr captured
[ ] Trial start timestamp recorded

Active Arm
[ ] LLM_THESIS_MODE=active set explicitly
[ ] --dry-run flag present
[ ] stdout/stderr captured
[ ] Trial end timestamp recorded
[ ] Model name and prompt version recorded

Post-Trial
[ ] Per-signal diff exported
[ ] Summary metrics computed
[ ] Validity gates evaluated
[ ] Interpretability gates evaluated
[ ] Known-bad gates evaluated
[ ] Known-good gates evaluated
[ ] Blind holdout adjudicated (blinded)
[ ] Holdout gate evaluated
[ ] Decision memo written within 72 hours
[ ] All durable artifacts committed
```

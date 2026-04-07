# Tier-2 Recall Evaluation — Gating Contract

**Date:** 2026-04-06
**Status:** Spec only — implemented in Move 1
**Resolves:** Risks R13 + R2 (the 64-row golden set is the wrong substrate for
recall gating; the engine-efficacy mechanism gap)

---

## 1. The two tiers

| Tier | Cohort | Measures | Gates | Cadence |
|---|---|---|---|---|
| **Tier 1** | `tests/fixtures/thesis_llm_golden_set.jsonl` (64 rows) | Classifier quality at the prompt level | LLM prompt changes | Every commit |
| **Tier 2** | `data/shadow/holdout_split/episodes_v1.csv` (~30-50 rows, holdout subset only) | Whether the engine surfaces companies that turn into meetings/funding | Substrate changes (Move 1, 2, 3) | At each move's promotion gate |

**Tier 1 already exists.** It's the v1.6.0 employer-distribution-guard work
shipped in PR #130. The 64 rows include 4 employer-distribution fixes that
are now in production.

**Tier 2 does NOT exist yet.** It depends on Track B's labelling sprint to
produce the company-episodes per `05-holdout-cohort-design.md`.

---

## 2. Why Tier-2 is necessary

Per red-team §3.2 (Data Scientist critique):
> The proposal's four moves could ship in full and produce zero analyst-visible
> recall improvement. The mechanism by which "evidence capture" turns into
> "more leads in the analyst's Notion inbox" is missing.

Tier-2 is the metric that closes this gap. Without it:
- Move 1 ships → no measurement → no way to know if it helped
- Move 2 ships → no measurement → no way to know if it helped
- Move 3 ships → no measurement → 6 months of work with no recall validation

With Tier-2:
- Each move has a baseline measurement before
- Each move has a delta measurement after
- The Move 4 decision gate has actual data: "Tier-2 recall is flat → pivot to
  Tracks B/D/E. Tier-2 recall is climbing → continue."

**Tier-2 is the substrate hardening's recall metric.** Without it, the
strategy has no quantitative defense against the "we hardened a layer that
was never the binding constraint" failure mode in the premortem.

---

## 3. The metric

### Primary: Recall at company-episode level

```
Tier-2 recall = (number of holdout episodes whose canonical_key was surfaced
                 by the engine within the episode window)
              / (number of holdout episodes total)
```

A holdout episode counts as "surfaced" if:
- The engine produced ≥1 signal with the same `canonical_key` between
  `episode_start` and `episode_end`
- AND the signal made it past the held queue (status = `pushed` OR `tracking`)

A holdout episode counts as "missed" if:
- The engine produced no signal for that canonical_key in the window
- OR the engine produced signals that were rejected/held without pushing

### Secondary metrics

| Metric | Formula | Purpose |
|---|---|---|
| Lead time | mean(`first_signal_seen_at` - `episode_start`) for surfaced episodes | How early did we catch them? |
| Precision-on-pushed | TPs / (TPs + FPs) on the train cohort | Sanity check against existing ~9% number |
| Coverage by source_api | Surfaced episodes broken down by which collector found them first | Identifies which collectors are pulling weight |
| Outcome-conditional recall | Recall conditioned on `outcome_label = meeting` vs `funded` vs `passed` | Are we missing the high-value episodes specifically? |

---

## 4. The gating contract

| Move | Pre-gate | Post-gate | Decision |
|---|---|---|---|
| **1** | Tier-2 baseline (first-ever measurement) | Tier-2 after Move 1 ships | Move 1 ships regardless — this is the baseline. Capture for Move 2 reference. |
| **2** | Tier-2 from end of Move 1 | Tier-2 after Move 2 ships | If recall declined, escalate. If flat, continue but flag. If improved, ship. |
| **3** | Tier-2 from end of Move 2 | Tier-2 after Move 3 ships | If recall declined, ROLLBACK Postgres dual-write (likely a data bug). If flat, escalate decision. If improved, continue. |
| **4** | Tier-2 from end of Move 3 | (decision gate, not a move) | Flat → pivot to Tracks B/D/E. Climbing → continue and identify next bottleneck. |

**The "ROLLBACK" rule for Move 3 is intentional.** Postgres dual-write is the
first irreversible move in the strategy. A recall decline after Move 3 likely
indicates a data corruption or schema-translation bug — exactly the kind of
silent failure the strategy was designed to catch. The rollback is the safety
net.

---

## 5. The "do not iterate" rule

The holdout cohort is touched ONCE per move (per `05-holdout-cohort-design.md`
§4). The team does NOT:
- Run Tier-2 weekly during a move
- Tune prompts against Tier-2 results
- Add more episodes to the holdout to "fix" a bad result

Iterating on the holdout would invalidate it (turn it into a second train set).

**The team CAN:**
- Run Tier-2 once at each promotion gate
- Capture the result as an artifact under
  `artifacts/red-team-hybrid/tier2-eval/<move>-<date>.json`
- Use the result to make a single ship/rollback decision
- Discuss what Tier-1 (train) data shows in detail; iterate on prompts
  using Tier-1 only

---

## 6. The eval harness (Move 1 deliverable)

A new script at `scripts/red-team-hybrid/run_tier2_eval.py`:

```
Usage:
  python -m scripts.red_team_hybrid.run_tier2_eval \
    --split data/shadow/holdout_split/episodes_v1.csv \
    --signals signals.db \
    --window-end 2026-04-25 \
    --output artifacts/red-team-hybrid/tier2-eval/move1-baseline.json
```

**Inputs:**
- The holdout split file
- The signals.db (read-only via shadow sidecar)
- A window-end date (which determines what counts as "surfaced in time")
- An output JSON path

**Outputs:**
- JSON file with recall, lead time, coverage by source_api, outcome-conditional recall
- Per-episode breakdown: which canonical_keys were surfaced, which were missed
- A summary line written to stdout for use by CI

**Properties:**
- Uses the shadow sidecar (read-only signals.db)
- Does NOT modify any production state
- Reproducible — same inputs → same outputs
- Idempotent — can be re-run without side effects

**This is a Move 1 deliverable, not Move 0.** Move 0 only specifies it.

---

## 7. Output format (locked)

```json
{
  "schema_version": 1,
  "ran_at": "2026-04-25T14:30:00Z",
  "split_file": "data/shadow/holdout_split/episodes_v1.csv",
  "split_version": "v1",
  "window_end": "2026-04-25",
  "n_holdout": 12,
  "n_surfaced": 5,
  "recall": 0.4167,
  "lead_time_days": {
    "mean": 14.2,
    "median": 11.0,
    "p90": 28.0
  },
  "coverage_by_source_api": {
    "hacker_news": 1,
    "news_api": 2,
    "rss_feeds": 1,
    "github": 1
  },
  "outcome_conditional_recall": {
    "meeting": 0.5,
    "funded": null,
    "passed": 0.4
  },
  "per_episode": [
    {
      "episode_id": "acme-ai_2026-Q1",
      "canonical_key": "domain:acme.ai",
      "outcome_label": "meeting",
      "surfaced": true,
      "first_signal_at": "2026-01-22T08:14:00Z",
      "first_signal_id": 423,
      "first_signal_source": "hacker_news",
      "lead_time_days": 7
    }
  ],
  "missed_episodes": [
    {
      "episode_id": "beta-bio_2026-Q1",
      "canonical_key": "name:beta_bio",
      "outcome_label": "passed"
    }
  ]
}
```

The schema is locked at version 1 for the duration of the strategy. Schema
bumps require an explicit migration of historical results.

---

## 8. The "first measurement" rule

**The first Tier-2 result is the baseline. Do not interpret it as a target.**

When Move 1 ships and the team runs Tier-2 for the first time, the result will
be a number — say, 41.67% recall. **This number is the starting line.** It is
neither good nor bad on its own. The team's job is to:
1. Capture it
2. Move forward with Move 2
3. Compare Move 2's result to the Move 1 baseline

Pre-judging the baseline ("we should be at 60%") is exactly the bias the
holdout split was designed to prevent.

---

## 9. Statistical caveats

With 9-15 holdout episodes (per `05-holdout-cohort-design.md` §7), the recall
metric has wide error bars. A naive 95% confidence interval on a sample of 12:

| True recall | Observed recall (point estimate) | 95% CI (Wilson) |
|---|---|---|
| 0.40 | 5/12 = 0.417 | (0.183, 0.696) |
| 0.50 | 6/12 = 0.500 | (0.249, 0.751) |
| 0.60 | 7/12 = 0.583 | (0.319, 0.806) |

**Implication:** the team should NOT make rollback decisions on a single Move's
delta of <15 percentage points. The "rollback Move 3 if recall declines" rule
in §4 means **statistically meaningful decline**, which at 12 episodes means
roughly ≥2 episodes.

The strategy compensates by:
- Looking at trends across multiple moves, not single deltas
- Looking at outcome-conditional recall (per §3) for higher-precision signal
- Letting the cohort grow over time as Track B continues labelling

---

## 10. CI integration (Move 2 deliverable)

The eval harness runs in CI on every PR that touches the substrate
(collectors, workflows, governance, monitoring, connectors, storage/migrations).

**Advisory mode (Move 2 first 30 days):**
- Result is reported as a comment on the PR
- A regression does NOT block the merge
- The team learns the false-fail rate

**Blocking mode (after 30 days of zero false-fails, Move 3 onward):**
- A regression of >2 episodes lost vs the previous Move's baseline blocks merge
- Override requires a documented justification
- The override is recorded in `audit_events` (in the Postgres era)

This mirrors the existing canary infrastructure pattern (per project memory:
canary at 91.46% pass with SPC-baselined thresholds, not freshly-tuned).

---

## 11. The relationship to the existing 9% precision number

Per LOB.txt evaluation (Phase 0): the existing 211 labels show ~9% precision
on what the pipeline surfaces. **Is Tier-2 going to land near 9%?**

Probably not directly. The two metrics measure different things:
- 9% = TP rate among signals pushed to Notion (precision of the surfaced set)
- Tier-2 recall = fraction of *companies* that turned into outcomes that the
  engine surfaced (recall over outcome-bearing entities)

A high Tier-2 recall with a low precision is consistent ("we surface lots of
things including the right ones, just buried in noise"). A low Tier-2 recall
with a high precision is also consistent ("we surface very few things and they
are mostly right but we miss most of the wins").

The strategy's job is to move BOTH numbers in the right direction, but they
move via different levers:
- Precision improves when classification gets better (Tier-1 work, prompt tuning)
- Recall improves when the substrate captures more, dedups better, holds
  fewer false negatives, and the labelling cohort gets bigger (Tracks B/D/E)

---

## 12. Open questions

1. **What's the right window-end policy for the eval?** Should the window be
   "everything from episode_start to today" or "everything within 30 days of
   episode_start"? Recommended: 30-day window, since the strategy's lead-time
   target is roughly that. Decision: Move 1 day 1.
2. **Should the eval consider canonical_key aliases?** Yes — the surfacing
   check should resolve aliases via `canonical_key_aliases` so that a signal
   under `domain:acme.ai` matches an episode for `name:acme_ai_inc`.
3. **Should Tier-2 results be pushed to Slack or just artifact files?**
   Probably both. Slack for visibility, artifact for the audit trail.
   Decision: Move 1 day 5.

---

## 13. Move 0 deliverable checklist

- [ ] This spec doc exists (DONE)
- [ ] Metric definitions locked (DONE)
- [ ] Output format JSON schema locked (DONE)
- [ ] Gating contract per move locked (DONE)
- [ ] Statistical caveats documented (DONE)
- [ ] Tracks B + cohort split feed into this (referenced)
- [ ] Eval harness implementation specced for Move 1 (NOT built in Move 0)

The harness implementation is Move 1 day 1-3. Move 0 produces only the spec.

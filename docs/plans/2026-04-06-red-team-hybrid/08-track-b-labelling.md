# Track B — Company-Episode Labelling Sprint

**Date:** 2026-04-06
**Status:** Active — starts on day 1 of Move 0
**Owner:** Lead engineer + analyst (pairing required)
**Resolves:** Risk R2 (engine-efficacy mechanism gap) — this is the canary
metric for the whole strategy framing

---

## 1. Why this is the most important parallel track

Per `00-strategy.md` §2:
> If Track A ships in full and B/D/E stall, this strategy fails on its own
> goal even if executed perfectly. Track B's labelling cadence is the canary
> metric for the whole framing.

Track B produces the company-episodes that:
- Feed the hold-out cohort split (`05-holdout-cohort-design.md`)
- Become the Tier-2 recall eval substrate (`06-tier-2-recall-eval.md`)
- Provide the only mechanism by which substrate hardening turns into measurable
  recall improvement

**If Track B is not running, Move 1+ has nothing to measure against.**

---

## 2. Target

| Metric | Move 0 (by 2026-04-19) | Move 1 (end of month 2) | Move 3 (end of month 5) |
|---|---|---|---|
| Total episodes | 30-50 | 60-80 | 100+ |
| Holdout episodes (30%) | 9-15 | 18-24 | 30+ |
| Cadence | ~3-5 episodes/day | ~1-2/day steady-state | ~1/day steady-state |

The Move 0 ramp is faster than steady-state because the strategy needs the
holdout cohort *built* before Move 1 ships.

---

## 3. Definition: company-episode

A *company-episode* is a labelled outcome event for a single canonical company
over a time window. See `05-holdout-cohort-design.md` §2 for the full schema.

A company-episode is NOT:
- A single signal label (TP/FP on one signal)
- A static snapshot of a company

A company-episode IS:
- A time-bounded observation of a company that has reached an outcome
- An aggregation of all signals about that company over the window
- An analyst's verdict on whether the engine should have surfaced it

**The shift from signal-labels to episode-labels** is what moves the metric
from precision-of-classifier to recall-of-engine.

---

## 4. Source of episodes

Two sources, processed in parallel:

### 4.1 Existing 612 signals + Notion CRM outcomes
The pipeline already has 612 signals and 15 pushed-to-Notion. The Notion CRM
contains the analyst's verdict on each pushed signal:
- 15 pushed signals → analyst can label whether each was a meeting/funded/passed
- For the 187 labelled FPs and 19 TPs, the canonical_keys can be aggregated
  into ~50-100 unique companies, then converted into episodes

**Action:** the analyst picks 30 high-information companies from the existing
data and labels them as episodes. Most of the work is *choosing* which 30 to
label, not the labelling itself.

### 4.2 Forward-looking labelling
As new signals arrive in the pipeline (3-5/day), the analyst spot-labels
~1-2 per day as forward episodes. The episode_end is left open until an
outcome is observed (or after 90 days when the episode is closed as
"no outcome observed").

---

## 5. Labelling protocol

### Tooling
The existing CLI subcommand `python -m ops.cli quality label` (verified
2026-04-06) takes:

```
positional: signal_id, {TP,FP,UNSURE,ADJ}
options: --by, --reason, --notes
```

This is **signal-level labelling**, not episode-level. Track B's first task
is to define the episode-to-signal mapping and either:
1. **Option A:** add a new subcommand `quality label-episode` that takes an
   episode_id + outcome_label and writes to a new table (BLOCKED by R1 — would
   require a v52 migration during the regret window)
2. **Option B:** record episodes in a flat CSV `data/shadow/track_b_episodes.csv`
   and use the existing signal-level labels for the per-signal evidence
   (ALLOWED — writes to data/shadow/ only)

**Decision: Option B during Move 0.** Episode CSV is the source of truth.
Promote to a table in Move 3 alongside the bounded-context refactor.

### Episode CSV format
```
data/shadow/track_b_episodes.csv
```

Columns (matches `05-holdout-cohort-design.md` §2):
```
episode_id,canonical_key,episode_start,episode_end,outcome_label,confidence,evidence_signal_ids,notes,labelled_by,labelled_at
```

Example rows:
```
acme-ai_2026-Q1,domain:acme.ai,2026-01-15T00:00:00Z,2026-03-20T00:00:00Z,meeting,0.9,"[123,124,156]","Met with founder; promising",alice,2026-04-08T14:00:00Z
beta-bio_2026-Q1,name:beta_bio,2026-02-01T00:00:00Z,2026-03-15T00:00:00Z,passed,0.8,"[201,202]","Wrong stage",alice,2026-04-08T14:30:00Z
```

### Labelling cadence
- **Daily target:** 3-5 episodes labelled per day during Move 0
- **Weekly review:** every Friday, the team reviews the new episodes for
  consistency
- **Disagreements:** if two analysts disagree, the third opinion (or majority
  vote) is the final label; the dissent is captured in `notes`

### Quality bar
- Each episode must have ≥1 evidence signal
- The outcome_label must be defensible — `notes` should explain why
- `confidence` reflects the analyst's certainty in the label, not the engine's
  confidence in the signal
- Episodes labelled `unknown` are kept but excluded from the eval cohort

---

## 6. Outcome label taxonomy

| Label | Meaning | Used for Tier-2? |
|---|---|---|
| `meeting` | Analyst took a meeting (or first call) with this company | Yes (positive) |
| `funded` | Company subsequently raised a round (within or after the episode window) | Yes (positive) |
| `passed` | Analyst saw the signal but explicitly passed | Yes (negative — engine surfaced correctly, analyst chose not to act) |
| `tracking` | Analyst added to watch list but no meeting yet | Partial — counts as "engine surfaced" |
| `dead` | Company shut down or ceased to be relevant | No (excluded from recall calc) |
| `unknown` | Analyst can't make a determination | No |

**Tier-2 recall counts:**
- Numerator: episodes where the engine surfaced ≥1 signal AND outcome_label
  ∈ {meeting, funded, tracking}
- Denominator: episodes with outcome_label ∈ {meeting, funded, tracking, passed}

`passed` is in the denominator because the engine *did* its job correctly
(surfaced for analyst review); the recall metric measures "did we surface,"
not "did the analyst act."

---

## 7. Day-by-day cadence target (Move 0)

| Day | Cumulative episodes | Notes |
|---|---|---|
| 1 (Mon 04-06) | 0 → 3 | Onboard analyst on protocol |
| 2 (Tue 04-07) | 3 → 8 | First batch from existing 15 pushed signals |
| 3 (Wed 04-08) | 8 → 13 | Second batch — labelled FPs from `signal_quality_metrics` |
| 4 (Thu 04-09) | 13 → 18 | First batch of forward-looking labels |
| 5 (Fri 04-10) | 18 → 22 | Friday review |
| 6-7 (weekend) | 22 → 25 | Optional |
| 8 (Mon 04-13) | 25 → 28 | Second forward batch |
| 9 (Tue 04-14) | 28 → 32 | **HIT 30 — minimum target reached** |
| 10 (Wed 04-15) | 32 → 35 | Buffer above target |
| 11 (Thu 04-16) | 35 → 40 | Stretch toward 50 |
| 12 (Fri 04-17) | 40 → 50 | **Stretch target — also Move 0 freeze day** |

**Escalation triggers:**
- Day 5 (Fri 04-10): if cumulative < 15, raise concern at Friday review
- Day 9 (Tue 04-14): if cumulative < 25, escalate — strategy framing at risk
- Day 12 (Fri 04-17): if cumulative < 30, the holdout split is built with
  whatever exists, and the strategy has a reframing conversation before Move 1

---

## 8. Why the cadence is aggressive

The Move 0 window is 12 days. Track B needs to deliver in those 12 days because
the holdout split (`05-holdout-cohort-design.md`) and the Tier-2 baseline
(`06-tier-2-recall-eval.md`) both depend on having ≥30 episodes by the time
Move 1 ships.

If Track B can't sustain 3-5 episodes/day in this window, that itself is a
data point: it means the strategy's assumption that "the analyst can label 30
episodes in 12 days while doing their normal job" is wrong, and the strategy
needs to either:
1. Lower the target (smaller holdout = noisier metric)
2. Defer Move 1 until the cohort exists
3. Reframe the strategy entirely

All three are bad options, but the worst option is shipping Move 1 with no
Tier-2 substrate.

---

## 9. Steady-state cadence (after Move 0)

Once the initial cohort exists, the cadence drops to ~1-2 episodes/day, which
is sustainable indefinitely. New episodes:
- Get appended to `data/shadow/track_b_episodes.csv`
- Get auto-classified into train/holdout by the deterministic split function
  (`05-holdout-cohort-design.md` §3)
- Become eligible for the next move's Tier-2 baseline

**Track B never ends.** As long as the engine is running, the analyst is
producing labels, and the cohort grows. After 6 months, the cohort should be
~150-200 episodes and the Tier-2 metric is much sharper.

---

## 10. What Track B is NOT

- Not a code change (writes only to data/shadow/)
- Not a Move 1+ blocker if delivered partial — degrades gracefully to a
  smaller holdout
- Not a substitute for the existing 64-row golden set — that's Tier-1, this
  is Tier-2 (different gates)
- Not an analyst-only task — the engineer pairs to define episodes precisely
  and to wire the CSV into the eval harness in Move 1

---

## 11. Move 0 deliverables for Track B

- [x] This spec doc exists
- [ ] Episode CSV file created at `data/shadow/track_b_episodes.csv` with
      header (created in Move 0 day 1, alongside this doc)
- [ ] ≥30 episodes labelled by 2026-04-19
- [ ] Friday review notes captured in `data/shadow/track_b_review_notes/`
      (one file per Friday)
- [ ] Hand-off to `05-holdout-cohort-design.md` step on day 12

---

## 12. Open questions

1. **Who is the analyst?** This spec assumes "alice" as a placeholder. Real
   ownership must be assigned before day 1. If no analyst is available, Track B
   stalls and the strategy is in immediate trouble.
2. **What's the labelling cost in analyst-hours per episode?** Estimate: 5-15
   minutes per episode for an analyst familiar with the company; longer for
   unfamiliar. At 10 min/episode × 5/day = ~50 min/day. This is sustainable
   if it's part of the analyst's normal review workflow.
3. **Should historical pushed-but-not-yet-labelled signals be retroactively
   episode-ified?** Yes — this is the fastest way to bootstrap the cohort.
   See §4.1.
4. **What about companies in the Notion CRM that the engine NEVER surfaced?**
   These are the recall-failure episodes — the most valuable holdout data
   because they prove the engine missed something. Add them in a separate
   "missed companies" pass after the initial 30.

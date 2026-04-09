# Hold-Out Cohort Split Design

**Date:** 2026-04-06
**Status:** Spec only — committed during Move 0, used from Move 1 onward
**Resolves:** Risk R7 (replay overfit on existing 612-signal cohort)

---

## 1. Why this exists

Without a held-out cohort, replay scores climb monotonically with every prompt
tweak and the team will believe the engine is improving while real-world recall
is unchanged. This is the standard overfit-on-replay failure mode.

The fix is a deterministic 70/30 split of the labelled cohort that:
- Is committed to git (anyone can reproduce it)
- Uses a fixed seed (the split is the same every time)
- Has an explicit refresh policy (when to add new labels)
- Has an explicit "do not touch" rule for the held-out 30%

---

## 2. Source data

The split operates on **company-episodes**, not on individual signals.

A *company-episode* is a labelled outcome event for a single canonical company
over a time window:

| Field | Description |
|---|---|
| `episode_id` | Stable string, e.g. `acme-ai_2026-Q1` |
| `canonical_key` | The company's canonical key (per `utils/canonical_keys.py`) |
| `episode_start` | ISO 8601 — when the labelling window opened |
| `episode_end` | ISO 8601 — when the labelled outcome was confirmed |
| `outcome_label` | One of: `meeting`, `funded`, `passed`, `dead`, `tracking` |
| `confidence` | Analyst confidence in the label, [0,1] |
| `evidence_signal_ids` | Array of signal IDs that contributed to the label |
| `notes` | Free-text rationale |
| `labelled_by` | Username |
| `labelled_at` | ISO 8601 |

**Why episodes, not signals:** the engine's job is to find *companies*, not
to classify individual signals. ~~The 9% precision metric (per LOB.txt
evaluation) is on signals; the metric the strategy needs to move is on
companies that turn into meetings/fundings.~~

> **Withdrawn 2026-04-08 (GOV-01):** The "9% precision" claim is selection
> bias per the 2026-04-06 bias audit (`docs/plans/2026-04-06-lob-progress-eval/bias-audit.md`).
> The 211 labeled signals are an opportunistic sample of suspected FPs, not
> a random sample. Actual pipeline precision is unknown. The metric the
> strategy needs to move remains "companies that turn into meetings/fundings"
> — that part of the original claim is preserved.

**Source of episodes:** Track B labelling sprint (per `08-track-b-labelling.md`).
Track B targets 30-50 episodes by 2026-04-19.

---

## 3. The split

### Algorithm
```python
import hashlib

def assign_split(episode_id: str, seed: int = 20260406, holdout_fraction: float = 0.3) -> str:
    """Deterministic, reproducible split.

    Returns 'train' or 'holdout'. Same episode_id always lands in the same bucket.
    """
    h = hashlib.sha256(f"{seed}:{episode_id}".encode()).digest()
    # Use first 8 bytes as a uint64, normalize to [0,1)
    bucket = int.from_bytes(h[:8], "big") / (1 << 64)
    return "holdout" if bucket < holdout_fraction else "train"
```

**Properties:**
- Deterministic: same input → same output forever
- Stable: adding new episodes does NOT reshuffle existing assignments
- Order-independent: the assignment depends only on `episode_id`, not on
  insertion order
- Hash-based: avoids the modulo bias of `hash(id) % 10`
- Tunable: `holdout_fraction` and `seed` can be changed for new experiments,
  but the default is locked

### Default parameters
```
seed = 20260406    # today's date as YYYYMMDD
holdout_fraction = 0.3
```

The seed is locked. Changing the seed creates a new split — the team commits
to the same split for the duration of the strategy.

### Output file
```
data/shadow/holdout_split/episodes_v1.csv
```

Columns:
```
episode_id, canonical_key, split, outcome_label, labelled_at
```

The CSV is committed to git. Move 1+ tooling reads this file at every replay
run to determine which episodes are train and which are holdout.

---

## 4. The "do not touch" rule for holdout

**Rule:** the holdout 30% is touched ONLY at promotion-gate review.

| Activity | Allowed on train? | Allowed on holdout? |
|---|---|---|
| Read for prompt tuning | Yes | NO |
| Read for replay validation | Yes | NO (except at gate) |
| Read for analyst inbox display | Yes | Yes (analyst can label/relabel) |
| Read for Tier-2 recall eval | NO | Yes (the gate is the ONLY use) |
| Inspect during postmortem | Yes | Yes (after-the-fact only) |

**Promotion-gate review means:** the lead engineer runs the Tier-2 eval against
the holdout immediately before promoting Move 1, Move 2, or Move 3 work to
production. The result is captured in an artifact file. The engineer does NOT
iterate on the holdout — one shot, then the result is final.

**Consequence of breaking the rule:** the holdout is invalidated. A new split
must be created with a new seed, and Track B must produce additional episodes
to refill the holdout 30%. This is expensive, which is the point.

---

## 5. Refresh policy

When can the split file be updated?

| Trigger | Action |
|---|---|
| New episodes from Track B | Append to the CSV; deterministic algorithm assigns split; existing rows unchanged |
| Re-labelling of an existing episode | Update `outcome_label` in place; `episode_id` is stable, split is preserved |
| Seed change | NEVER (without escalation); creates a new split file `episodes_v2.csv` and triggers a labelling sprint to refill |
| Episode deletion | NEVER; mark as `outcome_label = withdrawn` and exclude from eval |

**Append-only with in-place label corrections.** The append-only property is
what makes the split reproducible across time.

---

## 6. Versioning

The first split file is `episodes_v1.csv`. If the seed or `holdout_fraction`
ever changes, a new file is created (`episodes_v2.csv`) and old eval results
are tagged with the old version.

**Cross-version compatibility:** results from `v1` and `v2` are NOT comparable.
The team must explicitly re-run baselines after a version bump.

---

## 7. The "is the labelling sprint big enough?" question

Track B targets 30-50 episodes. With holdout_fraction = 0.3:
- 30 episodes → 9 holdout, 21 train
- 50 episodes → 15 holdout, 35 train

**Is 9-15 holdout episodes statistically meaningful?** Honestly, barely. With
9 episodes, the recall metric has wide error bars and the team should NOT make
production promotion decisions on a single Tier-2 number.

**Mitigation:** use the holdout as a *trend* metric, not an absolute. If the
Tier-2 recall on Move 1 baseline is 4/9 = 44%, and on Move 2 is 6/9 = 67%, the
*direction* is meaningful even if the absolute number is noisy.

**Long-term:** Track B should keep producing episodes after the Move 0 window
ends. By Move 3, the cohort should be 100+ with a meaningful holdout.

---

## 8. Why not use the existing 64-row golden set?

The existing `tests/fixtures/thesis_llm_golden_set.jsonl` is **a classifier
benchmark, not a recall benchmark.** It tests:
- Does the LLM still classify the same prompt the same way?
- Does the v1.6.0 employer-distribution guard still catch the same 4 cases?

It does NOT test:
- Did we surface a company that turned into a meeting?
- Did we miss a company we should have caught?

The two cohorts measure different things and serve different gates:
- Tier-1 (golden set, 64 rows): gates LLM prompt changes
- Tier-2 (holdout episodes): gates substrate changes

See `06-tier-2-recall-eval.md` for the gating contract.

---

## 9. The chicken-and-egg problem

Move 1 needs the holdout to measure baseline recall. The holdout needs Track B
to produce episodes. Track B needs ≥2 weeks to produce 30-50 episodes.

**Resolution:** Track B starts on day 1 of Move 0 (today, 2026-04-06). By
2026-04-19 (end of Move 0), Track B should have ≥30 episodes. The holdout
split is created on day 11-12 of Move 0 from whatever Track B has produced.

**Risk:** Track B stalls. Per `00-strategy.md` §2, this is the canary metric
for the whole framing — if labelling stalls below 20 episodes, the strategy
needs a reframing conversation, not just a deadline extension.

---

## 10. File format examples

### Input from Track B
```
episode_id,canonical_key,episode_start,episode_end,outcome_label,confidence,evidence_signal_ids,notes,labelled_by,labelled_at
acme-ai_2026-Q1,domain:acme.ai,2026-01-15T00:00:00Z,2026-03-20T00:00:00Z,meeting,0.9,"[123,124,156]","Met with founder; promising",alice,2026-04-08T14:00:00Z
beta-bio_2026-Q1,name:beta_bio,2026-02-01T00:00:00Z,2026-03-15T00:00:00Z,passed,0.8,"[201,202]","Wrong stage",alice,2026-04-08T14:30:00Z
```

### Output split file
```
episode_id,canonical_key,split,outcome_label,labelled_at
acme-ai_2026-Q1,domain:acme.ai,train,meeting,2026-04-08T14:00:00Z
beta-bio_2026-Q1,name:beta_bio,holdout,passed,2026-04-08T14:30:00Z
```

---

## 11. Move 0 deliverable checklist

- [ ] This spec doc exists (DONE)
- [ ] Hash function pseudocode is implementable (DONE)
- [ ] Seed and holdout_fraction defaults locked (DONE)
- [ ] File format defined (DONE)
- [ ] Track B has produced ≥30 episodes by 2026-04-19 (TRACK B's job)
- [ ] Initial split file created at
      `data/shadow/holdout_split/episodes_v1.csv` (Move 0 day 12)

The last item is the only thing that requires Track B to have delivered. If
Track B is at 20 episodes on day 12, the split file is created with 20 rows
(6 holdout, 14 train) and a note in the file header documenting the
under-target state.

---

## 12. Move 1+ tooling spec (NOT Move 0)

- [ ] `scripts/red-team-hybrid/build_holdout_split.py` — runs the algorithm
      against the Track B output, writes the CSV (Move 0 final day)
- [ ] `scripts/red-team-hybrid/run_tier2_eval.py` — reads the split, runs the
      classifier against `holdout` rows, reports recall (Move 1)
- [ ] CI integration that fails the build if the holdout file is read outside
      the gate context (Move 2)

---

## 13. Open questions

1. **Should the split be stratified by `outcome_label`?** Recommended: yes.
   Without stratification, a random split could put all `funded` episodes in
   the train set. Stratified sampling preserves the outcome distribution in
   both buckets. **Decision: implement stratified sampling in
   `build_holdout_split.py`.** The hash function above is the unstratified
   version; the script wraps it with per-label stratification.
2. **Can the analyst label "I don't know" episodes?** Yes — they get
   `outcome_label = unknown` and are excluded from the eval. They can be
   re-labelled later.
3. **What about episodes labelled by multiple analysts with disagreement?**
   Track B's labelling protocol resolves this — final label is whatever the
   reconciliation produces. The split file only sees the final label.

# Company-Episode Replay Harness — Spec (Phase 0, task p0.4)

**Status:** Design — implementation deferred to Phase 1
**Date:** 2026-04-06
**Plan reference:** Red-team v2 Phase 0 task `p0.4` (revised per user U7)

## Purpose

The replay harness is the **methodological replacement** for the strategy
document's "backtest CT logs against the 19 known TPs" — a cohort that does
not exist in the codebase. This spec defines the harness so that any future
shadow gate or new collector can be evaluated against a real labelled
cohort without re-inventing the wheel.

## Inputs

1. **Labelled episode cohort** (`data/shadow/labelled-episodes.csv`)
   from task `p0.3`. Must satisfy the Phase 0 acceptance criteria.

2. **Production signals.db** (read-only, immutable URI mode via
   `analytics.shadow_sidecar`).

3. **Shadow signals.db** (`data/shadow/discovery.db`) — optional. When
   present, the harness can replay episodes through the shadow ladder
   evaluator and compare against the production decision.

4. **Decision function under test** — a callable
   `(episode, signals_so_far) -> Decision` where `Decision` is one of:
   `surface_to_notion`, `surface_to_shadow_queue`, `watchlist_only`,
   `reject`. The production decision is computed by `_meets_promotion_criteria`
   + `RoutingDecision`. The shadow decision is computed by
   `analytics.evidence_ontology.evaluate_shadow_tier` + a candidate
   gate function.

## Output

Per episode, the harness writes one row to
`artifacts/red-team-execution/phase{N}/replay-results-{run_id}.csv`:

```csv
episode_id,company_id,label,production_decision,production_decision_at,
shadow_decision,shadow_decision_at,lead_time_delta_days,
production_correct,shadow_correct,first_evidence_class
```

| Column | Description |
|---|---|
| `episode_id` | from labelled cohort |
| `label` | TP / FP / UNSURE |
| `production_decision` | what the production system actually did |
| `production_decision_at` | when production reached that decision |
| `shadow_decision` | what the shadow gate would have done |
| `shadow_decision_at` | when the shadow gate would have reached that decision |
| `lead_time_delta_days` | `production_decision_at - shadow_decision_at` (positive = shadow earlier) |
| `production_correct` | `production_decision aligns with label` |
| `shadow_correct` | `shadow_decision aligns with label` |
| `first_evidence_class` | first non-ambient class observed in the episode |

## Replay procedure (per episode)

```
1. Load episode signals in evidence_sequence order
2. Initialize empty signal buffer for "signals_so_far"
3. For each (signal, ts) in evidence_sequence:
     a. Append to signals_so_far
     b. Re-evaluate production decision with signals_so_far
     c. Re-evaluate shadow decision with signals_so_far
     d. If either decision crosses a tier threshold, record (decision, ts)
4. Output the first decision crossing for each track + the deltas
```

This is a **temporal replay** — the harness replays signals at their
actual timestamps, so cold-start gates that need a baseline window are
handled correctly.

## Metrics computed across the cohort

| Metric | Definition |
|---|---|
| **Production precision** | TPs surfaced by production / total surfaced by production |
| **Production recall** | TPs surfaced by production / total TPs in cohort |
| **Shadow precision** | TPs surfaced by shadow / total surfaced by shadow |
| **Shadow recall** | TPs surfaced by shadow / total TPs in cohort |
| **Lead time uplift (median)** | median over TPs of `production_decision_at - shadow_decision_at` (positive = shadow earlier) |
| **Disagreement rate** | fraction of episodes where production and shadow reach different decisions |
| **Disagreement matrix** | 4x4 matrix of (production decision, shadow decision) counts |

All metrics are reported with **bootstrapped 95% CIs** (1000 resamples) per
the Phase 0 statistical-caveat acceptance criterion. Point estimates without
intervals are not acceptable for n=30-50.

## Comparison hypotheses

The harness is designed to test specific hypotheses, not just produce
metrics:

| Hypothesis | Pass criterion |
|---|---|
| H1: shadow ladder agrees with production on TPs | shadow_recall ≥ production_recall - 0.10 |
| H2: shadow ladder doesn't add false positives | shadow_precision ≥ production_precision - 0.05 |
| H3: shadow ladder finds TPs earlier | median lead_time_uplift > 0 days |
| H4: shadow ladder reduces noise | disagreement rate < 30% AND most disagreements are shadow=watchlist where production=surface |

If H1+H2 pass, the shadow ladder is non-inferior. If H3 also passes, it is
strictly better. H4 is a sanity gate against parallel-state-machine drift.

## Implementation notes for Phase 1

- **Pure-Python.** No new schema. The harness runs over the labelled CSV
  and the read-only signals.db connection.
- **Decision functions are callables.** The harness does not import the
  production code path directly; instead, the test driver provides
  callables that wrap `_meets_promotion_criteria` and the shadow ladder.
  This keeps the harness independent of refactors to those functions.
- **Run ID and reproducibility.** Each run gets a UUID, the harness records
  the git SHA of `analytics/`, the cohort hash, and the random seed.
- **Bootstrap.** Use `numpy.random.choice(replace=True)` over the cohort
  index. 1000 resamples by default.
- **Output is markdown + CSV + JSON.** Markdown for human review; CSV for
  per-episode rows; JSON for downstream tooling.

## What this harness does NOT do

1. **Does not modify production state.** Read-only signals.db access only.
2. **Does not push to Notion.** Decisions are recorded; nothing is queued.
3. **Does not retrain the thesis classifier.** The thesis filter is a
   black-box veto in the harness — its decisions are taken as ground truth
   for "would have been routed to Notion" determinations.
4. **Does not assume any new schema columns.** Pure derived computation.

## Acceptance criteria for Phase 1 entry

- This spec is approved at the Phase 0 review breakpoint (`p0.BP`)
- The labelled cohort from `p0.3` is in place
- The Phase 0 KPI baseline (`p0.10`) is in place — the replay harness
  uses the same KPI definitions

## Out of scope (Phase 2+)

- Replaying live decisions vs reconstructed decisions: Phase 1 only
  reconstructs from `signals.detected_at`. A later phase could compare
  against the actual `signal_processing` row timestamps to detect cases
  where the live pipeline made a different decision than its reconstructed
  twin.
- Replaying through entity resolution: Phase G v50 entity resolution is
  an additional dimension the harness should eventually exercise (per U9).
  For Phase 1 v1, the harness operates on `canonical_key` and ignores
  cross-key entity merges.

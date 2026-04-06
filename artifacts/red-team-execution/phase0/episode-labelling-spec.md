# Company-Episode Labelling Sprint — Spec (Phase 0, task p0.3)

**Status:** Design — labelling sprint not yet started
**Date:** 2026-04-06
**Plan reference:** Red-team v2 Phase 0 task `p0.3` (revised per user U4)
**Output target:** `data/shadow/labelled-episodes.csv` (~30–50 rows)

## Why episode-level, not signal-row level

The original red-team v1 plan proposed labelling 50–100 *signal rows*. The
user's pushback (U4) was correct: labelling at the signal-row level inflates
apparent precision via duplicate signals from chatty collectors and biases
the cohort toward sources that emit many signals per company.

A **company-episode** is the right unit of analysis for the strategy
document's vision. One episode = one (company, time-window) bundle of
evidence that the analyst either acted on, didn't act on, or didn't see.

## Episode definition

An **episode** is the union of all signals associated with a single
canonical_key (or `company_id` if Phase G entity resolution merged multiple
keys) within a 90-day window. Episodes do not overlap in time per company.

Formally::

    episode = (
        company_id,
        canonical_key,
        signal_ids: List[int],          # all signals in the window
        first_seen_at,                  # min(detected_at) across signal_ids
        last_seen_at,                   # max(detected_at) across signal_ids
        evidence_sequence,              # ordered list of (source_api, detected_at)
    )

Episodes are derived, not stored — they live only in the labelled CSV and
the Phase 1 replay harness.

## CSV schema

```csv
episode_id,company_id,canonical_key,first_seen_at,last_seen_at,
evidence_sequence_json,first_analyst_surfaced_at,first_public_mention_at,
meeting_booked,proxy_outcome,label,labeller,labelled_at,notes
```

| Column | Type | Description |
|---|---|---|
| `episode_id` | text | `ep_<n>` — assigned by the labelling tool |
| `company_id` | text | from `company_files.company_id` if available, else canonical_key |
| `canonical_key` | text | the canonical key |
| `first_seen_at` | iso 8601 | earliest signal in episode |
| `last_seen_at` | iso 8601 | latest signal in episode |
| `evidence_sequence_json` | json | `[{"source_api": "...", "detected_at": "...", "evidence_class": "..."}, ...]` ordered by `detected_at` |
| `first_analyst_surfaced_at` | iso 8601 | when the company first appeared in Notion (from `suppression_cache.cached_at`) — empty if never |
| `first_public_mention_at` | iso 8601 | earliest signal where `evidence_class == AMBIENT_CORROBORATION` — empty if none |
| `meeting_booked` | bool | true if Notion status is `Initial Meeting / Call` or beyond |
| `proxy_outcome` | text | `tracked` / `dilligence` / `committed` / `funded` / `passed` / `lost` / `unknown` (from Notion status if visible) |
| `label` | text | `TP` / `FP` / `UNSURE` — the labeller's verdict on whether this episode represented a real fit |
| `labeller` | text | initials of the human labeller |
| `labelled_at` | iso 8601 | when the label was applied |
| `notes` | text | free-form, especially for `UNSURE` reasoning |

## Labelling targets

| Cohort | Target count | Source |
|---|---|---|
| Promoted companies | 15 | `company_files.status='promoted'` |
| Tracking / Dilligence (analyst-surfaced, no decision yet) | 10 | `suppression_cache.status IN ('Tracking','Dilligence')` |
| Passed / Lost | 5 | `suppression_cache.status IN ('Passed','Lost')` |
| Held by thesis | 10 | `signals` joined to `thesis_classifications.decision='HOLD'` |
| Random sample of unsurfaced episodes | 10 | random episodes that never reached Notion |
| **Total** | **~50** | |

The exact mix is intended to over-represent positive outcomes (which are
rare) and to provide a comparison cohort of episodes that the system did
NOT surface.

## Labelling tool extension

Add `--unit company-episode` to `python -m ops.cli quality label` so the CLI
knows to operate over episode IDs rather than signal IDs.

**Phase 0 implementation note:** the actual CLI extension is a Phase 0
Day-2+ task; this spec only documents the contract. The labelling sprint
itself is human work and runs in parallel with the Phase 0 engineering work.

## Statistical caveats (acknowledged upfront)

- **n = 30–50 episodes** is small. Bootstrapped 95% CIs on derived metrics
  are expected to be ±15-25 percentage points. The replay harness will
  report bootstrap intervals alongside point estimates.
- **Selection bias:** the cohort over-samples promoted/tracked/passed
  episodes because we have ground truth on those. Held / never-surfaced
  cohorts must be sampled randomly within their strata.
- **Labeller agreement:** at least 5 episodes should be double-labelled
  to compute Cohen's kappa. Below kappa = 0.6, the cohort cannot be
  considered ground-truth quality.

## Acceptance criteria for Phase 0 → Phase 1

1. ≥30 episodes labelled
2. Cohen's kappa ≥0.6 on the double-labelled subset
3. CSV passes schema validation against this spec
4. At least 5 TPs and 5 FPs present (so the replay harness has enough
   signal to compute a non-degenerate AUC)

If any of these fail, Phase 1 is blocked until the labelling sprint
extends. (Phase 1 cannot run on a cohort that the replay harness cannot
score.)

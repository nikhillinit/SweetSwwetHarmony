# Track B Solo Local Review

Date: 2026-04-09
Status: local-only operating runbook

## Purpose

Run Track B as a solo local review workflow without pushing anything to Notion and without sending noise to anyone else.

## Core Rule

Keep everything local unless you explicitly decide otherwise.

Do not:
- push review output to Notion
- send status digests to bosses
- create external alerts by default

## Files To Use

Seed queue:
- `data/shadow/track_b_episodes.csv`

Real episode ledger:
- `data/shadow/track_b_company_episodes.csv`

Daily notes:
- `data/shadow/track_b_review_notes/YYYY-MM-DD.md`

## What Each File Means

### `track_b_episodes.csv`

This is a per-signal candidate seed.
Use it as input only.

### `track_b_company_episodes.csv`

This is the real episode ledger.
Use it as the actual Track B source of truth for your local review work.

## Episode CSV Conventions

Header:

```csv
episode_id,canonical_key,episode_start,episode_end,outcome_label,confidence,evidence_signal_ids,notes,labelled_by,labelled_at
```

### How to denote relevant vs irrelevant

Do **not** create a separate `relevant` column unless you later decide you need it.
Use `outcome_label` consistently instead.

Recommended mapping:
- `tracking` = relevant
- `meeting` = relevant
- `funded` = relevant
- `passed` = irrelevant
- `dead` = irrelevant
- `unknown` = not enough evidence yet

That gives you a clean local rule:
- Relevant set = `tracking | meeting | funded`
- Irrelevant set = `passed | dead`
- Unclear set = `unknown`

## Best Practices

1. One row per company-episode, not per signal.
2. Always include real `evidence_signal_ids`.
3. Use `notes` to capture why you made the judgment.
4. Keep `confidence` simple:
   - `0.9` = strong conviction
   - `0.7` = decent confidence
   - `0.5` = uncertain but leaning
5. If you are unsure, use `unknown` instead of forcing a decision.
6. Label supporting signals separately with:

```powershell
python -m ops.cli quality label <signal_id> <TP|FP|UNSURE|ADJ> --reason "Track B episode evidence"
```

7. Review the highest-signal candidates first:
   - companies with multiple supporting signals
   - companies you already remember from Notion or past review
   - companies with plausible consumer relevance

8. Do not try to review everything in one pass.
   Use a two-step approach:
   - pass 1: create episode rows quickly
   - pass 2: improve notes and signal labeling

## If You Have More Review Capacity

After you finish the first 10-15 real episodes:

1. Go back through the seed queue and prioritize candidates with:
   - multiple related signals
   - clearer canonical keys
   - stronger consumer fit

2. Add missing notes for the most important episodes.

3. Upgrade `unknown` rows into final judgments only when you actually have enough evidence.

4. Add a short summary section to the daily note:
   - best relevant companies seen today
   - most clearly irrelevant companies seen today
   - recurring noise patterns

5. Start building a lightweight local pattern list:
   - what keeps showing up as irrelevant?
   - what kinds of companies tend to be relevant?

## Daily Routine

1. Run freshness check
2. Review seed queue
3. Add real episode rows
4. Label supporting signals
5. Update daily note

## Weekly / Friday Routine

On Friday:
- count real episodes
- write whether Track B is on-plan or off-plan
- note the biggest blocker
- decide whether next week should be more review-heavy or more maintenance-heavy

---

This runbook is for solo local review only.

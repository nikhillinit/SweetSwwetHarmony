# Direction-A-Derived Hybrid — Plan Index

**Branch:** `prep/red-team-hybrid-prep`
**Window:** 2026-04-06 → 2026-04-19 (Move 0 prep) → Move 1 starts 2026-04-19
**Strategy:** see [`00-strategy.md`](00-strategy.md)

> This is **NOT Direction A** as defined in `cross_pollination_analysis.md`. It
> is a Direction-A-derived hybrid with three deliberate divergences (Evidence
> Lake promoted, Postgres deferred, soft-schema substituted). Naming it
> correctly is what gets the right discipline applied. See `00-strategy.md` §1.

## Documents

| # | File | Purpose |
|---|---|---|
| 00 | [`00-strategy.md`](00-strategy.md) | Top-level strategy charter (renamed) — goal framing, tracks, move sequence, decision gates |
| 01 | [`01-move-0-charter.md`](01-move-0-charter.md) | Move 0 deliverables, time-box, forbidden paths |
| 02 | [`02-bounded-context-map.md`](02-bounded-context-map.md) | `signals.db` bounded-context audit (Move 3 prerequisite) |
| 03 | [`03-dead-letter-contract.md`](03-dead-letter-contract.md) | Soft-schema quarantine spec — storage, triage, escalation, parser update loop |
| 04 | [`04-llm-failure-mode.md`](04-llm-failure-mode.md) | LLM structured-outputs failure mode = soft-fail with retain-raw |
| 05 | [`05-holdout-cohort-design.md`](05-holdout-cohort-design.md) | Deterministic 70/30 hold-out split for Track B episodes |
| 06 | [`06-tier-2-recall-eval.md`](06-tier-2-recall-eval.md) | Recall benchmark gating contract (Tier-1 = classifier, Tier-2 = recall) |
| 07 | [`07-collector-audit.md`](07-collector-audit.md) | Read-only systematic catalog of collector correctness risks |
| 08 | [`08-track-b-labelling.md`](08-track-b-labelling.md) | Parallel track — company-episode labelling sprint (canary metric) |
| 09 | [`09-track-e-watchlist.md`](09-track-e-watchlist.md) | Parallel track — founder watchlist population for shadow GH negative-space |
| 10 | [`10-risk-register.md`](10-risk-register.md) | 14 risks from red-team §4 + 4 new risks identified during Move 0 |

## Tracks

| Track | Status | Owner | Where it lives |
|---|---|---|---|
| **A** — Substrate hardening | Move 0 prep on prep branch | Eng | This dir + Move 1+ code |
| **B** — Labelling sprint | Active from day 1 | Eng + Analyst | `data/shadow/track_b_episodes.csv` |
| **C** — Hold-out split | Built on day 12 from Track B output | Eng | `data/shadow/holdout_split/episodes_v1.csv` |
| **D** — CT-log + DNS shadow collectors | Deferred until 2026-04-19 | Eng | Move 1+ |
| **E** — Founder watchlist | Active from day 1 | Eng + Analyst | `scripts/data/founder_watchlist_manual_seed.csv` + `data/shadow/founder_watchlist.csv` |

## Showstoppers

| # | Risk | Mitigation |
|---|---|---|
| R1 | Step 4B regret window contamination | `scripts/red-team-hybrid/check_protected_paths.sh` runs before commits |
| R2 | Engine-efficacy mechanism gap | Tracks B/D/E run in parallel + Tier-2 recall eval |

## Verification

Run before every commit on this branch:
```bash
bash scripts/red-team-hybrid/check_protected_paths.sh
```

The script greps `git diff --name-only main...HEAD` against:
```
collectors/
workflows/
governance/
monitoring/
connectors/
storage/migrations/
```

and exits non-zero on any match.

## Status (2026-04-06)

Move 0 prep: in progress. All 11 design docs landed. Scripts directory and
verifier in place. Track B episode CSV scaffolded with header. Track E populator
verified to run (output: empty seed expected — analyst dependency).

Next: commit on `prep/red-team-hybrid-prep`, do NOT push to main, do NOT merge
until 2026-04-19.

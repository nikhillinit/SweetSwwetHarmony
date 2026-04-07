# Move 0 Charter — Prep During the Step 4B Regret Window

**Window:** 2026-04-06 → 2026-04-19 (12 days, hard time-box)
**Branch:** `prep/red-team-hybrid-prep`
**Constraint:** Step 4B regret check is open until 2026-04-18. Touching protected
paths corrupts the evidence the regret check needs.

---

## 1. Time-box guardrail (the 80% rule)

> **Move 0 ships the artifacts that exist on day 12 or doesn't ship them at all.**
> No deliverable extensions. The bounded-context map can be 80% complete and
> still be useful; it cannot be 100% complete and 4 weeks late.

This guardrail is non-negotiable. It exists because the user's hybrid is *more*
granular than the source memo's Direction A (4 moves with sub-tasks vs. 1 sprint
with 3 things), making it *more* susceptible to the over-planning failure mode
the memo warned about.

If a deliverable is at risk of slipping past 2026-04-19:
1. Cut scope to the 80% version that exists
2. Document what's missing in the deliverable's "Known gaps" section
3. Ship anyway

The deliverables are *prioritization tools*, not project plans.

---

## 2. Forbidden paths

```
collectors/
workflows/
governance/
monitoring/
connectors/
storage/migrations/
```

**Allowed:**
- `docs/plans/2026-04-06-red-team-hybrid/` — all design docs
- `data/shadow/**` — shadow store and fixtures
- `artifacts/red-team-hybrid/` — output of audits and dry-run scripts
- `scripts/red-team-hybrid/` — verification and audit scripts
- `tests/red-team-hybrid/` — tests for new tooling
- `scripts/data/founder_watchlist_manual_seed.csv` — Track E seed (data, not code)

**Conditionally allowed (read-only):**
- `analytics/shadow_sidecar.py` — may be imported by audit scripts but NOT modified
- `storage/blob_store.py`, `storage/source_asset_store.py` — may be imported by audit scripts but NOT modified

**Enforcement:** `scripts/red-team-hybrid/check_protected_paths.sh` runs before
every commit. The script greps `git diff --name-only main` against the forbidden
list and exits non-zero on any match.

---

## 3. Deliverables

| # | Deliverable | Location | Owner | Done = |
|---|---|---|---|---|
| D1 | Strategy charter (renamed) | `00-strategy.md` | Lead | Doc exists, framing honest |
| D2 | Move 0 charter (this doc) | `01-move-0-charter.md` | Lead | Doc exists, time-box explicit |
| D3 | Bounded-context map | `02-bounded-context-map.md` | Eng | All ~120 tables grouped, FK lines listed, invariants noted (80% rule applies) |
| D4 | Dead-letter contract | `03-dead-letter-contract.md` | Eng | Storage path, cadence, escalation, feedback loop spec'd |
| D5 | LLM failure mode decision | `04-llm-failure-mode.md` | Eng | "Soft-fail with retain-raw" picked + 3 alternatives documented |
| D6 | Hold-out cohort split design | `05-holdout-cohort-design.md` | Eng | Seed + format + refresh policy spec'd |
| D7 | Tier-2 recall eval design | `06-tier-2-recall-eval.md` | Eng + Analyst | Metric, gating contract, source data spec'd |
| D8 | Systematic collector audit catalog | `07-collector-audit.md` | Eng | Read-only catalog of risks across 16 collectors |
| D9 | Track B labelling sprint plan | `08-track-b-labelling.md` | Eng + Analyst | 30-50 episode target with owner, cadence, source |
| D10 | Track E founder watchlist | `09-track-e-watchlist.md` + populated CSV | Eng | ≥50 rows in `data/shadow/founder_watchlist.csv` |
| D11 | Risk register snapshot | `10-risk-register.md` | Lead | 14 risks from red-team §4 with current status |
| D12 | Protected-paths verifier | `scripts/red-team-hybrid/check_protected_paths.sh` | Eng | Script exists, runs locally, passes on this branch |

---

## 4. Daily focus (suggested, not prescribed)

| Day | Focus |
|---|---|
| 1 (Mon 04-06) | D1 + D2 + D12; create branch; start Track B (D9) and Track E (D10) |
| 2 (Tue 04-07) | D3 (bounded-context map first pass) |
| 3 (Wed 04-08) | D4 + D5 (dead-letter contract + LLM failure mode) |
| 4 (Thu 04-09) | D6 + D7 (cohort split + Tier-2 design) |
| 5 (Fri 04-10) | D8 (collector audit, day 1) |
| 6-7 (weekend) | Track B labelling sprint (analyst + eng pairing) |
| 8 (Mon 04-13) | D8 (collector audit, day 2 — finalize) |
| 9 (Tue 04-14) | Track E populate from Notion if not done; refine D3-D7 |
| 10 (Wed 04-15) | Tier-2 baseline measurement design dry-run |
| 11 (Thu 04-16) | D11 risk register update; verify deliverables |
| 12 (Fri 04-17) | Final pass; verify D12 protected-paths gate; freeze |

Day 12 is **Friday 2026-04-17** to leave a 1-day buffer before the regret check.

Saturday 2026-04-18 = regret check day (don't touch).
Monday 2026-04-19 = first day Move 1 can start.

---

## 5. Success criteria for Move 0

Move 0 succeeds if **all** of the following are true on 2026-04-19:

1. **Zero changes to forbidden paths.** Verified by `check_protected_paths.sh`.
2. **Step 4B regret check passes** with no contamination from Move 0 work.
3. **Track B canary metric:** ≥30 labelled company-episodes exist in
   `signal_quality_metrics` (or wherever the labelling sprint stores them).
4. **Track E delivered:** `data/shadow/founder_watchlist.csv` contains ≥50 rows
   (sourced from manual seed, Notion CRM, or both).
5. **Deliverables D1-D12 exist** in this directory at the 80%-or-better level.
6. **Move 1 launch decision** is unblocked: lead engineer can start production
   wiring on Monday 2026-04-19 without re-debating any of the design specs.

If criterion #3 is at risk (labelling stalls), escalate before 2026-04-15.
Track B is the canary for the whole framing — if it fails, the strategy needs
a reframing conversation, not just a deadline extension.

---

## 6. What Move 0 explicitly does NOT do

- Does not write to collectors, workflows, governance, monitoring, connectors,
  or storage/migrations
- Does not create a v52+ migration
- Does not wire artifact capture into the production collector path
- Does not promote the golden-set gate to blocking
- Does not start the Postgres dual-write
- Does not decide Move 4 in advance
- Does not ship any code that runs in the production pipeline
- Does not change CI behavior on PRs that touch collectors

All of those are Move 1+ work and stay behind the 2026-04-19 fence.

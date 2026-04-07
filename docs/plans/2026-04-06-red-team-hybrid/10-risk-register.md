# Risk Register Snapshot — Direction-A-Derived Hybrid

**Date:** 2026-04-06
**Status:** Living document — update at the end of each Move
**Source:** Red-team review §4 (14 risks) + addendum §10

---

## Scoring

- **Severity:** 1 (low) → 5 (catastrophic)
- **Likelihood:** 1 (rare) → 5 (very likely)
- **Score:** Severity × Likelihood

| Class | Score |
|---|---|
| Showstopper | 20-25 |
| High Priority | 12-19 |
| Monitor | 6-11 |
| Accepted | ≤5 |

---

## Active risks

| # | Risk | Sev | Lik | Score | Class | Mitigation | Status |
|---|---|---|---|---|---|---|---|
| **R1** | Step 4B regret window contamination by collector edits before 2026-04-19 | 5 | 5 | **25** | Showstopper | Move 0 ships only docs + designs + read-only audits to `prep/red-team-hybrid-prep`. `check_protected_paths.sh` enforces. | **MITIGATING** — prep branch active; protected-paths verifier in place |
| **R2** | "Engine efficacy" goal mechanistically unconnected to the moves | 5 | 4 | **20** | Showstopper | Reframe Track A as substrate hardening; run Tracks B/D/E in parallel; Tier-2 recall eval gates each Move. | **MITIGATING** — strategy doc reframed; Tracks B/E starting today; Tier-2 designed |
| R3 | Bounded-context split is hidden Move 3 prerequisite | 4 | 4 | 16 | High Priority | Bounded-context map drafted in Move 0; Move 3 budget includes prerequisite | **MITIGATING** — see `02-bounded-context-map.md` |
| R13 | 64-row golden set is wrong substrate for recall gating | 4 | 4 | 16 | High Priority | Two-tier eval: Tier-1 = golden set (classifier); Tier-2 = holdout episodes (recall) | **MITIGATING** — see `06-tier-2-recall-eval.md` |
| R4 | Golden-set canary blocks all merges due to flake before baseline cycle | 3 | 4 | 12 | High Priority | Run Tier-1 gate as advisory for first 30 days; promote to blocking only after 0 false-fails | **DEFERRED** — Move 2 |
| R5 | Quarantine has no triage cadence; dead-letter ages into graveyard | 3 | 4 | 12 | High Priority | Dead-letter contract specifies weekly triage, escalation rules, parser update feedback loop | **MITIGATING** — see `03-dead-letter-contract.md` |
| R6 | Zero analyst-visible deliverables for 4-6 months erodes trust | 3 | 4 | 12 | High Priority | Add "Why was this held?" tooltip to inbox view as Move 1 deliverable | **DEFERRED** — Move 1 (specced in `04-llm-failure-mode.md` §8) |
| R7 | Replay overfits on existing 612-signal cohort | 4 | 3 | 12 | High Priority | Hold out 30% of Track B labels before Move 1 starts; never iterate on holdout | **MITIGATING** — see `05-holdout-cohort-design.md` |
| R14 | Quarantine state machine introduces v52+ migration blocked by R1 until 2026-04-19 | 3 | 4 | 12 | High Priority | Dead-letter is file-based (`data/shadow/dead_letter/<date>/<source>.jsonl`) during regret window; promote to table in Move 3 | **MITIGATING** — see `03-dead-letter-contract.md` §2 |
| R12 | Orchestration debt accumulates in `workflows/pipeline.py` while Move 4 deferred | 3 | 3 | 9 | Monitor | Acknowledged; Move 4 re-evaluates whether queueing is the next bottleneck | ACCEPTED for Move 1-3 |
| R8 | `_processed_identities` is one of N hidden correctness bugs | 3 | 3 | 9 | Monitor | Systematic collector audit (read-only) in Move 0; top 3-5 fixes in Move 2 | **MITIGATING** — see `07-collector-audit.md` |
| R10 | LLM structured-outputs failure mode unspecified | 3 | 3 | 9 | Monitor | Decision: soft-fail with retain-raw; integrates with dead-letter contract | **MITIGATED** — see `04-llm-failure-mode.md` |
| R11 | Founder watchlist stranded after PR #133 | 2 | 4 | 8 | Monitor | Track E populates seed in Move 0; Move 1+ wires negative-space collector | **MITIGATING** — see `09-track-e-watchlist.md` |
| R9 | Disk growth from artifact retention exceeds Fermi estimate at 10x volume | 2 | 3 | 6 | Monitor | Retention policy (90 days raw, 180 days archive) specified; cron lands in Move 1 BEFORE soft validation goes live | **DEFERRED** — Move 1 (per `03-dead-letter-contract.md` §10) |

---

## New risks identified during Move 0

| # | Risk | Sev | Lik | Score | Class | Mitigation | Status |
|---|---|---|---|---|---|---|---|
| **R15** | Track B labelling cadence stalls (analyst can't sustain 3-5 episodes/day for 12 days) | 4 | 3 | 12 | High Priority | Day-by-day cadence target with escalation triggers on days 5, 9, 12; if stalled at <30 by day 12, strategy reframing conversation | **MONITORING** — see `08-track-b-labelling.md` §7 |
| **R16** | Track E analyst can't or won't populate seed with real founder data | 3 | 3 | 9 | Monitor | Eng MUST NOT generate placeholder names; if analyst can't deliver, Track D is descoped | **MONITORING** — see `09-track-e-watchlist.md` §3 |
| **R17** | Move 0 deliverables slip past 2026-04-19 due to over-planning | 3 | 3 | 9 | Monitor | 80% rule + day 12 hard freeze; ship at 80% completion or document gaps and ship anyway | **MITIGATING** — see `01-move-0-charter.md` §1 |
| **R18** | The bounded-context map's 80% gap (TBC tables) hides a key invariant | 2 | 3 | 6 | Monitor | Day 11 final classification pass; trigger inventory captured in artifacts/ | **DEFERRED** — Move 0 day 11 |

---

## Risks NOT mitigated by Move 0 (deferred to later moves)

| # | Risk | Why deferred | Action gate |
|---|---|---|---|
| R4 | Golden-set canary flakiness | Needs Move 2 to ship the gate before flakiness can manifest | First 30 days of Move 2 advisory mode |
| R6 | Analyst tooltip | Touches `api/routers/triage.py`; not protected, but coupled to Move 1 dead-letter writer | Move 1 day 1 |
| R9 | Disk growth eviction cron | Cron landing requires the dead-letter writer to exist | Move 1 day 5 (BEFORE soft validation enables) |
| R10 | LLM failure mode implementation | Touches `workflows/pipeline.py` (protected) | Move 1 day 5+ |
| R12 | Orchestration debt | Acknowledged trade-off; revisit at Move 4 | Move 4 decision gate |

---

## Showstopper status check

| # | Showstopper | Mitigation in place? | Mitigation verified? |
|---|---|---|---|
| R1 | Step 4B regret window contamination | YES — `check_protected_paths.sh` runs before commits; all Move 0 work on prep branch with paths in `docs/`, `data/shadow/`, `artifacts/`, `scripts/data/` | TBD — verify after Move 0 commit |
| R2 | Engine-efficacy mechanism gap | YES — strategy reframed as substrate hardening; Tracks B/D/E run in parallel; Tier-2 eval gates each Move | TBD — verify Track B canary metric on day 12 |

**Both showstoppers are mitigated in design.** Verification of execution
happens at the end of Move 0.

---

## Update protocol

This file is updated:
1. At the end of every Move (1, 2, 3, 4) with status changes
2. When a new risk is identified (add to "New risks" section)
3. When a risk is closed (move to a "Closed risks" section, not deleted)
4. When a Showstopper status changes (immediate update + escalation)

The file is NEVER updated to lower a Severity or Likelihood without an
explicit "evidence and reasoning" section linked from the row.

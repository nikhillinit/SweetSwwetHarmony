# Active Sprint — Move 0 (2026-04-06 → 2026-04-19)

## Tracks at a glance

Definitions live in `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md` §2. Quick map:

| Track | Owner | Lever | Status |
|---|---|---|---|
| **A** | Eng | Substrate hardening (artifact capture, soft schema, quarantine, Postgres) | Move 0 (shadow paths only) |
| **B** | Eng + Analyst | Company-episode labelling sprint (target 30-50 episodes) — **canary metric** | Starts now |
| **C** | Eng | Holdout cohort split from Track B labels + 612 existing signals | After Track B ≥30 |
| **D** | Eng | CT-log + DNS shadow collectors | After 2026-04-19 (gated on Step 4B regret window) |
| **E** | Eng | Founder watchlist from Notion CRM | Starts now (analyst seed required) |

## Move semantics

- **Move 0** (current) — design + analyst tracks + D1–D12 deliverables. Time-box 2026-04-06 → 2026-04-19. Non-negotiable per §1 "80% rule".
- **Move 1** (next) — artifact capture on top 3 collectors + analyst tooltip + Tier-1/2 baseline. Starts 2026-04-19.

## Key terms

- **Strategic canary** = Track B labelling cadence. If labelling stalls, escalate by 2026-04-15. The framing fails if Track B fails, regardless of Track A success.
- **Tactical unblock** = Move 0 completion clearing the Step 4B regret-check constraint, unlocking Move 1 production wiring.
- **Protected paths** during Move 0 (enforced by `scripts/red-team-hybrid/check_protected_paths.sh`):
  `collectors/`, `workflows/`, `governance/`, `monitoring/`, `connectors/`, `storage/migrations/`

## Maintenance

This file is regenerated at the start of each new move by re-running the `claude-md-improver` skill. Between regenerations, press `#` during a Claude session to incrementally capture learnings into CLAUDE.md.

## Why this file exists

The branch name (`prep/red-team-hybrid-prep`) is the most reliable signal of which plan is active. Earlier sessions failed because no agent connected the branch to `docs/plans/2026-04-06-red-team-hybrid/`. The session-start rule now does that resolution automatically; this file holds the deeper context that doesn't fit in CLAUDE.md's always-loaded surface.

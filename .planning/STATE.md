# Harmonic Discovery Engine — STATE

> Project memory index. Updated at phase transitions and milestone boundaries.

## Project Reference

See: `.planning/PROJECT.md` (last updated 2026-04-08 with synthesis findings)

**Core value**: Qualified consumer leads in the analyst's Notion inbox that they would not have found otherwise.

**Current focus**: **Phase 1 (Move 0 Prep + Liveness Prep)** — resolving R19 frozen pipeline and preparing the engagement plumbing infrastructure for Phase 2 ship.

## Active phase

**Phase 1: Move 0 Prep + Liveness Prep**
- Started: 2026-04-08
- Target end: 2026-04-19 (Move 0 protected paths freeze ends)
- See: `.planning/ROADMAP.md` Phase 1 details
- Requirements in flight: LIV-01 to LIV-03, LIV-11, GOV-01 to GOV-04, SUB-01, REC-01 to REC-04

## Critical context to inherit on session start

1. **R19 (frozen pipeline)** is the highest-priority item. Pipeline frozen since 2026-03-01; discovered 2026-04-08; load-bearing for 2026-04-18 Step 4B regret check
2. **R20 (analyst abandonment)** is the new Showstopper. The synthesis identified analyst engagement as the binding constraint, not substrate quality
3. **Move 0.5 Liveness Restoration** is a hard prerequisite to Move 1 (inserted by 2026-04-08 synthesis)
4. **OutcomeJoiner already exists as `.claude/skills/`** — `quality-backfill-notion-status-events` + `quality-backfill-outcomes` + related quality-* skills. They need wiring into a daily cron, not building from scratch
5. **The "9% precision" claim is withdrawn** as selection bias per the 2026-04-06 bias audit. Actual pipeline precision is unknown until a randomly-sampled labeling sprint runs (Track B's reframed purpose)
6. **Substrate work and engagement work are COMPLEMENTARY**, not substitutive — framing correction load-bearing for not repeating the inverse of the bias-audit's "shifting the burden" anti-pattern
7. **Move 3 (Postgres dual-write) is deferred indefinitely** — only revisited if Move 4 decision validates substrate-vs-engagement framing

## Reference docs

| Doc | Purpose |
|-----|---------|
| `.planning/PROJECT.md` | Project context, success metrics, key decisions |
| `.planning/REQUIREMENTS.md` | v1 requirements with REQ-IDs |
| `.planning/ROADMAP.md` | Phase structure (5 phases) |
| `.planning/research/` | Research outputs (STACK / FEATURES / ARCHITECTURE / PITFALLS) |
| `.planning/codebase/` | Codebase analysis (ARCHITECTURE / STACK / STRUCTURE / CONCERNS / etc.) |
| `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md` | Canonical strategy doc (revised by synthesis) |
| `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md` | Active risk register (R1–R19; R20 to be added by Phase 1) |
| `docs/plans/2026-04-06-lob-progress-eval/README.md` | Bias audit retrospective — methodological reference |

## Synthesis artifacts (2026-04-08 jarvis evaluation)

The 5-agent jarvis evaluation + cross-pollination + six-thinking-hats + LOB.txt comparison produced findings that are now folded into PROJECT.md / REQUIREMENTS.md / ROADMAP.md. Key artifacts not yet in plan files:

- **Cross-pollination Sources** (3 patterns from external domains): wildfire lookout / canine handler / bee waggle dance — all incorporated into LIV-08, LIV-09, REC-07
- **Streaming services Top 3** (Pandora-lite / Letterboxd / Twitch) — Pandora-lite incorporated as LIV-13, Letterboxd as REC-05+REC-06, Twitch deferred to V2-01
- **Six-hats verdict**: ship Pandora-lite first (cheapest), pretotype Letterboxd second (validate substrate), defer Twitch (bias amplification risk)
- **LOB.txt grafts**: outreach narrative gen (UX-01), traffic-light timing (UX-02), founder reputation scoring (REC-04 extension), feature-flag rigor (cross-cutting constraint)

These do not need re-derivation — they're load-bearing for the REQ-IDs above.

---

*Last updated: 2026-04-08 after GSD-new-project workflow resume with synthesis improvements.*

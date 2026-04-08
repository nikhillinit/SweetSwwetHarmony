# Harmonic Discovery Engine — ROADMAP

> **Source**: derived from `.planning/PROJECT.md` + `.planning/REQUIREMENTS.md` + the 2026-04-08 5-agent jarvis evaluation + cross-pollination + six-thinking-hats + LOB.txt comparison. Reflects all discovered improvements.
>
> **Granularity**: Coarse — 5 phases, 1-3 plans each.
>
> **Strategy anchor**: Direction-A-derived hybrid (`docs/plans/2026-04-06-red-team-hybrid/00-strategy.md`) **revised** to insert Move 0.5 (Liveness Restoration) as a hard prerequisite to Move 1, defer Move 3 (Postgres) indefinitely, and replace Track B as primary canary with engagement metrics.

---

## Phase Summary

**5 phases** | **42 v1 requirements mapped** | All v1 requirements covered ✓

| # | Phase | Goal | REQ-IDs | Success Criteria |
|---|-------|------|---------|------------------|
| 1 | **Move 0 Prep + Liveness Prep** | Document the revised strategy, restart collection, prepare engagement plumbing infrastructure | LIV-01 to LIV-03, LIV-11, GOV-01 to GOV-04, SUB-01, REC-01 to REC-04 | 7 |
| 2 | **Move 0.5 Liveness Restoration** | Ship the analyst-visible engagement plumbing — daily digest, calibration positives, OutcomeJoiner wiring | LIV-04 to LIV-10, LIV-12 to LIV-14, REC-05 | 6 |
| 3 | **Move 1 Substrate + UX** | Top 3 collectors writing artifacts, Tier-1/2 baselines, UX surface (provenance, outreach, confessions) | SUB-02 to SUB-04, REC-06, REC-07, UX-01 to UX-04 | 5 |
| 4 | **Move 2 Advisory + Audit** | Golden-set advisory mode 30+ days, first Tier-2 recall vs baseline, collector-correctness fixes | SUB-05 to SUB-07 | 4 |
| 5 | **Move 4 Co-Canary Decision Gate** | Decision gate fires on engagement + Tier-2 recall co-canaries; pivot or continue | SUB-09 | 3 |

**Note**: Move 3 (Postgres dual-write) is intentionally absent — deferred to v2.

---

## Phase Details

### Phase 1: Move 0 Prep + Liveness Prep

**Goal**: Resolve R19 (frozen pipeline), document the revised strategy in canonical plan files, prepare the infrastructure scaffolding for Move 0.5 ship without touching protected paths.

**Requirements delivered**: LIV-01 (R19 restart), LIV-02 (freshness watchdog script), LIV-03 (regret check precondition), LIV-11 (R20 in risk register), GOV-01 (withdraw 9% precision claim), GOV-02 (R20 governance), GOV-03 (freshness precondition for all gates), GOV-04 (framing correction in strategy doc), SUB-01 (Move 0 charter deliverables), REC-01 (Track B 30 episodes), REC-02 (Track C cohort split), REC-03 (Track D design only), REC-04 (Track E watchlist + reputation scoring stub)

**Dependencies**: Move 0 protected paths active until 2026-04-19. All Phase 1 work happens in `scripts/`, `docs/`, `data/shadow/` (allowed paths only).

**Success criteria** (5):
1. **Pipeline freshness restored**: `python scripts/red-team-hybrid/freshness_watchdog.py` exits 0 by 2026-04-13 (R19 fix verified)
2. **R20 in risk register**: `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md` contains R20 with severity 25 and Move 0.5 as mitigation
3. **Track B 30+ episodes**: `data/shadow/track_b_episodes.csv` contains ≥30 labeled company-episodes by 2026-04-19
4. **Track E 50+ founders**: `data/shadow/founder_watchlist.csv` contains ≥50 founder records by 2026-04-19
5. **Strategy doc framing correction landed**: `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md` §2 references Move 0.5 as a complement to (not substitute for) Track A
6. **Step 4B regret check has freshness precondition**: documented in `docs/plans/2026-04-06-red-team-hybrid/02-bounded-context-map.md` or equivalent
7. **9% precision claim withdrawn**: no active doc quotes the number without the bias-audit caveat

### Phase 2: Move 0.5 Liveness Restoration

**Goal**: Ship the analyst-visible engagement plumbing. This is the load-bearing phase — every recommendation from the synthesis converges here. Phase 2 starts after the protected-paths freeze ends (2026-04-19) but the design + script work happens in Phase 1.

**Requirements delivered**: LIV-04 to LIV-10 (cron wiring + digest + engagement metric), LIV-12 (permanent Hold-Review batch), LIV-13 (Pandora-lite digest column), LIV-14 (inbox explanation panel), REC-05 (Letterboxd pretotype)

**Dependencies**: Phase 1 complete (specifically R19 fixed). Some sub-tasks need `monitoring/` or `connectors/` paths which unfreeze 2026-04-19.

**Success criteria** (6):
1. **Daily digest emits**: 9am Slack/email digest delivers 7 consecutive days, including at least one "0 today" structurally distinct empty-channel message
2. **OutcomeJoiner cron live**: `quality-backfill-notion-status-events` + `quality-backfill-outcomes` run on daily schedule; `signal_quality_metrics` table receives new rows daily
3. **Calibration positives delivered**: at least 1 known-good historical win injected into the digest per week, labeled as calibration
4. **Engagement metric published**: `analyst_inbox_engagement_7d` query result visible in the daily digest
5. **Permanent Hold-Review batch live**: weekly cadence routes top-50 held signals to a Tracking sub-view; analyst dismissals do NOT auto-suppress
6. **Pandora-lite digest column shipped**: 5-feature breakdown visible in the digest's per-signal annotation
7. **Inbox explanation panel live**: digest contains the structured "X ingested today, Y held, Z forwarded" report on every emission

### Phase 3: Move 1 Substrate + UX

**Goal**: Substrate hardening (top 3 collectors writing to BlobStore artifacts; Tier-1/2 baselines) running in parallel with the analyst-visible UX surface (Why-Now provenance, outreach narrative gen, traffic-light timing, engine confessions).

**Requirements delivered**: SUB-02 (top 3 collectors → artifacts), SUB-03 (Tier-1 baseline), SUB-04 (Tier-2 baseline), REC-06 (Letterboxd centroid scorer, conditional), REC-07 (outcome-modulated dispatch), UX-01 (outreach narrative gen), UX-02 (outreach traffic light), UX-03 (Why Now provenance block), UX-04 (engine confessions weekly)

**Dependencies**: Phase 2 complete; protected paths unfrozen; engagement metric > 3 days/week (Phase 2 success criterion 4 holds).

**Success criteria** (5):
1. **Top 3 collectors writing to artifact store**: `data/shadow/artifacts/` contains BlobStore-addressed entries from at least 3 collectors over 7 consecutive days
2. **Tier-1 golden set baseline measured**: no regression vs pre-Move-1 baseline
3. **Tier-2 hold-out cohort produces a baseline recall number**: first quantitative measurement (with Wilson CI noted)
4. **UX surface live in digest**: Why-Now provenance + outreach narrative gen + traffic light all visible in the digest as annotations on each pushed signal
5. **Engine confessions weekly report shipped**: at least 1 weekly report published showing signals the engine missed that the analyst found via warm intros
6. **Letterboxd pretotype validated**: REC-05 lists were maintained for 2 weeks → REC-06 centroid scorer ships in Move 1 week 2 (or REC-05 not maintained → REC-06 killed and the decision documented)

### Phase 4: Move 2 Advisory + Audit

**Goal**: Golden-set advisory mode runs ≥30 days; first Tier-2 recall number vs Move 1 baseline; collector audit fixes shipped.

**Requirements delivered**: SUB-05 (golden-set advisory 30+ days), SUB-06 (Tier-2 recall vs baseline), SUB-07 (collector-correctness fixes)

**Dependencies**: Phase 3 complete with success criteria met. Specifically, Phase 3 success criterion 3 (Tier-2 baseline) must exist before Phase 4 can measure delta.

**Success criteria** (4):
1. **Golden-set gate has 30+ days of advisory mode data**: false-fail rate documented
2. **First Tier-2 recall delta vs Move 1 baseline measured**: with statistical caveat (Wilson CI per `06-tier-2-recall-eval.md` §9)
3. **Collector audit findings remediated**: top 3-5 issues from `07-collector-audit.md` fixed
4. **Engagement co-canary still green**: `analyst_inbox_engagement_7d` ≥ 3 days/week throughout Phase 4 (else trigger Phase 5 early)

### Phase 5: Move 4 Co-Canary Decision Gate

**Goal**: Make the binding-constraint pivot decision based on BOTH Tier-2 recall AND analyst engagement. Either continue substrate work, pivot to recall (Tracks B/D/E), or freeze and re-evaluate.

**Requirements delivered**: SUB-09 (co-canary decision gate)

**Dependencies**: Phase 4 complete OR engagement canary breach trigger (engagement < 3 days/week for 2 consecutive weeks at any point).

**Success criteria** (3):
1. **Decision gate fires on co-canaries**: gate evaluates BOTH Tier-2 recall (climbing/flat/declining) AND `analyst_inbox_engagement_7d` (≥3/<3 days/week)
2. **Decision documented in `docs/plans/2026-04-06-red-team-hybrid/12-move-4-decision.md`** (new file): one of {continue substrate, pivot to recall tracks, freeze and reframe}
3. **If pivot is chosen**: the next milestone's REQUIREMENTS.md is generated and the v2-deferred items (Postgres, Twitch trust transfer, full Pandora, etc.) are re-evaluated against the new framing

---

## Cross-cutting requirements not in any phase

These are referenced in REQUIREMENTS.md but are continuous, not phase-bound:

- **GOV-01 to GOV-04**: governance corrections (bias audit + R20 + freshness preconditions + framing correction). Land in Phase 1 but apply for the lifetime of the project.
- **API key provisioning** (PROJECT.md Active gap): opportunistic, not phase-blocking.
- **Track B labelling cadence**: continues across all phases as the secondary canary + random-sample substrate.

---

## Deferred (v2) — explicitly not in v1

- **V2-01**: Twitch trust transfer with anti-amplification (deferred until labeled cohort + demographic guardrail exist)
- **V2-02**: Competitive intelligence mode (Move 2+ stretch)
- **V2-03**: Postgres dual-write (originally Move 3 — only revisited if Move 4 decision validates substrate framing)
- **V2-04**: Pandora at full scale (50+ features) — only after Pandora-lite proves analyst trust impact
- **V2-05**: Founder reputation scoring at full scale
- **V2-06**: Bird-banding "rare-event session log" pattern

---

## Coverage check

Total v1 REQs: 42
- LIVENESS: 14 (LIV-01 to LIV-14)
- SUBSTRATE: 9 (SUB-01 to SUB-09; SUB-08 deferred)
- RECALL: 7 (REC-01 to REC-07)
- UX: 4 (UX-01 to UX-04)
- GOVERNANCE: 4 (GOV-01 to GOV-04)

Mapped to phases: 41 (SUB-08 intentionally unmapped — Postgres deferred)
Coverage: 41 / 41 effective REQs = 100% ✓

---

*Generated: 2026-04-08, after the 5-agent jarvis evaluation + cross-pollination + six-hats passes + LOB.txt comparison. Reflects all discovered improvements through the resume of the GSD workflow.*

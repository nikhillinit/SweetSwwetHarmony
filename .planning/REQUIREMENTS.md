# Harmonic Discovery Engine — REQUIREMENTS

> **Source of truth**: this document is derived from `.planning/PROJECT.md` (the project context) plus the 2026-04-08 5-agent jarvis evaluation, the cross-pollination + six-thinking-hats passes, and the LOB.txt comparison. It folds in all discovered improvements.
>
> **Granularity**: Coarse (per `.planning/config.json` → 3-5 phases, 1-3 plans each).
>
> **Mode**: Brownfield retroactive — the existing `docs/plans/2026-04-06-red-team-hybrid/` strategy doc is the upstream substrate; this REQUIREMENTS.md is the GSD-formatted derivative with synthesis revisions.

---

## v1 Requirements (current milestone scope)

### Category: LIVENESS — Move 0.5 Liveness Restoration (NEW, hard prerequisite to Move 1)

The binding constraint is analyst engagement, not substrate quality. R19 (38-day frozen pipeline going undetected) was empirical proof. Move 0.5 ships the engagement plumbing the original strategy deferred to Move 1's "tooltip" mitigation.

- [ ] **LIV-01**: Resolve R19 — restart collection by EOD 2026-04-08; verify `max(detected_at)` advances within 24h
- [ ] **LIV-02**: Write `scripts/red-team-hybrid/freshness_watchdog.py` — per-collector freshness check, exit non-zero if any operational collector is >36h stale (lives in allowed Move 0 path)
- [ ] **LIV-03**: Add freshness precondition to 2026-04-18 Step 4B regret check — pass requires freshness < 5 days for ≥3 collectors over the prior 7 days. If R19 not fixed by 2026-04-15, postpone the check rather than running on stale data
- [ ] **LIV-04**: Wire `quality-backfill-notion-status-events` skill into daily cron (existing skill, no new code)
- [ ] **LIV-05**: Wire `quality-backfill-outcomes` skill into daily cron (existing skill — implements OutcomeJoiner)
- [ ] **LIV-06**: Wire `tuning-proposal-writer` + `tuning-proposal-apply` + `fp-pattern-finder-signals` into weekly cron (existing skills — implements the unconditional action that survived the 2026-04-06 bias audit)
- [ ] **LIV-07**: Build daily digest distribution channel — `distribution/digest.py` (allowed Move 0 path); 9am Slack/email; emits structured report regardless of content
- [ ] **LIV-08**: Daily digest implements **empty-channel discipline** (wildfire lookout pattern) — "0 today" version is structurally distinct from "freshness STALE" version. Format includes: ingested count, held count, rejected count, freshness status, last meaningful surface, next expected wave
- [ ] **LIV-09**: Daily digest implements **calibration positives** (canine handler pattern) — weekly injection of known-good historical wins as labeled calibration items
- [ ] **LIV-10**: Build `analyst_inbox_engagement_7d` query and publish daily — days in prior week analyst opened a non-empty Notion inbox view
- [ ] **LIV-11**: Add R20 (Analyst abandonment) to risk register at `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md` as Showstopper score 25, with Move 0.5 above as the mitigation
- [ ] **LIV-12**: Permanent weekly Hold-Review batch — top 50 highest-confidence held signals routed to a `Tracking` review sub-view; rejection by analyst does NOT auto-suppress (replaces sketched Track F4 which was rejected by premortem due to suppression-cache contamination)
- [ ] **LIV-13**: Pandora-lite digest column — extract 5 features from existing dismissal-reason labels via `thesis-disagreement-report` skill; render per-signal in the digest as explanatory annotation (does NOT change routing)
- [ ] **LIV-14**: Promote R6 mitigation from "tooltip" to **inbox explanation panel** — "47 ingested today, 38 held by classifier (link to reasons), 9 forwarded — here's why" — ships in the digest, not as a separate UI element

### Category: SUBSTRATE — Track A (continues in parallel with LIVENESS)

Substrate hardening as originally scoped, with the framing correction that it is **complementary, not substitutive** to engagement work. Both run in parallel.

- [ ] **SUB-01**: Move 0 prep deliverables ship per `01-move-0-charter.md` by 2026-04-19 (bounded-context map, dead-letter contract, hold-out cohort design, etc.)
- [ ] **SUB-02**: Move 1 — top 3 collectors writing to `data/shadow/artifacts/` via existing BlobStore + SourceAssetStore (60% of the work already exists per `02-bounded-context-map.md`)
- [ ] **SUB-03**: Move 1 — Tier-1 baseline measurement on existing golden set (no regression)
- [ ] **SUB-04**: Move 1 — Tier-2 baseline measurement on hold-out cohort (first quantitative recall number)
- [ ] **SUB-05**: Move 2 — golden-set advisory mode runs ≥30 days; false-fail rate documented
- [ ] **SUB-06**: Move 2 — first Tier-2 recall number vs Move 1 baseline (the first honest answer to "is this strategy working?")
- [ ] **SUB-07**: Move 2 — collector-correctness fixes shipped for top 3-5 audit findings from `07-collector-audit.md`
- [ ] **SUB-08**: Move 3 — DEFERRED. Postgres dual-write only revisited if Move 4 decision validates substrate-vs-engagement framing. The DB hardening incident `04a5e6e` is already addressed by watermark guards + DBToolLock.
- [ ] **SUB-09**: Move 4 — co-canary decision gate fires on BOTH Tier-2 recall AND `analyst_inbox_engagement_7d`. If engagement < 3 days/week for 2 consecutive weeks at any point in Moves 1-3, freeze substrate work and re-evaluate

### Category: RECALL — Parallel recall tracks

- [ ] **REC-01**: Track B (labelling) — 30+ company-episodes labelled by end of Move 0 (2026-04-19). **Reframed**: Track B is now the SECONDARY canary (engagement is primary), and its purpose is to build the **random-sampled** cohort that fixes the 2026-04-06 bias audit's selection-bias finding
- [ ] **REC-02**: Track C (hold-out cohort split) — deterministic seed, file-based, committed during Move 0
- [ ] **REC-03**: Track D (CT-log + DNS shadow collectors) — design only during Move 0; implementation starts after 2026-04-19 protected-paths freeze ends
- [ ] **REC-04**: Track E (founder watchlist) — 50+ founders in `data/shadow/founder_watchlist.csv` by end of Move 0; **founder reputation scoring** (LOB.txt graft) extends Track E from flat list to scored list
- [ ] **REC-05**: Letterboxd pretotype — create 4 manual lists in Notion (Funded, Diligence, Regret, Anti-thesis) during Move 1 day 1; analyst maintains them for 2 weeks; if maintained → build centroid scorer in Move 1 week 2; if not → kill the mechanism
- [ ] **REC-06**: Letterboxd centroid scorer (CONDITIONAL on REC-05) — built in Pandora-lite feature space (Reframe 4 from six-hats); ships as digest column annotation, not routing change
- [ ] **REC-07**: Outcome-modulated dispatch (bee waggle dance pattern) — `collector_health_score` modulates per-collector dispatch weights based on recent yield from `quality_metrics_daily`. Builds on LIV-05 OutcomeJoiner wiring.

### Category: UX — Analyst-visible surface (LOB.txt grafts + cross-pollination)

These ship as digest annotations / new fields, not as routing changes. Routing logic remains the existing v1.6.0 classifier.

- [ ] **UX-01**: Outreach narrative generation (LOB.txt graft) — for each pushed signal, auto-generate 3 discovery story templates (product-led, network-led, talent-led)
- [ ] **UX-02**: Outreach timing optimization / traffic light (LOB.txt graft) — too early / good / optimal / too late based on signal age + dependency additions
- [ ] **UX-03**: Why-Now provenance block (cross-pollination Source 3) — every signal pushed to Notion gets a structured `Why Now` block: signal type, historical hit rate from this collector, days since last comparable surface, recent yield trend
- [ ] **UX-04**: Engine confessions weekly report — surface signals the engine missed that the analyst found via warm intros (creates the trust differentiator no competitor publishes)

### Category: GOVERNANCE — Bias correction + risk model fix

- [ ] **GOV-01**: Withdraw the "9% pipeline precision" claim from any active doc that quotes it; replace with "actual pipeline precision unknown until random-sampled cohort exists"
- [ ] **GOV-02**: Add R20 (Analyst abandonment) to the active risk register (already in LIV-11 above; tracked separately here for governance traceability)
- [ ] **GOV-03**: Add freshness precondition to all governance gates (not just Step 4B regret check) — every regret check / canary / drift alert requires `max(detected_at)` < 5 days as a precondition
- [ ] **GOV-04**: Document the framing correction in `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md` — substrate work and engagement work are complementary, not substitutive

---

## v2 Requirements (deferred but tracked)

These were explicitly considered in the synthesis and deferred to a later milestone, not rejected.

- [ ] **V2-01**: Twitch trust-transfer mechanism with anti-amplification (Reframe 3 from six-hats) — gated on a labeled cohort existing AND a demographic guardrail metric. Behind feature flag with hard kill criterion
- [ ] **V2-02**: Competitive intelligence mode (LOB.txt graft) — reverse-engineer what successful funds' portfolio companies looked like 12 months pre-funding
- [ ] **V2-03**: Postgres dual-write (originally Move 3) — only if Move 4 decision validates substrate-vs-engagement framing
- [ ] **V2-04**: Pandora at full scale (50+ features) — only after Pandora-lite (LIV-13) proves analyst trust-impact
- [ ] **V2-05**: Founder reputation scoring at full scale (aggregate past projects, roles, activity) — Move 2+ extension of REC-04
- [ ] **V2-06**: Bird-banding "rare-event session log" pattern (cross-pollination follow-up) — every rare positive gets a written explanation that becomes a training example for the next iteration

---

## Out of Scope

### From the investment thesis (drives what signals get qualified)
- B2B / Enterprise SaaS (including tools sold to consumer industries) — *not consumer*
- Developer tools — *not consumer*
- Crypto / Web3 — *thesis exclusion*
- Cleantech / climate — *thesis exclusion*
- Services / agencies — *thesis exclusion*
- Series B+ — *too late stage*
- Hardware-only — *thesis exclusion (consumer hardware borderline)*

### Engine-side (per /gsd-new-project Q4 answer + synthesis review)
- **Outbound to founders**: Discovery Engine never auto-emails founders. Feeds Notion CRM only; humans run outbound. UX-01 (outreach narrative gen) generates suggestions for human review, not automated send
- **Investment decisions**: Engine never auto-routes a deal to "Funded" or makes commit recommendations. Confidence scores inform humans; humans decide
- **Multi-tenancy / multi-analyst**: single-analyst is the design center. No team features until that changes
- **LinkedIn scraping** (per LOB.txt risk analysis): even though declared in past plans, explicitly out of scope due to ToS exposure
- **Black-box ML scoring** (per LOB.txt requirement): every score component must be explainable in 1 sentence; no neural-network scorers without per-feature breakdowns

### Permanently rejected (synthesis-killed)
- **Track F4 as originally sketched** (one-shot promote 50 held signals with auto-suppress on dismissal) — premortem rejected due to suppression-cache contamination. Replaced by LIV-12 (permanent Hold-Review batch with no auto-suppression)
- **Track F5 as originally sketched** (loosen Stage 1 negative keywords for HN) — CEO 10x review rejected due to risk of returning the HN B2B firehose. The v1.6.0 thesis classifier is the only differentiator currently shipped; degrading it for inbox volume is wrong
- **"Lower thresholds during dry spells" as a primary lever** — would corrupt suppression cache and teach the analyst that the threshold IS the engine. Replaced by calibration positives (LIV-09)

---

## Traceability

REQ-IDs map to phases in `.planning/ROADMAP.md`. Each phase carries the REQ-IDs it delivers; coverage is 100% by construction.

| Phase | REQ-IDs delivered |
|-------|-------------------|
| Phase 1 (Move 0 Prep + Liveness Restoration prep) | LIV-01 to LIV-03, LIV-11, GOV-01 to GOV-04, SUB-01, REC-01 to REC-04 |
| Phase 2 (Move 0.5 Liveness Restoration ship) | LIV-04 to LIV-10, LIV-12 to LIV-14, REC-05 |
| Phase 3 (Move 1 Substrate + UX) | SUB-02 to SUB-04, REC-06, REC-07, UX-01 to UX-04 |
| Phase 4 (Move 2 Advisory + Audit) | SUB-05 to SUB-07 |
| Phase 5 (Move 4 Co-Canary Decision Gate) | SUB-09 |

**Note**: SUB-08 (Postgres) is intentionally not in any v1 phase — deferred to v2.

---

## Requirement quality criteria

Each REQ above is:
- **Specific and testable**: every REQ has a verifiable acceptance condition (file exists, query returns expected shape, cron runs on schedule, etc.)
- **User-centric where applicable**: LIV/UX requirements are framed from the analyst's perspective; SUB/REC/GOV requirements are framed from the operator's perspective
- **Atomic**: one capability per REQ
- **Independent where possible**: cross-REQ dependencies noted explicitly (e.g., REC-06 depends on REC-05 + LIV-13)

---

*Generated: 2026-04-08, after the 5-agent jarvis evaluation + cross-pollination + six-hats passes + LOB.txt comparison. Reflects all discovered improvements through the resume of the GSD workflow.*

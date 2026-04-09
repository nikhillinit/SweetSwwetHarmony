# Harmonic Discovery Engine - Roadmap

> Strategy anchor: `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md`, revised so Move 0.5 remains the hard prerequisite for activation and the existing Move 1 branch is refined into a **Cross-Channel Signal Surface** with a human-review endpoint.

## Phase Summary

| # | Phase | Goal | REQ-IDs | Success Criteria |
|---|-------|------|---------|------------------|
| 1 | **Move 0 Prep + Liveness Prep** | Resolve R19, document the refined branch model, and prepare allowed-path inputs without touching protected paths | LIV-01 to LIV-03, LIV-11, GOV-01 to GOV-04, SUB-01, REC-01 to REC-04 | 7 |
| 2 | **Move 0.5 Liveness Restoration** | Ship the analyst-visible engagement plumbing and prove the activation gate is green | LIV-04 to LIV-10, LIV-12 to LIV-14, REC-05 | 7 |
| 3 | **Move 1 Cross-Channel Signal Surface (Refined)** | Land top-3 artifact capture, packet contract, Track D first activation, and packet-visible review surfaces while keeping action surfaces deferred | SUB-02 to SUB-04, REC-06 to REC-07, UX-03 to UX-04, PKT-01 to PKT-05, MOD-01 to MOD-04 | 7 |
| 4 | **Move 2 Advisory + Audit** | Golden-set advisory mode 30+ days, first Tier-2 recall vs baseline, collector-correctness fixes | SUB-05 to SUB-07 | 4 |
| 5 | **Move 4 Co-Canary Decision Gate** | Decision gate fires on engagement + Tier-2 recall co-canaries; pivot or continue | SUB-09 | 3 |

**Note**: Move 3 (Postgres dual-write) remains intentionally absent from v1.

## Phase Details

### Phase 1: Move 0 Prep + Liveness Prep

**Goal**: Resolve R19, codify the refined Move 1 branch in canonical planning files, and prepare allowed-path inputs for later activation without touching protected paths.

**Requirements delivered**: LIV-01, LIV-02, LIV-03, LIV-11, GOV-01, GOV-02, GOV-03, GOV-04, SUB-01, REC-01, REC-02, REC-03 (design prep), REC-04 (watchlist prep)

**Dependencies**: Move 0 protected paths active until 2026-04-19. All work must stay in `scripts/`, `docs/`, and `data/shadow/`.

**Success criteria**:
1. **Pipeline freshness restored**: `python scripts/red-team-hybrid/freshness_watchdog.py` exits 0 by the Move 0 closeout.
2. **R20 in risk register**: the active risk register records analyst abandonment as a showstopper.
3. **Track B cohort ready**: `data/shadow/track_b_episodes.csv` contains at least 30 labeled company-episodes.
4. **Track E readiness assessed**: `data/shadow/founder_watchlist.csv` exists and readiness is explicit - >=50 founders enables first-wave founder activation, otherwise Track E remains auxiliary. Current known state is 44 founders.
5. **Canonical docs rewritten**: `.planning/PROJECT.md`, `.planning/ROADMAP.md`, and `.planning/REQUIREMENTS.md` all reflect the refined Move 1 branch.
6. **Step 4B regret check has freshness precondition**: stale-data execution is explicitly blocked.
7. **9% precision claim withdrawn**: no active doc treats it as a factual precision metric.

### Phase 2: Move 0.5 Liveness Restoration

**Goal**: Ship the analyst-visible engagement plumbing. This remains the load-bearing phase and the activation gate for any protected-path cross-channel rollout.

**Requirements delivered**: LIV-04 to LIV-10, LIV-12, LIV-13, LIV-14, REC-05

**Dependencies**: Phase 1 complete, specifically with R19 fixed and allowed-path prep finished.

**Success criteria**:
1. **Daily digest emits**: 9am digest delivers for 7 consecutive days.
2. **OutcomeJoiner cron live**: outcome/status backfill jobs run on schedule.
3. **Calibration positives delivered**: at least one known-good historical win is injected weekly.
4. **Engagement metric published**: `analyst_inbox_engagement_7d` is visible daily.
5. **Permanent Hold-Review batch live**: top-50 held signals route to the review surface without auto-suppression.
6. **Pandora-lite digest column shipped**: 5-feature explanatory annotation is visible.
7. **Inbox explanation panel live**: digest emits the structured "ingested / held / forwarded" explanation block.

### Phase 3: Move 1 Cross-Channel Signal Surface (Refined)

**Goal**: Refine the original Move 1 into a cross-channel packet surface: top 3 collectors writing artifacts, Track D CT/DNS as the first required new family, packet contract runtime ownership, Why-Now packet rendering, and shadow-only dispatch evaluation. Outreach narrative and timing stay deferred.

**Requirements delivered**: SUB-02, SUB-03, SUB-04, REC-06, REC-07, UX-03, UX-04, PKT-01 to PKT-05, MOD-01 to MOD-04

**Prep inputs consumed from Phase 1**:
- REC-03 Track D design artifact
- REC-04 founder watchlist input, only if readiness threshold clears

**Dependencies**:
- Phase 2 complete
- protected paths unfrozen
- engagement metric > 3 days/week
- liveness gate green: freshness watchdog passing, digest cadence proven, and engagement metric publishing

**Success criteria**:
1. **Top 3 collectors writing to artifact store**: `data/shadow/artifacts/` contains BlobStore-addressed entries from at least 3 collectors over 7 consecutive days.
2. **Tier-1 baseline measured**: no regression vs pre-Move-1 baseline.
3. **Tier-2 baseline measured**: first quantitative hold-out recall number is recorded.
4. **Runtime packet contract live**: `review_items.evidence_bundle` stores the canonical packet shape for active families.
5. **Packet surface live**: UX-03 Why-Now provenance and related packet fields are visible in digest/review surfaces without outreach narrative or timing fields.
6. **Track D first activation complete**: CT/DNS packets emit behind explicit family-mode controls; Track E only activates if the founder watchlist reaches >=50 founders, otherwise it stays auxiliary.
7. **Engine confessions weekly report shipped**: UX-04 lands as an evidence/reporting surface after packet transport exists.

### Phase 4: Move 2 Advisory + Audit

**Goal**: Run golden-set advisory mode for 30+ days, measure first Tier-2 recall vs baseline, and ship the top collector-correctness fixes.

**Requirements delivered**: SUB-05, SUB-06, SUB-07

**Dependencies**: Phase 3 complete and packet/reporting surfaces stable.

**Success criteria**:
1. **Advisory mode runs for 30+ days** with false-fail rate documented.
2. **First recall-vs-baseline decision** is recorded with confidence interval notes.
3. **Top collector audit fixes land** for the most important findings from `07-collector-audit.md`.
4. **Family-level observability remains intact** throughout the advisory window.

### Phase 5: Move 4 Co-Canary Decision Gate

**Goal**: Decide whether to continue, pivot, or freeze based on BOTH Tier-2 recall and analyst engagement.

**Requirements delivered**: SUB-09

**Dependencies**: Phase 4 complete or engagement canary breach.

**Success criteria**:
1. **Decision gate evaluates both co-canaries**: engagement and recall.
2. **Pivot/continue decision is documented** with evidence.
3. **If engagement is red, substrate work freezes** until re-evaluation completes.

## Notes

- `UX-01` and `UX-02` are not part of the refined Move 1 first wave. They remain post-packet candidates for a later lane.
- `REC-07` remains shadow-only in refined Move 1; active dispatch changes happen later.
- Track E is not allowed to block Track D first activation.

---

*Last updated: 2026-04-08 during canonical roadmap rewrite for the refined Move 1 cross-channel packet surface.*

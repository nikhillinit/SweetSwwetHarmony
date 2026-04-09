# Harmonic Discovery Engine - Requirements

> Source of truth: this document is derived from `.planning/PROJECT.md` plus the 2026-04-08 synthesis, and now reflects the refined Move 1 branch as a **Cross-Channel Signal Surface** with a human-review packet endpoint.

## v1 Requirements (current milestone scope)

### Category: LIVENESS - Move 0.5 Liveness Restoration

Move 0.5 remains the hard activation gate for the refined Move 1 branch.

- [ ] **LIV-01**: Resolve R19 - restart collection and verify `max(detected_at)` advances.
- [ ] **LIV-02**: Maintain `scripts/red-team-hybrid/freshness_watchdog.py` as the per-collector freshness gate.
- [ ] **LIV-03**: Keep freshness as a precondition on the 2026-04-18 Step 4B regret check.
- [ ] **LIV-04**: Wire `quality-backfill-notion-status-events` into the daily cadence.
- [ ] **LIV-05**: Wire `quality-backfill-outcomes` into the daily cadence.
- [ ] **LIV-06**: Wire `tuning-proposal-writer`, `tuning-proposal-apply`, and `fp-pattern-finder-signals` into the weekly cadence.
- [ ] **LIV-07**: Daily digest distribution channel emits on schedule.
- [ ] **LIV-08**: Daily digest implements empty-channel discipline.
- [ ] **LIV-09**: Daily digest implements calibration positives.
- [ ] **LIV-10**: `analyst_inbox_engagement_7d` publishes daily.
- [ ] **LIV-11**: R20 remains recorded as a showstopper in the risk register.
- [ ] **LIV-12**: Permanent Hold-Review batch exists.
- [ ] **LIV-13**: Pandora-lite digest annotation ships.
- [ ] **LIV-14**: Inbox explanation panel ships in the digest.

### Category: SUBSTRATE - Refined Move 1 floor

- [ ] **SUB-01**: Move 0 prep deliverables ship by 2026-04-19.
- [ ] **SUB-02**: Top 3 collectors write to `data/shadow/artifacts/`.
- [ ] **SUB-03**: Tier-1 baseline measurement is recorded.
- [ ] **SUB-04**: Tier-2 baseline measurement is recorded.
- [ ] **SUB-05**: Move 2 advisory mode runs for 30+ days.
- [ ] **SUB-06**: First recall-vs-baseline comparison is recorded.
- [ ] **SUB-07**: Collector-correctness fixes land for the top audit findings.
- [ ] **SUB-08**: Postgres dual-write remains deferred.
- [ ] **SUB-09**: Co-canary decision gate evaluates BOTH recall and engagement.

### Category: RECALL - Cross-channel families and inputs

- [ ] **REC-01**: Track B remains the random-sampled labeling cohort builder.
- [ ] **REC-02**: Track C remains the deterministic hold-out cohort split.
- [ ] **REC-03**: Track D CT-log + DNS collectors have design prep in Move 0 and first activation in the refined Move 1 branch.
- [ ] **REC-04**: Track E founder watchlist remains a bounded auxiliary input; >=50 founders enables first-wave founder-driven activation, otherwise Track E stays auxiliary and does not block Track D.
- [ ] **REC-05**: Letterboxd pretotype remains optional evidence/scoring discovery work.
- [ ] **REC-06**: Letterboxd centroid scorer remains packet annotation / scoring work only after the packet contract exists.
- [ ] **REC-07**: Outcome-modulated dispatch remains shadow-only in the refined Move 1 branch; active dispatch changes are deferred.

### Category: UX - Analyst-visible packet surfaces

- [ ] **UX-03**: Why-Now provenance block is the primary packet rendering surface in the refined Move 1 branch.
- [ ] **UX-04**: Engine confessions weekly report lands only after packet transport exists.

### Category: PACKET CONTRACT - Canonical packet architecture

- [ ] **PKT-01**: Canonical packet contract lives at `docs/plans/cross-channel-signal-surface/evidence-packet-contract.md`.
- [ ] **PKT-02**: Runtime packet owner is `review_items.evidence_bundle` after post-freeze schema expansion.
- [ ] **PKT-03**: Each packet includes `schema_version`, stable identity, `source_family`, contributing `signal_ids`, provenance summary, score rationale, `family_mode`, and review endpoint.
- [ ] **PKT-04**: Digest, dashboard, and `Why Now` text are projections of the runtime packet, not independent packet stores.
- [ ] **PKT-05**: Packet endpoint remains human review only; no outreach, CRM auto-create, or other action-surface behavior is permitted in this milestone.

### Category: FAMILY MODES - Explicit rollout controls

- [ ] **MOD-01**: Add explicit family-level mode controls rather than assuming the existing write-feature guards already cover this case.
- [ ] **MOD-02**: Provide config keys for at least `CHANNEL_FAMILY_CT_DNS_MODE` and `CHANNEL_FAMILY_FOUNDER_AUX_MODE`.
- [ ] **MOD-03**: Support `disabled`, `shadow`, and `active` semantics per family.
- [ ] **MOD-04**: Every packet and report records both `source_family` and `family_mode`.

### Category: GOVERNANCE

- [ ] **GOV-01**: Keep the 9% precision claim withdrawn.
- [ ] **GOV-02**: Keep R20 tracked.
- [ ] **GOV-03**: Keep freshness preconditions on governance gates.
- [ ] **GOV-04**: Keep the framing correction that substrate and engagement are complementary.

## v2 Requirements (deferred but tracked)

These remain deferred beyond the refined Move 1 first wave.

- [ ] **V2-01**: Twitch trust-transfer mechanism with guardrails.
- [ ] **V2-02**: Competitive intelligence mode.
- [ ] **V2-03**: Postgres dual-write if later decision gates justify it.
- [ ] **V2-04**: Pandora at full scale.
- [ ] **V2-05**: Founder reputation scoring at full scale.
- [ ] **V2-06**: Bird-banding rare-event session log pattern.
- [ ] **V2-07**: Outreach narrative generation (`UX-01`) after packet transport and review surfaces are proven.
- [ ] **V2-08**: Outreach timing / traffic-light optimization (`UX-02`) after packet transport and review surfaces are proven.

## Out of Scope

### Thesis-side exclusions
- B2B / Enterprise SaaS
- Developer tools
- Crypto / Web3
- Cleantech / climate
- Services / agencies
- Series B+
- Hardware-only

### Current milestone engine-side exclusions
- Outreach timing and narrative generation in the refined Move 1 first wave
- CRM auto-create beyond current routing surfaces
- Automated outbound to founders
- Automated investment decisions
- Multi-tenancy
- LinkedIn scraping
- Black-box ML scoring without per-feature explanation

## Traceability

REQ IDs map to phases in `.planning/ROADMAP.md`.

| Phase | REQ IDs delivered |
|-------|-------------------|
| Phase 1 (Move 0 Prep + Liveness Prep) | LIV-01 to LIV-03, LIV-11, GOV-01 to GOV-04, SUB-01, REC-01 to REC-04 |
| Phase 2 (Move 0.5 Liveness Restoration) | LIV-04 to LIV-10, LIV-12 to LIV-14, REC-05 |
| Phase 3 (Move 1 Cross-Channel Signal Surface) | SUB-02 to SUB-04, REC-06 to REC-07, UX-03 to UX-04, PKT-01 to PKT-05, MOD-01 to MOD-04 |
| Phase 4 (Move 2 Advisory + Audit) | SUB-05 to SUB-07 |
| Phase 5 (Move 4 Co-Canary Decision Gate) | SUB-09 |

**Notes**:
- `REC-03` and `REC-04` deliver prep outputs in Phase 1 and are consumed as activation inputs in Phase 3.
- `UX-01` and `UX-02` are intentionally outside the refined Move 1 first wave and move to v2.

---

*Last updated: 2026-04-08 during canonical requirements rewrite for the refined Move 1 cross-channel packet surface.*

# Harmonic Discovery Engine

## What This Is

A consumer deal-sourcing pipeline for Press On Ventures (Pre-Seed -> Series A, US/UK). It collects multi-source signals (GitHub, SEC, jobs, news, launches, domains, etc.), filters them through a thesis classifier (keyword pre-filter + Gemini LLM), de-duplicates via canonical keys, and routes qualifying prospects into the Press On Notion CRM with confidence-tiered statuses. Built for Press On analysts who would otherwise sift sources manually.

## Core Value

**Qualified consumer leads in the analyst's Notion inbox that they would not have found otherwise.** Every other capability - substrate hardening, governance, monitoring, knowledge graph, recall eval - exists in service of net new true-positive deals reaching a human reviewer.

When tradeoffs hurt, choose the option that increases qualified TPs/week into Notion. Defer everything that does not.

## Success Metrics

The product runs on a **three-tier metric ladder** because the lagging indicator is too slow to steer daily, and the original two-tier formulation missed analyst engagement as a leading indicator.

- **North Star (lagging, 6-month horizon):** Investments closed where Discovery Engine signals are trace-attributed to a Press On deal (Initial Meeting -> Funded). Requires CRM outcome backfill - and the underlying skills (`quality-backfill-notion-status-events` + `quality-backfill-outcomes`) already exist; they need wiring into a daily cron and visibility in the digest.
- **Operational proxy (leading, weekly):** Net new true-positive prospects routed to `Source` or `Tracking` that survive analyst review. Watched in tandem with Tier-2 recall.
- **Engagement canary (early warning, daily):** `analyst_inbox_engagement_7d` = days in the prior week the analyst opened a non-empty Notion inbox view. This is the binding-constraint canary. R19 (38-day frozen pipeline going undetected 2026-03-01 -> 2026-04-08) was empirical proof that the analyst was not engaging with the inbox.

When the three metrics diverge: engagement wins for daily decisions, the leading metric wins for weekly decisions, and the lagging metric wins for destination-setting. If engagement drops below 3 days/week for 2 consecutive weeks at any point, freeze substrate work and re-evaluate.

## Requirements

### Validated

Inferred from `.planning/codebase/` maps (2026-04-08) - these are shipped, in production, and load-bearing.

#### Collection
- Plugin collector framework (`collectors/base.py`) with retry, rate limiting, and error isolation
- 16 collector implementations across GitHub, SEC, jobs, news, RSS, ArXiv, HN, etc. (9 currently operational; 6 disabled by missing API keys; 1 abandoned)
- Per-source canonical key extraction with strong/weak key candidates
- Suppression cache prevents re-pushing existing Notion prospects

#### Storage
- Async SQLite signal store with WAL mode + FTS5 (`storage/signal_store.py`)
- Schema migrations v27 -> v51 (sequential, never renumbered)
- Knowledge graph tables (v50) and confidence ledger (v51)
- Post-incident DB hardening: signal-count watermark guard, DBToolLock, restore script sidecar handling

#### Processing and Routing
- Two-stage thesis filter: keyword pre-filter + Gemini LLM classification
- LLM thesis classifier prompt v1.6.0 with employer-distribution guard
- Multi-source verification gate (`verification/verification_gate_v2.py`) with weighted signal scoring, founder multiplier, and velocity boost
- Confidence-tiered routing: >=0.7 -> Source, 0.4-0.7 -> Tracking, <0.4 -> Hold

#### Notion Integration
- Notion connector v2 with schema preflight
- ProspectPayload marshaling honoring the exact Notion contract, including the `Dilligence` typo
- Suppression sync from Notion into the local cache at pipeline start

#### Governance and Delivery
- Two-lane state policies: env-backed flags (UPPER_CASE) plus feature registry (lower_case)
- DELIVERY_MODE staging: `staging_only` -> `manual_publish` -> `batch_publish` -> `auto_publish`
- Step 4A `batch_publish` active since 2026-03-16
- Step 4B `MERGE_WRITES_ENABLED=active` active since 2026-04-04, regret check due 2026-04-18
- MCP server boundary (`discovery_engine/mcp_server.py`) as the sole external API surface
- Audit event triggers on every state transition with actor + reason

#### Quality Ops
- 14-subcommand quality CLI (`python -m ops.cli quality ...`)
- Manual TP/FP labeling with audit trail and outcome backfill from `notion_status_events`
- False-positive pattern detection across collectors, categories, and temporal hotspots

#### Monitoring
- SPC monitor with control charts and zero-volume alerting
- Daily aggregator with backfill for absent collectors
- Drift detection plus canary runs
- Health check command (`run_pipeline.py health --json`)

#### Multi-LLM Collaboration
- Maestro orchestrator (`integrations/maestro.py`)

### Active

The current scope is the **Direction-A-derived hybrid** strategy at `docs/plans/2026-04-06-red-team-hybrid/`, revised by the 2026-04-08 synthesis that surfaced engagement (not substrate) as the binding constraint. The original strategy is preserved, Move 0.5 (Liveness Restoration) remains a hard prerequisite to activation, Move 3 (Postgres) stays deferred, and the existing Move 1 branch is now refined into a **Cross-Channel Signal Surface** rather than a generic substrate-plus-UX bucket.

**Framing correction (load-bearing):** substrate work and engagement work are complementary, not substitutive. The refined Move 1 branch broadens the signal surface, but it does not supersede Move 0.5 or make R19 less real.

#### Move 0.5 - Liveness Restoration (hard prerequisite to refined Move 1 activation)
- [ ] **R19 P0**: restart collection and keep freshness preconditions on the 2026-04-18 regret check
- [ ] **Wire existing closed-loop skills into cron**: `quality-backfill-notion-status-events` + `quality-backfill-outcomes` + `tuning-proposal-writer` + `tuning-proposal-apply` + `fp-pattern-finder-signals`
- [ ] **Daily digest with empty-channel discipline**
- [ ] **Calibration positives**
- [ ] **Permanent Hold-Review batch**
- [ ] **Pandora-lite digest column**
- [ ] **Engagement metric**: `analyst_inbox_engagement_7d` published daily and used to gate promotion decisions

#### Track A - Substrate hardening (continues in parallel)
- [ ] **Move 0 - Prep** (ends 2026-04-19): docs, designs, and read-only audits only; zero edits to protected paths
- [ ] **Move 1 - Cross-Channel Signal Surface (refined)**: top 3 collectors writing to `data/shadow/artifacts/`; canonical evidence-packet contract with runtime owner in `review_items.evidence_bundle`; Track D CT/DNS as the first required new family after 2026-04-19 and a green liveness gate; UX-03 Why-Now provenance as the primary packet surface; REC-07 outcome-modulated dispatch remains shadow-only in this wave
- [ ] **Move 2 - Golden-set advisory plus bounded-context map**: 30+ days advisory mode, first Tier-2 recall vs baseline, Letterboxd pretotype
- [ ] **Move 3 - DEFERRED**: Postgres dual-write only if the later decision gate validates the substrate-vs-engagement framing
- [ ] **Move 4 - Co-canary decision gate**: fires on BOTH Tier-2 recall AND `analyst_inbox_engagement_7d`

#### Parallel recall tracks
- [ ] **Track B - Company-episode labeling**: secondary canary and random-sampled cohort builder
- [ ] **Track C - Hold-out cohort split**: deterministic, file-based, used by Move 1+
- [ ] **Track D - CT-log + DNS shadow collectors**: starts after 2026-04-19 and is the first required new non-GitHub family in refined Move 1
- [ ] **Track E - Founder watchlist**: bounded auxiliary input; >=50 founders enables first-wave founder-driven activation, otherwise Track E remains auxiliary and does not block Track D. Current known state in `.planning/STATE.md` is 44 founders
- [ ] **Outreach narrative gen + traffic-light timing**: deferred out of the refined Move 1 first wave; post-packet lane only, still digest-only when it eventually lands

#### Cross-cutting
- [ ] **R20 (Showstopper):** analyst abandons inbox habit; mitigated by Move 0.5
- [ ] **API key provisioning:** 6 of 16 collectors disabled by missing keys; opportunistic, not blocking

### Out of Scope

#### From the investment thesis
- B2B / Enterprise SaaS
- Developer tools
- Crypto / Web3
- Cleantech / climate
- Services / agencies
- Series B+
- Hardware-only

#### Current milestone engine-side exclusions
- Outreach narrative generation and traffic-light timing are excluded from the refined Move 1 first wave even though they remain candidates for a later packet-derived lane.
- CRM auto-create behavior beyond current routing surfaces is excluded.
- Automated outbound to founders is excluded.
- Automated investment decisions are excluded.
- Multi-tenancy / multi-analyst work is excluded.
- LinkedIn scraping remains excluded due to ToS exposure.
- Black-box ML scoring remains excluded; every score component must be explainable.

## Context

### Project shape
- Brownfield codebase with substantial existing architecture
- Windows-first development; Linux/Docker in production
- Single-user product today
- Multi-LLM collaboration available

### Critical active issue (R19 - 2026-04-08)
**The data collection pipeline has been silently frozen since 2026-03-01.**
- Signal corpus = 612 (no new ingest)
- `max(detected_at)` = 2026-03-01
- Last actual pipeline run = 2026-03-24
- 2026-04-04 "signals processed" entries were re-classifications, not new ingest
- The 2026-04-18 Step 4B regret check will be invalid on stale data unless collection is restarted or the gate is postponed

### Operational state at initialization (2026-04-08)
- Branch: `prep/red-team-hybrid-prep`
- Canonical plan: `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md`
- Protected paths during Move 0: `collectors/`, `workflows/`, `governance/`, `monitoring/`, `connectors/`, `storage/migrations/`
- Last canary: run #56 = 91.46% pass (on frozen data)
- Drift alerts: 0 unacknowledged
- `LLM_THESIS_MODE=active`
- `DELIVERY_MODE=batch_publish`

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Three-tier success metric (engagement + leading + lagging) | R19 proved engagement is load-bearing | Pending |
| Move 0.5 inserted as hard prerequisite | Engagement is the binding constraint | Pending |
| Refined Move 1 = Cross-Channel Signal Surface | Broaden by channel family while keeping human-review endpoint and Move 0.5 activation gate | Pending |
| Runtime packet owner = `review_items.evidence_bundle` | One canonical packet store avoids drift across digest, dashboard, and review flows | Pending |
| Track D is the first required new family | It is the cleanest non-GitHub family and already has a design artifact | Pending |
| Track E is auxiliary until >=50 founders | Current known state is 44 founders; founder-driven activation cannot be the first-wave dependency | Pending |
| REC-07 stays shadow-only in refined Move 1 | Dispatch changes require packet and outcome evidence first | Pending |
| UX-01 / UX-02 deferred out of first wave | They are action-adjacent and violate the approved milestone endpoint | Pending |
| Postgres deferred indefinitely | Insurance, not immediate value | Pending |
| OutcomeJoiner already exists | Wiring + display work, not greenfield build | Pending |
| Withdraw "9% precision" claim | Selection bias invalidated it | Corrected |

## Phase 2 confidence-score vocabulary

References use `file:symbol` (not `file:line`) so they don't go stale.

| Term | What it is | Where defined |
|---|---|---|
| `signals.confidence` | REAL column, in [0,1], per-signal raw confidence at storage time | `storage/signal_store.py` (column declaration in the signals-table CREATE) |
| `score_binding.semantic_name = "signal_stored_confidence"` | Locked semantic name for `signals.confidence` in the Day 4 artifact contract | `scripts/recalibrate_conformal.py:SCORE_SEMANTIC_NAME` |
| `ConfidenceBreakdown.overall` | Aggregate confidence after VerificationGate fusion (LLM + structural + reputation). NOT the same as `signals.confidence`. | `verification/verification_gate_v2.py:ConfidenceBreakdown` |
| `state/conformal_calibration.json` | Day 4 artifact (`artifact_type=threshold_selection`) with `score_binding` and `chosen_cutoff` over `signals.confidence` | `scripts/recalibrate_conformal.py` (gitignored) |
| `state/router_config_status.json` | Day 5 status writer output (`readiness_scope=human_review_only`). Embeds `candidate_router_threshold_config` only when not blocked. NEVER a production routing config. | `scripts/write_router_config_status.py` (gitignored) |
| `calibration_semantic_digest` | SHA256 over the semantic content of the Day 4 artifact (excludes `generated_at`, `seed`, `git`; floats normalized to 6 decimals; `allow_nan=False`). | `verification/router_threshold_config.py` |
| Reserved promotion drift codes | `promotion_prompt_version_drift`, `promotion_scoring_path_drift`, `promotion_runtime_threshold_incompatible` — owned by the future router-application gate. NEVER emitted by Day 5; Pydantic Literal enums prevent accidental emission. | reserved in `verification/router_threshold_config.py` module docstring |

### Day 5 human-review handoff (current)

Day 5 human review currently means manual inspection of `state/router_config_status.json`
by the engineer/operator running Phase 2. No approval, rejection, or promotion decision
is persisted by Day 5. Future router-application/gating work owns review persistence and
promotion. The Day 3 dashboard may later surface this status, but Day 5 does not notify
Slack, Notion, or governance systems.

## Evolution

This document evolves at phase transitions and milestone boundaries.

After each phase transition:
1. Move invalidated requirements out of scope with reason.
2. Move validated requirements into the validated section with phase references.
3. Add newly discovered active requirements.
4. Record decisions that future modifiers should not need to rediscover.

After each milestone:
1. Re-check core value and metric ladder.
2. Re-check current exclusions.
3. Update packet/runtime ownership if consumer surfaces change.
4. Update Track E readiness state before any founder-driven activation decision.

---

*Last updated: 2026-04-08 during canonical planning-doc rewrite for the refined Move 1 cross-channel surface branch.*

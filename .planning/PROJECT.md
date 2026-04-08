# Harmonic Discovery Engine

## What This Is

A consumer deal-sourcing pipeline for Press On Ventures (Pre-Seed → Series A, US/UK). It collects multi-source signals (GitHub, SEC, jobs, news, launches, domains, etc.), filters them through a thesis classifier (keyword pre-filter + Gemini LLM), de-duplicates via canonical keys, and routes qualifying prospects into the Press On Notion CRM with confidence-tiered statuses. Built for Press On analysts who would otherwise sift sources manually.

## Core Value

**Qualified consumer leads in the analyst's Notion inbox that they would not have found otherwise.** Every other capability — substrate hardening, governance, monitoring, knowledge graph, recall eval — exists in service of net new true-positive deals reaching a human reviewer.

When tradeoffs hurt, choose the option that increases qualified TPs/week into Notion. Defer everything that doesn't.

## Success Metrics

The product runs on a **two-tier metric** because the lagging indicator is too slow to steer with daily.

- **North Star (lagging, 6-month horizon):** Investments closed where Discovery Engine signals are trace-attributed to a Press On deal (Initial Meeting → Funded). Requires CRM outcome backfill — itself an active workstream gap.
- **Operational proxy (leading, weekly):** Net new true-positive prospects routed to "Source" or "Tracking" that survive analyst review. Watched in tandem with Tier-2 recall (per `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md`).

The two metrics will diverge. When they do, the lagging metric wins; the leading metric is the steering wheel, not the destination.

## Requirements

### Validated

Inferred from `.planning/codebase/` maps (2026-04-08) — these are shipped, in production, and load-bearing.

#### Collection
- ✓ Plugin collector framework (`collectors/base.py`) with retry, rate limiting, error isolation — existing
- ✓ 16 collector implementations across GitHub, SEC, jobs, news, RSS, ArXiv, HN, etc. (9 currently operational; 6 disabled by missing API keys; 1 abandoned) — existing
- ✓ Per-source canonical key extraction with strong/weak key candidates — existing
- ✓ Suppression cache prevents re-pushing existing Notion prospects — existing

#### Storage
- ✓ Async SQLite signal store with WAL mode + FTS5 (`storage/signal_store.py`) — existing
- ✓ Schema migrations v27→v51 (sequential, never renumbered) — existing
- ✓ Knowledge graph tables (v50) and confidence ledger (v51) — existing
- ✓ Post-incident DB hardening: signal-count watermark guard, DBToolLock, restore script sidecar handling — existing (PR #131)

#### Processing & Routing
- ✓ Two-stage thesis filter: keyword pre-filter + LLM (Gemini) classification — existing
- ✓ LLM thesis classifier prompt v1.6.0 with employer-distribution guard (100% golden set accuracy) — existing
- ✓ Multi-source verification gate (`verification/verification_gate_v2.py`) with weighted signal scoring, founder multiplier, velocity boost — existing
- ✓ Confidence-tiered routing: ≥0.7 → Source, 0.4–0.7 → Tracking, <0.4 → Hold — existing

#### Notion Integration
- ✓ Notion connector v2 with schema preflight (statuses, stages, custom properties) — existing
- ✓ ProspectPayload marshaling honoring exact Notion contract (incl. "Dilligence" status typo) — existing
- ✓ Suppression sync: Notion → local cache, refreshed on pipeline start — existing

#### Governance & Delivery
- ✓ Two-lane state policies: env-backed flags (UPPER_CASE) + feature registry (lower_case) — existing
- ✓ DELIVERY_MODE staging: `staging_only` → `manual_publish` → `batch_publish` → `auto_publish` — existing
- ✓ Step 4A `batch_publish` promoted **2026-03-16** (governance event #17) — active
- ✓ Step 4B `MERGE_WRITES_ENABLED=active` promoted **2026-04-04** (governance event #21) — active, regret check due 2026-04-18
- ✓ MCP server boundary (`discovery_engine/mcp_server.py`) — sole external API surface — existing
- ✓ Audit event triggers on every state transition with actor + reason — existing

#### Quality Ops
- ✓ 14-subcommand quality CLI (`python -m ops.cli quality ...`) — labels, stats, patterns, tuning, thesis, export — existing
- ✓ Manual TP/FP labelling with audit trail and outcome backfill from notion_status_events — existing
- ✓ False-positive pattern detection across collectors/categories/temporal hotspots — existing

#### Monitoring
- ✓ SPC monitor with control charts; zero-volume alerting (`SPC_ZERO_VOLUME_ALERTING=true`) — existing (commit `f6602c1`)
- ✓ Daily aggregator with backfill for absent collectors — existing
- ✓ Drift detection + canary runs (last canary #56 = 91.46% pass) — existing
- ✓ Health check command (`run_pipeline.py health --json`) — existing

#### Multi-LLM Collaboration
- ✓ Maestro orchestrator (Codex CLI + Kimi API) for forensic engineer workflows — existing (`integrations/maestro.py`)

### Active

The current scope is the **Direction-A-derived hybrid** strategy at `docs/plans/2026-04-06-red-team-hybrid/`. This is imported as-is into the GSD roadmap (per /gsd-new-project Q2 answer) and decomposed in `.planning/ROADMAP.md`.

#### Track A — Substrate hardening (this branch)
- [ ] **Move 0 — Prep** (current, ends 2026-04-19): docs + designs + read-only audits, **0 edits to protected paths**
- [ ] **Move 1 — Artifact capture**: top 3 collectors writing to `data/shadow/artifacts/`; analyst tooltip; Tier-1/2 baseline
- [ ] **Move 2 — Golden-set advisory + bounded-context map**: 30+ days advisory mode; first Tier-2 recall vs baseline
- [ ] **Move 3 — Postgres dual-write + quarantine**: ≥14 days dual-write; quarantine review cadence
- [ ] **Move 4 — Re-evaluate binding constraint**: pivot to B/D/E if recall flat; continue if climbing

#### Parallel recall tracks (different owners, different commits)
- [ ] **Track B — Company-episode labelling**: target 30–50 episodes by end of Move 0 (canary metric for whole framing)
- [ ] **Track C — Hold-out cohort split**: deterministic seed, file-based, used by Move 1+
- [ ] **Track D — CT-log + DNS shadow collectors**: starts after 2026-04-19
- [ ] **Track E — Founder watchlist**: ≥50 founders in `data/shadow/founder_watchlist.csv` by end of Move 0

#### Cross-cutting active gaps (not in red-team-hybrid plan)
- [ ] **CRM outcome backfill** — required for the lagging success metric. Without this, "investments closed" is unmeasurable.
- [ ] **R19 resolution** — collection pipeline frozen since 2026-03-01; decision needed before 2026-04-18 regret check (see Context).
- [ ] **API key provisioning** — 6 of 16 collectors disabled (companies_house, product_hunt, linkedin, crunchbase, opencorporates, uspto). Reduces source diversity 37.5%.

### Out of Scope

#### From the investment thesis (drives what signals get qualified)
- B2B / Enterprise SaaS — including tools sold to consumer industries — *not consumer*
- Developer tools — *not consumer*
- Crypto / Web3 — *thesis exclusion*
- Cleantech / climate — *thesis exclusion*
- Services / agencies — *thesis exclusion*
- Series B+ — *too late stage for Pre-Seed → Series A focus*
- Hardware-only — *thesis exclusion (consumer hardware is borderline; pure-hardware excluded)*

#### Engine-side
The user explicitly declined to add engine-side scope cuts beyond the thesis exclusions during initialization. Outbound to founders, automated investment decisions, and similar are **not yet excluded** — leaving the door open for future scope expansion.

## Context

### Project shape
- **Brownfield**: substantial existing codebase (16 collectors, 50+ migrations, governance, monitoring, KG, multi-LLM). The codebase maps in `.planning/codebase/` are the entry point — read those before hypothesizing about architecture.
- **Windows-first** development; production runs Linux/Docker; primary dev path is bash on Windows.
- **Single-user product** today: one analyst at Press On Ventures consumes the output. UX optimizations should assume that audience.
- **Multi-LLM collaboration available**: Codex CLI (sandbox-isolated proposals) + Kimi API (256K context) via `integrations/maestro.py` for forensic-engineer workflows.

### Critical active issue (R19 — 2026-04-08)
**The data collection pipeline has been silently frozen since 2026-03-01.** Discovered by 5-agent codebase audit on 2026-04-08.
- Signal corpus = 612 (no new ingest)
- `max(detected_at)` = 2026-03-01
- Last actual pipeline run = 2026-03-24 (run_id 44)
- "X signals processed" entries from 2026-04-04 in MEMORY.md were **re-classifications of existing rows**, not new ingest
- **Impact**: 2026-04-18 Step 4B regret check (MERGE_WRITES_ENABLED=active, governance event #21) will run on stale data unless collection is restarted
- **Required decision before 2026-04-18**: restart collection now with ≥5 days buffer / document explicitly that the regret check evaluates frozen-data behavior / postpone the regret check
- **Tracked in**: `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md` R19 (severity 25, OPEN)

### Operational state at initialization (2026-04-08)
- Branch: `prep/red-team-hybrid-prep` · Move 0 ends 2026-04-19
- Canonical plan: `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md`
- Protected paths during Move 0 (enforced by `scripts/red-team-hybrid/check_protected_paths.sh`): `collectors/`, `workflows/`, `governance/`, `monitoring/`, `connectors/`, `storage/migrations/`
- Last canary: run #56 = 91.46% pass (2026-04-04, on frozen data)
- Drift alerts: 0 unacknowledged (last updated 2026-04-04, on frozen data)
- LLM_THESIS_MODE: active since 2026-03-25 (`THESIS_SKIP_LLM_BELOW=0.0`)
- DELIVERY_MODE: `batch_publish` since 2026-03-16

### Known concerns (from `.planning/codebase/CONCERNS.md`)
- **CRITICAL**: R19 frozen pipeline (above)
- **IMPORTANT**: Savepoint rollback error path untested (`ops/quality/thesis.py:614-641, 670-697`)
- **IMPORTANT**: `_processed_identities` per-run dedup ordering bug (R8) — frozen until Move 0 closes
- **IMPORTANT**: `--dry-run` does not guard all state mutations in active LLM_THESIS_MODE
- **IMPORTANT**: DB guard read-error handler intentionally `# pragma: no cover` at `run_pipeline.py:251`
- **MEDIUM**: ~360 historical signals with NULL `company_name` (likely expected for arxiv/rss; needs re-evaluation)
- **MEDIUM**: Disk growth from artifact retention (R9, deferred to Move 1)
- **MEDIUM**: Pipeline orchestrator monolith (`workflows/pipeline.py` ~2000+ lines, R12, deferred to Move 4)

## Constraints

- **Tech stack**: Python 3.11+ (enforced via `pyproject.toml`); async/await throughout — *codebase invariant, not negotiable in current planning horizon*
- **Storage**: SQLite WAL + FTS5; v51 schema. Postgres dual-write planned for Move 3 — *incremental migration path, not rip-and-replace*
- **External access**: All goes through `discovery_engine/mcp_server.py` MCP boundary — *security invariant (`.claude/rules/invariants.md`)*
- **DB credentials**: Read-only only — Claude/agents never get write DB credentials — *security invariant*
- **Notion contract**: Exact status strings including the "Dilligence" typo; preflight required before every push — *Notion-side schema, can't change without Notion DB migration*
- **Move 0 protected paths**: `collectors/`, `workflows/`, `governance/`, `monitoring/`, `connectors/`, `storage/migrations/` are frozen until 2026-04-19 — *enforced by `scripts/red-team-hybrid/check_protected_paths.sh` and `.claude/hooks/postedit_protected_paths.ps1`*
- **Step 4B regret window**: 2026-04-06 → 2026-04-18, +1 day buffer → 2026-04-19 first safe day for production wiring — *governance contract*
- **Single-user audience**: Press On analyst is the only operator — *no multi-tenancy, no auth complexity until that changes*
- **Investment thesis**: Pre-Seed → Series A consumer (CPG, health tech, travel/hospitality, marketplaces) — *thesis is the source of truth for what counts as "qualified"*
- **API key budget**: 6 collectors disabled by missing keys; provisioning is opportunistic, not blocking — *operational reality, not architectural*
- **Free LLM tier**: Gemini at 1.5M tokens/day from `aistudio.google.com/apikey` is sufficient at current volume — *budget constraint informs prompt design (no unbounded chains-of-thought)*

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| **Direction-A-derived hybrid strategy** (2026-04-06) | Substrate hardening before Postgres lock-in; Evidence Lake promoted to Move 1 because BlobStore + SourceAssetStore + ShadowSidecar already exist (60%+ of work done). | — Pending (Move 4 binding-constraint re-eval) |
| **Soft schema-on-write, not strict Pydantic** (2026-04-06) | Strict schema fails asymmetrically — you miss precisely the early signals you care about when a source changes shape. Soft schema + quarantine + raw retention is the natural extension of the existing two-entity model. | — Pending (validates in Move 1) |
| **Postgres deferred to Move 3** (2026-04-06) | Postgres alone improves nothing about classification quality. The DB hardening incident `04a5e6e` (WAL/SHM corruption) is the failure mode addressed by quarantine at lower cost than migration. | — Pending |
| **Two-tier success metric: leading (leads/week) + lagging (investments closed)** (2026-04-08) | The lagging metric is too slow to steer daily. Leading metric is the steering wheel; lagging metric is the destination. | — Pending |
| **Track B labelling cadence is the canary for the entire framing** (2026-04-06) | If parallel tracks B/D/E stall while Track A ships, Track A's value collapses. Watching Track B's cadence weekly forces honest reframing. | — Pending |
| **LLM_THESIS_MODE = active** (2026-03-25, governance event #16) | Pipeline-bug-fixed `ThesisFilterConfig.from_env()` (PR#127); HN false positives 98.69% → 100% rejection of B2B/dev tools; sandbox validator built first. | ✓ Good (regret check cleared 2026-04-04) |
| **Step 4A DELIVERY_MODE = batch_publish** (2026-03-16, governance event #17) | 5-day observation window (Mar 19–23) all passed; canary stable; batch commit verified. | ✓ Good (regret check cleared 2026-04-04) |
| **Step 4B MERGE_WRITES_ENABLED = active** (2026-04-04, governance event #21) | Step 4A clean for 19 days; SPC stable; merge cascade governance debt cleared. | ⚠️ Revisit (regret check 2026-04-18; load-bearing on frozen data per R19) |
| **GSD initialized retroactively on a brownfield project mid-sprint** (2026-04-08) | Project has functioning canonical plan but no GSD planning structure. Importing the red-team-hybrid plan as-is into ROADMAP.md preserves the existing discipline while gaining GSD's phase/state machinery. | — Pending |
| **PROJECT.md scope = whole Discovery Engine, not just current sprint** (2026-04-08) | Per /gsd-new-project Q1 answer. Lets future milestones inherit the long-running product context instead of re-scoping each sprint. | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state (canary numbers, drift alerts, governance state)

---

*Last updated: 2026-04-08 after retroactive GSD initialization on brownfield project at branch `prep/red-team-hybrid-prep`, Move 0.*

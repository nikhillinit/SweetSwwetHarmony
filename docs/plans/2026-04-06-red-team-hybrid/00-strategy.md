# Strategy: Direction-A-Derived Hybrid (Substrate Hardening + Parallel Recall Tracks)

**Date:** 2026-04-06
**Owner:** TBD (lead engineer)
**Status:** Move 0 — prep on `prep/red-team-hybrid-prep` branch
**Supersedes:** "Hybrid Engine-Efficacy Strategy" working draft (renamed per red-team §10.2)

---

## 1. Naming correction

This strategy is **NOT Direction A** as written in `cross_pollination_analysis.md`.
It is a **Direction-A-derived hybrid** that inherits Direction A's "innate-layer
before adaptive-layer" pattern but rebuilds the sequence around three deliberate
divergences:

| | Memo's Direction A | This strategy |
|---|---|---|
| Critical chain | Postgres Core → Pydantic schema → Typed control plane | Artifact capture → soft schema-on-write → CI gating → Postgres dual-write |
| Innate layer (week 1) | Pydantic on top-3 collectors + 1 golden-set smoke test | Artifact capture on top 3-5 collectors + soft schema + golden-set/canary gate + collector dedup tests |
| Lock sequence | Lock data layer (Postgres) FIRST | Defer Postgres to Move 3 |
| Evidence Lake | Float (high-value but non-blocking) | Promoted to Move 1 |
| Schema enforcement | Strict on-write (Pydantic discards malformed) | Soft on-write with quarantine + raw retention |

**Calling this "Direction A" is wrong** because Direction A's failure modes
(over-planning the dependency graph, treating Pydantic as the recall fix) and
this hybrid's failure modes (Evidence Lake aesthetic mistaken for recall
improvement) are different. Naming it correctly is what gets the right
discipline applied.

The three divergences are individually defensible. The strategy doc must own
them rather than hide them.

### 1.1 Justification for the three divergences

**Evidence Lake promotion (Float → Move 1).** The existing infrastructure makes
the cost asymmetric:
- `storage/blob_store.py` already provides content-addressable, zstd-compressed,
  deduplicated, directory-sharded blob storage
- `storage/source_asset_store.py` already provides the Two-Entity Model
  (`SourceAsset` raw vs `Lead` CRM entity) with `assets.db` separate from
  `signals.db`
- `analytics/shadow_sidecar.py` already enforces the read-only safety contract
  (immutable URI mode + isolated `data/shadow/discovery.db`)

Direction A's float classification was correct for *greenfield* Evidence Lake
work; it is wrong for *amplification* of the existing substrate. The cost
estimate has changed because the floor has moved.

**Postgres deferral (Lock layer → Move 3).** Postgres alone improves nothing
about classification quality. Postgres on top of a quarantine layer that retains
raw evidence inherits the value of those defenses. Postgres before them inherits
the bugs. The DB hardening incident `04a5e6e` (WAL/SHM corruption) is the
specific failure mode the quarantine layer addresses at lower cost than a
migration.

**Soft-schema substitution.** Strict schema-on-write fails asymmetrically: you
miss precisely the early signals you care about when a source changes shape. The
existing `storage/source_asset_store.py` already separates raw from parsed —
soft-schema with quarantine is the natural extension, not a new pattern.

---

## 2. Goal framing (honest version)

**Track A (this document) is substrate hardening, not engine-efficacy improvement.**
It buys runway for the recall levers by stopping silent corruption and creating
replayable evidence. It does not, by itself, put more leads in the analyst's
Notion inbox.

The recall improvements come from **parallel tracks** that run concurrently:

| Track | Owner | Lever | Dependency | Can start |
|---|---|---|---|---|
| **A** (this doc) | Eng | Substrate hardening — artifact capture, soft schema, quarantine, Postgres | Step 4B regret window for production wiring | Move 0 today (shadow paths only) |
| **B** | Eng + Analyst | Company-episode labelling sprint (target 30-50 episodes) | None — uses existing `ops/quality_cli.py quality label` | TODAY |
| **C** | Eng | Hold-out cohort split from Track B labels + 612 existing signals | Track B labels | After Track B reaches 30+ |
| **D** | Eng | CT-log + DNS shadow collectors (deferred Phase 0 move) | Step 4B regret window | After 2026-04-19 |
| **E** | Eng | Founder watchlist population from Notion CRM | None | TODAY |

**The strategy is coherent only if Tracks B/D/E actually run.** If Track A
ships in full and B/D/E stall, this strategy fails on its own goal even if
executed perfectly. Track B's labelling cadence is the canary metric for the
whole framing.

Per the §11 scoping decision: **parallel tracks are confirmed acceptable**
(different people, different commits). This unblocks the framing.

---

## 3. Showstopper guards

### R1 — Step 4B regret window contamination

**Window:** 2026-04-06 → 2026-04-18 (regret check 2026-04-18, +1 day buffer
makes 2026-04-19 the first safe day for production wiring).

**Forbidden paths during the window:**
```
collectors/
workflows/
governance/
monitoring/
connectors/
storage/migrations/
```

**Allowed paths:** `docs/`, `data/shadow/`, `artifacts/`, `scripts/red-team-hybrid/`,
`tests/red-team-hybrid/`, `analytics/shadow_sidecar.py` (read-only consumer only,
no new write paths).

**Enforcement:** `scripts/red-team-hybrid/check_protected_paths.sh` runs before
every commit on this branch. PR #133 demonstrated the discipline; we apply the
same gate.

**Move 0 scope:** docs + design specs + read-only audits ONLY. Code that wires
collectors into the artifact path waits until 2026-04-19.

### R2 — Engine-efficacy mechanism gap

**Hazard:** Track A's moves do not mechanically improve recall/precision. They
improve the substrate that recall work needs. If the goal is presented as
"engine efficacy" without the parallel tracks, the strategy ships in full and
produces zero analyst-visible improvement.

**Mitigation:** Tracks B/D/E run in parallel. The strategy doc (this file)
explicitly frames Track A as substrate hardening. Track B's cadence is watched
weekly; if labelling stalls, the framing is in trouble and the team must escalate
before Move 1 ships.

---

## 4. Move sequence

```
        2026-04-06 ──────── 2026-04-19 ─────── ~Month 2 ──── ~Month 3 ──── ~Month 5 ──── Month 6+
              │                  │                 │              │              │              │
   ┌──────────────────────┐      │                 │              │              │              │
   │  Move 0: prep        │      │                 │              │              │              │
   │  - docs + designs    │      │                 │              │              │              │
   │  - audit (read-only) │      │                 │              │              │              │
   │  - 0 protected edits │      │                 │              │              │              │
   └──────────────────────┘      │                 │              │              │              │
                                 │ ┌─────────────────────────────┐│              │              │
                                 │ │ Move 1: artifact capture   ││              │              │
                                 │ │   on top 3 collectors      ││              │              │
                                 │ │   + analyst tooltip        ││              │              │
                                 │ │   + Tier-1/2 baseline      ││              │              │
                                 │ └─────────────────────────────┘│              │              │
                                 │                                ┌─────────────────────────────┐
                                 │                                │ Move 2: golden-set advisory │
                                 │                                │   + collector fixes         │
                                 │                                │   + bounded-context map     │
                                 │                                │   + first Tier-2 recall #   │
                                 │                                └─────────────────────────────┘
                                 │                                                ┌─────────────────────────────┐
                                 │                                                │ Move 3: Postgres dual-write │
                                 │                                                │   + quarantine table        │
                                 │                                                │   + gate promoted blocking  │
                                 │                                                └─────────────────────────────┘
                                 │                                                              ┌─────────────────┐
                                 │                                                              │ Move 4: re-eval │
                                 │                                                              │   binding       │
                                 │                                                              │   constraint    │
                                 │                                                              └─────────────────┘
   ───────────────────────────────────────────────────────────────────────────────────────────────────────────
   Track B (labelling): runs continuously from today
   Track E (founder watchlist): populated by end of Move 0
   Track D (CT-log + DNS shadow collectors): starts after 2026-04-19
   Track C (hold-out split): created during Move 0, used in Move 1
```

Per-move details live in dedicated docs in this directory. See:

- `01-move-0-charter.md` — Move 0 deliverables and time-box
- `02-bounded-context-map.md` — Move 2/3 prerequisite, drafted in Move 0
- `03-dead-letter-contract.md` — Move 1 quarantine spec
- `04-llm-failure-mode.md` — soft-fail with retain-raw, used in Move 1
- `05-holdout-cohort-design.md` — Track C, used by Move 1 onward
- `06-tier-2-recall-eval.md` — gating contract for Move 1/2/3
- `07-collector-audit.md` — Move 2 prerequisite, drafted in Move 0
- `08-track-b-labelling.md` — parallel track, starts today
- `09-track-e-watchlist.md` — parallel track, starts today
- `10-risk-register.md` — 14 risks from red-team §4 with status

---

## 5. Decision gates

### End of Move 0 (2026-04-19)
- **Track B has produced ≥30 labelled company-episodes** (canary metric for the
  framing — if this is missing, Track A's value collapses)
- Track E has populated `data/shadow/founder_watchlist.csv` with ≥50 founders
- Bounded-context map exists at `02-bounded-context-map.md`
- Dead-letter contract spec exists at `03-dead-letter-contract.md`
- Hold-out cohort split is committed (deterministic seed, file-based)
- Step 4B regret check passes — 0 changes to forbidden paths in last 12 days
- `prep/red-team-hybrid-prep` branch ready to merge (or to stay open if Move 1
  needs more design)

### End of Move 1 (~month 2)
- Top 3 collectors writing to `data/shadow/artifacts/`
- Tier-1 golden set still at baseline (no regression)
- Tier-2 held-out cohort produces a baseline recall number (first-ever measurement)
- Inbox tooltip shipped, visible to analyst
- Disk growth in `data/shadow/artifacts/` matches Fermi estimate within 2x

### End of Move 2 (~month 3)
- Golden-set gate has 30+ days of advisory mode data; false-fail rate documented
- Collector-correctness fixes shipped for top 3-5 audit findings
- Bounded-context map published (promoted from prep doc)
- **First Tier-2 recall eval result vs the Move-1 baseline** — first honest answer
  to "is this strategy working?"

### End of Move 3 (~month 5)
- Postgres dual-write live for ≥14 days
- Quarantine table populated; weekly review cadence demonstrated
- Golden-set gate either promoted to blocking or has a concrete reason it isn't

### Move 4 decision gate
- **If Tier-2 recall is flat:** pivot to Tracks B/D/E. Substrate hardening was a
  prerequisite, not a fix.
- **If Tier-2 recall is climbing:** continue with Move 4 and re-evaluate the
  next bottleneck.

---

## 6. Open questions resolved

| # | Question | Answer (2026-04-06) |
|---|---|---|
| 1 | Tracks B/D/E parallel OK? | YES (different people, different commits) |
| 2 | Analyst tooltip OK? | TBD — assumed YES; verify with analyst before Move 1 |
| 3 | "Engine efficacy" definition | Recall/precision *via* substrate enabling Tracks B/D/E |
| 4 | Founder watchlist in or out? | IN — Track E, scaffolded in Move 0 |
| 5 | Bounded-context split now? | Documentation pass in Move 0; refactor in Move 2/3 |

---

## 7. What this strategy is NOT

- It is not a goal substitution. Track A is data integrity work, framed honestly.
- It is not a recall fix on its own. Tracks B/D/E supply the recall mechanism.
- It is not "Direction A." It is a Direction-A-derived hybrid with different
  sequence and different failure modes.
- It is not greenfield. The existing BlobStore + SourceAssetStore + ShadowSidecar
  + governance state policies are doing 60%+ of the work already; this strategy
  is amplification.
- It is not an excuse to defer Tracks B/D/E. If parallel tracks stall, the
  framing fails and the team must reframe rather than ship Track A in isolation.

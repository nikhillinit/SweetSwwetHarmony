# Bounded-Context Map of `signals.db`

**Date:** 2026-04-06
**Purpose:** Surface the largest hidden cost in Move 3 (Postgres dual-write)
by mapping `signals.db`'s ~120 tables into proposed bounded contexts before
the migration starts. **This is documentation, not code.** No tables move.
No FKs change. The map exists so Move 2 can publish a refactor plan and
Move 3 can budget honestly.

**Source:** `sqlite_master` query on `signals.db` (immutable URI mode) on 2026-04-06.
**Coverage target:** 80% (per Move 0 charter §1). Tables not yet classified are
listed in §6 with a "TBC" label.

---

## 1. Why this matters

The user's hybrid plan defers Postgres to Move 3 with the framing "the bounded
contexts already exist; the migration is dual-write + cutover." Verification
shows that **the bounded contexts do NOT exist** as clean schemas. They exist as
implicit groupings via `canonical_key` and shared FK columns scattered across
~120 tables.

Splitting them into clean contexts is a *prerequisite* to Postgres migration,
not a feature of it. The Move 3 budget needs to include this prerequisite or
the migration will silently slip.

The premortem-time estimate (red-team §3.4):
- 3 weeks of refactoring `signals.db` schema into N contexts
- 6 weeks of dual-write
- 2 weeks of cutover
- = ~11 weeks total, not 6

This map is the first step to making that estimate honest.

---

## 2. Proposed contexts

Twelve contexts. Some are tight (Identity, KG); some are loose (Operational
Core absorbs the cross-cutting tables).

| # | Context | Purpose | Tables (count) | Move 3 priority |
|---|---|---|---|---|
| 1 | **Operational Core** | The signal pipeline state machine | ~14 | Critical — must dual-write first |
| 2 | **Companies** | Company entities + their state machine | ~9 | High |
| 3 | **Founders** | Founder records + provenance | ~4 | High (currently empty per Phase 0) |
| 4 | **Investors** | Investor records, matching, portfolios | ~9 | Medium |
| 5 | **Identity & Resolution** | Canonical keys, aliases, blocking, snapshots | ~10 | Critical (cross-cuts everything) |
| 6 | **Knowledge Graph** | KG nodes/edges + provenance + runs | ~7 | Medium |
| 7 | **Claims & Facts** | Extracted claims, facts, evidence, citations | ~7 | Medium |
| 8 | **Quality & Labels** | TP/FP labels, gold sets, anti-pattern proposals | ~9 | High (Track B feeds this) |
| 9 | **Audit & Governance** | audit_events, audit_log, confidence_ledger, llm_decisions | ~5 | Critical (Step 4B history lives here) |
| 10 | **Monitoring & Drift** | SPC, drift, canary, shadow logs | ~13 | Medium |
| 11 | **Publishing & Notion** | Outbox, batches, status events, suppression | ~7 | High (analyst-facing) |
| 12 | **Search & Hunting** | Hunter queries, saved searches, FTS, filter presets | ~25 (incl. FTS internals) | Low (FTS rebuilds easily) |

Plus ~10 cross-cutting / utility / leftover tables (see §6).

---

## 3. Tables by context

> **Note:** This is a first-pass mapping. Where a table plausibly belongs to
> multiple contexts (common with cross-cutting concerns), it is placed in its
> *primary owner* and noted as cross-cutting.

### 3.1 Operational Core
```
signals                        — the canonical signal record
signal_processing              — staging table for pipeline state
signals_fts*                   — FTS5 internals over signals (5 tables)
signal_quality_metrics         — TP/FP labels (also Quality, see §3.8)
suppression_cache              — Notion-mirrored suppression
idempotency_keys               — pipeline run idempotency
pipeline_runs                  — run history
pipeline_run_history           — extended run history
pipeline_schedules             — scheduler config
run_history                    — generic run history
jobs                           — job queue
job_logs                       — job execution logs
scheduler_locks                — multi-process scheduler lock state
schema_migrations              — migration ledger
```
**FKs out:** `canonical_key` → Identity; `company_canonical_key` → Companies;
`thesis_classification_id` → Quality.
**Invariants:**
- `signals.canonical_key` MUST be present and resolvable (Identity contract)
- `signal_processing.status` transitions go through governance state policies
- Insert into `signals` triggers FTS5 update via SQLite triggers

### 3.2 Companies
```
company_actions
company_embeddings
company_files                  — primary company record (status = promoted/etc.)
company_profiles_fts*          — FTS5 over company profiles (5 tables)
company_state                  — company state machine
discovery_candidates           — pre-promotion candidates
discovery_runs                 — discovery batch run history
```
**FKs out:** `canonical_key` → Identity; signals → Operational Core.
**Invariants:** company_files.status uses governance state policies.

### 3.3 Founders
```
founders                       — currently empty (Phase 0 finding)
founder_experiences
founder_signals                — links founders → signals
founder_schema_migrations      — separate migration ledger (TBC: needed?)
```
**FKs out:** `signal_id` → Operational Core.
**State:** Empty as of 2026-04-06. Track E (founder watchlist) populates a
*shadow* CSV, not these tables.

### 3.4 Investors
```
investors
investor_matches
investor_portfolios
investor_preferences
investor_profile_claims
investor_profile_fts*          — FTS5 over investor profiles (5 tables)
investor_profiles
```
**FKs out:** `canonical_key` → Identity (for investor entity resolution).

### 3.5 Identity & Resolution
```
canonical_key_aliases
asset_registry
asset_to_lead                  — Two-Entity Model link
entity_aliases
entity_blocking_index
entity_key_aliases
entity_migrations
entity_snapshots
entity_stage_history
entity_stages
```
**Cross-cutting role:** Every other context references `canonical_key`. This is
the connective tissue that makes the current schema "one big table" in practice.
**Move 3 implication:** This context CANNOT move independently. It must move
*with* Operational Core or be promoted to its own service that everything calls.

### 3.6 Knowledge Graph
```
kg_nodes
kg_edges
kg_edge_evidence
kg_runs
kg_run_sources
kg_provenance
```
**FKs out:** `signal_id` → Operational Core (via evidence).
**Move 3 implication:** Self-contained except for the evidence link.

### 3.7 Claims & Facts
```
claims
claim_evidence
claim_extractions
claim_facts
fact_citations
predicates
precedents
```
**FKs out:** `signal_id` → Operational Core (via extractions and evidence).

### 3.8 Quality & Labels
```
signal_quality_metrics         — also Operational Core (cross-context)
quality_feedback
quality_metrics_daily
gold_set_companies
gold_set_investor_labels
gold_set_labels
anti_pattern_proposals
pattern_runs
evaluation_runs                — eval harness state
```
**Track B writes here:** new labels from the labelling sprint land in
`signal_quality_metrics` via `ops/quality_cli.py quality label`.
**Track C reads from here:** the hold-out cohort split is computed on
`signal_quality_metrics` rows joined to `signals`.

### 3.9 Audit & Governance
```
audit_events                   — Step 4B governance history (CRITICAL)
audit_log
confidence_ledger              — v51 migration table
llm_decisions
extraction_runs                — also Claims & Facts (cross-context)
```
**Move 3 implication:** `audit_events` triggers fire on operational tables
(per project memory: "feature_promote trigger requires metadata JSON"). This is
the crossing-the-streams problem the premortem warned about. **The trigger
behavior must be replicated 1:1 in Postgres or Step 4B history breaks.**

### 3.10 Monitoring & Drift
```
monitoring_alerts
monitoring_config
monitoring_runs
drift_alerts
canary_drift_alerts
canary_runs
system_health
global_baselines               — SPC baselines
shadow_log
shadow_log_metrics
shadow_disagreements
shadow_entity_runs
ach_analyses                   — ACH matrix outputs
```
**Move 3 implication:** Read-mostly. Could be the *first* context migrated as
a low-risk pilot. SPC baselines are time-series; consider TimescaleDB extension
for Postgres.

### 3.11 Publishing & Notion
```
notion_outbox
notion_status_events
batch_items
publish_batches
review_items
merge_proposals
merge_suggestions
```
**FKs out:** `signal_id` → Operational Core.
**Move 3 implication:** Suppression_cache (in Operational Core) is the
denormalized read side of Notion; keep them in the same context during
migration.

### 3.12 Search & Hunting
```
hunter_budget
hunter_budget_transactions
hunter_negative_keywords
hunter_queries
hunter_results
saved_searches
filter_presets
collections
collection_members
collector_metrics
memory_action_state
memory_facts
memory_facts_fts*              — FTS5 (5 tables)
```
**Move 3 implication:** Lowest priority. FTS5 indexes rebuild from source
tables; can be the last context migrated.

---

## 4. Cross-cutting concerns (the connectivity problem)

**Every context references `canonical_key`.** This is the largest single risk
for the migration: there is no such thing as moving "Operational Core" alone
without dragging Identity & Resolution along, and there is no clean way to
move Identity without breaking 11 other contexts at once.

Three options for Move 3:

| Option | Approach | Cost | Risk |
|---|---|---|---|
| **A** | Big-bang migrate everything to Postgres in one cutover | Lowest dev time | Highest cutover risk |
| **B** | Migrate Monitoring + Hunting first as pilots; learn; then big-bang the rest | Medium | Medium |
| **C** | Promote Identity to a service all contexts call; migrate contexts independently behind the service | Highest dev time | Lowest cutover risk |

The user's hybrid plan implicitly assumes Option B. The Move 3 budget should be
written assuming Option B + a 50% time multiplier for the bounded-context
discovery work.

---

## 5. Triggers and stored procedures (the silent killer)

Per project memory: `audit_events feature_promote trigger requires metadata
JSON with feature_name, from_state, to_state, regret_due_at,
config_snapshot_hash`.

If there are other SQLite triggers in the schema, they must be cataloged before
Move 3 starts. **Trigger inventory is a TODO for Move 0 day 8** (after the
table catalog is stable).

```bash
# Run during Move 0:
sqlite3 signals.db "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
```

Capture the result in `artifacts/red-team-hybrid/trigger-inventory.sql`.

---

## 6. Tables not yet classified (TBC)

The 80% rule applies. The following tables need to be confirmed and classified
on Move 0 day 11:

```
ach_analyses                   — placed in Monitoring tentatively
exit_predictions               — TBC: Companies or Quality?
functional_schemas             — TBC: utility?
diffs                          — TBC: probably Identity (entity diffs)
snapshots                      — TBC: probably Monitoring (canary snapshots)
dns_promotion_aliases          — TBC: Identity (alias type) or Operational Core?
thesis_classifications         — TBC: Quality or Operational Core?
thesis_exemplars               — Quality (gold set support)
thesis_ml_predictions          — TBC: Quality
user_actions                   — TBC: own context (User Sessions)?
user_sessions                  — TBC: own context (User Sessions)?
token_nonces                   — TBC: own context (User Sessions)?
watch_events                   — TBC: own context (Watches)?
watches                        — TBC: own context (Watches)?
community_mentions             — TBC: own context (Community)?
community_sentiment            — TBC: own context (Community)?
sqlite_sequence                — sqlite internal, ignore
```

These are not blocking — Move 3 can proceed with the 80% map if these are
documented as "to be classified during the Move 2 refactor."

---

## 7. Invariants spanning contexts

Documenting invariants surfaces the bugs that will bite during dual-write:

1. **`signals.canonical_key` resolves through Identity & Resolution.**
   The signal is unwritable if the canonical key doesn't resolve. Cross-context
   FK from Operational Core → Identity.

2. **Status transitions must go through governance state policies.**
   `governance/state_policies.py` enforces two-lane policies (env-backed UPPER
   + feature-registry lower). Any direct UPDATE to status columns is illegal
   except via `--direct-db` break-glass. This is enforced in code, not in the
   schema. **Postgres migration must replicate the policy enforcement layer.**

3. **`audit_events.feature_promote` trigger requires JSON metadata schema.**
   See §3.9. The trigger is data-validating, not just logging. Postgres
   equivalent is a `CHECK` constraint on a JSONB column.

4. **`signal_quality_metrics` is the source of truth for TP/FP.**
   Track B writes here; Track C reads from here; the FP rate alerts read here.
   Any context split must keep this table accessible to all readers without
   breaking the FK to `signals`.

5. **`canonical_key_aliases` is the resolution path.**
   Alias-to-canonical resolution happens at write time and at query time. The
   table must be available to every context that references `canonical_key`.

6. **FTS5 triggers fire on parent table writes.**
   `signals` insert → `signals_fts` update via SQLite trigger. Postgres equivalent
   is a generated column or a trigger on the source table; either way, the FTS
   is part of the same context as its parent.

---

## 8. Move 3 prerequisite checklist (for the lead engineer)

Before Move 3 dual-write code lands, the following must exist:

- [ ] Trigger inventory in `artifacts/red-team-hybrid/trigger-inventory.sql`
- [ ] Cross-context FK inventory (one-line summary per FK)
- [ ] Decision: Option A vs B vs C from §4
- [ ] Postgres equivalent of `governance/state_policies.py` (likely: same Python
      module, different DB driver)
- [ ] Postgres equivalent of the `audit_events.feature_promote` trigger (likely:
      a Postgres trigger function with JSONB CHECK)
- [ ] Pilot context selected (recommended: Monitoring + Drift)
- [ ] Cutover rehearsal on a copy of `signals.db` → Postgres (read replication
      first)
- [ ] Rollback plan for each context (likely: revert the FDW pointer back to
      SQLite)

This checklist is the *work* that the bounded-context split represents. Each
item is real engineering hours that the original Move 3 budget did not include.

---

## 9. Open questions

1. **Are Identity & Resolution and Operational Core actually one context in
   disguise?** They share so many invariants that splitting them may not be
   worth the cost. Decision needed before Move 3.
2. **Does the team want to use Postgres FDW for incremental migration?** This
   is the lowest-risk path but adds operational complexity.
3. **Is TimescaleDB worth the dependency for the Monitoring & Drift context?**
   Time-series queries on SPC baselines and canary runs are the natural fit;
   the cost is one more service to operate.

---

## 10. Known gaps in this map (the 80% admission)

- Tables in §6 are not yet classified
- Trigger inventory is a TODO (depends on Move 0 day 8 work)
- View definitions are not surveyed (`SELECT name, sql FROM sqlite_master WHERE
  type='view'` should run on day 8)
- Cross-context query patterns from the application code are not surveyed
  (would require auditing every `cur.execute("SELECT ... JOIN ...")` in the
  codebase — out of scope for Move 0)
- The map does not propose a Postgres schema; it only catalogs the SQLite reality

These gaps are acknowledged. The map is useful at 80% — it surfaces the
prerequisite cost so Move 3 can be budgeted honestly.

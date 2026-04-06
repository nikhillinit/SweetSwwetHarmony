# Evidence Ontology — Schema Mapping (Phase 0, task p0.1)

**Status:** Design — derived from existing columns, no schema migration.
**Date:** 2026-04-06
**Plan reference:** Red-team v2 Phase 0 task `p0.1`
**Companion module:** `analytics/evidence_ontology.py`

## Purpose

The strategy document proposes a new vocabulary (`signal_class`, `gate_state`,
`ladder_level`, Watch / Eligible / Promoted / Archived, L0–L5).

The red-team v2 plan explicitly requires that this ontology be implemented as
**derived values computed from existing columns** before any schema migration.
Migration only happens after the ontology has earned its way in via Phase 1
attribution metrics + Phase 2 shadow ladder evaluation.

This document is the **explicit mapping table** that resolves R5 (parallel
state machine risk).

## Verified existing state machines

| Layer | Field | Values | Source |
|---|---|---|---|
| Routing | `RoutingDecision` (utils/thesis_filter.py) | `QUALIFIED` / `HELD` / `REJECTED` | Computed at processing stage |
| Confidence | `signals.confidence` | `float` 0.0–1.0 | Set by collector |
| Review state | `review_items.status` | `pending` / `approved` / `publish_queued` / `published` / `rejected` | v29 |
| Company file | `company_files.status` | `thin` / `promoted` / `archived` | v29 |
| Notion | Notion page status | `Source` / `Initial Meeting / Call` / `Dilligence` / `Tracking` / `Committed` / `Funded` / `Passed` / `Lost` | Notion contract — fixed |

## Proposed evidence classes (derived, not stored)

The strategy doc proposes four classes. Each is computed from `signals.source_api`
plus optional metadata. **No new column. No new table. Pure function over existing rows.**

| Class | Source APIs that count toward this class | Strength heuristic |
|---|---|---|
| `INFRASTRUCTURE_INTENT` | `domain_whois`, `sec_edgar`, `companies_house`, `opencorporates`, (future: `ct_log`, `dns_fingerprint`) | High when ≥2 sub-sources agree on same canonical_key |
| `HUMAN_TRANSITION` | `linkedin`, `github_activity`, (future: `gh_negative_space`, `founder_watchlist`) | High when timestamp clusters within 14 days |
| `HIRING_VALIDATION` | `job_postings` (greenhouse + lever) | High when ≥3 distinct roles posted within 30 days |
| `AMBIENT_CORROBORATION` | `hacker_news`, `arxiv`, `rss_feeds`, `news_api`, `product_hunt`, `uspto`, `crunchbase` | Single-class, never qualifies tier-2 alone |

**Critical:** `AMBIENT_CORROBORATION` collectors are explicitly **demoted** from
trusted single-source promotion in this ontology (per U5). They corroborate but
never sole-qualify. This is a derived-rule change, not a runtime change — it
only affects the *shadow* sidecar's evaluation, not the live `_meets_promotion_criteria`.

## Mapping table — proposed → existing

| Proposed (strategy doc) | Existing equivalent | Mapping |
|---|---|---|
| `signal_class` (column) | derived from `signals.source_api` | computed by `analytics/evidence_ontology.classify_source_api()` |
| `gate_state = Watch` | `company_files.status = thin` AND no `RoutingDecision = QUALIFIED` yet | derived |
| `gate_state = Eligible` | `company_files.status = thin` AND tier-2 evidence-bundle rule passes (shadow only) | derived |
| `gate_state = Promoted` | `company_files.status = promoted` | 1:1 |
| `gate_state = Archived` | `company_files.status = archived` | 1:1 |
| `ladder_level = L0` | no signals yet (does not exist in DB; theoretical) | not stored |
| `ladder_level = L1` | `company_files.status = thin`, ambient class only | derived |
| `ladder_level = L2` | `company_files.status = thin`, ≥1 non-ambient class | derived |
| `ladder_level = L3` | `company_files.status = thin`, ≥2 distinct non-ambient classes | derived |
| `ladder_level = L4` | `company_files.status = promoted` (current OR-rule) | derived |
| `ladder_level = L5` | `review_items.status IN (approved, publish_queued, published)` | derived |

**Result:** every proposed concept maps to a pure function over existing
columns. **No schema migration needed for the ontology itself.** The migration
question (`shadow_ladder_level INTEGER`) only arises in Phase 2 if the shadow
ladder needs persistent storage for SPC comparison — and even then, it is one
additive column on `company_files`, not five new fields.

## Notion vocabulary preservation

The Notion contract is fixed (`.claude/rules/invariants.md`). The new internal
vocabulary maps to Notion **only through tier 3 routing**, and only after Phase
5 explicitly proposes the change with analyst sign-off:

| Internal (tier 3 → Notion) | Notion status |
|---|---|
| L4 + analyst approves | `Source` |
| L4 + analyst defers | `Tracking` |
| L5 (published) | (whatever the analyst sets in Notion) |
| L1/L2/L3 | **never reaches Notion** — shadow only |

This guarantees R9 cannot fire: analysts continue to see only the Notion vocabulary they trust.

## What this document does NOT propose

1. No `signal_class` column on `signals` table.
2. No `gate_state` column on `company_files` table.
3. No `ladder_level` column on `company_files` table.
4. No new state-machine in `governance/state_policies.py`.
5. No edits to `_meets_promotion_criteria` in `workflows/thin_file_manager.py`.

All four are deferred until shadow comparison (Phase 2) demonstrates value.

## Acceptance criterion for promoting any concept to schema

A concept earns its way into a column only if **all** of the following hold:

1. The shadow sidecar has used it for ≥14 days
2. SPC comparison shows the derived value adds discrimination over the existing
   column union
3. A query that needs the value cannot be answered with a SQL CASE / Python
   helper in <100ms over the full DB
4. Phase 2 review breakpoint approves the migration

Until then, every value lives in `analytics/evidence_ontology.py` as a pure function.

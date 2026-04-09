# Evidence Packet Contract

Date: 2026-04-08
Status: canonical contract for the refined Move 1 cross-channel packet surface

## Purpose

Define one canonical evidence-packet contract for the refined Move 1 branch so digest, review, dashboard, and `Why Now` consumers all project from the same runtime object.

This contract exists because the current brownfield surfaces are only partial:
- `review_items.evidence_bundle` is currently minimal
- the digest builder is company-oriented
- the dashboard reads a flat Notion `Why Now` field

The refined Move 1 branch upgrades those consumers to a shared packet contract instead of letting each surface invent its own payload.

## Milestone Boundary

This contract is for the refined Move 1 first wave only.

Included:
- discovery
- scoring
- provenance
- review endpoint semantics
- family mode metadata

Excluded:
- outreach narrative generation
- outreach timing / traffic-light recommendations
- CRM auto-create behavior
- automated routing changes beyond current review surfaces

## Ownership Model

### Canonical document owner

This file is the canonical schema and semantics reference.

### Runtime owner

After post-freeze implementation, the runtime packet owner is:
- `review_items.evidence_bundle`

### Derived consumers

These are projections of the runtime packet, not independent packet stores:
- digest annotations
- dashboard packet views
- generated `Why Now` text

## Activation Timing

### Pre-2026-04-19

Allowed:
- schema definition
- JSON fixtures
- consumer contracts in docs

Not allowed:
- protected-path implementation
- migrations
- runtime packet claims beyond the current brownfield shape

### Post-2026-04-19

Required implementation work:
- expand `review_items.evidence_bundle`
- add any required migration/store changes
- bind digest/dashboard/`Why Now` projections to the same runtime packet

## Required Runtime Fields

Every packet instance must contain:

- `schema_version`
- `canonical_key`
- `company_name`
- `source_family`
- `signal_ids`
- `provenance_summary`
- `score_rationale`
- `family_mode`
- `review_endpoint`
- `created_at`

Optional fields:

- `source_links`
- `supporting_quotes`
- `comparable_surface_summary`
- `freshness_context`
- `engagement_context`

## Field Semantics

### `schema_version`

Versioned so packet consumers can evolve without silent breakage.

### `canonical_key`

Primary stable identity for packet joins and review-item ownership.

### `source_family`

Must be one of the explicitly modeled channel families, for example:
- `production_existing`
- `ct_dns`
- `founder_aux`
- `pattern_search`

### `signal_ids`

List of contributing source signals. This remains the minimum audit spine even if higher-level summaries change.

### `provenance_summary`

Human-readable summary of why the packet exists and what evidence families contributed.

### `score_rationale`

Short explanation of why the packet is ranked where it is. This is not a model dump; it is a reviewer-facing rationale.

### `family_mode`

Must be one of:
- `disabled`
- `shadow`
- `active`

### `review_endpoint`

Must identify the human-review destination, for example:
- `review_item`
- `digest_annotation`
- `dashboard_projection`

## Family Mode Contract

Because `workflows/feature_guards.py` is write-feature-scoped and not a generic family-mode system, refined Move 1 must define family-level controls explicitly.

Initial config keys:
- `CHANNEL_FAMILY_CT_DNS_MODE`
- `CHANNEL_FAMILY_FOUNDER_AUX_MODE`
- `CHANNEL_FAMILY_PATTERN_SEARCH_MODE`

Mode semantics:
- `disabled`: no collection, no packet emission
- `shadow`: collect and emit packet artifacts/metrics only; no review-item promotion
- `active`: eligible to create review items and digest-visible packet projections; still no outreach or CRM behavior in this milestone

## Track E Readiness Gate

Founder-driven activation is optional in the first wave.

Activation rule:
- if `data/shadow/founder_watchlist.csv` has at least 50 founders, `founder_aux` may enter first-wave activation
- otherwise `founder_aux` remains auxiliary and does not block `ct_dns`

Current known state at planning time:
- 44 founders, based on `.planning/STATE.md`

## JSON Shape

```json
{
  "schema_version": 1,
  "canonical_key": "domain:example.com",
  "company_name": "Example Co",
  "source_family": "ct_dns",
  "signal_ids": [101, 205, 333],
  "provenance_summary": "New CT certificate and DNS hostname activity for a previously unseen domain cluster.",
  "score_rationale": "Multiple fresh infrastructure signals matched a consumer thesis pattern with no conflicting hold signals.",
  "family_mode": "shadow",
  "review_endpoint": "review_item",
  "created_at": "2026-04-08T18:27:09Z",
  "source_links": [
    "https://crt.sh/?q=example.com",
    "dns://example.com"
  ],
  "supporting_quotes": [],
  "comparable_surface_summary": "No comparable surfaced packet in the last 30 days.",
  "freshness_context": "Operational collectors fresh within threshold.",
  "engagement_context": "analyst_inbox_engagement_7d=4"
}
```

## Consumer Rules

### Review queue

`review_items.evidence_bundle` is the source of truth after the migration.

### Digest

Digest output may render a shortened packet view, but it must derive from the packet runtime owner.

### Dashboard

Dashboard output may render a richer packet view, but it must derive from the packet runtime owner.

### `Why Now`

`Why Now` is a text projection of the packet, not a separate manually-shaped contract.

## Verification Requirements

Packet implementation is not complete until all of the following are true:

1. Required fields validate.
2. Packet consumers use the canonical contract.
3. `review_items.evidence_bundle` is the runtime owner.
4. Family mode is recorded in every packet/report.
5. No action-surface fields leak into the first-wave packet.

## Non-Goals

- replacing broader prospect or company storage
- introducing outreach timing fields in the first wave
- introducing CRM automation fields in the first wave
- allowing packet consumers to fork their own schemas

---

*This file is the contract owner. Future modifiers should update this file before changing any packet consumer.*

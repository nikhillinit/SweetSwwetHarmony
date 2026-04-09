# Family Mode Contract

Date: 2026-04-08
Status: pre-freeze contract for refined Move 1 rollout controls

## Purpose

Define explicit family-level rollout controls for the refined Move 1 Cross-Channel Signal Surface branch.

This contract exists because the current `workflows/feature_guards.py` surface is write-feature scoped and does not yet provide a generic channel-family mode system.

## Scope Boundary

This contract governs first-wave channel families only:
- `ct_dns`
- `founder_aux`
- `pattern_search`

It does not govern:
- CRM routing states
- outreach timing
- outreach narrative generation
- broader action-surface behavior

## Config Keys

Required first-wave config keys:
- `CHANNEL_FAMILY_CT_DNS_MODE`
- `CHANNEL_FAMILY_FOUNDER_AUX_MODE`
- `CHANNEL_FAMILY_PATTERN_SEARCH_MODE`

## Mode Semantics

### `disabled`

- no collection
- no packet emission
- no review-item promotion

### `shadow`

- collection allowed
- packet artifacts and metrics allowed
- no review-item promotion
- no digest-visible projection unless explicitly documented as a shadow-only operator view

### `active`

- collection allowed
- packet emission allowed
- eligible for review-item creation
- eligible for digest-visible packet projection
- still no outreach or CRM auto-create behavior in the refined Move 1 first wave

## Observability Keys

Every emitted packet or report must record:
- `source_family`
- `family_mode`

Recommended first-wave values:
- `production_existing`
- `ct_dns`
- `founder_aux`
- `pattern_search`

## First-Wave Activation Rules

### Required family

`ct_dns` is the first required new family in refined Move 1.

### Conditional family

`founder_aux` is conditional.

Activation rule:
- if `data/shadow/founder_watchlist.csv` contains at least 50 founders, `founder_aux` may enter first-wave activation
- otherwise it remains auxiliary and does not block `ct_dns`

Current known planning-time state:
- 44 founders

### Reserved family

`pattern_search` remains reserved in spec/shadow status until packet/runtime/observability work is proven.

## Activation Preconditions

No protected-path activation is permitted until all of the following are true:

1. `python scripts/red-team-hybrid/freshness_watchdog.py --json` exits `0`
2. the digest path has 7 consecutive successful emissions, including one empty-but-fresh emission
3. `analyst_inbox_engagement_7d` is publishing daily
4. packet fixtures and packet validation tests are green

## Non-Goals

- replacing existing delivery-mode flags
- introducing outreach or CRM actions
- allowing founder readiness to block CT/DNS activation
- turning shadow packets into action-surface behavior

---

*Update this contract before changing any family-mode consumer or first-wave activation rule.*

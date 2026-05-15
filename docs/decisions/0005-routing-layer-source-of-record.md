# ADR 0005: Routing Layer Source of Record

Status: Accepted
Date: 2026-05-15

## Context

Harmonic uses "routing" in several adjacent but distinct places:

- API route auth policy classifies HTTP endpoints and their operator authority.
- Thesis routing decides whether a prospect should continue to verification.
- Verification-gate routing decides the default disposition for verified
  prospect evidence.
- Notion status is a CRM projection of a disposition decision.
- `confidence_ledger` records historical verification decisions and the runtime
  gate configuration used for those decisions.
- Router-threshold candidate config is a human-review artifact and is not
  runtime-effective.

Those meanings are intentionally separate. Treating one document, table, or
module as the authority for every routing-adjacent concern would erase real
pipeline boundaries and make future refactors harder to verify.

## Decision

For pipeline-origin verification disposition, the forward runtime policy source
of record is `VerificationGate` / `PushDecision` in
`verification/verification_gate_v2.py`, after thesis qualification and before
operator override or Notion delivery projection.

The durable historical source of record for recorded verification evaluations
is `confidence_ledger`, including the persisted `policy_version`,
`decision`, `verification_status`, reason payloads, and `routing_config_json`
runtime gate snapshot.

The routing-adjacent layers are assigned as follows:

| Layer | Authority | Source of record |
|---|---|---|
| API route auth policy | HTTP endpoint exposure and operator authority | `docs/decisions/0001-api-route-auth-policy.md`, `tests/fixtures/api_route_policy_inventory.csv`, and `tests/api/test_route_policy_inventory.py` |
| Thesis routing | Upstream thesis-fit qualification; may stop before verification-gate evaluation | `workflows/pipeline.py` thesis-filter branch and persisted thesis classification rows |
| Verification disposition policy | Forward runtime policy for pipeline-origin verification disposition | `verification/verification_gate_v2.py::VerificationGate` and `PushDecision` |
| Verification disposition history | Durable record of evaluated decisions and runtime gate config snapshots | `confidence_ledger` via `storage/signal_store.py::save_confidence_ledger` and migration `v51_confidence_ledger.py` |
| Notion status | Downstream CRM projection/output; may diverge through explicit operator override | `workflows/pipeline.py` queue metadata and `workflows/notion_pusher.py` payload/override logic |
| Router-threshold candidate config | Human-review-only candidate status; not a live router | `verification/router_threshold_config.py` |

ADR 0001 remains the route-auth policy record. This ADR cross-references it
instead of redefining API auth policy.

## Drivers

- Avoid ambiguous "routing" references in future refactors and incident notes.
- Preserve the real pipeline order: thesis qualification precedes verification,
  and delivery projection follows verification.
- Keep policy, historical audit, and downstream projection authority separate.
- Align documentation with the CSV-backed API route-policy ratchet instead of
  older explanatory markdown snapshots.

## Alternatives Considered

### Use ADR 0001 / API route auth as the routing source of record

Rejected. ADR 0001 governs `/api/v1` endpoint exposure and operator authority.
It does not decide prospect disposition, Notion status, verification confidence,
or persisted decision history.

### Use Notion status as the routing source of record

Rejected. Notion status is a downstream projection such as `Source` or
`Tracking`. It can differ from the default gate outcome under explicit operator
override, and it does not encode the full verification policy or evidence.

### Use `confidence_ledger` as the forward policy source of record

Rejected. `confidence_ledger` is the durable historical record for evaluated
gate decisions and runtime snapshots. It is authoritative for what was recorded,
not for what future policy should decide.

### Use router-threshold candidate config as runtime routing config

Rejected. `verification/router_threshold_config.py` marks the status artifact
as `readiness_scope="human_review_only"`, `runtime_effective=False`, and
`may_route=False`. Its embedded candidate config is also inert through
`activation="manual_review_required"` and `production_routing_enabled=False`.

### Use pipeline orchestration as the single routing authority

Rejected. The pipeline sequences thesis routing, gate evaluation, ledger
persistence, and Notion projection, but orchestration is not the same as policy
authority. Naming the orchestrator as the source of record would blur layer
ownership.

## Why Chosen

`VerificationGate` / `PushDecision` is the narrowest current authority that
actually owns forward verification disposition policy:

- `AUTO_PUSH` maps high-confidence evidence to the default `Source` projection.
- `NEEDS_REVIEW` maps medium-confidence or conflicting evidence to the default
  `Tracking` projection.
- `HOLD` and `REJECT` decide non-push outcomes before delivery.
- The gate owns the threshold policy and reads runtime threshold overrides.

This choice keeps the policy source of record in the code that evaluates
evidence, while assigning historical authority to `confidence_ledger` and
downstream CRM shape to Notion projection logic.

## Consequences

- Future docs must qualify "routing" by layer.
- Runtime policy changes to pipeline-origin verification disposition must start
  from `VerificationGate` / `PushDecision` and update corresponding tests.
- Historical audits must use `confidence_ledger`, not a reconstruction from
  current code or Notion status alone.
- Notion status remains an output projection and must not be treated as the
  policy source for Harmonic verification disposition.
- Router-threshold status artifacts remain non-operative until a future ADR and
  promotion gate explicitly activate a runtime path.
- API route auth policy remains independent and continues to be guarded by the
  CSV inventory and route-policy test.

## Follow-ups

- Keep `docs/plans/2026-05-11-refactor-sprint-0-route-policy-inventory.md`
  explicitly secondary to the CSV-backed inventory when current auth markers
  differ from the original Sprint 0 markdown snapshot.
- If router-threshold candidate config ever becomes runtime-effective, create a
  new ADR and promotion gate before wiring it into `VerificationGate`.
- If operator override semantics expand beyond low-confidence `HOLD`, add a
  structured override reason before relying on override rows for audit.
- Add focused tests with any future runtime change; this ADR itself does not
  change behavior.

## Acceptance Criteria

- This ADR names `VerificationGate` / `PushDecision` as the forward runtime
  policy source of record only for pipeline-origin verification disposition.
- This ADR names `confidence_ledger` as the durable historical source of record
  for recorded verification evaluations.
- This ADR states that thesis routing is upstream and can prevent gate
  evaluation.
- This ADR states that Notion status is downstream projection/output and may
  diverge under explicit operator override.
- This ADR states that router-threshold candidate config is non-operative.
- API route auth policy remains cross-referenced through ADR 0001 and the
  route-policy inventory ratchet.
- No runtime behavior changes are part of this ADR.

## Verification

```powershell
python -m pytest tests/api/test_route_policy_inventory.py -q
git diff -- docs/decisions docs/plans tests/api tests/fixtures
```

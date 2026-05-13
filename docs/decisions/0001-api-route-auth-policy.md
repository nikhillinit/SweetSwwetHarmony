# ADR 0001: API Route Auth Policy

Status: Proposed
Date: 2026-05-11

## Context

The API has mixed auth enforcement. RBAC-backed routers exist, but some
mutating surfaces currently have no auth dependency or accept request-body
`actor` as operator identity.

High-risk current gaps:

- `api/routers/actions.py` JSON mutations use body `actor`.
- `api/routers/scheduler.py` schedule CRUD and trigger endpoints depend only
  on `get_scheduler`.
- `api/routers/health.py` ops-rule CRUD has no auth marker.

## Decision

Every route must have an explicit policy classification. Mutating operator
routes use authenticated operator context, never request-body `actor`, except
for public magic-link routes where one-time token validation is the authority.

## Consequences

- Sprint 0 adds route inventory tests before behavior changes.
- Scheduler and ops-rule mutation endpoints receive admin/operator policy in
  focused post-drill branches.
- Existing public health and auth-bootstrap routes remain public only if their
  exposure is explicitly classified.

## Acceptance Criteria

- Every `/api/v1` route is classified.
- New mutating routes without auth policy fail CI.
- Body `actor` cannot be used as authority in changed mutating routes.
- Magic-link routes document token validation as their public authority model.

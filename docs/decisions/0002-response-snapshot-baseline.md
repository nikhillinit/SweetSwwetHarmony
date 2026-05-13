# ADR 0002: V1 Response Snapshot Baseline

Status: Proposed
Date: 2026-05-11

## Context

Broad API refactors risk changing response envelopes, pagination metadata,
error details, and health DTOs. The current API already has shared contracts
such as `BaseResponse`, `ListResponse`, and `ListMeta`, but coverage is uneven
across routers.

## Decision

Before route rewrites or repository/unit-of-work migrations, Sprint 0 captures
fixture-backed response-shape snapshots for representative `/api/v1` routes.
Snapshots assert stable keys and envelopes while normalizing volatile values.

## Consequences

- Runtime behavior is protected before refactor work starts.
- Snapshot tests use ASGI transport with fixture stores or mocks, not live
  `signals.db`.
- Mutation snapshots are dry-run or mocked only.

## Acceptance Criteria

- Selected public, authenticated read, and mutating dry-run/mock routes have
  snapshot coverage.
- `ListMeta.next_cursor` is preserved where cursor pagination exists.
- Snapshot fixtures normalize timestamps, IDs, tokens, and request IDs.
- A response-shape change requires an explicit snapshot update and rationale.

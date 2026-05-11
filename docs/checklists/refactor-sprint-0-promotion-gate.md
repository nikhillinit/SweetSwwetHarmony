# Refactor Sprint 0 Promotion Gate

Status: draft
Applies to: broad API/service refactor work after the freeze-drill readout

## Preconditions

- [ ] `HarmonicFreezeDrill` readout for the 2026-05-12 run is recorded.
- [ ] RSS was not touched before the readout.
- [ ] Operational drift is excluded from the Sprint 0 branch or committed in a
      separate operational note/commit.
- [ ] No live `signals.db` write is part of the Sprint 0 baseline branch.

## Baseline Artifacts

- [ ] Route policy inventory is current.
- [ ] Every `/api/v1` route has an intended auth policy classification.
- [ ] Response-shape snapshot target list is approved.
- [ ] Performance baseline commands are recorded.
- [ ] Static architecture-lint baseline is recorded.
- [ ] ADRs are present and still marked `Proposed` until tests are green.

## Required Tests Before Refactor Promotion

- [ ] Route inventory test: every route has explicit policy.
- [ ] Auth regression test: mutating routes without policy fail.
- [ ] Snapshot scaffold: selected `/api/v1` responses are captured without
      changing shape.
- [ ] Architecture lint: changed files cannot add ratcheted violations.
- [ ] Static checks cover:
      `ListMeta(cursor=...)`, `threading.Lock` in async middleware,
      `HTTPException` below the API boundary, body `actor` authority, and
      fake/static model perspective stubs.

## Promotion Decision

Promote out of Sprint 0 only when:

- route policy, snapshot, and architecture-lint tests pass;
- known baseline violations are listed with owners;
- no scheduler, RSS/HN collection, or live DB behavior changed as collateral;
- the first P0 implementation branch is scoped to route policy, not repository
  or unit-of-work migration.

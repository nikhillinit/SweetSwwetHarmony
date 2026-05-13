# Refactor Sprint 0 Promotion Gate

Status: promoted (gate satisfied 2026-05-12, PRs #169–174)
Applies to: broad API/service refactor work after the freeze-drill readout

## Preconditions

- [x] `HarmonicFreezeDrill` readout for the 2026-05-12 run is recorded. (PR #170)
- [x] RSS was not touched before the readout.
- [x] Operational drift is excluded from the Sprint 0 branch or committed in a
      separate operational note/commit.
- [x] No live `signals.db` write is part of the Sprint 0 baseline branch.

## Baseline Artifacts

- [x] Route policy inventory is current. (PR #171 — 95-entry CSV fixture)
- [x] Every `/api/v1` route has an intended auth policy classification. (PR #171)
- [x] Response-shape snapshot target list is approved. (PR #172)
- [x] Performance baseline commands are recorded. (`tests/baseline_snapshot.json`)
- [x] Static architecture-lint baseline is recorded. (PR #173 — `architecture_lint_baseline.json`)
- [x] ADRs are present and still marked `Proposed` until tests are green. (PR #169 — ADRs 0001–0003)

## Required Tests Before Refactor Promotion

- [x] Route inventory test: every route has explicit policy. (PR #171)
- [x] Auth regression test: mutating routes without policy fail. (PR #171)
- [x] Snapshot scaffold: selected `/api/v1` responses are captured without
      changing shape. (PR #172)
- [x] Architecture lint: changed files cannot add ratcheted violations. (PR #173)
- [x] Static checks cover:
      `ListMeta(cursor=...)`, `threading.Lock` in async middleware (covered by
      lint ratchet PR #173; violation **fixed** in PR #174),
      `HTTPException` below the API boundary, body `actor` authority, and
      fake/static model perspective stubs.

## Promotion Decision

Promote out of Sprint 0 only when:

- route policy, snapshot, and architecture-lint tests pass;
- known baseline violations are listed with owners;
- no scheduler, RSS/HN collection, or live DB behavior changed as collateral;
- the first P0 implementation branch is scoped to route policy, not repository
  or unit-of-work migration.

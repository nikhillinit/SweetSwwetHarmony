# Refactor Sprint 0 Baseline

Status: pre-drill planning only
Created: 2026-05-11
Scope: documentation, inventories, and post-drill baseline targets only

## Gate

Sprint 0 implementation must wait until the induced freeze drill readout is
captured. A read-only task check on 2026-05-11 showed:

| Task | Last run | Result | Next run | Missed runs |
|---|---|---:|---|---:|
| `HarmonicFreezeDrill` | 2026-05-11 08:00:01 local | 0 | 2026-05-12 08:00:00 local | 0 |

The next implementation branch starts after the 2026-05-12 readout is recorded.
Until then, do not change runtime code, scheduler state, RSS/HN collection, or
live DB behavior. RSS remains intentionally untouched for the drill.

Use `docs/runbooks/monday-am-freeze-drill-readout.md` for the current readout
procedure, but treat the 2026-05-12 run above as the active blocker for this
refactor start.

## Current Dirty-State Boundary

The worktree has operational drift that must not be swept into Sprint 0:

- `state/collectors.json`
- `artifacts/keepalive/2026-05-06.json`
- `artifacts/keepalive/2026-05-07.json`
- `artifacts/keepalive/2026-05-10.json`
- `artifacts/keepalive/2026-05-11.json`
- `backups/`
- `scripts/red-team-hybrid/_keepalive_HarmonicFreezeDrill.cmd`
- `.agents/`, `.codex/`, `.omx/`, `.tmp/`
- archived generated plans under `docs/archive/superpowers/`

Before the first post-drill implementation branch, either leave those files
unstaged or make an explicit operational note/commit decision. Do not mix them
with refactor scaffolding.

## Sprint 0 Goals

Sprint 0 establishes baselines before any repository/unit-of-work migration:

1. Route policy inventory: every `/api/v1` route has an intended auth policy.
2. Response-shape targets: selected v1 responses have snapshot coverage.
3. Static architecture ratchet: current violations are recorded and new
   violations fail in changed files.
4. Promotion gate: broad refactor work cannot start until baselines are green.
5. ADR skeletons: the expected architectural decisions have named owners and
   acceptance criteria.

## Non-Goals Before Drill Completion

- No runtime router rewrites.
- No scheduler CRUD or runner changes.
- No RSS, HN, or keepalive collection changes.
- No live `signals.db` writes.
- No repository or unit-of-work migration.
- No broad import graph cleanup.

## Baseline Findings

| Finding | Evidence | Sprint 0 treatment |
|---|---|---|
| `api/routers/actions.py` exposes JSON mutating actions without auth dependency and accepts request-body `actor` | `POST /actions/track`, `/pass`, `/pipeline`, `/snooze` call `request.actor` | classify policy now; patch after drill |
| `api/routers/scheduler.py` exposes schedule CRUD/trigger without auth dependency | routes depend only on `get_scheduler` | classify as scheduler admin surface |
| Ops rule CRUD under `api/routers/health.py` is unauthenticated | `POST/PUT/DELETE /health/ops/rules` have no auth marker | classify as ops admin surface |
| `api/middleware.py` uses `threading.Lock` in async middleware path | `_RateTracker._lock = threading.Lock()` | ratchet baseline, then replace after drill |
| `services/classification_service.py` is 0 bytes | file length 0 | classify as dead/stub service before promotion |
| `integrations/strategy_iterator.py` has placeholder Claude perspective | `_get_claude_perspective()` returns static placeholder content | ratchet against fake consensus promotion |
| Some routers raise `HTTPException` directly instead of contract helpers | actions, companies, entities, health, jobs, scheduler | baseline only; migrate per route after snapshots |

## Route Policy Inventory

The route inventory lives in
`docs/plans/2026-05-11-refactor-sprint-0-route-policy-inventory.md`.

Sprint 0 acceptance for route policy is not "all auth fixed." It is:

- every route has an intended policy classification,
- current gaps are explicit,
- mutating routes without an intended policy fail the inventory test once the
  post-drill test scaffold lands,
- body `actor` is never accepted as authority in new or changed mutating routes.

## Response-Shape Snapshot Targets

Initial snapshot coverage should use ASGI transport and test databases or
mocks. Do not hit production `signals.db` for snapshot generation.

| Target | Why it matters | Notes |
|---|---|---|
| `GET /health` | root liveness envelope | public, minimal shape |
| `GET /api/v1/auth/roles` | bootstrap metadata | public auth-adjacent response |
| `GET /api/v1/jobs/types` | unauthenticated metadata route | decide if it remains public |
| `GET /api/v1/health/detailed` | current health DTO | use fixture store |
| `GET /api/v1/health/collectors` | collector health DTO | use fixture store |
| `GET /api/v1/companies/inbox` | inbox v1 list shape | fixture store, no live DB |
| `GET /api/v1/entities` | entity list shape | seeded test token |
| `GET /api/v1/triage` | `ListResponse` with cursor metadata | seeded test token |
| `GET /api/v1/batches` | batch list envelope | seeded test token |
| `GET /api/v1/hunter/runs` | hunter list envelope | seeded test token |
| `GET /api/v1/canary/status` | canary status envelope | seeded test token |
| `GET /api/v1/entities/merge-suggestions` | merge-review list envelope | seeded test token |

Snapshot rules:

- normalize timestamps, IDs, request IDs, and generated tokens;
- assert keys and envelope shape, not volatile row counts;
- keep mutation snapshots mocked or dry-run only;
- fail if `ListMeta(cursor=...)` appears instead of `next_cursor`.

## Performance Baseline Targets

Capture these after the drill, before code-changing refactors:

```powershell
python -m pytest tests/api/test_contracts.py tests/api/test_middleware.py -q
python -m pytest tests/api/test_rate_limiting.py tests/api/test_scheduler_endpoints.py tests/api/test_governance_router.py -q
python -m pytest tests/api -q --tb=short
python -m pytest tests/performance -q
```

Record:

- route count from `GET /api/openapi.json` under ASGI test transport;
- per-test runtime for the focused API suites above;
- p95 latency for representative fixture-backed route calls;
- import time for `api.main` in a clean process;
- number of static-lint baseline violations.

Do not set a strict latency target until the baseline exists. The first ratchet
target is "no regression larger than 10% without written justification."

## Architecture-Lint Ratchet Design

The first linter should run in ratchet mode:

1. Load a JSON baseline of known violations.
2. Scan changed files first.
3. Fail new violations outside the baseline.
4. Permit existing baseline violations only if the exact file and rule match.
5. Require baseline shrinkage when a touched file fixes a violation.

Initial rule set:

| Rule | Pattern | Current baseline |
|---|---|---|
| `api-listmeta-next-cursor` | `ListMeta(cursor=` | none observed |
| `api-no-threading-lock-in-async-middleware` | `threading.Lock` in `api/middleware.py` | `api/middleware.py` |
| `api-http-exception-boundary` | `HTTPException` outside API boundary | none observed outside `api/` |
| `api-no-body-actor-authority` | `request.actor`, `body.actor`, Pydantic request `actor` on mutating route | `api/routers/actions.py` |
| `strategy-no-placeholder-consensus` | static LLM perspective placeholders presented as consensus | `integrations/strategy_iterator.py` |
| `services-no-empty-module` | 0-byte service modules | `services/classification_service.py` |

Keep the linter separate from runtime code. The post-drill branch can add it
under `scripts/ci/` with tests under `tests/ci/`.

## ADR Drafts

Draft ADR skeletons were added under `docs/decisions/`:

- `docs/decisions/0001-api-route-auth-policy.md`
- `docs/decisions/0002-response-snapshot-baseline.md`
- `docs/decisions/0003-architecture-lint-ratchet.md`

These remain `Proposed` until the freeze-drill readout is captured and the
first Sprint 0 branch is cut.

## Branch Order After Drill

1. `chore/refactor-sprint0-state-boundary`: document or exclude operational
   drift; no runtime changes.
2. `test/refactor-sprint0-route-policy`: add route inventory test and current
   policy fixture.
3. `test/refactor-sprint0-v1-snapshots`: add response-shape snapshot harness.
4. `test/refactor-sprint0-architecture-ratchet`: add static lint baseline and
   ratchet test.
5. `docs/refactor-sprint0-promotion-gates`: land ADRs and promotion gate.
6. Only after those are green, start P0 route auth fixes. Do not start with
   repository/unit-of-work migrations.

## Verification For This Pre-Drill Slice

This pre-drill slice should be verified with docs-only checks:

```powershell
git status --short
python scripts/ci/check_docs_utf8.py docs/plans/2026-05-11-refactor-sprint-0-baseline.md docs/plans/2026-05-11-refactor-sprint-0-route-policy-inventory.md docs/checklists/refactor-sprint-0-promotion-gate.md docs/decisions/0001-api-route-auth-policy.md docs/decisions/0002-response-snapshot-baseline.md docs/decisions/0003-architecture-lint-ratchet.md
```

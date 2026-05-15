# Scheduler Admin RBAC Refactor Slice - RALPLAN-DR

Status: Final review candidate
Date: 2026-05-13
Branch target: `fix/refactor-p0-scheduler-auth-policy`

## Scope

Harden the nine FastAPI scheduler routes with explicit RBAC, update the
route-policy inventory to match runtime markers, and add a complete auth
regression matrix.

This branch is auth-policy only. It must not change scheduler business logic,
storage behavior, live DB state, Windows Task Scheduler state, keepalive
artifacts, collector runtime state, or adjacent health-route auth.

## Guardrails

- Do not touch `signals.db`.
- Do not mutate Windows Task Scheduler state.
- Do not edit keepalive artifacts.
- Do not edit `state/collectors.json`.
- Do not edit `scripts/red-team-hybrid/_keepalive_daily.cmd`.
- Do not edit today's keepalive JSON.
- Defer `OPS_ADMIN` and health ops-rule auth to a separate slice.
- Keep scheduler threadpool and synchronous scheduler behavior unchanged.

## Repo Grounding

- `api/routers/scheduler.py` currently exposes all nine `/api/v1/schedules*`
  routes with only the `get_scheduler` dependency.
- `tests/fixtures/api_route_policy_inventory.csv` already classifies those
  routes as `SCHEDULER_ADMIN`, but their `current_auth_marker` is `none`.
- `api/auth/rbac.py` has the permission-based auth boundary used by newer
  routers, but currently has no `Permission.SCHEDULER_ADMIN`.
- `Role.GP` receives `set(Permission)`; `Role.ANALYST` and `Role.READONLY` do
  not receive admin permissions.
- `tests/api/test_scheduler_endpoints.py` already uses a mock scheduler
  override, so auth regressions can be added without live scheduler or DB use.

## Principles

1. Protect scheduler routes with explicit RBAC and keep the policy visible.
2. Treat GP-only scheduler access as a branch decision to justify, not a
   pre-settled architectural principle.
3. Keep this slice auth-only: RBAC enum/mapping, route dependencies, inventory
   marker, and tests.
4. Require full route-by-route proof of `401` unauthenticated and `403`
   insufficient-role behavior.
5. Avoid collateral churn outside the scheduler auth surface.

## Decision Drivers

1. Scheduler routes are a documented Sprint 0 auth gap.
2. Scheduler endpoints expose operational metadata and control surfaces,
   including create, pause, resume, delete, and trigger.
3. The inventory already names the intended policy class as `SCHEDULER_ADMIN`.
4. The slice should close the code/inventory gap without redesigning scheduler
   read/write product policy.
5. Sprint 0 guardrails require no live scheduler, DB, keepalive, or adjacent
   auth-surface work.

## Viable Options

### Option A: Dedicated Scheduler Permission

Use `Depends(require_permission(Permission.SCHEDULER_ADMIN))` on all nine
scheduler routes.

Pros:

- Matches the existing `SCHEDULER_ADMIN` inventory vocabulary.
- Keeps scheduler ownership explicit and reviewable.
- Preserves a permission seam that can be remapped later without rewriting
  route dependencies.
- Aligns with newer route-level `require_permission(...)` patterns and the
  route-inventory marker introspection.

Cons:

- Adds a new permission enum value and corresponding RBAC assertions.
- Repeats the dependency across nine endpoints.

### Option B: Direct GP Role Gate

Use `Depends(require_role([Role.GP]))` on all nine scheduler routes.

Pros:

- Smaller RBAC surface today.
- Expresses current GP-only behavior directly.

Cons:

- Couples route policy to a specific role instead of a domain permission.
- Weakens the inventory vocabulary already standardized on `SCHEDULER_ADMIN`.
- Makes future scheduler policy changes harder if scheduler administration
  stops being identical to the GP role.
- Uses the coarser legacy role gate instead of the newer permission-based
  denial semantics.

### Option C: Router-Level Shared Dependency

Add a shared scheduler-admin dependency to `APIRouter(...)`.

Pros:

- Lowest code repetition.
- Uniformly protects all routes registered on the router.

Cons:

- Less explicit at each endpoint.
- More dependent on route-inventory introspection of router-level dependencies.
- Does not match the requested route-level dependency shape.

### Option D: Split Scheduler Reads From Writes Now

Use a lower read permission for list/detail/status/history and scheduler-admin
for create/pause/resume/delete/trigger.

Pros:

- Strongest least-privilege story if schedule metadata is later deemed safe
  for analyst/read-only visibility.

Cons:

- Conflicts with the current inventory, which classifies all nine routes as
  `SCHEDULER_ADMIN`.
- Expands this branch from gap closure into product policy redesign.
- Risks reopening Sprint 0 scope while keepalive recurrence evidence is still
  being carried separately.

## Decision

Choose Option A for this branch: introduce `Permission.SCHEDULER_ADMIN`, grant
it to `Role.GP` in the current mapping, and protect all nine scheduler routes
with `Depends(require_permission(Permission.SCHEDULER_ADMIN))`.

This does not claim GP-only scheduler access is a permanent architectural
truth. It is a branch-local policy decision that aligns runtime enforcement
with the current inventory. If scheduler read visibility should later be
relaxed, that should be a separate inventory-backed read/write split.

## ADR Snapshot

Decision:

- Introduce `Permission.SCHEDULER_ADMIN`.
- Grant it to `Role.GP` in this branch.
- Protect all nine scheduler endpoints with
  `Depends(require_permission(Permission.SCHEDULER_ADMIN))`.

Drivers:

- Existing inventory policy names scheduler routes as `SCHEDULER_ADMIN`.
- Scheduler routes expose operationally sensitive metadata and control
  operations.
- Dedicated permission keeps policy vocabulary stable while allowing future
  role remapping.

Alternatives considered:

- `Depends(require_role([Role.GP]))`
- Router-level shared dependency
- Read/write permission split
- Deferred auth hardening

Why chosen:

- Best alignment with current inventory and newer permission-based router
  patterns.
- Narrowest change that makes scheduler policy explicit and testable.
- Keeps future policy flexibility without widening this branch.

Consequences:

- Existing scheduler endpoint tests must authenticate as GP for success-path
  assertions.
- Both `Role.ANALYST` and `Role.READONLY` must be blocked by regression tests.
- `OPS_ADMIN` remains a separate follow-up slice.

Follow-ups:

- Separate narrow health ops-rule auth slice for `OPS_ADMIN`.
- Optional documentation cleanup after implementation so ADR 0001 no longer
  describes scheduler auth as a live gap.
- Potential future inventory-backed split if scheduler reads should be visible
  to non-GP roles.

## Pre-Mortem

### Scenario 1: One Scheduler Route Stays Unprotected

Failure mode:

- A route keeps only `get_scheduler`, leaving an operational control surface
  unauthenticated.

Mitigation:

- Treat the scheduler surface as the fixed nine-route matrix listed below.
- Add route-policy inventory parity and route-complete 401/403 tests.

### Scenario 2: Tests Pass Without Proving RBAC

Failure mode:

- Existing behavior tests gain GP headers, but negative auth tests cover only a
  representative route.

Mitigation:

- Require a valid-request auth matrix for every scheduler endpoint:
  GP success-path coverage where applicable, `401` anonymous, `403` analyst,
  and `403` readonly.

### Scenario 3: Scope Bleeds Into Runtime Or Adjacent Auth

Failure mode:

- The branch starts touching keepalive files, scheduler runtime behavior,
  health ops rules, or live DB state.

Mitigation:

- Keep edits limited to:
  `api/auth/rbac.py`,
  `api/routers/scheduler.py`,
  `tests/api/test_rbac.py`,
  `tests/api/test_scheduler_endpoints.py`,
  `tests/fixtures/api_route_policy_inventory.csv`.
- Review `git diff --name-only` against the guardrails before handoff.

## Execution Plan

### Task 1: Extend RBAC For Scheduler Administration

Files:

- `api/auth/rbac.py`
- `tests/api/test_rbac.py`

Actions:

- Add `Permission.SCHEDULER_ADMIN = "scheduler_admin"` under the admin
  permission block.
- Preserve `Role.GP: set(Permission)` so GP receives the new permission.
- Add explicit assertions:
  - `has_permission(Role.GP, Permission.SCHEDULER_ADMIN)` is true.
  - `has_permission(Role.ANALYST, Permission.SCHEDULER_ADMIN)` is false.
  - `has_permission(Role.READONLY, Permission.SCHEDULER_ADMIN)` is false.

Acceptance:

- Permission exists.
- GP has it.
- Analyst and readonly do not.

### Task 2: Protect All Nine Scheduler Endpoints

File:

- `api/routers/scheduler.py`

Actions:

- Import `OperatorContext`, `Permission`, and `require_permission` from
  `api.auth.rbac`.
- Add an unused `_operator: OperatorContext = Depends(require_permission(Permission.SCHEDULER_ADMIN))`
  parameter to each scheduler handler.
- Keep scheduler injection, return shapes, threadpool usage, and error mapping
  unchanged.

Routes to protect:

1. `GET /api/v1/schedules`
2. `POST /api/v1/schedules`
3. `GET /api/v1/schedules/{schedule_id}`
4. `GET /api/v1/schedules/{schedule_id}/status`
5. `PUT /api/v1/schedules/{schedule_id}/pause`
6. `PUT /api/v1/schedules/{schedule_id}/resume`
7. `DELETE /api/v1/schedules/{schedule_id}`
8. `POST /api/v1/schedules/{schedule_id}/trigger`
9. `GET /api/v1/schedules/{schedule_id}/history`

Acceptance:

- Every route above carries the scheduler-admin dependency.
- No scheduler business logic changes.

### Task 3: Update Route-Policy Inventory

File:

- `tests/fixtures/api_route_policy_inventory.csv`

Actions:

- Change only the nine scheduler rows from `current_auth_marker=none` to
  `current_auth_marker=require_permission(SCHEDULER_ADMIN)`.
- Leave `intended_policy=SCHEDULER_ADMIN`.
- Do not touch `OPS_ADMIN` rows.

Acceptance:

- No scheduler inventory row remains unprotected.
- No non-scheduler row changes.

### Task 4: Add Full Scheduler Auth Regression Matrix

File:

- `tests/api/test_scheduler_endpoints.py`

Actions:

- Add auth-header helpers using `create_access_token` for:
  - `Role.GP`
  - `Role.ANALYST`
  - `Role.READONLY`
- Refit existing success/error behavior tests to send GP headers.
- Add a route matrix with valid request data for all nine endpoints.
- For each route in the matrix, assert:
  - anonymous request returns `401`;
  - `Role.ANALYST` request returns `403`;
  - `Role.READONLY` request returns `403`.

Required matrix rows:

| Method | Path | Valid request setup |
| --- | --- | --- |
| GET | `/schedules` | no body |
| POST | `/schedules` | valid schedule JSON |
| GET | `/schedules/1` | mock schedule exists |
| GET | `/schedules/1/status` | mock status exists |
| PUT | `/schedules/1/pause` | mock pause succeeds |
| PUT | `/schedules/1/resume` | mock resume succeeds |
| DELETE | `/schedules/1` | mock delete succeeds |
| POST | `/schedules/1/trigger` | mock schedule exists and enqueue succeeds |
| GET | `/schedules/1/history` | mock schedule exists |

Acceptance:

- All nine routes have valid-request negative auth coverage.
- Anonymous callers get `401` on all nine routes.
- Analyst and readonly callers get `403` on all nine routes.
- Existing behavior tests still validate `200`, `201`, `202`, `404`, `409`,
  and `422` paths under GP auth.

## Deliberate-Mode Test Plan

### Unit

- `tests/api/test_rbac.py`
- Verify `SCHEDULER_ADMIN` exists and role mapping is exactly:
  - GP allowed;
  - analyst denied;
  - readonly denied.

### Integration

- `tests/api/test_scheduler_endpoints.py`
- Full route-level auth matrix for all nine endpoints with valid requests.
- Existing scheduler behavior remains covered under GP auth.
- `tests/api/test_route_policy_inventory.py`
- Runtime route markers match fixture markers after dependency changes.

### E2E

- N/A for this slice.
- Rationale: this change is isolated to API RBAC on mocked scheduler routes.
  No browser flow, deployed UI, or operator UX contract is being changed.

### Observability

- N/A for new observability artifacts.
- Rationale: this branch does not change scheduler runtime execution, logging
  contracts, Task Scheduler integration, keepalive artifacts, or collector
  state.
- Replacement check: verify no guarded observability/runtime files appear in
  `git diff --name-only`.

## Acceptance Criteria

1. `Permission.SCHEDULER_ADMIN` exists in RBAC.
2. In this branch, GP has `SCHEDULER_ADMIN`; analyst and readonly do not.
3. All nine scheduler routes use `require_permission(SCHEDULER_ADMIN)`.
4. The route inventory fixture marks all nine scheduler routes as
   `require_permission(SCHEDULER_ADMIN)`.
5. Scheduler endpoint tests include full valid-request auth coverage for all
   nine routes.
6. Every scheduler route has `401` unauthenticated coverage.
7. Every scheduler route has `403` insufficient-role coverage for both
   `Role.ANALYST` and `Role.READONLY`.
8. Existing scheduler success/error behavior still passes when called as GP.
9. No guarded files or guarded runtime surfaces are modified.
10. Focused verification passes:

```powershell
pytest tests/api/test_scheduler_endpoints.py tests/api/test_route_policy_inventory.py tests/api/test_rbac.py -q
```

## Verification

Primary verification:

```powershell
pytest tests/api/test_scheduler_endpoints.py tests/api/test_route_policy_inventory.py tests/api/test_rbac.py -q
```

Diff review:

```powershell
git diff --name-only
git diff -- api/auth/rbac.py api/routers/scheduler.py tests/api/test_rbac.py tests/api/test_scheduler_endpoints.py tests/fixtures/api_route_policy_inventory.csv
```

Guardrail check:

- Confirm no diff in `signals.db`.
- Confirm no diff in `state/collectors.json`.
- Confirm no diff in `artifacts/keepalive/`.
- Confirm no diff in `scripts/red-team-hybrid/_keepalive_daily.cmd`.
- Confirm no Task Scheduler commands were run for this slice.

## Execution Handoff

Recommended execution order:

1. Create/switch to `fix/refactor-p0-scheduler-auth-policy`.
2. Edit RBAC enum and RBAC tests.
3. Add scheduler route dependencies.
4. Update scheduler inventory fixture.
5. Add full nine-route auth matrix and GP headers.
6. Run the focused pytest command.
7. Review diff against the guardrails before commit or PR.

Suggested lane:

- `$ralph`: sequential implementation is preferred because the write set is
  compact and the main risk is route coverage completeness.

If using `$team`, split only if needed:

- Worker 1: RBAC enum/tests.
- Worker 2: scheduler route dependencies and inventory.
- Worker 3: scheduler endpoint auth matrix.
- Verifier: focused pytest plus diff guardrail check.

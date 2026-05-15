# Health Ops-Rule CRUD RBAC Plan

Status: consensus plan approved for execution
Created: 2026-05-15
Task: health.py ops-rule CRUD auth-policy slice
Context snapshot: `.omx/context/health-py-ops-rule-crud-20260515T060236Z.md`

## Scope

Guard only the alert-rule CRUD routes in `api/routers/health.py`:

1. `GET /api/v1/health/ops/rules`
2. `POST /api/v1/health/ops/rules`
3. `GET /api/v1/health/ops/rules/{rule_id}`
4. `PUT /api/v1/health/ops/rules/{rule_id}`
5. `DELETE /api/v1/health/ops/rules/{rule_id}`

Do not change alert-rule storage, validation, condition DSL, metrics
collection, builtin rule deletion semantics, public health endpoints, scheduler
routes, collector state, keepalive artifacts, or live DB state.

## Evidence Reviewed

- `.omx/context/health-py-ops-rule-crud-20260515T060236Z.md`
- `.omx/context/routing-layer-as-sor-20260515T053719Z.md`
- `.omx/plans/scheduler-admin-rbac-ralplan-dr-20260513.md`
- `docs/decisions/0001-api-route-auth-policy.md`
- `docs/plans/2026-05-11-refactor-sprint-0-route-policy-inventory.md`
- `docs/checklists/refactor-sprint-0-promotion-gate.md`
- `api/auth/rbac.py`
- `api/routers/health.py`
- `api/routers/scheduler.py`
- `tests/api/test_rbac.py`
- `tests/api/test_rules_endpoints.py`
- `tests/api/test_ops_integration_api.py`
- `tests/api/test_scheduler_endpoints.py`
- `tests/api/test_route_policy_inventory.py`
- `tests/fixtures/api_route_policy_inventory.csv`

Baseline verification before this plan:

```powershell
python -m pytest tests/api/test_route_policy_inventory.py tests/api/test_rules_endpoints.py -q
```

Result:

```text
24 passed
```

## Principles

1. Align runtime auth with the route-policy inventory instead of inventing a
   parallel policy language.
2. Keep reads and mutations distinct because the inventory already classifies
   rule reads as `AUTH_VIEW` and rule mutations as `OPS_ADMIN`.
3. Preserve public health and metrics endpoints outside `/health/ops/rules*`.
4. Add tests that prove both route coverage and role semantics, not only happy
   paths with auth headers.
5. Keep this branch to auth policy and regression tests; leave routing-layer
   source-of-record documentation to the sibling ADR lane.

## Decision Drivers

1. `tests/fixtures/api_route_policy_inventory.csv` is the live ratchet and
   currently marks the five ops-rule routes as gaps with `current_auth_marker=none`.
2. `api.auth.rbac` already provides `Permission`, `OperatorContext`, and
   `require_permission`, and scheduler auth just proved this pattern locally.
3. Existing alert-rule behavior tests are broad enough that the implementation
   should add auth without changing the CRUD business logic.

## Options

### Option A: Read/Write Split With New `OPS_ADMIN` Permission

Use `require_permission(Permission.VIEW)` for the two GET routes. Add
`Permission.OPS_ADMIN = "ops_admin"` and use
`require_permission(Permission.OPS_ADMIN)` for POST, PUT, and DELETE.

Pros:

- Matches the existing inventory exactly.
- Lets readonly and analyst users inspect rules while keeping mutation GP-only.
- Preserves the permission-based pattern used by scheduler-admin.
- Allows future role remapping without changing route code.

Cons:

- Requires endpoint tests to cover two auth behaviors instead of one.
- Exposes rule definitions to all authenticated viewers, which should remain an
  explicit product/security decision.

### Option B: `OPS_ADMIN` For All Five Ops-Rule Routes

Create `OPS_ADMIN` and protect reads and writes with it.

Pros:

- Simple and conservative for rule visibility.
- Smaller role matrix in endpoint tests.

Cons:

- Conflicts with the current `AUTH_VIEW` inventory classification for GET
  routes.
- Turns a route-policy closure into a product-policy change.
- May break readonly operator dashboards that only need to display rule state.

### Option C: Direct `require_role([Role.GP])`

Protect mutating routes by role instead of permission.

Pros:

- Minimal RBAC enum change.
- Clear current role behavior.

Cons:

- Bypasses the existing permission vocabulary.
- Hard-codes the current role decision into route code.
- Does not align cleanly with route inventory marker extraction.

### Option D: Router-Level Dependency

Attach auth to the health router or a broad sub-surface.

Pros:

- Harder to forget a route inside the guarded group.

Cons:

- Risky in this large health router because public health and metrics endpoints
  intentionally remain unauthenticated.
- Requires refactoring route grouping to avoid collateral behavior changes.

## Decision

Choose Option A.

Implement a read/write split:

- `GET /api/v1/health/ops/rules` ->
  `require_permission(Permission.VIEW)`
- `GET /api/v1/health/ops/rules/{rule_id}` ->
  `require_permission(Permission.VIEW)`
- `POST /api/v1/health/ops/rules` ->
  `require_permission(Permission.OPS_ADMIN)`
- `PUT /api/v1/health/ops/rules/{rule_id}` ->
  `require_permission(Permission.OPS_ADMIN)`
- `DELETE /api/v1/health/ops/rules/{rule_id}` ->
  `require_permission(Permission.OPS_ADMIN)`

Grant `OPS_ADMIN` only through `Role.GP: set(Permission)` in the current RBAC
mapping. Do not grant it to `Role.ANALYST` or `Role.READONLY` in this slice.

## ADR Snapshot

Decision:

- Add `Permission.OPS_ADMIN`.
- Use `VIEW` for authenticated ops-rule reads.
- Use `OPS_ADMIN` for ops-rule mutations.
- Keep public health, metrics, ops summary, and ops history routes unchanged.

Drivers:

- Route inventory already classifies ops-rule reads as `AUTH_VIEW` and writes
  as `OPS_ADMIN`.
- Alert-rule mutations affect operational monitoring behavior and require admin
  control.
- Dedicated permission vocabulary keeps policy testable and adjustable.

Alternatives considered:

- `OPS_ADMIN` for reads and writes.
- Direct `Role.GP` dependency.
- Router-level dependency.
- Deferring the gap.

Why chosen:

- It is the narrowest change that closes the live inventory gap while
  preserving the documented public-health boundary.
- It follows the scheduler-admin precedent without broadening scheduler or
  health behavior.
- It gives tests a precise contract: anonymous users are blocked everywhere,
  authenticated viewers can read, and only GP can mutate.

Consequences:

- Existing rules endpoint tests must authenticate.
- Real-DB ops integration tests must authenticate for CRUD setup.
- Dashboard or client code that reads rules without an auth token will need a
  separate product/UI follow-up if that path is real.
- The historical markdown inventory can remain secondary to the CSV fixture.

Follow-ups:

- Routing-layer source-of-record ADR should decide how stale route-policy
  markdown snapshots are archived or refreshed.
- A future product-policy slice can make rule reads admin-only if rule
  visibility proves sensitive.
- Dashboard auth review can validate whether `/health/ops/rules*` browser calls
  already carry the right token.

## Pre-Mortem

### Scenario 1: A Rule Route Remains Public

Failure mode:

- One of the five routes keeps `current_auth_marker=none`.

Mitigation:

- Update the CSV fixture for all five rows.
- Run `tests/api/test_route_policy_inventory.py`.
- Add an auth matrix for all five paths, including anonymous `401`.

### Scenario 2: Mutating Tests Authenticate But Do Not Prove Role Denial

Failure mode:

- Happy-path tests add GP headers, but analyst and readonly mutation requests
  are not covered.

Mitigation:

- Add `403` tests for analyst and readonly against POST, PUT, and DELETE with
  valid payloads.
- Add explicit RBAC role-mapping assertions for `OPS_ADMIN`.

### Scenario 3: Read Endpoints Become Over-Restricted

Failure mode:

- All five routes receive `OPS_ADMIN`, accidentally diverging from the
  `AUTH_VIEW` read classification.

Mitigation:

- Add read success tests for `Role.READONLY` or `Role.ANALYST`.
- Keep route inventory rows for GET routes on `AUTH_VIEW` with
  `require_permission(VIEW)` markers.

### Scenario 4: Scope Bleeds Into Health Runtime Logic

Failure mode:

- The branch changes storage behavior, condition validation, metrics collection,
  public health endpoints, or dashboard UX.

Mitigation:

- Keep implementation files limited to the RBAC, health-router dependency,
  API tests, and CSV fixture surfaces listed below.
- Review `git diff --name-only` before handoff.

## Execution Plan

### Task 1: Extend RBAC With `OPS_ADMIN`

Files:

- `api/auth/rbac.py`
- `tests/api/test_rbac.py`

Actions:

- Add `Permission.OPS_ADMIN = "ops_admin"` under the admin permission block.
- Preserve `Role.GP: set(Permission)` so GP receives the new permission.
- Add explicit assertions:
  - GP has `OPS_ADMIN`.
  - analyst does not have `OPS_ADMIN`.
  - readonly does not have `OPS_ADMIN`.

Acceptance:

- `Permission.OPS_ADMIN` exists.
- Role mapping is GP-only in the current policy.
- Existing scheduler-admin assertions remain unchanged.

### Task 2: Protect The Five Health Ops-Rule Routes

File:

- `api/routers/health.py`

Actions:

- Import `OperatorContext`, `Permission`, and `require_permission`.
- Add `_operator: OperatorContext = Depends(require_permission(Permission.VIEW))`
  to `list_rules` and `get_rule`.
- Add `_operator: OperatorContext = Depends(require_permission(Permission.OPS_ADMIN))`
  to `create_rule`, `update_rule`, and `delete_rule`.
- Do not use `_operator` in business logic.
- Leave `/health/ops`, `/health/ops/metrics`, and `/health/ops/history`
  unchanged.

Acceptance:

- All five targeted routes have the intended dependency.
- No alert-rule storage, validation, response model, or error mapping changes.

### Task 3: Update Route-Policy Inventory

File:

- `tests/fixtures/api_route_policy_inventory.csv`

Actions:

- Change only these five rows:
  - `GET /api/v1/health/ops/rules` ->
    `current_auth_marker=require_permission(VIEW)`
  - `POST /api/v1/health/ops/rules` ->
    `current_auth_marker=require_permission(OPS_ADMIN)`
  - `GET /api/v1/health/ops/rules/{rule_id}` ->
    `current_auth_marker=require_permission(VIEW)`
  - `PUT /api/v1/health/ops/rules/{rule_id}` ->
    `current_auth_marker=require_permission(OPS_ADMIN)`
  - `DELETE /api/v1/health/ops/rules/{rule_id}` ->
    `current_auth_marker=require_permission(OPS_ADMIN)`
- Leave `intended_policy` values unchanged.
- Do not touch scheduler rows.

Acceptance:

- Route inventory markers match FastAPI dependency extraction.
- No non-health ops-rule row changes.

### Task 4: Add Focused Rules Endpoint Auth Coverage

File:

- `tests/api/test_rules_endpoints.py`

Actions:

- Add `_auth_header(role: Role)` helper using `create_access_token`.
- Update existing behavior tests:
  - GET behavior tests use `Role.READONLY` or another `VIEW` role.
  - POST, PUT, DELETE behavior tests use `Role.GP`.
- Add an auth matrix for the five target routes:
  - Anonymous requests return `401` for all five routes.
  - `Role.READONLY` and `Role.ANALYST` can read list/detail when storage is
    present.
  - `Role.READONLY` and `Role.ANALYST` receive `403` for POST, PUT, and DELETE.
- Use valid request bodies for negative mutation checks so failures prove auth,
  not request validation.

Acceptance:

- Rules endpoint tests prove public access is closed.
- Rules endpoint tests prove read access remains available to authenticated
  viewers.
- Rules endpoint tests prove mutation access is GP-only.
- Existing CRUD behavior assertions still pass.

### Task 5: Update Real-DB Ops Integration Tests

File:

- `tests/api/test_ops_integration_api.py`

Actions:

- Add `_auth_header(role: Role)` helper.
- Authenticate create, update, and delete calls as `Role.GP`.
- Authenticate read calls as `Role.READONLY` where practical to prove real-DB
  read behavior under `VIEW`.
- Leave `/health/ops` and `/health/ops/history` tests unauthenticated because
  those routes remain public in this slice.

Acceptance:

- Real DB rule lifecycle still passes after auth dependencies are added.
- Public ops-health and history behavior remains unchanged.

### Task 6: Minimal Documentation Cleanup

Files:

- Optional: `docs/decisions/0001-api-route-auth-policy.md`

Actions:

- If touched, add a short implementation note that scheduler and health
  ops-rule remediation now use dedicated permission classes.
- Do not refresh the historical markdown route inventory in this slice; the
  file already states the CSV fixture is current.
- Do not combine this with the routing-layer source-of-record ADR.

Acceptance:

- Documentation cleanup, if done, does not imply broader route policy closure.
- No stale planning artifacts are treated as runtime source of truth.

## Deliberate Test Plan

Unit:

- `tests/api/test_rbac.py`
- Proves `OPS_ADMIN` exists and is GP-only under the current mapping.

Integration:

- `tests/api/test_rules_endpoints.py`
- Proves endpoint-level auth and existing alert-rule behavior with patched
  storage.
- `tests/api/test_ops_integration_api.py`
- Proves real SQLite ops-rule CRUD still works with auth headers.
- `tests/api/test_route_policy_inventory.py`
- Proves registered route markers match the CSV fixture.

E2E:

- Not required for this API-only slice.
- A separate dashboard/client follow-up can test browser token propagation if
  rule-management UI calls these endpoints directly.

Observability:

- No new observability artifact required.
- Replacement check: `git diff --name-only` should not include collector state,
  keepalive artifacts, scheduler runtime files, live DB files, or dashboard
  assets.

## Acceptance Criteria

1. `Permission.OPS_ADMIN` exists.
2. GP has `OPS_ADMIN`; analyst and readonly do not.
3. The two GET ops-rule routes use `require_permission(VIEW)`.
4. The three mutating ops-rule routes use `require_permission(OPS_ADMIN)`.
5. Public health and ops metrics/history routes remain unauthenticated.
6. Route inventory fixture markers match runtime route dependencies.
7. Anonymous callers receive `401` for all five ops-rule routes.
8. READONLY and ANALYST callers can read ops rules.
9. READONLY and ANALYST callers receive `403` for rule mutations.
10. Existing alert-rule CRUD behavior still passes under the correct auth role.
11. Real-DB rule CRUD integration still passes.
12. No unrelated dirty local state is staged or modified.

## Verification Commands

Focused verification:

```powershell
python -m pytest tests/api/test_rbac.py tests/api/test_rules_endpoints.py tests/api/test_ops_integration_api.py tests/api/test_route_policy_inventory.py -q
```

Optional static guard:

```powershell
python -m py_compile api/auth/rbac.py api/routers/health.py
```

Diff guard before execution handoff:

```powershell
git diff --name-only
git diff --check
```

Expected touched files for implementation:

```text
api/auth/rbac.py
api/routers/health.py
tests/api/test_rbac.py
tests/api/test_rules_endpoints.py
tests/api/test_ops_integration_api.py
tests/fixtures/api_route_policy_inventory.csv
```

Optional documentation file:

```text
docs/decisions/0001-api-route-auth-policy.md
```

Expected untouched local state:

```text
state/collectors.json
artifacts/keepalive/*
scripts/red-team-hybrid/_HarmonicKeepAliveCompositeVerify.cmd
scripts/red-team-hybrid/_keepalive_daily.cmd
.omx/plans/corrected-operational-priorities-ralplan-dr-20260514.md
.omx/plans/keepalive-composite-verdict-ralplan-dr-20260514.md
.omx/plans/open-questions.md
.omx/plans/scheduler-admin-rbac-ralplan-dr-20260513.md
```

## Execution Handoff

Sequential execution lane:

- Use a single implementation pass for the six core files.
- Start with tests for RBAC and endpoint auth matrices, then add route
  dependencies and fixture markers.
- Run focused verification before considering any docs cleanup.

Parallel team lane:

- Not necessary for this narrow slice.
- If split anyway, keep write scopes disjoint:
  - RBAC and `health.py` route dependency worker.
  - `test_rules_endpoints.py` auth-matrix worker.
  - `test_ops_integration_api.py` plus route inventory fixture worker.

Suggested agent roles if the user explicitly requests delegation later:

- `executor`: implement the bounded code/test slice.
- `reviewer`: check route-policy and security regressions.
- `test-engineer`: harden auth matrices and focused verification.

## Consensus Review

Planner verdict: approve.

- The plan closes exactly the documented health ops-rule gap and preserves the
  public-health boundary.

Architect verdict: approve with one tension.

- The health router is large, so route-level dependencies are safer than a
  broad router dependency for this slice. A future subrouter extraction could
  improve structure, but that is outside this auth-policy change.

Critic verdict: approve.

- The plan has testable acceptance criteria, explicit file boundaries, a
  pre-mortem, and verification commands. The main residual risk is downstream
  dashboard/client token propagation, which is correctly marked as a follow-up
  rather than pulled into this API slice.
